"""The chain itself: blocks, the state they produce, and durable storage.

Appending a block is a two-key lock.  The consensus engine decides whether the
block *may* extend the tip, and the state machine decides whether its
transactions are *applicable*.  Both must agree, and the resulting state root
must equal the one the proposer committed to -- so a proposer cannot hand a
peer a block whose transactions say one thing and whose state says another.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from .block import Block, BlockHeader
from .consensus import ConsensusEngine, ConsensusError, ProofOfAuthority, build_engine
from .crypto import SigningKey, canonical_bytes, merkle_root, sha256_hex
from .state import GenesisConfig, Receipt, StateError, WorldState
from .transactions import InvalidTransaction, Transaction

__all__ = ["Chain", "ChainError", "LedgerBusy", "ProposalResult", "MAX_TXS_PER_BLOCK"]

MAX_TXS_PER_BLOCK = 512

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


class ChainError(Exception):
    """The chain refused a block."""


class LedgerBusy(ChainError):
    """Another process already holds the write lock on this ledger."""


class _WriteLock:
    """An advisory exclusive lock, held for as long as a chain may append.

    A ledger file has exactly one valid next block, so two processes
    appending to it independently do not merge -- they produce two blocks at
    the same height and the file stops loading at all.  That is not a
    hypothetical: it happens the first time someone runs ``chainmind policy``
    while ``chainmind serve --chat`` is up.

    The lock lives in a sidecar file so that rewriting the ledger itself
    (which happens when a genesis block is written) does not disturb it.
    """

    def __init__(self, ledger_path: Path) -> None:
        self.path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
        self._handle = None

    def acquire(self) -> None:
        if self._handle is not None or fcntl is None:
            # Without fcntl there is no advisory locking to do; the caller
            # keeps working rather than being blocked on a platform that
            # cannot offer the guarantee.
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise LedgerBusy(
                f"another process is writing to {self.path.stem}; "
                "stop it before running a command that changes the ledger "
                "(a running 'chainmind serve --chat' holds this lock)"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __del__(self) -> None:
        # The OS drops an flock when the process exits, but closing the
        # handle here keeps a long-lived program from leaking descriptors.
        try:
            self.release()
        except Exception:
            pass


@dataclass
class ProposalResult:
    """What came out of building a block."""

    block: Block
    receipts: list[Receipt]
    rejected: list[tuple[Transaction, str]]

    @property
    def accepted(self) -> int:
        return len(self.receipts)


class Chain:
    def __init__(self, genesis: GenesisConfig, consensus: ConsensusEngine | None = None,
                 *, path: Path | str | None = None) -> None:
        self.genesis_config = genesis
        self.consensus = consensus or ProofOfAuthority(
            list(genesis.authorities) + list(genesis.validators)
        )
        self.path = Path(path) if path else None
        self.blocks: list[Block] = []
        self.state = WorldState(genesis)
        self._receipts: dict[str, Receipt] = {}
        self._lock = _WriteLock(self.path) if self.path else None

    # -- construction ------------------------------------------------------

    @staticmethod
    def genesis_commitment(genesis: GenesisConfig) -> str:
        """The genesis block's ``prev_hash``.

        Pointing at a hash of the configuration rather than at 64 zeros means
        the founding authorities, prices and quotas are cryptographically part
        of the chain: swap them and every block hash after it changes.
        """
        return sha256_hex(canonical_bytes(genesis.to_dict()))

    @classmethod
    def create(cls, genesis: GenesisConfig, sealer: SigningKey,
               consensus: ConsensusEngine | None = None, *,
               path: Path | str | None = None, timestamp: int | None = None) -> "Chain":
        """Build a chain and seal its genesis block."""
        chain = cls(genesis, consensus, path=path)
        chain._ensure_writable()
        header = BlockHeader(
            height=0,
            prev_hash=cls.genesis_commitment(genesis),
            merkle_root=merkle_root([]),
            state_root=chain.state.state_root(),
            timestamp=timestamp if timestamp is not None else int(time.time()),
            proposer=sealer.public_hex(),
            chain_id=genesis.chain_id,
        )
        block = chain.consensus.seal(Block(header, ()), sealer)
        chain.consensus.validate(block, None)
        chain.blocks.append(block)
        chain.state.height = 0
        chain._persist(block)
        return chain

    # -- accessors ---------------------------------------------------------

    @property
    def tip(self) -> Block:
        if not self.blocks:
            raise ChainError("the chain has no genesis block yet")
        return self.blocks[-1]

    @property
    def height(self) -> int:
        return self.tip.height

    def block_at(self, height: int) -> Block:
        if not 0 <= height < len(self.blocks):
            raise ChainError(f"no block at height {height}")
        return self.blocks[height]

    def receipt(self, txid: str) -> Receipt | None:
        return self._receipts.get(txid)

    def transactions(self) -> Iterator[tuple[Block, Transaction]]:
        for block in self.blocks:
            for tx in block.transactions:
                yield block, tx

    def find_transaction(self, txid: str) -> tuple[Block, Transaction] | None:
        for block, tx in self.transactions():
            if tx.txid == txid:
                return block, tx
        return None

    # -- block production --------------------------------------------------

    def propose(self, sealer: SigningKey, transactions: Sequence[Transaction], *,
                timestamp: int | None = None) -> ProposalResult:
        """Build the next block, dropping transactions the state rejects.

        Dropping rather than failing is deliberate: one agent submitting an
        over-budget usage record must not stall everyone else's block.  The
        rejection and its reason come back to the caller so the agent can be
        told exactly why the chain refused it.
        """
        height = self.height + 1
        work = self.state.copy()
        work.height = height

        accepted: list[Transaction] = []
        receipts: list[Receipt] = []
        rejected: list[tuple[Transaction, str]] = []

        for tx in transactions[:MAX_TXS_PER_BLOCK]:
            try:
                receipts.append(work.apply(tx, height))
                accepted.append(tx)
            except (StateError, InvalidTransaction) as exc:
                rejected.append((tx, str(exc)))

        work.check_invariants()
        header = BlockHeader(
            height=height,
            prev_hash=self.tip.hash,
            merkle_root=merkle_root([tx.txid for tx in accepted]),
            state_root=work.state_root(),
            timestamp=timestamp if timestamp is not None else int(time.time()),
            proposer=sealer.public_hex(),
            chain_id=self.genesis_config.chain_id,
        )
        block = self.consensus.seal(Block(header, tuple(accepted)), sealer)
        return ProposalResult(block=block, receipts=receipts, rejected=rejected)

    def _ensure_writable(self) -> None:
        """Claim the right to append before any state is advanced.

        Taking the lock inside ``_persist`` was too late: by then the block
        was already committed in memory, so a losing writer ended up one
        block ahead of its own file.
        """
        if self.path is not None and self._lock is not None:
            self._lock.acquire()

    def append(self, block: Block) -> list[Receipt]:
        """Validate a block from anywhere and, if it holds up, commit it."""
        self._ensure_writable()
        parent = self.blocks[-1] if self.blocks else None
        try:
            self.consensus.validate(block, parent)
        except ConsensusError as exc:
            raise ChainError(f"consensus rejected block {block.hash[:12]}: {exc}") from exc

        work = self.state.copy()
        work.height = block.height
        receipts: list[Receipt] = []
        for tx in block.transactions:
            try:
                receipts.append(work.apply(tx, block.height))
            except (StateError, InvalidTransaction) as exc:
                raise ChainError(
                    f"block {block.hash[:12]} contains an inapplicable transaction "
                    f"{tx.txid[:12]}: {exc}"
                ) from exc

        if work.state_root() != block.header.state_root:
            raise ChainError(
                f"state root mismatch in block {block.hash[:12]}: "
                f"computed {work.state_root()[:12]}, header claims "
                f"{block.header.state_root[:12]}"
            )
        work.check_invariants()

        self.state = work
        self.blocks.append(block)
        for receipt in receipts:
            self._receipts[receipt.txid] = receipt
        self._persist(block)
        return receipts

    def seal_block(self, sealer: SigningKey, transactions: Sequence[Transaction], *,
                   timestamp: int | None = None) -> ProposalResult:
        """Propose and immediately append -- the single-node path."""
        result = self.propose(sealer, transactions, timestamp=timestamp)
        self.append(result.block)
        return result

    # -- verification ------------------------------------------------------

    def verify(self) -> WorldState:
        """Replay the whole chain from genesis and return the state it yields.

        This is the audit entry point: it trusts nothing that is already in
        memory, so a tampered block or a rewritten usage record surfaces here.
        """
        if not self.blocks:
            raise ChainError("nothing to verify: the chain is empty")

        genesis_block = self.blocks[0]
        expected_prev = self.genesis_commitment(self.genesis_config)
        if genesis_block.header.prev_hash != expected_prev:
            raise ChainError("the genesis block does not commit to this genesis configuration")

        state = WorldState(self.genesis_config)
        self.consensus.validate(genesis_block, None)
        if genesis_block.header.state_root != state.state_root():
            raise ChainError("the genesis state root does not match the configuration")

        for index, block in enumerate(self.blocks[1:], start=1):
            parent = self.blocks[index - 1]
            try:
                self.consensus.validate(block, parent)
            except ConsensusError as exc:
                raise ChainError(f"block {block.height} fails consensus: {exc}") from exc
            state.height = block.height
            for tx in block.transactions:
                try:
                    state.apply(tx, block.height)
                except (StateError, InvalidTransaction) as exc:
                    raise ChainError(
                        f"block {block.height} transaction {tx.txid[:12]} is inapplicable: {exc}"
                    ) from exc
            if state.state_root() != block.header.state_root:
                raise ChainError(f"block {block.height} commits to a different state root")
            state.check_invariants()
        return state

    # -- persistence -------------------------------------------------------

    def _persist(self, block: Block) -> None:
        if self.path is None:
            return
        self._ensure_writable()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if block.height == 0:
            header_line = json.dumps(
                {
                    "kind": "chainmind-ledger",
                    "version": 1,
                    "genesis": self.genesis_config.to_dict(),
                    "consensus": self.consensus.to_dict(),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            self.path.write_text(header_line + "\n", encoding="utf-8")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(block.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: Path | str) -> "Chain":
        """Read a ledger file and replay it, validating every block."""
        path = Path(path)
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            raise ChainError(f"{path} is empty")

        head = json.loads(lines[0])
        if head.get("kind") != "chainmind-ledger":
            raise ChainError(f"{path} is not a ChainMind ledger")

        genesis = GenesisConfig.from_dict(head["genesis"])
        chain = cls(genesis, build_engine(head.get("consensus", {})), path=path)

        for raw in lines[1:]:
            block = Block.from_dict(json.loads(raw))
            if block.height == 0:
                chain.consensus.validate(block, None)
                if block.header.prev_hash != cls.genesis_commitment(genesis):
                    raise ChainError("stored genesis block does not match the stored configuration")
                if block.header.state_root != chain.state.state_root():
                    raise ChainError("stored genesis state root does not match the configuration")
                chain.blocks.append(block)
                chain.state.height = 0
            else:
                saved_path, chain.path = chain.path, None   # replay must not rewrite the file
                try:
                    chain.append(block)
                finally:
                    chain.path = saved_path
        return chain

    def close(self) -> None:
        """Release the write lock, if this chain took one."""
        if self._lock is not None:
            self._lock.release()

    def __enter__(self) -> "Chain":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False

    def __len__(self) -> int:
        return len(self.blocks)

    def __repr__(self) -> str:
        return f"<Chain {self.genesis_config.chain_id} height={len(self.blocks) - 1}>"
