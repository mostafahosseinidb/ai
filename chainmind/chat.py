"""A conversation that pays for itself, turn by turn.

This is the one place in the project that writes to the chain in response to
something a browser sent.  It exists because a ledger nobody looks at is not
much use, and the most natural way to look at an agent is to talk to it.

What it does **not** become is a general write endpoint.  One operation is
exposed -- take a prompt, run it through the metered agent, return the answer
-- and it goes through exactly the same authorise-before-act path as the
command line.  It cannot grant credits, change policy, or reach the keys
directory.  The agent's budget is the rate limit: a runaway browser loop
runs out of money rather than out of patience.

Conversation history lives here rather than in the browser, so the agent's
own record of what it said is the one that counts.  The digest of each turn
is attested on chain, which makes "what was this conversation" a question the
ledger can answer later.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from .agent import AgentKernel, LocalLedger
from .crypto import canonical_bytes, sha256_hex
from .resources import format_credits

__all__ = ["ChatService", "Conversation", "ChatBusy", "ChatRejected", "MAX_PROMPT_BYTES"]

#: Generous for a prompt, small enough that a single request cannot be used
#: to push megabytes through the model on someone else's budget.
MAX_PROMPT_BYTES = 32 * 1024

#: How much of a conversation is replayed to the model.  Every turn resends
#: the history, so an unbounded one grows the per-turn bill without limit.
DEFAULT_HISTORY_TURNS = 20


class ChatBusy(RuntimeError):
    """A turn is already in flight; the model is not called twice at once."""


class ChatRejected(RuntimeError):
    """The chain refused this turn.  Nothing was sent to the model."""

    def __init__(self, reason: str, estimated_cost: int = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.estimated_cost = estimated_cost


@dataclass
class Turn:
    role: str
    content: str
    at: int = field(default_factory=lambda: int(time.time()))
    cost: int = 0
    usage: Mapping[str, int] = field(default_factory=dict)
    sources: Mapping[str, str] = field(default_factory=dict)
    txids: list[str] = field(default_factory=list)
    height: int | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "at": self.at,
            "cost": self.cost,
            "usage": dict(self.usage),
            "sources": dict(self.sources),
            "txids": list(self.txids),
            "height": self.height,
            "note": self.note,
        }


@dataclass
class Conversation:
    id: str
    turns: list[Turn] = field(default_factory=list)
    started_at: int = field(default_factory=lambda: int(time.time()))

    def history(self, limit: int = DEFAULT_HISTORY_TURNS) -> list[dict[str, str]]:
        """What gets replayed to the model, oldest first, bounded."""
        return [
            {"role": turn.role, "content": turn.content}
            for turn in self.turns[-(limit * 2):]
            if turn.content
        ]

    def digest(self) -> str:
        return sha256_hex(canonical_bytes(
            [{"role": t.role, "content": t.content} for t in self.turns]
        ))

    def title(self) -> str:
        for turn in self.turns:
            if turn.role == "user" and turn.content:
                return turn.content[:60]
        return "conversation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "started_at": self.started_at,
            "title": self.title(),
            "turns": [turn.to_dict() for turn in self.turns],
            "spent": sum(turn.cost for turn in self.turns),
        }


class ChatService:
    """Runs metered conversations against one agent identity."""

    def __init__(self, kernel: AgentKernel, ledger: LocalLedger, *,
                 model_name: str = "", max_tokens: int = 4_000,
                 history_turns: int = DEFAULT_HISTORY_TURNS,
                 attest: bool = True) -> None:
        self.kernel = kernel
        self.ledger = ledger
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.history_turns = history_turns
        self.attest = attest
        self.conversations: dict[str, Conversation] = {}
        # One turn at a time. The budget already bounds total spend, but
        # serialising keeps a stuck tab from opening ten model calls at once.
        self._lock = threading.Lock()

    # -- accessors ---------------------------------------------------------

    def get(self, conversation_id: str) -> Conversation | None:
        return self.conversations.get(conversation_id)

    def open(self, conversation_id: str | None = None) -> Conversation:
        if conversation_id and conversation_id in self.conversations:
            return self.conversations[conversation_id]
        conversation = Conversation(id=conversation_id or uuid.uuid4().hex[:16])
        self.conversations[conversation.id] = conversation
        return conversation

    def listing(self) -> list[dict[str, Any]]:
        return [
            {
                "id": conversation.id,
                "title": conversation.title(),
                "turns": len(conversation.turns),
                "spent": sum(turn.cost for turn in conversation.turns),
                "started_at": conversation.started_at,
            }
            for conversation in sorted(
                self.conversations.values(), key=lambda c: c.started_at, reverse=True
            )
        ]

    def status(self) -> dict[str, Any]:
        state = self.ledger.state
        return {
            "enabled": True,
            "agent": self.kernel.address,
            "label": self.kernel.label,
            "balance": self.kernel.balance,
            "balance_text": format_credits(self.kernel.balance),
            "halted": self.kernel.halted,
            "halt_reason": self.kernel.halt_reason(),
            "max_tokens": self.max_tokens,
            "history_turns": self.history_turns,
            "model": self.model_name,
            "affordable_output_tokens": state.affordable_units(
                self.kernel.address, "llm_output_tokens"
            ),
        }

    # -- the turn ----------------------------------------------------------

    def send(self, prompt: str, conversation_id: str | None = None,
             max_tokens: int | None = None) -> dict[str, Any]:
        """One turn: authorise, ask, settle, remember."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("the prompt is empty")
        if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError(f"the prompt exceeds {MAX_PROMPT_BYTES} bytes")

        if not self._lock.acquire(blocking=False):
            raise ChatBusy("a turn is already in flight")
        try:
            conversation = self.open(conversation_id)
            history = conversation.history(self.history_turns)

            before = self.kernel.balance
            outcome = self.kernel.perform(
                "ask",
                prompt=prompt,
                history=history,
                max_tokens=int(max_tokens or self.max_tokens),
            )
            self.ledger.flush()
            spent = before - self.kernel.balance

            if outcome.refused:
                # The chain said no, so nothing was sent and nothing is
                # remembered: a refused turn must not pollute the history the
                # next turn will be billed for.
                raise ChatRejected(outcome.reason, outcome.estimated_cost)

            answer = ""
            note = ""
            if isinstance(outcome.value, dict):
                answer = outcome.value.get("text", "")
                if outcome.value.get("refused"):
                    note = f"the model declined: {outcome.value.get('reason', '')}"
                elif outcome.value.get("truncated"):
                    note = "the answer hit its token ceiling and was cut short"
            if not outcome.ok and not note:
                note = outcome.error or "the call failed"

            usage = dict(outcome.usage.items()) if outcome.usage else {}
            sources = dict(outcome.usage.sources) if outcome.usage else {}
            height = self.ledger.chain.height

            conversation.turns.append(Turn(role="user", content=prompt, height=height))
            conversation.turns.append(Turn(
                role="assistant", content=answer, cost=spent, usage=usage,
                sources=sources, txids=list(outcome.txids), height=height, note=note,
            ))

            if self.attest:
                self._attest(conversation)

            return {
                "conversation_id": conversation.id,
                "answer": answer,
                "note": note,
                "ok": outcome.ok,
                "cost": spent,
                "cost_text": format_credits(spent),
                "usage": usage,
                "sources": sources,
                "txids": list(outcome.txids),
                "balance": self.kernel.balance,
                "balance_text": format_credits(self.kernel.balance),
                "height": self.ledger.chain.height,
                "turns": len(conversation.turns),
            }
        finally:
            self._lock.release()

    def _attest(self, conversation: Conversation) -> None:
        """Commit the conversation's digest, so its content is provable later.

        Attestations are free, so this never competes with the agent's budget
        for doing the actual work.
        """
        try:
            self.kernel.attest(
                "chat",
                {"conversation": conversation.id, "digest": conversation.digest(),
                 "turns": len(conversation.turns)},
                summary=f"{conversation.id}: {len(conversation.turns)} turns",
            )
            self.ledger.flush()
        except Exception:
            # Failing to record the digest must not lose the answer the user
            # is waiting for; the usage transactions are already on chain.
            pass
