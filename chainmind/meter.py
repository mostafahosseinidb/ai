"""Measuring what an action actually consumed.

The ledger can only be as honest as its inputs, so metering is deliberately
narrow and explicit: CPU time is sampled by the runtime, and everything else
has to be declared by the code doing the work.  A :class:`Meter` produces a
:class:`UsageRecord` whose digest is what the on-chain usage transaction
points at -- the chain stores the commitment, the operator keeps the detail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from .crypto import canonical_bytes, sha256_hex
from .resources import PriceTable, ResourceKind

__all__ = ["Meter", "UsageRecord", "MeasurementSource", "TRUST_ORDER", "least_trusted"]


#: How a number was obtained, from least to most independently verified.
#:
#: ``declared`` is the tool's own word.  ``provider`` is the counterparty's
#: count -- a model API reporting the tokens it billed -- which the tool cannot
#: quietly shrink but which is not cryptographically proven either.  ``kernel``
#: is an operating system measuring the work from outside it.
TRUST_ORDER: Mapping[str, int] = {"declared": 0, "provider": 1, "kernel": 2}
MeasurementSource = str


def least_trusted(left: str, right: str) -> str:
    """The weaker of two provenances.

    A resource whose total mixes a provider-reported figure with a
    self-declared one is only as trustworthy as the self-declared part, so
    the pair collapses downward rather than upward.
    """
    return left if TRUST_ORDER.get(left, 0) <= TRUST_ORDER.get(right, 0) else right


@dataclass(frozen=True)
class UsageRecord:
    """An immutable statement of what one action cost."""

    label: str
    totals: Mapping[str, int]
    started_at: int
    finished_at: int
    context: Mapping[str, Any] = field(default_factory=dict)
    #: resource -> how that total was obtained; absent means ``declared``.
    sources: Mapping[str, str] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """The evidence hash committed on chain."""
        return sha256_hex(canonical_bytes(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "totals": {k: v for k, v in sorted(self.totals.items())},
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "context": dict(self.context),
            "sources": {k: v for k, v in sorted(self.sources.items())},
        }

    def source_of(self, kind: ResourceKind | str) -> str:
        resolved = ResourceKind.parse(kind.value if isinstance(kind, ResourceKind) else kind)
        return self.sources.get(resolved.value, "declared")

    def cost(self, prices: PriceTable) -> int:
        return sum(prices.cost(kind, amount) for kind, amount in self.totals.items())

    def items(self) -> Iterator[tuple[str, int]]:
        return iter(sorted(self.totals.items()))

    def __bool__(self) -> bool:
        return any(amount > 0 for amount in self.totals.values())


class Meter:
    """Accumulates resource usage for a single action.

    Used as a context manager, it charges the CPU time spent inside the block
    automatically::

        with Meter("summarise") as meter:
            meter.record(ResourceKind.LLM_INPUT_TOKENS, 800)
            ...
        record = meter.finish()
    """

    def __init__(self, label: str, *, context: Mapping[str, Any] | None = None,
                 charge_compute: bool = True) -> None:
        self.label = label
        self.context = dict(context or {})
        self.charge_compute = charge_compute
        self._totals: dict[str, int] = {}
        self._sources: dict[str, str] = {}
        self._started_wall = 0
        self._started_cpu = 0.0
        self._finished_wall = 0
        self._record: UsageRecord | None = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "Meter":
        self._started_wall = int(time.time())
        self._started_cpu = time.process_time()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._close()
        return False  # never swallow the exception; a failed action still costs

    def _close(self) -> None:
        if self._record is not None:
            return
        if self.charge_compute:
            elapsed_ms = int((time.process_time() - self._started_cpu) * 1000)
            if elapsed_ms > 0:
                self.record(ResourceKind.COMPUTE_MS, elapsed_ms)
        self._finished_wall = int(time.time())
        self._record = UsageRecord(
            label=self.label,
            totals=dict(self._totals),
            started_at=self._started_wall,
            finished_at=self._finished_wall,
            context=dict(self.context),
            sources=dict(self._sources),
        )

    def finish(self) -> UsageRecord:
        """The record for this action.  Safe to call more than once."""
        self._close()
        assert self._record is not None
        return self._record

    # -- recording ---------------------------------------------------------

    def record(self, kind: ResourceKind | str, amount: int, *,
               source: str = "declared") -> "Meter":
        """Add ``amount`` units of ``kind``, noting where the number came from.

        ``source`` defaults to ``declared`` because that is what a plain
        measurement in the tool's own code is.  A tool that gets its numbers
        from the service it called should say ``source="provider"``.
        """
        if self._record is not None:
            raise RuntimeError("this meter is already closed")
        resolved = ResourceKind.parse(kind.value if isinstance(kind, ResourceKind) else kind)
        if source not in TRUST_ORDER:
            raise ValueError(f"unknown measurement source {source!r}")
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise TypeError("metered amounts must be integers")
        if amount < 0:
            raise ValueError("metered amounts must not be negative")
        if amount:
            self._totals[resolved.value] = self._totals.get(resolved.value, 0) + amount
            previous = self._sources.get(resolved.value)
            self._sources[resolved.value] = (
                source if previous is None else least_trusted(previous, source)
            )
        return self

    def note(self, key: str, value: Any) -> "Meter":
        """Attach detail that the evidence digest will cover."""
        if self._record is not None:
            raise RuntimeError("this meter is already closed")
        self.context[key] = value
        return self

    @property
    def totals(self) -> dict[str, int]:
        return dict(self._totals)

    @property
    def sources(self) -> dict[str, str]:
        return dict(self._sources)
