"""Choosing and wrapping the model the agent thinks with.

No hosted API, no SDK, no account, no key, no outbound request to anyone.
The project depends on nothing it cannot be handed on a USB stick.

What this module owns is the seam between the agent kernel and whatever is
doing the thinking: resolve a backend, wrap it as a metered tool, and give
the rest of the program one name to call it by.

There are three, and they differ in how much of the model came from
somewhere else:

*   **own** (:mod:`chainmind.native`) -- architecture, tokenizer and weights
    all produced here, by ``training/pretrain.py``, from your corpus.
    Nothing was downloaded. It is also, unavoidably, a small model: see that
    module for the arithmetic of why.
*   **embedded** (:mod:`chainmind.embedded`) -- somebody else's open weights,
    loaded into this process. Far more capable, and yours once the file is on
    your disk, but the weights were trained elsewhere.
*   **served** (:mod:`chainmind.local`) -- an inference runtime already
    listening on this machine. Still local, still no account, but a second
    program to install and keep running.

None of the three reaches the public internet. The distinction the ledger
records is the weights fingerprint, so an auditor can always tell which of
them produced a given answer.
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
from .nano import backend_name, load_weights
from .native import (
    NATIVE_SUFFIXES,
    NativeModel,
    NativeUnavailable,
    discover_native,
)
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

    Order matters and is deliberate -- least borrowed first:

    1.  The agent's own model: a ``.cmw`` file this project trained, loaded
        into this process. No part of it came from anywhere else.
    2.  Open weights in the workspace, loaded into this process. Somebody
        else's model, but yours to keep and nothing else to run.
    3.  An inference runtime already listening on this machine. Still local,
        still no account -- but a second program.

    None of them reaches the public internet. Nothing here starts a server or
    downloads weights: it uses what is there, or says clearly what is not.
    """
    params: dict[str, Any] = dict(overrides)
    if model:
        params["model"] = model

    file_params = {k: v for k, v in params.items() if k != "model"}
    named_file = Path(model) if model_is_path(model) else None

    native_error: Exception | None = None
    if named_file is None or named_file.suffix.lower() in NATIVE_SUFFIXES:
        if workspace is not None or named_file is not None:
            try:
                return NativeModel(path=named_file, workspace=workspace, **file_params)
            except NativeUnavailable as exc:
                native_error = exc

    embedded_error: Exception | None = None
    if named_file is None or named_file.suffix.lower() in MODEL_SUFFIXES:
        if workspace is not None or named_file is not None:
            try:
                return EmbeddedModel(path=named_file, workspace=workspace, **file_params)
            except EmbeddedUnavailable as exc:
                embedded_error = exc

    try:
        return LocalModel(**params)
    except LocalRuntimeUnavailable as exc:
        reasons = [
            f"own model:       {native_error}" if native_error else "",
            f"open weights:    {embedded_error}" if embedded_error else "",
            f"running runtime: {exc}",
        ]
        if native_error is None and embedded_error is None:
            raise ModelUnavailable(str(exc)) from exc
        raise ModelUnavailable(
            "no model available.\n\n"
            + "\n\n".join(reason for reason in reasons if reason)
        ) from exc


def model_is_path(model: str | None) -> bool:
    """Whether ``--model`` names a weights file rather than a runtime's model."""
    if not model:
        return False
    return Path(model).suffix.lower() in (*NATIVE_SUFFIXES, *MODEL_SUFFIXES)


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
    report: dict[str, Any] = {
        "own": {"available": False},
        "embedded": {"available": False},
        "served": {"available": False},
    }

    if workspace is not None:
        own = discover_native(workspace)
        if own:
            report["own"] = {
                "available": True,
                "backend": backend_name(),
                "models": [_describe_file(path) | _describe_native(path) for path in own],
            }
        else:
            report["own"] = {
                "available": False,
                "reason": (
                    f"no .cmw file in {Path(workspace) / MODELS_DIRNAME} — train one "
                    "with `python3 training/pretrain.py --corpus ...`"
                ),
            }

        files = discover_models(workspace)
        if files:
            report["embedded"] = {
                "available": True,
                "models": [_describe_file(path) for path in files],
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

    report["available"] = any(
        report[kind]["available"] for kind in ("own", "embedded", "served")
    )
    return report


def _describe_file(path: Path) -> dict[str, Any]:
    return {"name": path.stem, "path": str(path),
            "size_mb": round(path.stat().st_size / (1 << 20))}


def _describe_native(path: Path) -> dict[str, Any]:
    """Read a ``.cmw`` header for the report, without loading the weights."""
    try:
        architecture, _tokenizer, _tensors, meta = load_weights(path)
    except Exception as exc:                      # a corrupt file is worth naming
        return {"error": str(exc)}
    return {
        "parameters": architecture.parameters,
        "dim": architecture.dim,
        "layers": architecture.layers,
        "context": architecture.context,
        "trained_from": meta.get("trained_from", "unknown"),
    }


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
