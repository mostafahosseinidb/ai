"""Metering that does not take the agent's word for it.

Everywhere else in this project, a tool declares what it consumed and the
ledger enforces that the declaration is affordable.  That leaves one gap,
named plainly in the architecture notes: a tool that under-reports its own
CPU time is believed.  This module closes it for the resources an operating
system can actually observe.

Two mechanisms, and it is worth being precise about which does what:

*   **Measurement.**  The work runs in a forked child, and the parent reads
    its CPU time and peak memory from ``os.wait4`` -- that is, from the
    kernel's accounting, not from anything the child says.  A child that
    reports zero milliseconds is simply contradicted.
*   **Enforcement.**  Before the child runs, ``setrlimit`` turns the chain's
    budget into a hard ceiling.  Exceeding it does not produce a ledger
    dispute; it produces a dead process.  This is the point where a credit
    balance stops being bookkeeping and becomes a physical limit.

Everything the kernel cannot see -- model tokens, most notably -- is still
declared by the tool, and the declaration is still only as good as the code
making it.  The honest boundary is drawn in :data:`KERNEL_MEASURED`.

POSIX only.  :data:`SUPPORTED` says whether this machine can do it, and
callers are expected to fall back to in-process metering when it cannot.
"""

from __future__ import annotations

import json
import math
import os
import resource
import select
import signal
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .meter import Meter, UsageRecord
from .resources import ResourceKind

__all__ = [
    "SUPPORTED",
    "KERNEL_MEASURED",
    "SandboxLimits",
    "SandboxResult",
    "SandboxUnavailable",
    "run_sandboxed",
]

#: Whether out-of-process metering can run here at all.
SUPPORTED = hasattr(os, "fork") and hasattr(os, "wait4")

#: The resources whose totals come from the kernel rather than from the tool.
KERNEL_MEASURED = frozenset({ResourceKind.COMPUTE_MS})

#: How much of the child's output we are willing to read back.
MAX_RESULT_BYTES = 4 * 1024 * 1024


class SandboxUnavailable(RuntimeError):
    """Out-of-process metering was asked for on a platform that lacks it."""


@dataclass(frozen=True)
class SandboxLimits:
    """Hard ceilings imposed on the child before it runs.

    ``cpu_seconds`` is whole seconds because that is the granularity
    ``RLIMIT_CPU`` offers; sub-second budgets are enforced by
    ``wall_seconds`` instead, which is checked by the parent.
    """

    cpu_seconds: int | None = None
    address_space_bytes: int | None = None
    file_size_bytes: int | None = None
    open_files: int | None = None
    wall_seconds: float | None = 30.0

    @classmethod
    def from_compute_budget(cls, compute_ms: int, **overrides: Any) -> "SandboxLimits":
        """Turn an affordable number of CPU milliseconds into real limits.

        The chain says how many milliseconds the agent can pay for; this
        converts that into the ceiling the kernel will enforce.  The CPU
        limit rounds up (a budget of 1 ms still needs a whole second of
        ``RLIMIT_CPU`` to be expressible), while the wall clock keeps a
        sub-second budget from running away.
        """
        if compute_ms < 0:
            raise ValueError("a compute budget must not be negative")
        params: dict[str, Any] = {
            "cpu_seconds": max(1, math.ceil(compute_ms / 1000)),
            "wall_seconds": max(1.0, compute_ms / 1000 * 4),
        }
        params.update(overrides)
        return cls(**params)

    def apply(self) -> None:
        """Install these limits on the current process.  Called in the child."""
        if self.cpu_seconds is not None:
            # A soft limit one second below the hard one means the child gets
            # SIGXCPU (terminating by default) before the unkillable SIGKILL,
            # which makes the cause legible in the exit status.
            resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_seconds, self.cpu_seconds + 1))
        if self.address_space_bytes is not None:
            resource.setrlimit(
                resource.RLIMIT_AS, (self.address_space_bytes, self.address_space_bytes)
            )
        if self.file_size_bytes is not None:
            resource.setrlimit(
                resource.RLIMIT_FSIZE, (self.file_size_bytes, self.file_size_bytes)
            )
        if self.open_files is not None:
            resource.setrlimit(resource.RLIMIT_NOFILE, (self.open_files, self.open_files))

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_seconds": self.cpu_seconds,
            "address_space_bytes": self.address_space_bytes,
            "file_size_bytes": self.file_size_bytes,
            "open_files": self.open_files,
            "wall_seconds": self.wall_seconds,
        }


@dataclass(frozen=True)
class SandboxResult:
    """What the child did, as observed from outside it."""

    ok: bool
    value: Any = None
    error: str = ""
    cpu_ms: int = 0
    wall_ms: int = 0
    max_rss_kb: int = 0
    declared: Mapping[str, int] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)
    killed_by: str | None = None
    exit_status: int = 0

    @property
    def totals(self) -> dict[str, int]:
        """Declared usage, with the kernel's numbers taking precedence.

        A tool cannot lower its CPU bill by claiming a smaller figure: the
        declared value for a kernel-measured resource is discarded outright.
        """
        merged = {
            kind: amount
            for kind, amount in self.declared.items()
            if ResourceKind.parse(kind) not in KERNEL_MEASURED
        }
        if self.cpu_ms > 0:
            merged[ResourceKind.COMPUTE_MS.value] = self.cpu_ms
        return merged

    def to_usage_record(self, label: str, started_at: int, finished_at: int) -> UsageRecord:
        context = dict(self.context)
        context["measurement"] = "out-of-process"
        context["max_rss_kb"] = self.max_rss_kb
        if self.killed_by:
            context["killed_by"] = self.killed_by
        return UsageRecord(
            label=label,
            totals=self.totals,
            started_at=started_at,
            finished_at=finished_at,
            context=context,
        )


# --------------------------------------------------------------------------
# The child side
# --------------------------------------------------------------------------

def _child(fn: Callable[..., Any], kwargs: Mapping[str, Any], limits: SandboxLimits,
           label: str, write_fd: int) -> None:
    """Run the work and report back.  Never returns."""
    status = 0
    try:
        limits.apply()
        meter = Meter(label, charge_compute=False)   # CPU comes from the kernel
        payload: dict[str, Any]
        with meter:
            try:
                value = fn(meter, **kwargs)
                payload = {"ok": True, "value": value, "error": ""}
            except BaseException as exc:                      # noqa: BLE001
                meter.note("failed", True)
                payload = {"ok": False, "value": None, "error": f"{type(exc).__name__}: {exc}"}
        record = meter.finish()
        payload["declared"] = dict(record.totals)
        payload["context"] = dict(record.context)

        try:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            # A sandboxed tool has to hand back something that survives a
            # pipe; say so clearly rather than losing the whole measurement.
            encoded = json.dumps(
                {
                    "ok": False,
                    "value": None,
                    "error": f"result is not JSON-serialisable: {exc}",
                    "declared": dict(record.totals),
                    "context": dict(record.context),
                },
                ensure_ascii=False,
            ).encode("utf-8")

        with os.fdopen(write_fd, "wb", closefd=True) as handle:
            handle.write(encoded)
    except BaseException:                                     # noqa: BLE001
        status = 1
    finally:
        # os._exit skips atexit handlers, flushing and, crucially, unwinding
        # into the parent's test runner or event loop.
        os._exit(status)


# --------------------------------------------------------------------------
# The parent side
# --------------------------------------------------------------------------

def _describe_status(status: int) -> tuple[str | None, str]:
    """Turn a wait status into (killed_by, human explanation)."""
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        name = signal.Signals(sig).name if sig in set(s.value for s in signal.Signals) else str(sig)
        if sig in (signal.SIGXCPU, signal.SIGKILL):
            return ("cpu" if sig == signal.SIGXCPU else "killed",
                    f"the child was terminated by {name}")
        return f"signal:{name}", f"the child was terminated by {name}"
    if os.WIFEXITED(status) and os.WEXITSTATUS(status) != 0:
        return "exit", f"the child exited with status {os.WEXITSTATUS(status)}"
    return None, ""


def _read_until_eof(read_fd: int, deadline: float | None) -> tuple[bytes, bool]:
    """Drain the pipe.  Returns (payload, timed_out)."""
    chunks: list[bytes] = []
    size = 0
    while True:
        timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
        if timeout == 0.0:
            return b"".join(chunks), True
        ready, _, _ = select.select([read_fd], [], [], timeout)
        if not ready:
            return b"".join(chunks), True
        chunk = os.read(read_fd, 65536)
        if not chunk:
            return b"".join(chunks), False
        size += len(chunk)
        if size > MAX_RESULT_BYTES:
            return b"".join(chunks), False
        chunks.append(chunk)


def run_sandboxed(fn: Callable[..., Any], kwargs: Mapping[str, Any] | None = None, *,
                  limits: SandboxLimits | None = None, label: str = "sandboxed") -> SandboxResult:
    """Run ``fn(meter, **kwargs)`` in a child process and measure it from outside.

    ``fn`` must take a :class:`~chainmind.meter.Meter` as its first argument
    and return something JSON-serialisable.  Side effects on in-memory
    objects do not cross back -- that is the price of the isolation, and the
    reason sandbox-safe tools return their results instead of mutating
    shared state.
    """
    if not SUPPORTED:
        raise SandboxUnavailable(
            "out-of-process metering needs os.fork and os.wait4, which this platform lacks"
        )

    kwargs = dict(kwargs or {})
    limits = limits or SandboxLimits()
    started_wall = time.monotonic()
    started_at = int(time.time())

    read_fd, write_fd = os.pipe()
    pid = os.fork()

    if pid == 0:                                   # child
        try:
            os.close(read_fd)
        except OSError:
            pass
        _child(fn, kwargs, limits, label, write_fd)
        raise AssertionError("unreachable")        # pragma: no cover

    os.close(write_fd)
    deadline = None if limits.wall_seconds is None else started_wall + limits.wall_seconds

    try:
        raw, timed_out = _read_until_eof(read_fd, deadline)
    finally:
        os.close(read_fd)

    if timed_out:
        # The child is still running and has no intention of stopping; the
        # wall clock is the only limit that can express a sub-second budget.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    _, status, usage = os.wait4(pid, 0)
    wall_ms = int((time.monotonic() - started_wall) * 1000)
    cpu_ms = int(round((usage.ru_utime + usage.ru_stime) * 1000))
    killed_by, explanation = _describe_status(status)
    if timed_out:
        killed_by, explanation = "wall", (
            f"the child exceeded its wall-clock limit of {limits.wall_seconds}s and was killed"
        )

    payload: dict[str, Any] = {}
    if raw:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}

    ok = bool(payload.get("ok")) and killed_by is None
    error = payload.get("error", "")
    if killed_by is not None and not error:
        error = explanation
    elif killed_by is not None and explanation:
        error = f"{error}; {explanation}" if error else explanation
    if not payload and killed_by is None:
        ok, error = False, "the child produced no result"

    return SandboxResult(
        ok=ok,
        value=payload.get("value"),
        error=error,
        cpu_ms=cpu_ms,
        wall_ms=wall_ms,
        max_rss_kb=int(usage.ru_maxrss),
        declared={k: int(v) for k, v in dict(payload.get("declared", {})).items()},
        context=dict(payload.get("context", {})),
        killed_by=killed_by,
        exit_status=status,
    )
