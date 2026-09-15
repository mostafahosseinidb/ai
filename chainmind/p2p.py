"""The network layer: turning an installed copy into a node of the chain.

Until now a ledger was a file one process owned.  This module makes it a
thing several machines keep in step -- which is what a chain is for.

The protocol is newline-delimited JSON over TCP, because it is inspectable
with ``nc`` and needs no dependency.  What a node does is small:

*   **Handshake.**  Peers exchange chain id, genesis commitment, height and
    tip.  A peer on a different chain, or on the same chain id with a
    different genesis, is refused at the door rather than allowed to feed us
    blocks that will never validate.
*   **Sync.**  Whoever is behind asks for the blocks it is missing, in
    batches, and applies them through the same validation path as any other
    block.  Nothing is trusted because it arrived over a socket.
*   **Gossip.**  A new block or transaction is relayed once to everyone
    else; the relay stops when a peer already has it.
*   **Join.**  A fresh install with no ledger can ask a peer for the genesis
    configuration and the whole history, verify it, and start participating.

What this does **not** do is reorganise.  A node that receives a competing
branch of equal or greater height reports the fork and keeps its own chain
rather than silently rewriting history.  With a single authority -- the
common case -- forks cannot happen; with several, this is the honest
behaviour until a reorg path exists that has been tested properly.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .block import Block
from .chain import Chain, ChainError
from .consensus import build_engine
from .crypto import SigningKey, verify_signature
from .mempool import Mempool
from .state import GenesisConfig
from .transactions import InvalidTransaction, Transaction

__all__ = ["Node", "NodeError", "PeerInfo", "join_network", "submit_to_node",
           "PROTOCOL_VERSION", "DEFAULT_P2P_PORT"]

PROTOCOL_VERSION = 1
DEFAULT_P2P_PORT = 8877

#: A block full of transactions is the largest thing on the wire; anything
#: beyond this is a peer misbehaving, not a big block.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
SYNC_BATCH = 64
HANDSHAKE_TIMEOUT = 15.0
#: How long a reader waits before looking at the stop flag again. Short,
#: because this is what decides how quickly a node shuts down.
POLL_INTERVAL = 0.5


class NodeError(Exception):
    """The node refused a peer or a message."""


@dataclass(frozen=True)
class PeerInfo:
    """What we know about the other end of a connection."""

    node_id: str
    host: str
    port: int
    chain_id: str
    genesis_hash: str
    height: int
    tip_hash: str
    #: True while a peer is downloading its first copy of the chain and so
    #: has no chain of its own to compare against ours.
    joining: bool = False

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id, "host": self.host, "port": self.port,
            "chain_id": self.chain_id, "genesis_hash": self.genesis_hash,
            "height": self.height, "tip_hash": self.tip_hash, "joining": self.joining,
        }


# --------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------

class Wire:
    """One socket, framed as newline-delimited JSON.

    The read buffer is managed by hand rather than through ``makefile``.
    A buffered file object and a socket timeout do not mix: a timeout part
    way through a line loses whatever had already arrived, and the next read
    resumes mid-message.  Keeping the buffer here means a timeout is simply
    "nothing complete yet", which is what a polling reader needs.
    """

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._buffer = bytearray()
        self._send_lock = threading.Lock()
        self._closed = False

    def send(self, kind: str, payload: Mapping[str, Any] | None = None) -> None:
        line = json.dumps({"type": kind, "payload": dict(payload or {})},
                          ensure_ascii=False).encode("utf-8") + b"\n"
        if len(line) > MAX_MESSAGE_BYTES:
            raise NodeError(f"refusing to send a {len(line)} byte message")
        with self._send_lock:
            if self._closed:
                raise ConnectionError("this connection is closed")
            self.sock.sendall(line)

    def receive(self, timeout: float | None = None) -> tuple[str, dict[str, Any]]:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[:newline + 1]
                return self._parse(line)
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                raise NodeError("a peer sent an oversized message")

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise socket.timeout("no complete message arrived in time")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("the peer closed the connection")
            self._buffer += chunk

    @staticmethod
    def _parse(line: bytes) -> tuple[str, dict[str, Any]]:
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise NodeError("a peer sent something that is not JSON") from exc
        if not isinstance(message, dict) or "type" not in message:
            raise NodeError("a peer sent a malformed message")
        payload = message.get("payload")
        return str(message["type"]), dict(payload) if isinstance(payload, dict) else {}

    def close(self) -> None:
        self._closed = True
        # shutdown() is the half that actually wakes a thread blocked in
        # recv(); close() alone leaves it waiting until its timeout expires.
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------

def _hello_payload(node_key: SigningKey, chain: Chain | None, listen_port: int,
                   label: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version": PROTOCOL_VERSION,
        "node_id": node_key.public_hex(),
        "listen_port": listen_port,
        "label": label,
        "chain_id": chain.genesis_config.chain_id if chain else "",
        "genesis_hash": Chain.genesis_commitment(chain.genesis_config) if chain else "",
        "height": chain.height if chain and len(chain) else -1,
        "tip_hash": chain.tip.hash if chain and len(chain) else "",
        "timestamp": int(time.time()),
    }
    # Signing the greeting means a node id in a peer list cannot simply be
    # claimed by whoever connects next.
    signable = {k: v for k, v in body.items() if k != "signature"}
    body["signature"] = node_key.sign(
        json.dumps(signable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return body


def _verify_hello(payload: Mapping[str, Any]) -> bool:
    signature = payload.get("signature")
    node_id = payload.get("node_id")
    if not isinstance(signature, str) or not isinstance(node_id, str):
        return False
    signable = {k: v for k, v in payload.items() if k != "signature"}
    return verify_signature(
        node_id,
        json.dumps(signable, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        signature,
    )


# --------------------------------------------------------------------------
# The node
# --------------------------------------------------------------------------

class Node:
    """A peer on the network, holding one ledger.

    The node owns the chain: it is the process that appends, so it is the
    process that holds the ledger's write lock.  A dashboard reading the same
    file alongside it needs no lock and is unaffected.
    """

    def __init__(self, chain: Chain, *, node_key: SigningKey | None = None,
                 mempool: Mempool | None = None, host: str = "127.0.0.1",
                 port: int = DEFAULT_P2P_PORT, label: str = "",
                 on_block: Callable[[Block], None] | None = None,
                 on_transaction: Callable[[Transaction], None] | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self.chain = chain
        self.node_key = node_key or SigningKey.generate()
        self.mempool = mempool if mempool is not None else Mempool()
        self.host = host
        self.port = port
        self.label = label
        self.on_block = on_block
        self.on_transaction = on_transaction
        self._log = log or (lambda message: None)

        self._lock = threading.RLock()
        self._peers: dict[str, tuple[Wire, PeerInfo]] = {}
        self._known: set[tuple[str, int]] = set()
        self._server: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.forks_seen: list[dict[str, Any]] = []

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "Node":
        # Claim the ledger before listening, not on the first block written.
        # A node that only takes the lock lazily leaves a window where another
        # process can append to the same file, and the two then disagree about
        # what the chain is.
        self.chain._ensure_writable()

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(32)
        server.settimeout(0.5)
        self._server = server
        self.port = server.getsockname()[1]
        self._spawn(self._accept_loop, "accept")
        self._log(f"listening on {self.host}:{self.port} as {self.node_id[:12]}")
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        with self._lock:
            peers = list(self._peers.values())
            self._peers.clear()
        for wire, _ in peers:
            wire.close()
        for thread in self._threads:
            thread.join(timeout=5)
        self.chain.close()

    def __enter__(self) -> "Node":
        return self.start()

    def __exit__(self, *exc_info) -> bool:
        self.stop()
        return False

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=f"chainmind-{name}", daemon=True)
        thread.start()
        self._threads.append(thread)

    # -- accessors ---------------------------------------------------------

    @property
    def node_id(self) -> str:
        return self.node_key.public_hex()

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    def peers(self) -> list[PeerInfo]:
        with self._lock:
            return [info for _, info in self._peers.values()]

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "node_id": self.node_id,
                "label": self.label,
                "address": f"{self.host}:{self.port}",
                "chain_id": self.chain.genesis_config.chain_id,
                "genesis_hash": Chain.genesis_commitment(self.chain.genesis_config),
                "height": self.chain.height,
                "tip_hash": self.chain.tip.hash,
                "peers": [info.to_dict() for _, info in self._peers.values()],
                "pending": len(self.mempool),
                "forks_seen": len(self.forks_seen),
            }

    # -- connecting --------------------------------------------------------

    def connect(self, host: str, port: int, *, timeout: float = HANDSHAKE_TIMEOUT) -> PeerInfo:
        """Dial a peer, shake hands, and start following it."""
        sock = socket.create_connection((host, port), timeout=timeout)
        wire = Wire(sock)
        try:
            wire.send("hello", _hello_payload(self.node_key, self.chain, self.port, self.label))
            kind, payload = wire.receive(timeout)
            if kind == "error":
                raise NodeError(payload.get("message", "the peer refused us"))
            if kind != "hello":
                raise NodeError(f"expected a hello, got {kind!r}")
            info = self._accept_hello(payload, host)
        except Exception:
            wire.close()
            raise

        self._register(wire, info)
        self._spawn(lambda: self._serve(wire, info), f"peer-{info.node_id[:8]}")
        self._request_missing(wire, info)
        wire.send("get_peers")
        return info

    def connect_all(self, seeds: Iterable[tuple[str, int]]) -> list[PeerInfo]:
        connected = []
        for host, port in seeds:
            try:
                connected.append(self.connect(host, port))
            except (OSError, NodeError) as exc:
                self._log(f"could not reach {host}:{port}: {exc}")
        return connected

    def _accept_hello(self, payload: Mapping[str, Any], host: str) -> PeerInfo:
        if payload.get("version") != PROTOCOL_VERSION:
            raise NodeError(f"peer speaks protocol {payload.get('version')}, we speak {PROTOCOL_VERSION}")
        if not _verify_hello(payload):
            raise NodeError("the peer's greeting is not signed by the key it claims")

        if payload.get("node_id") == self.node_id:
            raise NodeError("refusing to connect to ourselves")

        # A fresh install has no ledger yet, so it cannot name a chain. It is
        # not on a *different* chain -- it is on none -- and refusing it would
        # make joining impossible. It gets served, not synced from.
        if not payload.get("chain_id") and int(payload.get("height", -1)) < 0:
            return PeerInfo(
                node_id=str(payload["node_id"]), host=host,
                port=int(payload.get("listen_port") or 0),
                chain_id="", genesis_hash="", height=-1, tip_hash="", joining=True,
            )

        ours_id = self.chain.genesis_config.chain_id
        ours_genesis = Chain.genesis_commitment(self.chain.genesis_config)
        if payload.get("chain_id") != ours_id:
            raise NodeError(
                f"peer is on chain {payload.get('chain_id')!r}, we are on {ours_id!r}"
            )
        if payload.get("genesis_hash") != ours_genesis:
            # Same name, different founding configuration: every block they
            # send would fail to validate, so there is nothing to gain by
            # staying connected and a lot of confusion to gain by trying.
            raise NodeError(
                "peer shares our chain id but not our genesis; refusing to sync with it"
            )
        return PeerInfo(
            node_id=str(payload["node_id"]),
            host=host,
            port=int(payload.get("listen_port") or 0),
            chain_id=str(payload.get("chain_id", "")),
            genesis_hash=str(payload.get("genesis_hash", "")),
            height=int(payload.get("height", -1)),
            tip_hash=str(payload.get("tip_hash", "")),
        )

    def _register(self, wire: Wire, info: PeerInfo) -> None:
        with self._lock:
            existing = self._peers.get(info.node_id)
            self._peers[info.node_id] = (wire, info)
            if info.port:
                self._known.add(info.address)
        if existing is not None:
            existing[0].close()

    def _drop(self, info: PeerInfo, reason: str = "") -> None:
        with self._lock:
            entry = self._peers.pop(info.node_id, None)
        if entry is not None:
            entry[0].close()
            self._log(f"dropped {info.node_id[:12]}{': ' + reason if reason else ''}")

    # -- serving -----------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, address = self._server.accept()   # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError:
                break
            self._spawn(lambda s=sock, a=address: self._greet(s, a), "greet")

    def _greet(self, sock: socket.socket, address: tuple[str, int]) -> None:
        wire = Wire(sock)
        try:
            kind, payload = wire.receive(HANDSHAKE_TIMEOUT)
            if kind != "hello":
                raise NodeError(f"expected a hello, got {kind!r}")
            info = self._accept_hello(payload, address[0])
        except (NodeError, ConnectionError, OSError, socket.timeout) as exc:
            try:
                wire.send("error", {"message": str(exc)})
            except OSError:
                pass
            wire.close()
            self._log(f"refused {address[0]}: {exc}")
            return

        try:
            wire.send("hello", _hello_payload(self.node_key, self.chain, self.port, self.label))
        except OSError:
            wire.close()
            return

        if info.joining:
            # Serve it the chain, but do not treat it as a peer: it has
            # nothing to offer yet and should not receive gossip mid-download.
            self._log(f"{info.node_id[:12]} is joining; serving it the chain")
            self._serve(wire, info)
            wire.close()
            return

        self._register(wire, info)
        self._log(f"peer {info.node_id[:12]} at height {info.height}")
        self._request_missing(wire, info)
        self._serve(wire, info)

    def _serve(self, wire: Wire, info: PeerInfo) -> None:
        while not self._stop.is_set():
            try:
                kind, payload = wire.receive(POLL_INTERVAL)
            except socket.timeout:
                continue
            except (ConnectionError, OSError, NodeError) as exc:
                self._drop(info, str(exc))
                return
            try:
                self._handle(wire, info, kind, payload)
            except (ConnectionError, OSError) as exc:
                # The peer went away while we were answering it. That is a
                # disconnect, not a fault, and it must not take the serving
                # thread down with a traceback.
                self._drop(info, str(exc))
                return
            except (NodeError, ChainError, InvalidTransaction, ValueError) as exc:
                self._log(f"{info.node_id[:12]} sent something we refused: {exc}")

    def _handle(self, wire: Wire, info: PeerInfo, kind: str, payload: dict[str, Any]) -> None:
        if kind == "get_genesis":
            wire.send("genesis", {
                "genesis": self.chain.genesis_config.to_dict(),
                "consensus": self.chain.consensus.to_dict(),
            })
        elif kind == "get_blocks":
            self._send_blocks(wire, int(payload.get("from_height", 0)),
                              int(payload.get("limit", SYNC_BATCH)))
        elif kind == "blocks":
            self._receive_blocks(wire, info, payload.get("blocks", []))
        elif kind == "block":
            self._receive_blocks(wire, info, [payload.get("block")])
        elif kind == "tx":
            self._receive_transaction(info, payload.get("tx"))
        elif kind == "get_peers":
            wire.send("peers", {"peers": self.known_addresses()})
        elif kind == "peers":
            self._learn_peers(payload.get("peers", []))
        elif kind in ("hello", "error", "bye"):
            pass
        else:
            raise NodeError(f"unknown message {kind!r}")

    # -- sync and gossip ---------------------------------------------------

    def _request_missing(self, wire: Wire, info: PeerInfo) -> None:
        if info.height > self.chain.height:
            wire.send("get_blocks",
                      {"from_height": self.chain.height + 1, "limit": SYNC_BATCH})

    def _send_blocks(self, wire: Wire, from_height: int, limit: int) -> None:
        from_height = max(0, from_height)
        limit = max(1, min(limit, SYNC_BATCH))
        with self._lock:
            blocks = [
                block.to_dict()
                for block in self.chain.blocks[from_height:from_height + limit]
            ]
        if blocks:
            wire.send("blocks", {"blocks": blocks})

    def _receive_blocks(self, wire: Wire, info: PeerInfo, raw_blocks: Iterable[Any]) -> None:
        applied = 0
        for raw in raw_blocks:
            if not isinstance(raw, Mapping):
                continue
            try:
                block = Block.from_dict(raw)
            except (ValueError, InvalidTransaction) as exc:
                raise NodeError(f"undecodable block: {exc}") from exc

            with self._lock:
                height = self.chain.height
                if block.height <= height:
                    known = self.chain.block_at(block.height)
                    if known.hash != block.hash:
                        self._note_fork(info, block, known)
                    continue
                if block.height > height + 1:
                    wire.send("get_blocks",
                              {"from_height": height + 1, "limit": SYNC_BATCH})
                    return
                if block.header.prev_hash != self.chain.tip.hash:
                    self._note_fork(info, block, self.chain.tip)
                    return
                self.chain.append(block)
                self.mempool.drop(block.transactions)
                applied += 1

            if self.on_block is not None:
                self.on_block(block)
            self._relay("block", {"block": block.to_dict()}, skip=info.node_id)

        if applied:
            self._log(f"applied {applied} block(s) from {info.node_id[:12]}, "
                      f"now at {self.chain.height}")
            # There may be more behind what we just got.
            wire.send("get_blocks",
                      {"from_height": self.chain.height + 1, "limit": SYNC_BATCH})

    def _note_fork(self, info: PeerInfo, theirs: Block, ours: Block) -> None:
        """Record a competing branch instead of quietly diverging.

        Reorganisation is not implemented, so the honest response is to keep
        our chain and make the disagreement visible -- an operator can see
        that two validators are producing at the same height, which is a
        governance problem rather than a networking one.
        """
        record = {
            "at": int(time.time()),
            "height": theirs.height,
            "peer": info.node_id,
            "their_hash": theirs.hash,
            "our_hash": ours.hash,
        }
        self.forks_seen.append(record)
        self._log(
            f"FORK at height {theirs.height}: {info.node_id[:12]} has "
            f"{theirs.hash[:12]}, we have {ours.hash[:12]} — keeping ours"
        )

    def _receive_transaction(self, info: PeerInfo, raw: Any) -> None:
        if not isinstance(raw, Mapping):
            return
        tx = Transaction.from_dict(raw)
        with self._lock:
            try:
                fresh = self.mempool.add(tx)
            except InvalidTransaction:
                return
        if not fresh:
            return          # already had it; the relay stops here
        if self.on_transaction is not None:
            self.on_transaction(tx)
        self._relay("tx", {"tx": tx.to_dict()}, skip=info.node_id)

    def _learn_peers(self, entries: Iterable[Any]) -> None:
        for entry in entries:
            try:
                host, port = entry[0], int(entry[1])
            except (TypeError, ValueError, IndexError):
                continue
            if (host, port) == self.address or not port:
                continue
            with self._lock:
                if (host, port) in self._known:
                    continue
                self._known.add((host, port))
            try:
                self.connect(host, port)
            except (OSError, NodeError):
                pass

    def known_addresses(self) -> list[list[Any]]:
        with self._lock:
            return [[host, port] for host, port in sorted(self._known)]

    # -- outbound ----------------------------------------------------------

    def _relay(self, kind: str, payload: Mapping[str, Any], *, skip: str = "") -> None:
        with self._lock:
            targets = [
                (node_id, wire) for node_id, (wire, _) in self._peers.items()
                if node_id != skip
            ]
        for node_id, wire in targets:
            try:
                wire.send(kind, payload)
            except (OSError, NodeError):
                with self._lock:
                    entry = self._peers.pop(node_id, None)
                if entry:
                    entry[0].close()

    def announce_block(self, block: Block) -> None:
        """Tell every peer about a block this node just produced."""
        self._relay("block", {"block": block.to_dict()})

    def announce_transaction(self, tx: Transaction) -> None:
        self._relay("tx", {"tx": tx.to_dict()})

    def submit(self, tx: Transaction) -> bool:
        """Accept a transaction locally and gossip it."""
        with self._lock:
            fresh = self.mempool.add(tx)
        if fresh:
            self.announce_transaction(tx)
        return fresh

    def seal(self, sealer: SigningKey, limit: int = 64) -> Block | None:
        """Produce a block from the pool, if this node may and there is work."""
        with self._lock:
            pending = self.mempool.take(limit)
            if not pending:
                return None
            result = self.chain.seal_block(sealer, pending)
            self.mempool.drop(pending)
        for tx, reason in result.rejected:
            self._log(f"dropped {tx.txid[:10]}: {reason}")
        self.announce_block(result.block)
        return result.block


# --------------------------------------------------------------------------
# Joining a network with no ledger of your own
# --------------------------------------------------------------------------

def join_network(host: str, port: int, path: Path | str, *,
                 expected_genesis: str | None = None,
                 node_key: SigningKey | None = None,
                 log: Callable[[str], None] | None = None) -> Chain:
    """Download a chain from a peer and verify it end to end.

    ``expected_genesis`` is how you avoid trusting the peer: publish the
    genesis hash out of band and a node that is handed a different founding
    configuration refuses it.  Without it this is trust on first use, and the
    caller is told so.
    """
    emit = log or (lambda message: None)
    key = node_key or SigningKey.generate()
    path = Path(path)

    sock = socket.create_connection((host, port), timeout=HANDSHAKE_TIMEOUT)
    wire = Wire(sock)
    try:
        wire.send("hello", _hello_payload(key, None, 0, "joining"))
        kind, payload = wire.receive(HANDSHAKE_TIMEOUT)
        if kind == "error":
            raise NodeError(payload.get("message", "the peer refused us"))
        if kind != "hello" or not _verify_hello(payload):
            raise NodeError("the peer did not greet us properly")

        wire.send("get_genesis")
        kind, payload = wire.receive(HANDSHAKE_TIMEOUT)
        if kind != "genesis":
            raise NodeError(f"expected the genesis configuration, got {kind!r}")

        genesis = GenesisConfig.from_dict(payload["genesis"])
        commitment = Chain.genesis_commitment(genesis)
        if expected_genesis and commitment != expected_genesis:
            raise NodeError(
                f"this peer's genesis is {commitment[:16]}..., "
                f"you expected {expected_genesis[:16]}..."
            )
        if not expected_genesis:
            emit(f"trusting this peer's genesis on first use: {commitment}")

        consensus = build_engine(payload.get("consensus", {}))
        chain = Chain(genesis, consensus, path=path)

        while True:
            wire.send("get_blocks", {"from_height": len(chain), "limit": SYNC_BATCH})
            kind, payload = wire.receive(HANDSHAKE_TIMEOUT)
            if kind != "blocks":
                break
            batch = payload.get("blocks") or []
            if not batch:
                break
            for raw in batch:
                block = Block.from_dict(raw)
                if block.height == 0:
                    if block.header.prev_hash != commitment:
                        raise NodeError("the genesis block does not match the configuration")
                    chain._ensure_writable()
                    chain.consensus.validate(block, None)
                    if block.header.state_root != chain.state.state_root():
                        raise NodeError("the genesis state root does not match")
                    chain.blocks.append(block)
                    chain.state.height = 0
                    chain._persist(block)
                else:
                    chain.append(block)
            emit(f"synced to height {chain.height}")
            if len(batch) < SYNC_BATCH:
                break
    finally:
        wire.close()

    chain.verify()
    return chain


def submit_to_node(host: str, port: int, tx: Transaction, *, chain: Chain | None = None,
                   node_key: SigningKey | None = None,
                   timeout: float = HANDSHAKE_TIMEOUT) -> None:
    """Hand a signed transaction to a running node.

    Once a node is up it owns the ledger file, so a command that changes
    state cannot simply append to it -- and should not want to.  In a network
    the correct move is to give the transaction to a node and let it reach
    consensus like any other.  The signing key never leaves the caller; only
    the signed transaction crosses the wire.
    """
    tx.validate()
    key = node_key or SigningKey.generate()
    sock = socket.create_connection((host, port), timeout=timeout)
    wire = Wire(sock)
    try:
        wire.send("hello", _hello_payload(key, chain, 0, "submitting"))
        kind, payload = wire.receive(timeout)
        if kind == "error":
            raise NodeError(payload.get("message", "the node refused us"))
        if kind != "hello" or not _verify_hello(payload):
            raise NodeError("the node did not greet us properly")
        wire.send("tx", {"tx": tx.to_dict()})
        # Wait for the node to stop talking before hanging up, so the frame is
        # not dropped by a close that races the node reading it.
        try:
            wire.send("bye")
            wire.receive(0.5)
        except (socket.timeout, ConnectionError, OSError, NodeError):
            pass
    finally:
        wire.close()
