"""A read-only HTTP view of a ledger, plus the dashboard that renders it.

Deliberately small: the standard library's ``http.server``, no framework, no
dependencies, and **no route that writes anything**.  Granting credits and
running agents stay on the command line, where they are signed by keys the
server never reads.

What it serves:

===================  =======================================================
``/``                the dashboard page
``/api/overview``    chain header, accounts, prices, quotas
``/api/usage``       per-resource totals, per-block spend, recent records
``/api/blocks``      recent blocks and the transactions inside them
``/api/events``      attestations: goals the agent set, actions it refused
``/api/verify``      a full replay from genesis, on demand
===================  =======================================================

It binds to the loopback interface unless told otherwise, because a ledger
exposes balances, addresses and an agent's whole activity history.  Private
keys live beside the ledger in the workspace; this module never opens that
directory, and it should stay that way.
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .chain import Chain, ChainError
from .chat import ChatBusy, ChatRejected, ChatService
from .resources import ResourceKind
from .state import StateError
from .transactions import MEASUREMENT_SOURCES, TxType

__all__ = ["LedgerView", "serve", "build_application"]

DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")
FONTS_DIR = Path(__file__).with_name("fonts")
DEFAULT_PORT = 8787

#: Only these files are reachable under /fonts/.  A whitelist rather than a
#: path join, so no amount of ``../`` in a request can walk out of the
#: directory -- the request never becomes a path at all, it becomes a lookup.
FONT_FILES = {
    "Vazirmatn-Regular.woff2": "font/woff2",
    "Vazirmatn-SemiBold.woff2": "font/woff2",
    "Vazirmatn-OFL.txt": "text/plain; charset=utf-8",
}


class LedgerView:
    """A thread-safe, self-refreshing read of a ledger file.

    The command line writes to the same file while the dashboard is open, so
    the view reloads whenever the file's size or modification time changes.
    Reloading means a full replay, which is also a continuous integrity
    check: a ledger that stops verifying stops being served.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._chain: Chain | None = None
        self._stamp: tuple[float, int] | None = None
        self._error: str | None = None

    def _current_stamp(self) -> tuple[float, int]:
        stat = self.path.stat()
        return (stat.st_mtime, stat.st_size)

    def chain(self) -> Chain:
        with self._lock:
            if not self.path.exists():
                raise ChainError(f"no ledger at {self.path}")
            stamp = self._current_stamp()
            if self._chain is None or stamp != self._stamp:
                self._chain = Chain.load(self.path)
                self._stamp = stamp
            return self._chain

    def invalidate(self) -> None:
        with self._lock:
            self._chain = None
            self._stamp = None


# --------------------------------------------------------------------------
# Projections -- the shapes the dashboard consumes
# --------------------------------------------------------------------------

def _usage_rows(chain: Chain) -> list[dict[str, Any]]:
    prices = chain.state.prices
    rows = []
    for block, tx in chain.transactions():
        if tx.type is not TxType.USAGE:
            continue
        resource = tx.body["resource"]
        amount = tx.body["amount"]
        rows.append({
            "height": block.height,
            "timestamp": block.header.timestamp,
            "txid": tx.txid,
            "agent": tx.sender,
            "resource": resource,
            "amount": amount,
            "cost": prices.cost(resource, amount),
            "tool": tx.body.get("tool", ""),
            "measured": tx.body.get("measured", "declared"),
            "evidence": tx.body["evidence"],
        })
    return rows


def build_overview(chain: Chain) -> dict[str, Any]:
    state = chain.state
    labels = {a.address: (a.label or a.address[:10]) for a in state.accounts.values()}
    return {
        "chain_id": state.chain_id,
        "height": chain.height,
        "blocks": len(chain),
        "consensus": chain.consensus.to_dict(),
        "epoch": state.epoch_at(chain.height),
        "epoch_length": state.epoch_length,
        "initial_supply": state.initial_supply,
        "burned_total": state.burned_total,
        "circulating": sum(a.balance for a in state.accounts.values()),
        "prices": state.prices.to_dict(),
        "limits": state.limits,
        "genesis_hash": chain.block_at(0).hash,
        "tip_hash": chain.tip.hash,
        "tip_timestamp": chain.tip.header.timestamp,
        "labels": labels,
        "resources": {kind.value: kind.unit for kind in ResourceKind},
        "accounts": [
            {
                **account.to_dict(),
                "quotas": {
                    resource: state.quota_remaining(account.address, resource, chain.height)
                    for resource in sorted(state.limits)
                },
            }
            for account in sorted(state.accounts.values(), key=lambda a: (a.role, a.label))
        ],
    }


def build_usage(chain: Chain) -> dict[str, Any]:
    rows = _usage_rows(chain)

    by_resource: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, int] = {}
    # Every source is seeded, so a consumer can total the dict without having
    # to know which ones happen to appear in this particular chain.
    by_measurement = {source: 0 for source in sorted(MEASUREMENT_SOURCES)}
    per_block: dict[int, int] = {}

    for row in rows:
        bucket = by_resource.setdefault(
            row["resource"],
            {"amount": 0, "cost": 0, "by_source": {s: 0 for s in sorted(MEASUREMENT_SOURCES)}},
        )
        bucket["amount"] += row["amount"]
        bucket["cost"] += row["cost"]
        bucket["by_source"][row["measured"]] = (
            bucket["by_source"].get(row["measured"], 0) + row["cost"]
        )
        by_agent[row["agent"]] = by_agent.get(row["agent"], 0) + row["cost"]
        by_measurement[row["measured"]] = by_measurement.get(row["measured"], 0) + row["cost"]
        per_block[row["height"]] = per_block.get(row["height"], 0) + row["cost"]

    cumulative = 0
    series = []
    for height in range(chain.height + 1):
        spent = per_block.get(height, 0)
        cumulative += spent
        series.append({"height": height, "spent": spent, "cumulative": cumulative})

    return {
        "rows": rows[-200:],
        "total_rows": len(rows),
        "by_resource": by_resource,
        "by_agent": by_agent,
        "by_measurement": by_measurement,
        "measurement_sources": sorted(MEASUREMENT_SOURCES),
        "series": series,
        "total_cost": sum(row["cost"] for row in rows),
    }


def build_blocks(chain: Chain, limit: int = 40) -> dict[str, Any]:
    blocks = []
    for block in chain.blocks[-limit:][::-1]:
        blocks.append({
            "height": block.height,
            "hash": block.hash,
            "prev_hash": block.header.prev_hash,
            "timestamp": block.header.timestamp,
            "proposer": block.header.proposer,
            "merkle_root": block.header.merkle_root,
            "state_root": block.header.state_root,
            "transactions": [
                {
                    "txid": tx.txid,
                    "type": tx.type.value,
                    "sender": tx.sender,
                    "summary": tx.summary(),
                    "body": dict(tx.body),
                }
                for tx in block.transactions
            ],
        })
    return {"blocks": blocks, "height": chain.height}


def build_events(chain: Chain, limit: int = 60) -> dict[str, Any]:
    events = []
    for block, tx in chain.transactions():
        if tx.type is not TxType.ATTEST:
            continue
        events.append({
            "height": block.height,
            "timestamp": block.header.timestamp,
            "txid": tx.txid,
            "agent": tx.sender,
            "topic": tx.body["topic"],
            "summary": tx.body.get("summary", ""),
            "digest": tx.body["digest"],
        })
    refusals = sum(1 for event in events if event["topic"] == "budget-refusal")
    return {"events": events[-limit:][::-1], "refusals": refusals, "total": len(events)}


def build_verification(chain: Chain) -> dict[str, Any]:
    try:
        state = chain.verify()
        state.check_invariants()
    except (ChainError, StateError) as exc:
        return {"valid": False, "error": str(exc)}
    return {
        "valid": True,
        "blocks": len(chain),
        "height": chain.height,
        "state_root": state.state_root(),
        "matches_live_state": state.state_root() == chain.state.state_root(),
        "burned_total": state.burned_total,
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

Route = Callable[[Chain, dict[str, list[str]]], dict[str, Any]]

ROUTES: dict[str, Route] = {
    "/api/overview": lambda chain, query: build_overview(chain),
    "/api/usage": lambda chain, query: build_usage(chain),
    "/api/blocks": lambda chain, query: build_blocks(chain, _int(query, "limit", 40)),
    "/api/events": lambda chain, query: build_events(chain, _int(query, "limit", 60)),
    "/api/verify": lambda chain, query: build_verification(chain),
}


def _int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return max(1, min(1000, int(query.get(name, [default])[0])))
    except (TypeError, ValueError):
        return default


#: A request to the one write route must carry this header.  A cross-origin
#: page cannot set a custom header without a CORS preflight, which this server
#: never answers -- so a random site in another tab cannot spend the agent's
#: budget just because the dashboard is listening on localhost.
CHAT_HEADER = "X-ChainMind-Chat"

#: Cap on the request body, so a single POST cannot be used to push an
#: enormous prompt through the model.
MAX_BODY_BYTES = 64 * 1024


def build_application(view: LedgerView, *, quiet: bool = True,
                      chat: ChatService | None = None,
                      allowed_hosts: frozenset[str] | None = None,
                      ) -> type[BaseHTTPRequestHandler]:
    """Create a handler class bound to one ledger view.

    ``chat`` is ``None`` by default, which leaves the server strictly
    read-only.  Passing one mounts a single write route and nothing else.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "ChainMind"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover
            if not quiet:
                super().log_message(fmt, *args)

        # -- responses -----------------------------------------------------

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Cache-Control",
                "public, max-age=604800, immutable" if content_type.startswith("font/")
                else "no-store",
            )
            # The dashboard is self-contained; nothing should be embedding it
            # or loading anything into it from elsewhere.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._json({"error": message}, status)

        # -- routing -------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/":
                try:
                    body = DASHBOARD_PATH.read_bytes()
                except OSError:
                    self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "the dashboard file is missing")
                    return
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return

            if path.startswith("/fonts/"):
                name = path[len("/fonts/"):]
                content_type = FONT_FILES.get(name)
                if content_type is None:
                    self._error(HTTPStatus.NOT_FOUND, "unknown font")
                    return
                try:
                    body = (FONTS_DIR / name).read_bytes()
                except OSError:
                    self._error(HTTPStatus.NOT_FOUND, "font file is missing")
                    return
                self._send(HTTPStatus.OK, body, content_type)
                return

            if path == "/api/chat":
                if chat is None:
                    self._error(HTTPStatus.NOT_FOUND, "chat is not enabled on this server")
                    return
                query = parse_qs(parsed.query)
                wanted = query.get("conversation", [None])[0]
                if wanted:
                    conversation = chat.get(wanted)
                    if conversation is None:
                        self._error(HTTPStatus.NOT_FOUND, "no such conversation")
                        return
                    self._json(conversation.to_dict())
                else:
                    self._json({"conversations": chat.listing(), **chat.status()})
                return

            route = ROUTES.get(path)
            if route is None:
                self._error(HTTPStatus.NOT_FOUND, f"no route for {path}")
                return

            try:
                chain = view.chain()
            except (ChainError, OSError) as exc:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
                return

            try:
                self._json(route(chain, parse_qs(parsed.query)))
            except (ChainError, StateError, ValueError) as exc:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        do_HEAD = do_GET  # noqa: N815

        # -- the one write route -------------------------------------------

        def _origin_is_acceptable(self) -> bool:
            """Refuse a request that another site sent on the user's behalf.

            Binding to loopback is not by itself protection: any page the
            user opens can POST to 127.0.0.1, and a DNS-rebinding trick can
            make a remote name resolve here.  So the Host header must name an
            address we actually serve, and an Origin, if present, must match.
            """
            host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
            if allowed_hosts is not None and host not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            if origin:
                origin_host = urlparse(origin).hostname or ""
                if origin_host != (urlparse(f"//{self.headers.get('Host', '')}").hostname or ""):
                    return False
            return True

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if chat is None or path != "/api/chat":
                # Everything else that changes the ledger needs a signing key,
                # and those stay on the command line.
                self._error(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "this server is read-only" if chat is None
                    else "the only write route is /api/chat",
                )
                return

            if self.headers.get(CHAT_HEADER) is None:
                self._error(HTTPStatus.FORBIDDEN, f"missing {CHAT_HEADER} header")
                return
            if not self._origin_is_acceptable():
                self._error(HTTPStatus.FORBIDDEN, "this request did not come from the dashboard")
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "malformed Content-Length")
                return
            if length > MAX_BODY_BYTES:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the request body is too large")
                return

            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error(HTTPStatus.BAD_REQUEST, "the request body is not JSON")
                return
            if not isinstance(body, dict):
                self._error(HTTPStatus.BAD_REQUEST, "the request body must be an object")
                return

            try:
                result = chat.send(
                    prompt=str(body.get("prompt", "")),
                    conversation_id=body.get("conversation_id") or None,
                    max_tokens=body.get("max_tokens"),
                )
            except ChatRejected as exc:
                # Not an error in the server: the chain did its job.
                self._json(
                    {"refused": True, "reason": exc.reason,
                     "estimated_cost": exc.estimated_cost,
                     "balance": chat.kernel.balance},
                    HTTPStatus.PAYMENT_REQUIRED,
                )
                return
            except ChatBusy as exc:
                self._error(HTTPStatus.CONFLICT, str(exc))
                return
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:
                self._error(HTTPStatus.BAD_GATEWAY, f"{type(exc).__name__}: {exc}")
                return

            view.invalidate()      # the ledger just grew; re-read it next time
            self._json(result)

        do_PUT = do_DELETE = do_PATCH = do_POST  # noqa: N815

    return Handler


def serve(ledger_path: Path | str, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
          quiet: bool = True, chat: ChatService | None = None) -> ThreadingHTTPServer:
    """Start a dashboard server.  Returns it without blocking.

    Without ``chat`` the server has no write route at all.
    """
    view = LedgerView(ledger_path)
    allowed = frozenset({host, "localhost", "127.0.0.1", "::1"})
    server = ThreadingHTTPServer(
        (host, port),
        build_application(view, quiet=quiet, chat=chat, allowed_hosts=allowed),
    )
    server.daemon_threads = True
    return server
