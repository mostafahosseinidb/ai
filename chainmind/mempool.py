"""A small pending-transaction pool.

The pool keeps at most one transaction per ``(sender, nonce)`` pair, which is
enough to stop accidental double submission while leaving deliberate
replacement (same nonce, higher timestamp) possible.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from .transactions import InvalidTransaction, Transaction

__all__ = ["Mempool"]


class Mempool:
    def __init__(self, *, capacity: int = 10_000) -> None:
        self.capacity = capacity
        self._by_slot: dict[tuple[str, int], Transaction] = {}

    def add(self, tx: Transaction) -> bool:
        """Accept a structurally valid transaction.  Returns ``True`` if stored."""
        tx.validate()
        slot = (tx.sender, tx.nonce)
        existing = self._by_slot.get(slot)
        if existing is not None:
            if existing.txid == tx.txid:
                return False
            if tx.timestamp <= existing.timestamp:
                raise InvalidTransaction(
                    f"a transaction already occupies nonce {tx.nonce} for this sender"
                )
        if len(self._by_slot) >= self.capacity and slot not in self._by_slot:
            raise InvalidTransaction("mempool is full")
        self._by_slot[slot] = tx
        return True

    def extend(self, transactions: Iterable[Transaction]) -> int:
        return sum(1 for tx in transactions if self.add(tx))

    def take(self, limit: int) -> list[Transaction]:
        """Up to ``limit`` transactions, ordered so a sender's nonces ascend."""
        ordered = sorted(self._by_slot.values(), key=lambda tx: (tx.sender, tx.nonce))
        return ordered[:limit]

    def drop(self, transactions: Iterable[Transaction]) -> None:
        for tx in transactions:
            self._by_slot.pop((tx.sender, tx.nonce), None)

    def clear(self) -> None:
        self._by_slot.clear()

    def __len__(self) -> int:
        return len(self._by_slot)

    def __iter__(self) -> Iterator[Transaction]:
        return iter(self.take(len(self._by_slot)))

    def __contains__(self, txid: object) -> bool:
        return any(tx.txid == txid for tx in self._by_slot.values())
