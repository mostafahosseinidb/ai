"""The agent kernel: an autonomous loop that must pay its own way.

The order of operations is the whole design:

1.  **Estimate.**  Ask the tool what the action will cost.
2.  **Authorise.**  Ask the chain whether this identity can afford it, under
    both its credit balance and its epoch quota.  If not, the action does not
    run -- and the refusal itself is written to the chain, so "the agent did
    nothing" is distinguishable from "the agent was not allowed to".
3.  **Act.**  Run the tool under a meter.
4.  **Settle.**  Turn the measured usage into signed transactions and submit
    them.  If measured usage overshoots what the agent can pay, the chain
    refuses the record and the kernel halts rather than quietly continuing --
    an agent that cannot account for what it spent has no business spending
    more.

Nothing here trusts the agent to be honest about its own limits, because
nothing here *is* the agent's decision: the balance, the prices and the quota
all live in state the agent cannot write to.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence

from .chain import Chain
from .crypto import SigningKey, canonical_bytes, sha256_hex
from .mempool import Mempool
from .meter import UsageRecord
from .resources import PriceTable, ResourceKind, format_credits
from .state import Receipt, WorldState
from .tools import ToolRegistry, ToolResult, default_registry
from .transactions import (
    Transaction,
    build_attestation,
    build_registration,
    build_usage,
)

__all__ = [
    "Ledger",
    "LocalLedger",
    "AgentKernel",
    "ActionOutcome",
    "Step",
    "Planner",
    "StaticPlanner",
    "BudgetAwarePlanner",
    "AgentHalted",
]


class AgentHalted(RuntimeError):
    """The kernel stopped because it could not account for its own usage."""


@contextmanager
def _batched(ledger: Ledger) -> Iterator[None]:
    """Group submissions into one block when the ledger supports it."""
    batch = getattr(ledger, "batch", None)
    if batch is None:
        yield
        return
    with batch():
        yield


# --------------------------------------------------------------------------
# Ledger access
# --------------------------------------------------------------------------

class Ledger(Protocol):
    """What the kernel needs from a chain, local or remote."""

    @property
    def state(self) -> WorldState: ...

    def next_nonce(self, address: str) -> int: ...

    def submit(self, tx: Transaction) -> None: ...

    def flush(self) -> list[Receipt]: ...

    def rejection(self, txid: str) -> str | None: ...

    def batch(self) -> Any:
        """Context manager that settles everything submitted inside it at once."""
        ...


class LocalLedger:
    """An in-process chain, sealed by a validator this program controls.

    A networked deployment replaces this class and nothing else: the kernel
    only ever talks through the :class:`Ledger` protocol.
    """

    def __init__(self, chain: Chain, sealer: SigningKey, *, auto_seal: bool = True,
                 mempool: Mempool | None = None) -> None:
        self.chain = chain
        self.sealer = sealer
        self.auto_seal = auto_seal
        self.mempool = mempool or Mempool()
        self._rejections: dict[str, str] = {}
        self._pending_by_sender: dict[str, int] = {}

    @property
    def state(self) -> WorldState:
        return self.chain.state

    def next_nonce(self, address: str) -> int:
        account = self.chain.state.get(address)
        base = account.nonce if account else 0
        return base + self._pending_by_sender.get(address, 0)

    def submit(self, tx: Transaction) -> None:
        self.mempool.add(tx)
        self._pending_by_sender[tx.sender] = self._pending_by_sender.get(tx.sender, 0) + 1
        if self.auto_seal:
            self.flush()

    def flush(self) -> list[Receipt]:
        """Seal everything pending into a block and report what happened."""
        pending = self.mempool.take(len(self.mempool))
        if not pending:
            return []
        result = self.chain.seal_block(self.sealer, pending)
        self.mempool.drop(pending)
        self._pending_by_sender.clear()
        for tx, reason in result.rejected:
            self._rejections[tx.txid] = reason
        return result.receipts

    def rejection(self, txid: str) -> str | None:
        return self._rejections.get(txid)

    @contextmanager
    def batch(self) -> Iterator["LocalLedger"]:
        """Hold sealing until the block exits, so a group settles atomically.

        One action can burn several resources, and settling them across
        different blocks would let an auditor see an agent that paid for its
        tokens but not yet for its compute.  Inside this block they land
        together or not at all.
        """
        previous, self.auto_seal = self.auto_seal, False
        try:
            yield self
        finally:
            self.auto_seal = previous
            self.flush()


# --------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------

@dataclass
class ActionOutcome:
    """The full record of one attempted action, affordable or not."""

    tool: str
    authorised: bool
    executed: bool = False
    ok: bool = False
    value: Any = None
    error: str = ""
    reason: str = ""
    estimated_cost: int = 0
    charged: int = 0
    usage: UsageRecord | None = None
    txids: list[str] = field(default_factory=list)
    settled: bool = True

    @property
    def refused(self) -> bool:
        return not self.authorised

    def summary(self) -> str:
        if self.refused:
            return f"refused  {self.tool}: {self.reason}"
        status = "ok " if self.ok else "err"
        settled = "" if self.settled else "  UNSETTLED"
        return f"{status} {self.tool}: {format_credits(self.charged)}{settled}"


@dataclass(frozen=True)
class Step:
    """One intended action in a plan."""

    tool: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""


class Planner(Protocol):
    def plan(self, goal: str, context: Mapping[str, Any]) -> Sequence[Step]: ...


class StaticPlanner:
    """Replays a fixed list of steps.  Useful for tests and scripted agents."""

    def __init__(self, steps: Iterable[Step]) -> None:
        self._steps = list(steps)

    def plan(self, goal: str, context: Mapping[str, Any]) -> Sequence[Step]:
        return list(self._steps)


class BudgetAwarePlanner:
    """A minimal planner that scales its ambition to the wallet.

    It is not clever, and it is not meant to be -- it exists to show the shape
    a real planner must have: the budget is an input to planning, not an
    error discovered afterwards.
    """

    def __init__(self, *, reserve_fraction: int = 4) -> None:
        self.reserve_fraction = max(1, reserve_fraction)

    def plan(self, goal: str, context: Mapping[str, Any]) -> Sequence[Step]:
        balance: int = context.get("balance", 0)
        prices: PriceTable = context["prices"]
        spendable = balance - balance // self.reserve_fraction   # keep a reserve

        token_price = prices.prices[ResourceKind.LLM_OUTPUT_TOKENS.value]
        budget_tokens = min(512, spendable // (2 * token_price)) if token_price else 512
        if budget_tokens < 8:
            return []

        note = f"goal={goal}"
        return [
            Step("think", {"prompt": goal, "budget_tokens": int(budget_tokens)},
                 rationale="form a view before spending on anything external"),
            Step("remember", {"key": "conclusion", "text": note},
                 rationale="persist the conclusion so the next epoch can build on it"),
        ]


# --------------------------------------------------------------------------
# The kernel
# --------------------------------------------------------------------------

class AgentKernel:
    def __init__(self, key: SigningKey, ledger: Ledger, *, tools: ToolRegistry | None = None,
                 label: str = "", planner: Planner | None = None,
                 halt_on_unsettled: bool = True) -> None:
        self.key = key
        self.ledger = ledger
        self.tools = tools if tools is not None else default_registry()
        self.label = label
        self.planner = planner or BudgetAwarePlanner()
        self.halt_on_unsettled = halt_on_unsettled
        self.memory: dict[str, Any] = {}
        self.journal: list[ActionOutcome] = []
        self._halted_reason: str | None = None

    # -- identity ----------------------------------------------------------

    @property
    def address(self) -> str:
        return self.key.public_hex()

    @property
    def balance(self) -> int:
        return self.ledger.state.balance_of(self.address)

    @property
    def halted(self) -> bool:
        return self._halted_reason is not None

    def register(self, metadata: Mapping[str, Any] | None = None) -> Transaction | None:
        """Announce this identity on chain.  A no-op if already registered.

        Existence is not registration: a grant can create the account before
        the agent ever signs anything, so the flag is what decides.
        """
        account = self.ledger.state.get(self.address)
        if account is not None and account.registered:
            return None
        tx = build_registration(self.key, 0, role="agent", label=self.label, metadata=metadata)
        self.ledger.submit(tx)
        return tx

    # -- authorisation -----------------------------------------------------

    def authorise(self, estimate: Mapping[str, int]) -> tuple[bool, str, int]:
        """Can this agent afford ``estimate`` right now?

        Returns ``(allowed, reason, cost)``.  Both ceilings are checked: the
        credit balance covers the whole basket, and each resource separately
        fits inside the epoch quota.
        """
        state = self.ledger.state
        prices = state.prices
        total = 0
        for kind, amount in sorted(estimate.items()):
            resolved = ResourceKind.parse(kind)
            total += prices.cost(resolved, int(amount))
            remaining = state.quota_remaining(self.address, resolved)
            if remaining is not None and amount > remaining:
                return (
                    False,
                    f"epoch quota for {resolved.value} allows {remaining} more units, "
                    f"the action needs {amount}",
                    total,
                )
        if total > self.balance:
            return (
                False,
                f"needs {format_credits(total)} but holds {format_credits(self.balance)}",
                total,
            )
        return True, "", total

    # -- acting ------------------------------------------------------------

    def perform(self, tool_name: str, **kwargs: Any) -> ActionOutcome:
        """Estimate, authorise, act, settle -- in that order, always."""
        if self.halted:
            raise AgentHalted(self._halted_reason or "agent is halted")

        tool = self.tools.get(tool_name)
        estimate = tool.estimated_cost(**kwargs)
        allowed, reason, estimated_cost = self.authorise(estimate)

        if not allowed:
            outcome = ActionOutcome(
                tool=tool_name, authorised=False, reason=reason, estimated_cost=estimated_cost
            )
            self._attest_refusal(tool_name, estimate, reason)
            self.journal.append(outcome)
            return outcome

        result = tool.invoke(**kwargs)
        outcome = ActionOutcome(
            tool=tool_name,
            authorised=True,
            executed=True,
            ok=result.ok,
            value=result.value,
            error=result.error,
            estimated_cost=estimated_cost,
            usage=result.usage,
        )
        self._settle(outcome, result)
        self.journal.append(outcome)
        return outcome

    def _settle(self, outcome: ActionOutcome, result: ToolResult) -> None:
        """Write the measured usage to the chain, one transaction per resource."""
        evidence = result.usage.digest
        submitted: list[Transaction] = []
        with _batched(self.ledger):
            for kind, amount in result.usage.items():
                if amount <= 0:
                    continue
                tx = build_usage(
                    self.key,
                    self.ledger.next_nonce(self.address),
                    resource=kind,
                    amount=amount,
                    evidence=evidence,
                    tool=result.tool,
                    note=result.error[:256] if not result.ok else "",
                )
                try:
                    self.ledger.submit(tx)
                except Exception as exc:  # pool refusal is as fatal as chain refusal
                    outcome.settled = False
                    outcome.reason = f"could not submit usage for {kind}: {exc}"
                    break
                submitted.append(tx)
                outcome.txids.append(tx.txid)

        for tx in submitted:
            rejection = self.ledger.rejection(tx.txid)
            if rejection is not None:
                outcome.settled = False
                outcome.reason = f"chain refused the usage record: {rejection}"
                break
        outcome.charged = self._charged_for(submitted)

        if not outcome.settled and self.halt_on_unsettled:
            self._halted_reason = (
                f"unsettled usage from {result.tool}: {outcome.reason}. "
                "The agent consumed resources it could not pay for and will not act again "
                "until an authority settles the difference."
            )

    def _charged_for(self, transactions: Sequence[Transaction]) -> int:
        prices = self.ledger.state.prices
        return sum(
            prices.cost(tx.body["resource"], tx.body["amount"])
            for tx in transactions
            if self.ledger.rejection(tx.txid) is None
        )

    def _attest_refusal(self, tool: str, estimate: Mapping[str, int], reason: str) -> None:
        """Record a declined action on chain.

        Transparency about what an agent *chose not to do* is as much a part
        of the audit trail as what it did, and attestations are free so this
        can never be skipped for cost reasons.
        """
        payload = {"tool": tool, "estimate": dict(sorted(estimate.items())), "reason": reason}
        tx = build_attestation(
            self.key,
            self.ledger.next_nonce(self.address),
            topic="budget-refusal",
            digest=sha256_hex(canonical_bytes(payload)),
            summary=f"declined {tool}: {reason}"[:1024],
        )
        try:
            self.ledger.submit(tx)
        except Exception:
            # A refusal we cannot record is not worth crashing over; the
            # absence of the usage transaction is still visible on chain.
            pass

    def attest(self, topic: str, payload: Any, summary: str = "") -> Transaction:
        """Commit an arbitrary record -- a decision, a plan, a result hash."""
        tx = build_attestation(
            self.key,
            self.ledger.next_nonce(self.address),
            topic=topic,
            digest=sha256_hex(canonical_bytes(payload)),
            summary=summary[:1024],
        )
        self.ledger.submit(tx)
        return tx

    # -- the loop ----------------------------------------------------------

    def context(self) -> dict[str, Any]:
        state = self.ledger.state
        return {
            "balance": self.balance,
            "prices": state.prices,
            "height": state.height,
            "epoch": state.epoch_at(state.height),
            "memory": dict(self.memory),
            "tools": [t.name for t in self.tools],
        }

    def run(self, goal: str, *, max_steps: int = 8) -> list[ActionOutcome]:
        """Pursue ``goal`` until the plan is done, the budget bites, or a halt."""
        self.register()
        self.attest("goal", {"goal": goal}, summary=goal[:200])

        steps = list(self.planner.plan(goal, self.context()))[:max_steps]
        outcomes: list[ActionOutcome] = []
        for step in steps:
            if self.halted:
                break
            kwargs = dict(step.kwargs)
            if step.tool == "remember":
                kwargs.setdefault("store", self.memory)
            outcome = self.perform(step.tool, **kwargs)
            outcomes.append(outcome)
            if outcome.refused:
                # Out of budget for this step; a cheaper plan may still exist,
                # so keep going rather than abandoning the goal outright.
                continue
        return outcomes

    def halt_reason(self) -> str | None:
        return self._halted_reason

    def resume(self) -> None:
        """Clear a halt.  Only sensible once the balance has been topped up."""
        self._halted_reason = None
