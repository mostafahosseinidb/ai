"""Consensus rules: who is allowed to extend the chain, and on what terms.

Two engines ship with the project and both satisfy the same small interface,
so a network can start as a single-operator authority chain and later swap in
something costlier without touching the state machine:

*   :class:`ProofOfAuthority` -- a fixed set of signers takes turns.  Cheap,
    deterministic, and the right default for an agent that mostly needs an
    auditable record rather than trustless global settlement.
*   :class:`ProofOfWork` -- open-membership sealing by brute force.  Included
    because it is the honest way to run a chain with no trusted set.

Neither engine is a replacement for the state machine's rules; an agent's
over-budget transaction is rejected under either one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, Mapping

from .block import Block, BlockHeader
from .crypto import SigningKey

__all__ = ["ConsensusError", "ConsensusEngine", "ProofOfAuthority", "ProofOfWork", "build_engine"]

#: How far into the future a block timestamp may sit before peers refuse it.
MAX_CLOCK_DRIFT_SECONDS = 120


class ConsensusError(Exception):
    """A block is well-formed but breaks the rules for extending the chain."""


class ConsensusEngine(ABC):
    name: str

    @abstractmethod
    def seal(self, block: Block, key: SigningKey) -> Block:
        """Finalise a freshly built block so peers will accept it."""

    @abstractmethod
    def validate(self, block: Block, parent: Block | None) -> None:
        """Raise :class:`ConsensusError` unless ``block`` may extend ``parent``."""

    def expected_proposer(self, height: int) -> str | None:
        """The signer whose turn it is, when the engine has a schedule."""
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name}

    @staticmethod
    def _check_common(block: Block, parent: Block | None) -> None:
        if parent is None:
            if block.height != 0:
                raise ConsensusError("a chain must start at height 0")
        else:
            if block.height != parent.height + 1:
                raise ConsensusError(
                    f"height must increase by one: parent {parent.height}, block {block.height}"
                )
            if block.header.prev_hash != parent.hash:
                raise ConsensusError("prev_hash does not point at the parent block")
            if block.header.timestamp < parent.header.timestamp:
                raise ConsensusError("block timestamp moves backwards")
            if block.header.chain_id != parent.header.chain_id:
                raise ConsensusError("chain_id does not match the parent")
        if block.computed_merkle_root() != block.header.merkle_root:
            raise ConsensusError("merkle root does not match the transactions")


class ProofOfAuthority(ConsensusEngine):
    """A known set of signers, in round-robin order by height."""

    name = "poa"

    def __init__(self, validators: Iterable[str], *, strict_rotation: bool = False,
                 min_spacing_seconds: int = 0) -> None:
        self.validators = sorted(set(validators))
        if not self.validators:
            raise ValueError("proof of authority needs at least one validator")
        self.strict_rotation = strict_rotation
        self.min_spacing_seconds = min_spacing_seconds

    def expected_proposer(self, height: int) -> str:
        return self.validators[height % len(self.validators)]

    def seal(self, block: Block, key: SigningKey) -> Block:
        if key.public_hex() not in self.validators:
            raise ConsensusError("this key is not in the validator set")
        return block.sign(key)

    def validate(self, block: Block, parent: Block | None) -> None:
        self._check_common(block, parent)
        proposer = block.header.proposer
        if proposer not in self.validators:
            raise ConsensusError(f"{proposer[:12]}... is not an authorised validator")
        if not block.verify_seal():
            raise ConsensusError("block seal is missing or does not verify")
        if self.strict_rotation and proposer != self.expected_proposer(block.height):
            raise ConsensusError(
                f"out of turn: height {block.height} belongs to "
                f"{self.expected_proposer(block.height)[:12]}..."
            )
        if parent is not None and self.min_spacing_seconds:
            elapsed = block.header.timestamp - parent.header.timestamp
            if elapsed < self.min_spacing_seconds:
                raise ConsensusError(
                    f"blocks are {elapsed}s apart, minimum is {self.min_spacing_seconds}s"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "validators": list(self.validators),
            "strict_rotation": self.strict_rotation,
            "min_spacing_seconds": self.min_spacing_seconds,
        }


class ProofOfWork(ConsensusEngine):
    """Sealing by hash difficulty, with the proposer signature still required.

    Keeping the signature means a usage record always names a real proposer,
    which matters for an audit trail even when membership is open.
    """

    name = "pow"

    def __init__(self, difficulty: int = 4, *, max_nonce: int = 1 << 32) -> None:
        if difficulty < 1:
            raise ValueError("difficulty must be at least 1")
        self.difficulty = difficulty
        self.max_nonce = max_nonce

    @property
    def target_prefix(self) -> str:
        return "0" * self.difficulty

    def seal(self, block: Block, key: SigningKey) -> Block:
        for nonce in range(self.max_nonce):
            candidate = BlockHeader(**{**block.header.to_dict(), "nonce": nonce})
            if candidate.hash.startswith(self.target_prefix):
                return Block(candidate, tuple(block.transactions), None).sign(key)
        raise ConsensusError(f"no nonce below {self.max_nonce} satisfies difficulty {self.difficulty}")

    def validate(self, block: Block, parent: Block | None) -> None:
        self._check_common(block, parent)
        if not block.hash.startswith(self.target_prefix):
            raise ConsensusError(f"block hash does not meet difficulty {self.difficulty}")
        if not block.verify_seal():
            raise ConsensusError("block seal is missing or does not verify")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "difficulty": self.difficulty}


def build_engine(spec: Mapping[str, Any]) -> ConsensusEngine:
    """Rebuild an engine from its serialised form (used when loading a chain)."""
    name = spec.get("name", "poa")
    if name == "poa":
        return ProofOfAuthority(
            spec.get("validators", []),
            strict_rotation=spec.get("strict_rotation", False),
            min_spacing_seconds=spec.get("min_spacing_seconds", 0),
        )
    if name == "pow":
        return ProofOfWork(spec.get("difficulty", 4))
    raise ValueError(f"unknown consensus engine {name!r}")
