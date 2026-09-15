"""Blocks and their headers.

A header commits to three things at once: the transactions it contains (via
the Merkle root), the state they produce (via the state root) and the history
behind it (via the previous hash).  Committing to the *resulting* state is
what lets an auditor check "did this agent really only spend what the chain
allowed?" without re-running the whole world -- the roots disagree the moment
anyone rewrites a usage record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .crypto import SigningKey, canonical_bytes, double_sha256_hex, merkle_root, verify_signature
from .transactions import Transaction

__all__ = ["BlockHeader", "Block", "GENESIS_PREV_HASH"]

GENESIS_PREV_HASH = "0" * 64
BLOCK_VERSION = 1


@dataclass(frozen=True)
class BlockHeader:
    height: int
    prev_hash: str
    merkle_root: str
    state_root: str
    timestamp: int
    proposer: str
    chain_id: str
    nonce: int = 0
    version: int = BLOCK_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "chain_id": self.chain_id,
            "height": self.height,
            "prev_hash": self.prev_hash,
            "merkle_root": self.merkle_root,
            "state_root": self.state_root,
            "timestamp": self.timestamp,
            "proposer": self.proposer,
            "nonce": self.nonce,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BlockHeader":
        return cls(
            height=data["height"],
            prev_hash=data["prev_hash"],
            merkle_root=data["merkle_root"],
            state_root=data["state_root"],
            timestamp=data["timestamp"],
            proposer=data["proposer"],
            chain_id=data["chain_id"],
            nonce=data.get("nonce", 0),
            version=data.get("version", BLOCK_VERSION),
        )

    def signing_payload(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @property
    def hash(self) -> str:
        return double_sha256_hex(self.signing_payload())

    def with_nonce(self, nonce: int) -> "BlockHeader":
        return BlockHeader(**{**self.to_dict(), "nonce": nonce})


@dataclass(frozen=True)
class Block:
    header: BlockHeader
    transactions: Sequence[Transaction] = field(default_factory=tuple)
    seal: str | None = None       # proposer signature over the header

    @property
    def hash(self) -> str:
        return self.header.hash

    @property
    def height(self) -> int:
        return self.header.height

    def computed_merkle_root(self) -> str:
        return merkle_root([tx.txid for tx in self.transactions])

    def sign(self, key: SigningKey) -> "Block":
        if key.public_hex() != self.header.proposer:
            raise ValueError("the sealing key does not match the declared proposer")
        return Block(self.header, tuple(self.transactions), key.sign(self.header.signing_payload()))

    def verify_seal(self) -> bool:
        if not self.seal:
            return False
        return verify_signature(self.header.proposer, self.header.signing_payload(), self.seal)

    def with_header(self, header: BlockHeader) -> "Block":
        return Block(header, tuple(self.transactions), None)  # resealing is required

    def to_dict(self) -> dict[str, Any]:
        return {
            "header": self.header.to_dict(),
            "hash": self.hash,
            "seal": self.seal,
            "transactions": [tx.to_dict() for tx in self.transactions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Block":
        block = cls(
            header=BlockHeader.from_dict(data["header"]),
            transactions=tuple(Transaction.from_dict(t) for t in data.get("transactions", [])),
            seal=data.get("seal"),
        )
        declared = data.get("hash")
        if declared is not None and declared != block.hash:
            raise ValueError("declared block hash does not match the header")
        return block

    def summary(self) -> str:
        return (
            f"#{self.height:<4} {self.hash[:12]} "
            f"txs={len(self.transactions):<3} proposer={self.header.proposer[:10]}"
        )
