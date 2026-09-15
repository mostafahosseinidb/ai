"""The resource vocabulary of the network.

Everything an autonomous agent can burn is expressed as an integer quantity of
one :class:`ResourceKind`.  Two rules keep the ledger reproducible across
nodes:

*   **No floating point.**  Consensus code never sees a ``float``; prices are
    integers of *microcredits* (``mc``) per resource unit, so a cost is an
    exact integer product.  A float would eventually round differently on two
    machines and fork the chain.
*   **Named units.**  A quantity is meaningless without its unit, so the unit
    lives next to the kind rather than in a comment somewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Mapping

__all__ = [
    "ResourceKind",
    "MICROCREDIT",
    "CREDIT",
    "PriceTable",
    "DEFAULT_PRICES",
    "format_credits",
]

#: One credit expressed in microcredits, the atomic accounting unit.
MICROCREDIT = 1
CREDIT = 1_000_000 * MICROCREDIT


class ResourceKind(str, Enum):
    """A metered resource.  The value is what appears on the wire."""

    COMPUTE_MS = "compute_ms"
    LLM_INPUT_TOKENS = "llm_input_tokens"
    LLM_OUTPUT_TOKENS = "llm_output_tokens"
    STORAGE_BYTES = "storage_bytes"
    NETWORK_BYTES = "network_bytes"
    TOOL_CALL = "tool_call"

    @property
    def unit(self) -> str:
        return _UNITS[self]

    @classmethod
    def parse(cls, value: str) -> "ResourceKind":
        try:
            return cls(value)
        except ValueError as exc:
            known = ", ".join(k.value for k in cls)
            raise ValueError(f"unknown resource {value!r}; known kinds: {known}") from exc


_UNITS: Mapping[ResourceKind, str] = {
    ResourceKind.COMPUTE_MS: "milliseconds of CPU time",
    ResourceKind.LLM_INPUT_TOKENS: "tokens sent to a model",
    ResourceKind.LLM_OUTPUT_TOKENS: "tokens produced by a model",
    ResourceKind.STORAGE_BYTES: "bytes committed to durable storage",
    ResourceKind.NETWORK_BYTES: "bytes moved over the network",
    ResourceKind.TOOL_CALL: "invocations of an external tool",
}


@dataclass(frozen=True)
class PriceTable:
    """Microcredits charged per unit of each resource.

    The table is part of the chain state: changing a price is a governance
    transaction, not a config-file edit, so every agent can audit exactly what
    it was charged and under which schedule.
    """

    prices: Mapping[str, int]

    def __post_init__(self) -> None:
        normalised: dict[str, int] = {}
        for key, value in self.prices.items():
            kind = ResourceKind.parse(key.value if isinstance(key, ResourceKind) else key)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"price for {kind.value} must be an int, got {type(value).__name__}")
            if value < 0:
                raise ValueError(f"price for {kind.value} must not be negative")
            normalised[kind.value] = value
        missing = [k.value for k in ResourceKind if k.value not in normalised]
        if missing:
            raise ValueError(f"price table is missing: {', '.join(sorted(missing))}")
        object.__setattr__(self, "prices", normalised)

    def cost(self, kind: ResourceKind | str, amount: int) -> int:
        """Exact integer cost in microcredits for ``amount`` units."""
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise TypeError("resource amounts must be integers")
        if amount < 0:
            raise ValueError("resource amounts must not be negative")
        resolved = ResourceKind.parse(kind.value if isinstance(kind, ResourceKind) else kind)
        return self.prices[resolved.value] * amount

    def to_dict(self) -> dict[str, int]:
        return dict(sorted(self.prices.items()))

    @classmethod
    def from_dict(cls, data: Mapping[str, int]) -> "PriceTable":
        return cls(dict(data))

    def replace(self, updates: Mapping[str, int]) -> "PriceTable":
        merged = dict(self.prices)
        for key, value in updates.items():
            kind = ResourceKind.parse(key.value if isinstance(key, ResourceKind) else key)
            merged[kind.value] = value
        return PriceTable(merged)

    def __iter__(self) -> Iterator[tuple[str, int]]:
        return iter(sorted(self.prices.items()))


#: A starting schedule, loosely proportional to what these resources really
#: cost.  Networks are expected to tune it through governance.
DEFAULT_PRICES = PriceTable(
    {
        ResourceKind.COMPUTE_MS: 10,
        ResourceKind.LLM_INPUT_TOKENS: 3,
        ResourceKind.LLM_OUTPUT_TOKENS: 15,
        ResourceKind.STORAGE_BYTES: 1,
        ResourceKind.NETWORK_BYTES: 1,
        ResourceKind.TOOL_CALL: 5_000,
    }
)


def format_credits(microcredits: int) -> str:
    """Render a microcredit amount the way the CLI shows it."""
    sign = "-" if microcredits < 0 else ""
    whole, fraction = divmod(abs(microcredits), CREDIT)
    return f"{sign}{whole}.{fraction:06d} cr"
