"""Putting a real model behind the agent, on the chain's terms.

The offline ``think`` tool exists so the project runs with no API key.  This
module is the other half: a tool that actually calls Claude, and does it in a
way that fits the ledger's order of operations rather than working around it.

Two API facts make the fit exact:

*   ``messages.count_tokens`` returns the **real** input token count before
    the request is sent.  So the authorisation step stops being a
    characters-over-four guess and becomes the number the provider will
    actually bill.
*   ``response.usage`` reports what the provider counted.  That is not the
    tool's own word, so it is recorded on chain as ``provider`` rather than
    ``declared`` -- weaker than a kernel measurement, stronger than a claim.

This module is the one place in the project with a third-party dependency.
It is optional: ``pip install 'chainmind[model]'``.  Importing it without the
SDK raises a clear error instead of breaking the ledger.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .meter import Meter
from .resources import ResourceKind
from .tools import Tool, ToolError

__all__ = [
    "ClaudeModel",
    "ModelUnavailable",
    "DEFAULT_MODEL",
    "BACKENDS",
    "build_model",
    "build_model_tool",
    "describe_model",
    "register_model_tool",
]

#: Where an agent's thinking can come from.  ``local`` runs on the operator's
#: own hardware and needs no key, no network and no account; ``claude`` is a
#: hosted API.  ``auto`` prefers local, because an agent that cannot think
#: without someone else's permission is not the independent thing this
#: project claims to build.
BACKENDS = ("auto", "local", "claude")

#: Anthropic's current flagship.  Changing this changes what agents cost, so
#: it is a deliberate choice rather than a floating "latest".
DEFAULT_MODEL = "claude-opus-5"

#: Non-streaming default.  High enough not to truncate a real answer,
#: low enough to stay inside the SDK's HTTP timeout.
DEFAULT_MAX_TOKENS = 16_000


class ModelUnavailable(ToolError):
    """The model could not be reached, or the SDK is not installed."""


def _load_sdk():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ModelUnavailable(
            "the anthropic SDK is not installed; run: pip install 'chainmind[model]'"
        ) from exc
    return anthropic


@dataclass
class ClaudeModel:
    """A thin, metered wrapper around the Messages API.

    ``client`` is injectable so tests can drive this without a network or a
    key; left as ``None`` it builds the SDK's own client, which resolves
    credentials from the environment (``ANTHROPIC_API_KEY``, or a profile
    from ``ant auth login``).
    """

    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    system: str = (
        "You are an autonomous agent whose resource consumption is recorded on a "
        "public ledger. Answer the task directly and completely. Prefer being "
        "concise over being exhaustive: every token you produce is paid for out "
        "of a finite budget."
    )
    effort: str = "high"
    client: Any = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.client is None:
            anthropic = _load_sdk()
            self.client = anthropic.Anthropic()

    # -- pre-flight ---------------------------------------------------------

    @staticmethod
    def conversation(prompt: str, history: Sequence[Mapping[str, str]] | None = None
                     ) -> list[dict[str, str]]:
        """The full message list for one turn.

        The API is stateless, so a multi-turn conversation means resending
        everything each time -- which is also why a long chat costs more per
        turn than a short one.  The ledger makes that growth visible instead
        of letting it hide in a bill at the end of the month.
        """
        messages = [
            {"role": str(entry["role"]), "content": str(entry["content"])}
            for entry in (history or [])
            if entry.get("content")
        ]
        messages.append({"role": "user", "content": prompt})
        return messages

    def count_input_tokens(self, prompt: str,
                           history: Sequence[Mapping[str, str]] | None = None) -> int:
        """Exactly how many input tokens this turn will cost.

        Used for the authorisation step, so the chain approves a real figure
        rather than an estimate.  If the endpoint is unreachable the caller
        still needs a number, so this falls back to a deliberately
        pessimistic approximation rather than to zero -- under-estimating
        would let an agent slip past its own budget check.
        """
        messages = self.conversation(prompt, history)
        try:
            counted = self.client.messages.count_tokens(
                model=self.model,
                system=self.system,
                messages=messages,
            )
            return int(counted.input_tokens)
        except Exception:
            return sum(
                self.approximate_tokens(message["content"]) for message in messages
            ) + self.approximate_tokens(self.system)

    @staticmethod
    def approximate_tokens(text: str) -> int:
        """A rough upper bound, for when counting is not available."""
        return max(1, len(text) // 3)

    def estimate(self, prompt: str, max_tokens: int | None = None,
                 history: Sequence[Mapping[str, str]] | None = None) -> dict[str, int]:
        """What to authorise before calling.  Output is a ceiling, not a guess."""
        return {
            ResourceKind.LLM_INPUT_TOKENS.value: self.count_input_tokens(prompt, history),
            ResourceKind.LLM_OUTPUT_TOKENS.value: int(max_tokens or self.max_tokens),
        }

    # -- the call -----------------------------------------------------------

    def respond(self, meter: Meter, prompt: str, max_tokens: int | None = None,
                system: str | None = None,
                history: Sequence[Mapping[str, str]] | None = None) -> dict[str, Any]:
        """Ask the model, record what it billed, return a JSON-safe result.

        The return value crosses a process boundary when the agent runs
        sandboxed, so it carries plain data only.
        """
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(max_tokens or self.max_tokens),
            "system": system if system is not None else self.system,
            "messages": self.conversation(prompt, history),
            # Adaptive thinking is on by default for this model family, and
            # effort is the lever that trades depth against tokens spent.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
            **dict(self.extra),
        }

        try:
            response = self.client.messages.create(**request)
        except Exception as exc:
            raise ModelUnavailable(f"{type(exc).__name__}: {exc}") from exc

        usage = getattr(response, "usage", None)
        if usage is not None:
            # The provider's own count, not ours. Cache reads and writes are
            # input tokens too -- billed differently, but consumed all the
            # same, so leaving them out would under-report the usage.
            input_tokens = (
                int(getattr(usage, "input_tokens", 0) or 0)
                + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            )
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            meter.record(ResourceKind.LLM_INPUT_TOKENS, input_tokens, source="provider")
            meter.record(ResourceKind.LLM_OUTPUT_TOKENS, output_tokens, source="provider")
        else:  # pragma: no cover - only a non-conforming client reaches this
            input_tokens = output_tokens = 0

        stop_reason = getattr(response, "stop_reason", None)
        meter.note("model", self.model)
        meter.note("stop_reason", stop_reason)

        # A refusal arrives as a normal 200 with no usable content, so the
        # stop reason has to be checked before the content is read.
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return {
                "text": "",
                "refused": True,
                "reason": getattr(details, "explanation", "") or "the model declined this request",
                "category": getattr(details, "category", None),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "stop_reason": stop_reason,
            }

        text = "".join(
            block.text for block in getattr(response, "content", [])
            if getattr(block, "type", None) == "text"
        )
        return {
            "text": text,
            "refused": False,
            "truncated": stop_reason == "max_tokens",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
            "model": getattr(response, "model", self.model),
        }


def build_model_tool(model: ClaudeModel | None = None, *, name: str = "ask") -> Tool:
    """A registry tool that answers a prompt with a real model."""
    engine = model or ClaudeModel()

    def run(meter: Meter, prompt: str, max_tokens: int | None = None,
            system: str | None = None,
            history: Sequence[Mapping[str, str]] | None = None) -> dict[str, Any]:
        return engine.respond(
            meter, prompt, max_tokens=max_tokens, system=system, history=history
        )

    return Tool(
        name=name,
        description=f"Answer a prompt with {engine.model}, billed by the tokens it reports.",
        run=run,
        estimate=lambda kw: engine.estimate(
            str(kw.get("prompt", "")), kw.get("max_tokens"), kw.get("history")
        ),
    )


def register_model_tool(registry, model: ClaudeModel | None = None, *, name: str = "ask") -> Tool:
    return registry.register(build_model_tool(model, name=name))


def _claude_credentials_available() -> bool:
    """Whether a hosted call could even be attempted.

    Checked explicitly rather than by constructing a client, because the SDK
    builds happily with no credentials and only fails at call time -- which
    would turn "you have no key" into a confusing error halfway through a
    prompt.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    profile = os.environ.get("ANTHROPIC_CONFIG_DIR")
    candidates = [Path(profile)] if profile else [
        Path.home() / ".config" / "anthropic",
        Path.home() / ".anthropic",
    ]
    return any(path.is_dir() and any(path.iterdir()) for path in candidates)


def build_model(backend: str = "auto", *, model: str | None = None,
                effort: str | None = None, **overrides: Any) -> Any:
    """Pick and build a model backend.

    ``auto`` looks for a runtime on this machine first and only then falls
    back to the hosted API.  That ordering is the point: the default should
    be the one that keeps working when the network, the account or the
    provider goes away.
    """
    from .local import LocalModel, LocalRuntimeUnavailable

    backend = (backend or os.environ.get("CHAINMIND_BACKEND") or "auto").lower()
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; choose from {', '.join(BACKENDS)}")

    def local() -> Any:
        params: dict[str, Any] = dict(overrides)
        if model:
            params["model"] = model
        return LocalModel(**params)

    def claude() -> Any:
        params: dict[str, Any] = {
            "model": model or os.environ.get("CHAINMIND_MODEL", DEFAULT_MODEL),
            "effort": effort or os.environ.get("CHAINMIND_EFFORT", "high"),
        }
        params.update(overrides)
        return ClaudeModel(**params)

    if backend == "local":
        try:
            return local()
        except LocalRuntimeUnavailable as exc:
            raise ModelUnavailable(str(exc)) from exc
    if backend == "claude":
        return claude()

    try:
        return local()
    except LocalRuntimeUnavailable as local_error:
        if _claude_credentials_available():
            return claude()
        raise ModelUnavailable(
            "no model is available. Two ways forward:\n\n"
            "  1. Run one yourself (no key, no account, no network):\n"
            "       ollama serve && ollama pull llama3.1\n\n"
            "  2. Use a hosted model:\n"
            "       pip install 'chainmind[model]' && export ANTHROPIC_API_KEY=...\n"
            "       chainmind ask '...' --backend claude\n\n"
            f"{local_error}"
        ) from local_error


def describe_model(engine: Any) -> str:
    """One line naming the model and where it runs."""
    described = getattr(engine, "describe", None)
    if callable(described):
        return described()
    return f"{getattr(engine, 'model', 'unknown')} (hosted)"


def model_from_environment(**overrides: Any) -> Any:
    """Build a model from the environment, preferring a local runtime."""
    backend = overrides.pop("backend", None) or os.environ.get("CHAINMIND_BACKEND", "auto")
    return build_model(backend, **overrides)
