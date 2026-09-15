"""Choosing and wrapping the model the agent thinks with.

There is exactly one kind of backend: an inference runtime the operator runs
themselves.  No hosted API, no SDK, no account, no key, no outbound request
to anyone.  The project depends on nothing it cannot be handed on a USB
stick.

What this module owns is the seam between the agent kernel and whatever is
doing the thinking: resolve a runtime, wrap it as a metered tool, and give
the rest of the program one name to call it by.

On the limit of the claim, stated plainly: running the weights yourself
removes the dependency on a *service*.  It does not mean the weights were
invented here -- open models come from somewhere, and pretending otherwise
would be the kind of thing this project exists to make impossible.  What
you get is that nobody can close your account, change your price, or
retire your model.
"""

from __future__ import annotations

from typing import Any

from .local import LocalModel, LocalRuntimeUnavailable, discover_runtime
from .meter import Meter
from .tools import Tool, ToolError

__all__ = [
    "ModelUnavailable",
    "build_model",
    "build_model_tool",
    "describe_model",
    "model_from_environment",
    "register_model_tool",
    "available_runtimes",
]


class ModelUnavailable(ToolError):
    """No inference runtime is reachable."""


def build_model(model: str | None = None, **overrides: Any) -> LocalModel:
    """Connect to a runtime already listening on this machine.

    This starts nothing and downloads nothing: it finds what is running and
    uses it, or says clearly that nothing is.
    """
    params: dict[str, Any] = dict(overrides)
    if model:
        params["model"] = model
    try:
        return LocalModel(**params)
    except LocalRuntimeUnavailable as exc:
        raise ModelUnavailable(str(exc)) from exc


def model_from_environment(**overrides: Any) -> LocalModel:
    """Build a model from ``CHAINMIND_LOCAL_*`` if those are set."""
    overrides.pop("backend", None)     # kept accepted so older callers do not break
    overrides.pop("effort", None)      # a hosted-API notion; local runtimes have no such knob
    return build_model(**overrides)


def describe_model(engine: Any) -> str:
    """One line naming the model and where it runs."""
    described = getattr(engine, "describe", None)
    if callable(described):
        return described()
    return str(getattr(engine, "model", "unknown"))


def available_runtimes() -> dict[str, Any]:
    """What this machine can currently offer, for reporting."""
    try:
        base, dialect, models = discover_runtime()
    except LocalRuntimeUnavailable as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "url": base, "dialect": dialect, "models": models}


def build_model_tool(model: LocalModel | None = None, *, name: str = "ask") -> Tool:
    """Wrap a model as a metered tool the kernel can authorise and bill."""
    engine = model or build_model()

    def run(meter: Meter, prompt: str, max_tokens: int | None = None,
            system: str | None = None,
            history: Any = None) -> dict[str, Any]:
        return engine.respond(
            meter, prompt, max_tokens=max_tokens, system=system, history=history
        )

    return Tool(
        name=name,
        description=f"Answer a prompt with {describe_model(engine)}.",
        run=run,
        # The estimate is handed the same system prompt the call will use,
        # so anything added to it -- recalled memory, for instance -- is paid
        # for rather than smuggled past the budget check.
        estimate=lambda kw: engine.estimate(
            str(kw.get("prompt", "")), kw.get("max_tokens"),
            kw.get("history"), kw.get("system"),
        ),
    )


def register_model_tool(registry, model: LocalModel | None = None, *, name: str = "ask") -> Tool:
    return registry.register(build_model_tool(model, name=name))
