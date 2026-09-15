"""``chainmind`` -- a command line over the ledger and the agent.

The commands mirror the lifecycle: create a network, fund an agent, let it
work, then audit what it did.  Every command operates on a workspace
directory (``.chainmind`` by default) holding the ledger file and the keys.
"""

from __future__ import annotations

import argparse
import errno
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from .agent import AgentKernel, LocalLedger
from .chain import Chain, ChainError, LedgerBusy
from .consensus import ProofOfAuthority, ProofOfWork
from .crypto import SigningKey
from .executors import InProcessExecutor, SandboxExecutor
from .models import register_model_tool
from .sandbox import SUPPORTED as SANDBOX_SUPPORTED
from .resources import CREDIT, DEFAULT_PRICES, ResourceKind, format_credits
from .state import GenesisConfig, StateError
from .transactions import TxType, build_grant, build_policy_update

DEFAULT_WORKSPACE = Path(".chainmind")


# --------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------

class Workspace:
    """The on-disk home of one network: a ledger file and a key directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.keys_dir = root / "keys"
        self.ledger_path = root / "ledger.jsonl"

    def exists(self) -> bool:
        return self.ledger_path.exists()

    def ensure(self) -> None:
        self.keys_dir.mkdir(parents=True, exist_ok=True)

    def key_path(self, name: str) -> Path:
        if not name or "/" in name or name.startswith("."):
            raise SystemExit(f"invalid key name {name!r}")
        return self.keys_dir / f"{name}.key"

    def create_key(self, name: str, *, overwrite: bool = False) -> SigningKey:
        self.ensure()
        path = self.key_path(name)
        if path.exists() and not overwrite:
            raise SystemExit(f"key {name!r} already exists at {path}")
        key = SigningKey.generate()
        path.write_text(key.to_hex() + "\n", encoding="utf-8")
        # A private key readable by everyone on the box is not a private key.
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return key

    def load_key(self, name: str) -> SigningKey:
        path = self.key_path(name)
        if not path.exists():
            raise SystemExit(f"no key named {name!r} in {self.keys_dir}")
        return SigningKey.from_hex(path.read_text(encoding="utf-8").strip())

    def key_names(self) -> list[str]:
        if not self.keys_dir.exists():
            return []
        return sorted(p.stem for p in self.keys_dir.glob("*.key"))

    def load_chain(self) -> Chain:
        if not self.exists():
            raise SystemExit(f"no ledger at {self.ledger_path}; run 'chainmind init' first")
        try:
            return Chain.load(self.ledger_path)
        except (ChainError, ValueError) as exc:
            raise SystemExit(f"the ledger failed to load: {exc}") from exc

    def resolve_address(self, value: str) -> str:
        """Accept either a key name in this workspace or a raw hex address."""
        if len(value) == 64:
            try:
                bytes.fromhex(value)
                return value.lower()
            except ValueError:
                pass
        return self.load_key(value).public_hex()


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def _emit(data: Any, as_json: bool, render: Iterable[str] | None = None) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    elif render is not None:
        for line in render:
            print(line)


def _parse_pairs(values: Sequence[str] | None, label: str) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for item in values or []:
        if "=" not in item:
            raise SystemExit(f"{label} must look like resource=amount, got {item!r}")
        resource, _, amount = item.partition("=")
        try:
            parsed[ResourceKind.parse(resource.strip()).value] = int(amount)
        except ValueError as exc:
            raise SystemExit(f"bad {label} {item!r}: {exc}") from exc
    return parsed


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    workspace = Workspace(args.workspace)
    if workspace.exists() and not args.force:
        raise SystemExit(f"{workspace.ledger_path} already exists; pass --force to replace it")
    workspace.ensure()

    authority = workspace.create_key("authority", overwrite=args.force)
    agent = workspace.create_key(args.agent_name, overwrite=args.force)

    genesis = GenesisConfig(
        authorities={authority.public_hex(): args.supply * CREDIT},
        prices=DEFAULT_PRICES.replace(_parse_pairs(args.price, "--price")),
        limits=_parse_pairs(args.limit, "--limit"),
        epoch_length=args.epoch_length,
        chain_id=args.chain_id,
        note=args.note,
    )
    consensus = (
        ProofOfWork(args.difficulty) if args.consensus == "pow"
        else ProofOfAuthority([authority.public_hex()])
    )
    chain = Chain.create(genesis, authority, consensus, path=workspace.ledger_path)

    _emit(
        {
            "chain_id": genesis.chain_id,
            "consensus": consensus.to_dict(),
            "ledger": str(workspace.ledger_path),
            "authority": authority.public_hex(),
            args.agent_name: agent.public_hex(),
            "genesis_hash": chain.tip.hash,
        },
        args.json,
        render=[
            f"network      {genesis.chain_id}",
            f"consensus    {consensus.name}",
            f"ledger       {workspace.ledger_path}",
            f"authority    {authority.public_hex()}",
            f"agent        {agent.public_hex()}  ({args.agent_name})",
            f"treasury     {format_credits(args.supply * CREDIT)}",
            f"genesis      {chain.tip.hash}",
            "",
            f"next: chainmind grant {args.agent_name} 1 --workspace {workspace.root}",
        ],
    )
    return 0


def cmd_keygen(args: argparse.Namespace) -> int:
    workspace = Workspace(args.workspace)
    key = workspace.create_key(args.name, overwrite=args.force)
    _emit({"name": args.name, "address": key.public_hex()}, args.json,
          render=[f"{args.name}  {key.public_hex()}"])
    return 0


def cmd_grant(args: argparse.Namespace) -> int:
    workspace = Workspace(args.workspace)
    chain = workspace.load_chain()
    authority = workspace.load_key(args.from_key)
    beneficiary = workspace.resolve_address(args.beneficiary)

    amount = int(round(args.amount * CREDIT))
    if amount <= 0:
        raise SystemExit("the amount must be greater than zero")
    account = chain.state.get(authority.public_hex())
    if account is None:
        raise SystemExit("the granting key is not an account on this chain")

    tx = build_grant(authority, account.nonce, beneficiary=beneficiary,
                     amount=amount, memo=args.memo)
    result = chain.seal_block(authority, [tx])
    if result.rejected:
        raise SystemExit(f"grant rejected: {result.rejected[0][1]}")

    _emit(
        {"txid": tx.txid, "beneficiary": beneficiary, "amount": amount,
         "balance": chain.state.balance_of(beneficiary)},
        args.json,
        render=[
            f"granted   {format_credits(amount)} -> {beneficiary[:16]}...",
            f"balance   {format_credits(chain.state.balance_of(beneficiary))}",
            f"tx        {tx.txid}",
        ],
    )
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    workspace = Workspace(args.workspace)
    chain = workspace.load_chain()
    authority = workspace.load_key(args.from_key)
    account = chain.state.get(authority.public_hex())
    if account is None:
        raise SystemExit("the signing key is not an account on this chain")

    prices = _parse_pairs(args.price, "--price")
    limits = _parse_pairs(args.limit, "--limit")
    if not prices and not limits:
        raise SystemExit("nothing to change: pass --price and/or --limit")

    tx = build_policy_update(authority, account.nonce, prices=prices, limits=limits, memo=args.memo)
    result = chain.seal_block(authority, [tx])
    if result.rejected:
        raise SystemExit(f"policy update rejected: {result.rejected[0][1]}")

    _emit({"txid": tx.txid, "prices": chain.state.prices.to_dict(), "limits": chain.state.limits},
          args.json,
          render=[f"policy updated in {tx.txid[:16]}",
                  f"prices  {chain.state.prices.to_dict()}",
                  f"limits  {chain.state.limits or 'none'}"])
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    workspace = Workspace(args.workspace)
    chain = workspace.load_chain()
    authority = workspace.load_key(args.sealer)
    agent_key = workspace.load_key(args.agent)

    if args.sandbox and not SANDBOX_SUPPORTED:
        raise SystemExit("--sandbox needs os.fork and os.wait4, which this platform lacks")
    executor = (
        SandboxExecutor(max_compute_ms=args.max_compute_ms) if args.sandbox
        else InProcessExecutor()
    )

    ledger = LocalLedger(chain, authority)
    kernel = AgentKernel(agent_key, ledger, label=args.agent, executor=executor,
                         memory_path=workspace.root / "memory" / f"{args.agent}.json")

    before = kernel.balance
    outcomes = kernel.run(args.goal, max_steps=args.max_steps)
    ledger.flush()
    spent = before - kernel.balance

    payload = {
        "goal": args.goal,
        "agent": kernel.address,
        "executor": executor.name,
        "spent": spent,
        "balance": kernel.balance,
        "halted": kernel.halted,
        "halt_reason": kernel.halt_reason(),
        "height": chain.height,
        "actions": [
            {"tool": o.tool, "authorised": o.authorised, "ok": o.ok, "charged": o.charged,
             "reason": o.reason, "txids": o.txids}
            for o in outcomes
        ],
    }
    lines = [f"goal      {args.goal}", f"agent     {kernel.address[:16]}...",
             f"metering  {executor.name}", ""]
    lines += [f"  {o.summary()}" for o in outcomes] or ["  (the planner proposed nothing affordable)"]
    lines += ["", f"spent     {format_credits(spent)}", f"balance   {format_credits(kernel.balance)}",
              f"height    {chain.height}"]
    if kernel.halted:
        lines += ["", f"HALTED    {kernel.halt_reason()}"]
    _emit(payload, args.json, render=lines)
    return 1 if kernel.halted else 0


def cmd_status(args: argparse.Namespace) -> int:
    workspace = Workspace(args.workspace)
    chain = workspace.load_chain()
    state = chain.state
    accounts = [state.accounts[a] for a in sorted(state.accounts)]

    payload = {
        "chain_id": state.chain_id,
        "height": chain.height,
        "consensus": chain.consensus.to_dict(),
        "initial_supply": state.initial_supply,
        "burned_total": state.burned_total,
        "epoch": state.epoch_at(chain.height),
        "prices": state.prices.to_dict(),
        "limits": state.limits,
        "accounts": [a.to_dict() for a in accounts],
    }
    lines = [
        f"chain     {state.chain_id}  height {chain.height}  epoch {state.epoch_at(chain.height)}",
        f"consensus {chain.consensus.name}",
        f"supply    {format_credits(state.initial_supply)} issued, "
        f"{format_credits(state.burned_total)} burned",
        "",
        f"{'address':<18} {'role':<10} {'label':<14} {'balance':>16} {'burned':>16}",
    ]
    for account in accounts:
        lines.append(
            f"{account.address[:16]:<18} {account.role:<10} {account.label[:14]:<14} "
            f"{format_credits(account.balance):>16} {format_credits(account.burned):>16}"
        )
    _emit(payload, args.json, render=lines)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    workspace = Workspace(args.workspace)
    chain = workspace.load_chain()
    address = workspace.resolve_address(args.address) if args.address else None

    totals: dict[str, int] = {}
    spend: dict[str, int] = {}
    by_source: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for block, tx in chain.transactions():
        if address and tx.sender != address:
            continue
        if tx.type is not TxType.USAGE:
            if args.all_types:
                rows.append({"height": block.height, "type": tx.type.value, "txid": tx.txid,
                             "sender": tx.sender, "detail": tx.summary()})
            continue
        resource = tx.body["resource"]
        amount = tx.body["amount"]
        cost = chain.state.prices.cost(resource, amount)
        totals[resource] = totals.get(resource, 0) + amount
        spend[resource] = spend.get(resource, 0) + cost
        measured = tx.body.get("measured", "declared")
        by_source[measured] = by_source.get(measured, 0) + cost
        rows.append({
            "height": block.height, "type": "usage", "txid": tx.txid, "sender": tx.sender,
            "resource": resource, "amount": amount, "cost": cost,
            "tool": tx.body.get("tool", ""), "measured": measured,
            "evidence": tx.body["evidence"],
        })

    lines = [
        f"{'height':>6} {'resource':<20} {'amount':>12} {'cost':>16} "
        f"{'measured':<9} tool"
    ]
    for row in rows[-args.limit:]:
        if row["type"] != "usage":
            lines.append(f"{row['height']:>6} {row['detail']}")
            continue
        lines.append(
            f"{row['height']:>6} {row['resource']:<20} {row['amount']:>12} "
            f"{format_credits(row['cost']):>16} {row['measured']:<9} {row['tool']}"
        )
    lines += ["", "totals by resource:"]
    for resource in sorted(totals):
        lines.append(
            f"  {resource:<20} {totals[resource]:>12} units  {format_credits(spend[resource]):>16}"
        )
    lines.append(f"  {'TOTAL':<20} {'':>12}        {format_credits(sum(spend.values())):>16}")
    if by_source:
        total = sum(by_source.values())
        verified = by_source.get("kernel", 0) + by_source.get("provider", 0)
        lines += ["", "how those figures were obtained:"]
        for source in sorted(by_source):
            lines.append(f"  {source:<20} {format_credits(by_source[source]):>16}")
        share = round(verified * 100 / total) if total else 0
        lines.append(f"  {'independently verified':<20} {share:>15}%")
    if not rows:
        lines = ["no usage recorded yet"]

    _emit({"address": address, "rows": rows, "totals": totals, "spend": spend,
           "by_source": by_source, "total_spend": sum(spend.values())},
          args.json, render=lines)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    workspace = Workspace(args.workspace)
    chain = workspace.load_chain()
    try:
        state = chain.verify()
        state.check_invariants()
    except (ChainError, StateError) as exc:
        _emit({"valid": False, "error": str(exc)}, args.json, render=[f"INVALID: {exc}"])
        return 1
    payload = {"valid": True, "height": chain.height, "blocks": len(chain),
               "state_root": state.state_root(), "burned_total": state.burned_total}
    _emit(payload, args.json, render=[
        f"valid       yes",
        f"blocks      {len(chain)} (height {chain.height})",
        f"state root  {state.state_root()}",
        f"burned      {format_credits(state.burned_total)}",
    ])
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Prompt in, answer out -- with every token paid for on chain."""
    from .models import ModelUnavailable, build_model, describe_model
    from .tools import ToolRegistry

    workspace = Workspace(args.workspace)
    chain = workspace.load_chain()
    sealer = workspace.load_key(args.sealer)
    agent_key = workspace.load_key(args.agent)

    prompt = args.prompt
    if prompt == "-":
        prompt = sys.stdin.read()
    prompt = prompt.strip()
    if not prompt:
        raise SystemExit("the prompt is empty")

    try:
        model = build_model(args.backend, model=args.model, effort=args.effort)
    except (ModelUnavailable, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    registry = ToolRegistry()
    register_model_tool(registry, model)

    executor = (
        SandboxExecutor(max_compute_ms=args.max_compute_ms) if args.sandbox
        else InProcessExecutor()
    )
    ledger = LocalLedger(chain, sealer)
    kernel = AgentKernel(agent_key, ledger, tools=registry, label=args.agent,
                         executor=executor,
                         memory_path=workspace.root / "memory" / f"{args.agent}.json")
    kernel.register()

    before = kernel.balance
    outcome = kernel.perform("ask", prompt=prompt, max_tokens=args.max_tokens)
    ledger.flush()
    spent = before - kernel.balance

    answer = ""
    refused_by_model = False
    if isinstance(outcome.value, dict):
        answer = outcome.value.get("text", "")
        refused_by_model = bool(outcome.value.get("refused"))

    payload = {
        "prompt": prompt,
        "answer": answer,
        "authorised": outcome.authorised,
        "ok": outcome.ok,
        "reason": outcome.reason or outcome.error,
        "model": describe_model(model),
        "spent": spent,
        "balance": kernel.balance,
        "estimated_cost": outcome.estimated_cost,
        "usage": dict(outcome.usage.items()) if outcome.usage else {},
        "sources": dict(outcome.usage.sources) if outcome.usage else {},
        "txids": outcome.txids,
        "height": chain.height,
    }

    if args.json:
        _emit(payload, True)
    elif outcome.refused:
        # The chain said no. Nothing was sent to the model.
        print(f"REFUSED   {outcome.reason}", file=sys.stderr)
        print(f"estimate  {format_credits(outcome.estimated_cost)}", file=sys.stderr)
        print(f"balance   {format_credits(kernel.balance)}", file=sys.stderr)
    elif not outcome.ok:
        print(f"FAILED    {outcome.error}", file=sys.stderr)
    else:
        print(answer)
        if not args.quiet:
            used = ", ".join(
                f"{amount} {resource} ({outcome.usage.source_of(resource)})"
                for resource, amount in outcome.usage.items()
            )
            print(f"\n---\n{used}", file=sys.stderr)
            print(
                f"cost {format_credits(spent)} · balance {format_credits(kernel.balance)} "
                f"· block #{chain.height}",
                file=sys.stderr,
            )

    if outcome.refused:
        return 2
    if not outcome.ok or refused_by_model:
        return 1
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    workspace = Workspace(args.workspace)
    if not workspace.exists():
        raise SystemExit(f"no ledger at {workspace.ledger_path}; run 'chainmind init' first")
    chain = workspace.load_chain()   # fail loudly here rather than on the first request

    chat = _build_chat_service(args, workspace, chain) if args.chat else None

    try:
        server = serve(workspace.ledger_path, host=args.host, port=args.port,
                       quiet=not args.verbose, chat=chat)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"port {args.port} is already in use; pass --port to pick another one"
            ) from exc
        raise SystemExit(f"could not bind {args.host}:{args.port}: {exc}") from exc
    where = f"http://{args.host}:{args.port}/"
    print(f"dashboard  {where}")
    print(f"ledger     {workspace.ledger_path}")
    if chat is None:
        print("read-only  no route on this server can change the ledger or read your keys")
    else:
        print(f"chat       on, as '{args.agent}' ({chat.kernel.address[:16]}...)")
        print(f"model      {chat.model_name}")
        print(f"budget     {format_credits(chat.kernel.balance)} — the wallet is the rate limit")
        print("writes     /api/chat only; nothing else on this server can change the ledger")
        print(f"key        this process now holds the '{args.agent}' signing key in memory")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning    bound to {args.host}: balances and addresses are reachable from the network")
        if chat is not None:
            print("warning    with --chat that also exposes an endpoint that spends the agent's budget")
    print("\nCtrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
    return 0


def _build_chat_service(args: argparse.Namespace, workspace: "Workspace", chain):
    """Assemble the chat service, or explain clearly why it cannot be built."""
    from .chat import ChatService
    from .models import ModelUnavailable, build_model, describe_model
    from .tools import ToolRegistry

    try:
        model = build_model(args.backend, model=args.model, effort=args.effort)
    except (ModelUnavailable, ValueError) as exc:
        raise SystemExit(f"--chat needs a model:\n{exc}") from exc

    registry = ToolRegistry()
    register_model_tool(registry, model)

    sealer = workspace.load_key(args.sealer)
    agent_key = workspace.load_key(args.agent)
    ledger = LocalLedger(chain, sealer)
    kernel = AgentKernel(agent_key, ledger, tools=registry, label=args.agent,
                         memory_path=workspace.root / "memory" / f"{args.agent}.json")
    kernel.register()
    ledger.flush()

    if kernel.balance <= 0:
        raise SystemExit(
            f"'{args.agent}' has no credits, so every turn would be refused; "
            f"run: chainmind grant {args.agent} 1"
        )

    return ChatService(kernel, ledger, model_name=describe_model(model),
                       max_tokens=args.chat_max_tokens, history_turns=args.history_turns)


def cmd_backends(args: argparse.Namespace) -> int:
    """Report where this machine could run the agent's thinking."""
    from .local import LocalRuntimeUnavailable, discover_runtime
    from .models import _claude_credentials_available

    found: dict[str, Any] = {}
    try:
        base, dialect, models = discover_runtime()
        found["local"] = {"available": True, "url": base, "dialect": dialect, "models": models}
    except LocalRuntimeUnavailable as exc:
        found["local"] = {"available": False, "reason": str(exc).splitlines()[0]}

    has_key = _claude_credentials_available()
    # find_spec asks whether the SDK could be imported without importing it,
    # which keeps a broken install from taking this command down with it.
    sdk = importlib.util.find_spec("anthropic") is not None
    found["claude"] = {"available": bool(has_key and sdk), "sdk_installed": sdk,
                       "credentials": has_key}

    lines = []
    local = found["local"]
    if local["available"]:
        lines.append(f"local     yes — {local['dialect']} at {local['url']}")
        lines.append(f"          models: {', '.join(local['models']) or '(none pulled yet)'}")
    else:
        lines.append(f"local     no — {local['reason']}")
    claude = found["claude"]
    lines.append(
        "claude    " + ("yes" if claude["available"] else "no") +
        f" — sdk {'installed' if sdk else 'missing'}, "
        f"credentials {'found' if has_key else 'not found'}"
    )
    lines += ["", "local needs no key, no account and no network."]
    _emit(found, args.json, render=lines)
    return 0 if (found["local"]["available"] or found["claude"]["available"]) else 1


def cmd_keys(args: argparse.Namespace) -> int:
    workspace = Workspace(args.workspace)
    names = workspace.key_names()
    entries = {name: workspace.load_key(name).public_hex() for name in names}
    _emit(entries, args.json, render=[f"{name:<14} {address}" for name, address in entries.items()]
          or ["no keys in this workspace"])
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chainmind",
        description="An autonomous agent whose resource use is governed by a blockchain ledger.",
    )
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE,
                        help="directory holding the ledger and keys (default: .chainmind)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")

    # The same flags again on every subcommand, because `chainmind backends
    # --json` is what people type and argparse would otherwise reject it.
    # SUPPRESS is the point: without it the subparser's own default would
    # overwrite a value given before the subcommand.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit machine-readable output")
    shared.add_argument("--workspace", type=Path, default=argparse.SUPPRESS,
                        help="directory holding the ledger and keys")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[shared], help="create a network, its authority key and a first agent")
    p.add_argument("--chain-id", default="chainmind-dev")
    p.add_argument("--supply", type=int, default=1000, help="initial treasury in credits")
    p.add_argument("--agent-name", default="agent")
    p.add_argument("--epoch-length", type=int, default=100, help="blocks per quota window")
    p.add_argument("--limit", action="append", metavar="RESOURCE=UNITS",
                   help="per-epoch quota, repeatable")
    p.add_argument("--price", action="append", metavar="RESOURCE=MICROCREDITS",
                   help="override a price, repeatable")
    p.add_argument("--consensus", choices=["poa", "pow"], default="poa")
    p.add_argument("--difficulty", type=int, default=4, help="leading zeros, for --consensus pow")
    p.add_argument("--note", default="")
    p.add_argument("--force", action="store_true", help="replace an existing workspace")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("keygen", parents=[shared], help="create a new key in the workspace")
    p.add_argument("name")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_keygen)

    p = sub.add_parser("backends", parents=[shared], help="show where this machine can run the agent's thinking")
    p.set_defaults(func=cmd_backends)

    p = sub.add_parser("keys", parents=[shared], help="list the keys in the workspace")
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser("grant", parents=[shared], help="move credits from the treasury to an agent")
    p.add_argument("beneficiary", help="a key name in the workspace, or a hex address")
    p.add_argument("amount", type=float, help="credits to grant (fractions allowed)")
    p.add_argument("--from-key", default="authority")
    p.add_argument("--memo", default="")
    p.set_defaults(func=cmd_grant)

    p = sub.add_parser("policy", parents=[shared], help="change prices or quotas (authority only)")
    p.add_argument("--price", action="append", metavar="RESOURCE=MICROCREDITS")
    p.add_argument("--limit", action="append", metavar="RESOURCE=UNITS")
    p.add_argument("--from-key", default="authority")
    p.add_argument("--memo", default="")
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser("run", parents=[shared], help="give the agent a goal and let it spend within its means")
    p.add_argument("goal")
    p.add_argument("--agent", default="agent", help="key name of the acting agent")
    p.add_argument("--sealer", default="authority", help="key name that seals blocks")
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--sandbox", action="store_true",
                   help="run tools in a child process and bill the CPU time the kernel reports")
    p.add_argument("--max-compute-ms", type=int, default=30_000,
                   help="ceiling on the CPU limit handed to a sandboxed tool")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("ask", parents=[shared], help="send a prompt to a real model, paid for on chain")
    p.add_argument("prompt", help="the prompt; use - to read it from stdin")
    p.add_argument("--agent", default="agent", help="key name of the acting agent")
    p.add_argument("--sealer", default="authority", help="key name that seals blocks")
    p.add_argument("--backend", choices=["auto", "local", "claude"], default="auto",
                   help='where the agent thinks: "local" runs on this machine and needs no key, "claude" is a hosted API, "auto" tries local first')
    p.add_argument("--model", help="model name; defaults to what the runtime offers")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                   help="how hard the model should think (hosted backends only)")
    p.add_argument("--max-tokens", type=int, default=16_000,
                   help="ceiling on the answer, and what gets authorised up front")
    p.add_argument("--sandbox", action="store_true",
                   help="run the call in a child process and bill kernel-measured CPU too")
    p.add_argument("--max-compute-ms", type=int, default=30_000)
    p.add_argument("--quiet", action="store_true", help="print only the answer")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("status", parents=[shared], help="show accounts, prices and quotas")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("audit", parents=[shared], help="report metered usage recorded on chain")
    p.add_argument("--address", help="restrict to one agent (key name or hex address)")
    p.add_argument("--limit", type=int, default=25, help="rows to show")
    p.add_argument("--all-types", action="store_true", help="include non-usage transactions")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("verify", parents=[shared], help="replay the whole chain and check every invariant")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("serve", parents=[shared], help="open a dashboard over the ledger")
    p.add_argument("--host", default="127.0.0.1",
                   help="interface to bind (default: loopback only)")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--verbose", action="store_true", help="log every request")
    p.add_argument("--chat", action="store_true",
                   help="enable the chat panel; adds the only write route and makes "
                        "this process hold the agent's signing key")
    p.add_argument("--agent", default="agent", help="key name that chat speaks as")
    p.add_argument("--sealer", default="authority", help="key name that seals blocks")
    p.add_argument("--backend", choices=["auto", "local", "claude"], default="auto",
                   help='where the agent thinks: "local" runs on this machine and needs no key, "claude" is a hosted API, "auto" tries local first')
    p.add_argument("--model", help="model name for chat")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--chat-max-tokens", type=int, default=4_000,
                   help="ceiling authorised for each answer")
    p.add_argument("--history-turns", type=int, default=20,
                   help="how many past turns are replayed to the model")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except LedgerBusy as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (ChainError, StateError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
