"""Tools an agent can invoke, each with a declared price before it runs.

A tool must be able to say what it will *probably* cost before the agent
commits to it, because the whole point of the design is that the agent checks
affordability first and acts second.  The estimate is a promise about
magnitude, not exactness; what settles on chain is always the measured usage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .meter import Meter, UsageRecord
from .resources import ResourceKind

__all__ = ["Tool", "ToolRegistry", "ToolResult", "ToolError", "default_registry",
           "describe_arguments"]


class ToolError(Exception):
    """A tool failed.  The resources it burned before failing still count."""


@dataclass(frozen=True)
class ToolResult:
    tool: str
    value: Any
    usage: UsageRecord
    ok: bool = True
    error: str = ""


Estimator = Callable[[Mapping[str, Any]], Mapping[str, int]]


@dataclass(frozen=True)
class Tool:
    """A metered capability.

    ``run`` receives the meter so it can declare tokens, bytes and calls as it
    goes; CPU time is charged automatically.
    """

    name: str
    description: str
    run: Callable[..., Any]
    estimate: Estimator

    def invoke(self, **kwargs: Any) -> ToolResult:
        with Meter(self.name, context={"args": describe_arguments(kwargs)}) as meter:
            try:
                value = self.run(meter, **kwargs)
            except Exception as exc:  # the work is billed even when it fails
                meter.note("failed", True)
                return ToolResult(
                    tool=self.name, value=None, usage=meter.finish(), ok=False, error=str(exc)
                )
        return ToolResult(tool=self.name, value=value, usage=meter.finish())

    def estimated_cost(self, **kwargs: Any) -> dict[str, int]:
        return {
            ResourceKind.parse(k).value: int(v)
            for k, v in self.estimate(kwargs).items()
        }


def describe_arguments(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """A JSON-safe, size-bounded view of the arguments, for the evidence hash."""
    described: dict[str, Any] = {}
    for key, value in kwargs.items():
        if isinstance(value, (int, float, bool)) or value is None:
            described[key] = value
        else:
            text = str(value)
            described[key] = text if len(text) <= 200 else text[:200] + f"...(+{len(text) - 200})"
    return described


class ToolRegistry:
    def __init__(self, tools: Mapping[str, Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = dict(tools or {})

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"a tool named {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def add(self, name: str, description: str, estimate: Estimator) -> Callable[[Callable], Tool]:
        """Decorator form: ``@registry.add("echo", "...", estimator)``."""
        def decorator(fn: Callable) -> Tool:
            return self.register(Tool(name=name, description=description, run=fn, estimate=estimate))
        return decorator

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._tools)) or "none"
            raise ToolError(f"unknown tool {name!r}; registered: {known}") from exc

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools[name] for name in sorted(self._tools))

    def __len__(self) -> int:
        return len(self._tools)


# --------------------------------------------------------------------------
# A small offline toolset, so the reference agent runs with no API keys
# --------------------------------------------------------------------------

def default_registry() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.add(
        "think",
        "Reason about a prompt locally, billed as model tokens.",
        lambda kw: {
            ResourceKind.LLM_INPUT_TOKENS: max(1, len(str(kw.get("prompt", ""))) // 4),
            ResourceKind.LLM_OUTPUT_TOKENS: int(kw.get("budget_tokens", 128)),
        },
    )
    def think(meter: Meter, prompt: str, budget_tokens: int = 128) -> str:
        input_tokens = max(1, len(prompt) // 4)
        # The offline stand-in "thinks" by restating the prompt; a real
        # deployment swaps this body for a model call and records the token
        # counts the provider reports.
        conclusion = f"considered: {prompt.strip()[:160]}"
        output_tokens = min(budget_tokens, max(1, len(conclusion) // 4))
        meter.record(ResourceKind.LLM_INPUT_TOKENS, input_tokens)
        meter.record(ResourceKind.LLM_OUTPUT_TOKENS, output_tokens)
        meter.note("output_tokens", output_tokens)
        return conclusion

    @registry.add(
        "remember",
        "Write a note to durable storage, billed by bytes stored.",
        lambda kw: {ResourceKind.STORAGE_BYTES: len(str(kw.get("text", "")).encode("utf-8"))},
    )
    def remember(meter: Meter, store: str, key: str, text: str) -> dict[str, Any]:
        # The bill is in storage bytes, so the bytes really do go to storage.
        # Writing to a file rather than to a dict in the parent's memory also
        # makes this tool safe to run in a sandboxed child process, whose
        # mutations to shared objects would otherwise vanish.
        path = Path(store)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            notes = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(notes, dict):
                notes = {}
        except (OSError, json.JSONDecodeError):
            notes = {}
        notes[key] = text
        serialised = json.dumps(notes, ensure_ascii=False, sort_keys=True)
        path.write_text(serialised, encoding="utf-8")

        written = len(text.encode("utf-8"))
        meter.record(ResourceKind.STORAGE_BYTES, written)
        meter.note("key", key)
        return {"key": key, "bytes": written, "store": str(path)}

    @registry.add(
        "fetch",
        "Call an external endpoint, billed as one tool call plus transferred bytes.",
        lambda kw: {
            ResourceKind.TOOL_CALL: 1,
            ResourceKind.NETWORK_BYTES: int(kw.get("expected_bytes", 4096)),
        },
    )
    def fetch(meter: Meter, url: str, fetcher: Callable[[str], bytes] | None = None,
              expected_bytes: int = 4096) -> bytes:
        if fetcher is None:
            raise ToolError(
                "no fetcher supplied: this reference tool does not reach the network on its own"
            )
        payload = fetcher(url)
        meter.record(ResourceKind.TOOL_CALL, 1)
        meter.record(ResourceKind.NETWORK_BYTES, len(payload))
        meter.note("url", url)
        return payload

    return registry
