"""How a tool actually gets run, and who does the measuring.

The kernel's logic -- estimate, authorise, act, settle -- is the same either
way; what changes is whether the measurement is something the tool reports
about itself or something the operating system observes about it.

*   :class:`InProcessExecutor` runs the tool right here.  Fast, keeps side
    effects on shared objects, and trusts the tool's own accounting.
*   :class:`SandboxExecutor` runs it in a child process under ``setrlimit``
    and bills the CPU time the kernel reports.  The agent's credit balance
    becomes the child's hard limit, so overspending stops being a ledger
    dispute and becomes an impossible one.

Both return the same :class:`~chainmind.tools.ToolResult`, so swapping them
is a one-line change at the call site.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Protocol

from .meter import UsageRecord
from .sandbox import SUPPORTED, SandboxLimits, SandboxUnavailable, run_sandboxed
from .tools import Tool, ToolResult, describe_arguments

__all__ = ["Executor", "InProcessExecutor", "SandboxExecutor"]


class Executor(Protocol):
    def run(self, tool: Tool, kwargs: Mapping[str, Any], *,
            compute_budget_ms: int | None = None) -> ToolResult: ...


class InProcessExecutor:
    """The default: call the tool and believe what it declares."""

    name = "in-process"

    def run(self, tool: Tool, kwargs: Mapping[str, Any], *,
            compute_budget_ms: int | None = None) -> ToolResult:
        return tool.invoke(**kwargs)


class SandboxExecutor:
    """Run the tool in a child process and take the kernel's word instead.

    ``compute_budget_ms`` -- how much CPU time the chain says the agent can
    afford right now -- is turned into an ``RLIMIT_CPU`` ceiling, capped by
    ``max_compute_ms`` so that a richly funded agent still cannot occupy the
    machine indefinitely.

    The isolation has a cost worth stating: arguments and return values must
    survive a JSON round trip, and a tool that mutates a shared object in
    memory will find those mutations gone.  Sandbox-safe tools return their
    results rather than writing to objects the parent holds.
    """

    name = "sandbox"

    def __init__(self, *, max_compute_ms: int = 30_000, max_memory_bytes: int | None = None,
                 max_file_bytes: int | None = None, wall_seconds: float | None = None,
                 allow_fallback: bool = False) -> None:
        if not SUPPORTED and not allow_fallback:
            raise SandboxUnavailable(
                "this platform cannot fork; pass allow_fallback=True to meter in-process instead"
            )
        self.max_compute_ms = max_compute_ms
        self.max_memory_bytes = max_memory_bytes
        self.max_file_bytes = max_file_bytes
        self.wall_seconds = wall_seconds
        self.allow_fallback = allow_fallback
        self._fallback = InProcessExecutor()

    def limits_for(self, compute_budget_ms: int | None) -> SandboxLimits:
        budget = self.max_compute_ms if compute_budget_ms is None else compute_budget_ms
        if budget < 0:                      # -1 means "unbounded" upstream
            budget = self.max_compute_ms
        budget = min(budget, self.max_compute_ms)

        overrides: dict[str, Any] = {}
        if self.wall_seconds is not None:
            overrides["wall_seconds"] = self.wall_seconds
        if self.max_memory_bytes is not None:
            overrides["address_space_bytes"] = self.max_memory_bytes
        if self.max_file_bytes is not None:
            overrides["file_size_bytes"] = self.max_file_bytes
        return SandboxLimits.from_compute_budget(budget, **overrides)

    def run(self, tool: Tool, kwargs: Mapping[str, Any], *,
            compute_budget_ms: int | None = None) -> ToolResult:
        if not SUPPORTED:
            return self._fallback.run(tool, kwargs, compute_budget_ms=compute_budget_ms)

        started_at = int(time.time())
        outcome = run_sandboxed(
            tool.run, kwargs, limits=self.limits_for(compute_budget_ms), label=tool.name
        )
        record = outcome.to_usage_record(tool.name, started_at, int(time.time()))
        record = UsageRecord(
            label=record.label,
            totals=record.totals,
            started_at=record.started_at,
            finished_at=record.finished_at,
            context={**record.context, "args": describe_arguments(kwargs)},
        )
        return ToolResult(
            tool=tool.name, value=outcome.value, usage=record,
            ok=outcome.ok, error=outcome.error,
        )
