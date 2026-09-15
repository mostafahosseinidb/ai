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

from pathlib import Path
from typing import Any

from .embedded import (
    MODELS_DIRNAME,
    MODEL_SUFFIXES,
    EmbeddedModel,
    EmbeddedUnavailable,
    discover_models,
)
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
    "model_is_path",
]


class ModelUnavailable(ToolError):
    """No inference runtime is reachable."""


def build_model(model: str | None = None, *, workspace: Any = None,
                **overrides: Any) -> Any:
    """Find something to think with, preferring the most self-contained option.

    Order matters and is deliberate:

    1.  A weights file in the workspace, loaded into this process.  Nothing
        else to install, nothing else to keep running, and the file is the
        agent's own.
    2.  An inference runtime already listening on this machine.  Still local,
        still no account -- but a second program.

    Neither reaches the public internet.  Nothing here starts a server or
    downloads weights: it uses what is there, or says clearly what is not.
    """
    params: dict[str, Any] = dict(overrides)
    if model:
        params["model"] = model

    embedded_error: Exception | None = None
    if workspace is not None or model_is_path(model):
        try:
            return EmbeddedModel(
                path=Path(model) if model_is_path(model) else None,
                workspace=workspace,
                **{k: v for k, v in params.items() if k != "model"},
            )
        except EmbeddedUnavailable as exc:
            embedded_error = exc

    try:
        return LocalModel(**params)
    except LocalRuntimeUnavailable as exc:
        if embedded_error is not None:
            raise ModelUnavailable(
                f"no model available.\n\n"
                f"in this workspace: {embedded_error}\n\n"
                f"on this machine:   {exc}"
            ) from exc
        raise ModelUnavailable(str(exc)) from exc


def model_is_path(model: str | None) -> bool:
    """Whether ``--model`` names a weights file rather than a runtime's model."""
    if not model:
        return False
    candidate = Path(model)
    return candidate.suffix.lower() in MODEL_SUFFIXES


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


def available_runtimes(workspace: Any = None) -> dict[str, Any]:
    """What this machine can currently offer, for reporting."""
    report: dict[str, Any] = {"embedded": {"available": False}, "served": {"available": False}}

    if workspace is not None:
        files = discover_models(workspace)
        if files:
            report["embedded"] = {
                "available": True,
                "models": [
                    {"name": path.stem, "path": str(path),
                     "size_mb": round(path.stat().st_size / (1 << 20))}
                    for path in files
                ],
            }
        else:
            report["embedded"] = {
                "available": False,
                "reason": f"no .gguf file in {Path(workspace) / MODELS_DIRNAME}",
            }

    try:
        base, dialect, models = discover_runtime()
        report["served"] = {"available": True, "url": base, "dialect": dialect,
                            "models": models}
    except LocalRuntimeUnavailable as exc:
        report["served"] = {"available": False, "reason": str(exc)}

    report["available"] = report["embedded"]["available"] or report["served"]["available"]
    return report


def build_model_tool(model: Any = None, *, name: str = "ask") -> Tool:
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


def register_model_tool(registry, model: Any = None, *, name: str = "ask") -> Tool:
    return registry.register(build_model_tool(model, name=name))
