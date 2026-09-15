"""``chainmind`` -- a command line over the ledger and the agent.

The commands mirror the lifecycle: create a network, fund an agent, let it
work, then audit what it did.  Every command operates on a workspace
directory (``.chainmind`` by default) holding the ledger file and the keys.
"""

from __future__ import annotations

import argparse
import errno
import threading
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
        self.models_dir = root / "models"
        self.memory_path = root / "memory" / "notes.jsonl"
        self.feedback_path = root / "memory" / "feedback.jsonl"

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
    workspace.models_dir.mkdir(parents=True, exist_ok=True)

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
            f"models       {workspace.models_dir}  (put a .gguf here)",
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


def _deliver(args: argparse.Namespace, workspace: "Workspace", chain, tx,
             sealer) -> dict[str, Any]:
    """Get a transaction into the chain, by whichever route is available.

    With ``--node`` the transaction is handed to a running node and reaches
    the chain through consensus.  Without it, this process seals a block
    itself -- which only works when no node holds the ledger's write lock.
    """
    if args.node:
        from .p2p import submit_to_node

        host, port = _parse_peers([args.node])[0]
        submit_to_node(host, port, tx, chain=chain)
        return {"delivered": "node", "node": f"{host}:{port}", "sealed": False}

    result = chain.seal_block(sealer, [tx])
    if result.rejected:
        raise SystemExit(f"rejected: {result.rejected[0][1]}")
    return {"delivered": "local", "sealed": True, "height": chain.height}


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
    delivery = _deliver(args, workspace, chain, tx, authority)

    payload = {"txid": tx.txid, "beneficiary": beneficiary, "amount": amount,
               "balance": chain.state.balance_of(beneficiary), **delivery}
    lines = [f"granted   {format_credits(amount)} -> {beneficiary[:16]}..."]
    if delivery["sealed"]:
        lines.append(f"balance   {format_credits(chain.state.balance_of(beneficiary))}")
    else:
        lines.append(f"handed to {delivery['node']}; it settles when a validator seals it")
    lines.append(f"tx        {tx.txid}")
    _emit(payload, args.json, render=lines)
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
    delivery = _deliver(args, workspace, chain, tx, authority)

    payload = {"txid": tx.txid, "prices": chain.state.prices.to_dict(),
               "limits": chain.state.limits, **delivery}
    lines = [
        f"policy {'updated in' if delivery['sealed'] else 'submitted as'} {tx.txid[:16]}",
        f"prices  {chain.state.prices.to_dict()}",
        f"limits  {chain.state.limits or 'none'}",
    ]
    if not delivery["sealed"]:
        lines.append(f"handed to {delivery['node']}; it settles when a validator seals it")
    _emit(payload, args.json, render=lines)
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
        model = build_model(args.model, workspace=workspace.root)
    except ModelUnavailable as exc:
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
        model = build_model(args.model, workspace=workspace.root)
    except ModelUnavailable as exc:
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

    from .memory import FeedbackStore, MemoryStore

    memory = None if args.no_memory else MemoryStore(workspace.memory_path)
    feedback = None if args.no_memory else FeedbackStore(workspace.feedback_path)
    return ChatService(kernel, ledger, model_name=describe_model(model),
                       max_tokens=args.chat_max_tokens, history_turns=args.history_turns,
                       memory=memory, feedback=feedback,
                       recall_limit=args.recall)


def _parse_peers(values: Sequence[str] | None) -> list[tuple[str, int]]:
    peers: list[tuple[str, int]] = []
    for item in values or []:
        host, _, port = item.rpartition(":")
        if not host or not port.isdigit():
            raise SystemExit(f"a peer must look like host:port, got {item!r}")
        peers.append((host.strip("[]"), int(port)))
    return peers


def cmd_join(args: argparse.Namespace) -> int:
    """Download an existing chain and become a node of it."""
    from .p2p import join_network

    workspace = Workspace(args.workspace)
    if workspace.exists() and not args.force:
        raise SystemExit(
            f"{workspace.ledger_path} already exists; pass --force to replace it"
        )
    workspace.ensure()
    host, port = _parse_peers([args.peer])[0]

    chain = join_network(host, port, workspace.ledger_path,
                         expected_genesis=args.genesis_hash,
                         log=lambda message: print(f"  {message}"))
    try:
        state = chain.verify()
        if not args.genesis_hash:
            print("\nwarning   you trusted this peer's genesis on first use.")
            print("          publish the hash below and pass it as --genesis-hash next time.")
        payload = {
            "chain_id": chain.genesis_config.chain_id,
            "genesis_hash": Chain.genesis_commitment(chain.genesis_config),
            "height": chain.height,
            "state_root": state.state_root(),
            "ledger": str(workspace.ledger_path),
        }
        lines = [
            "",
            f"chain        {payload['chain_id']}",
            f"genesis      {payload['genesis_hash']}",
            f"height       {payload['height']}",
            f"state root   {payload['state_root']}",
            f"ledger       {payload['ledger']}",
            "",
            f"next: chainmind node --peer {host}:{port}",
        ]
        _emit(payload, args.json, render=lines)
    finally:
        chain.close()

    if not workspace.key_names():
        key = workspace.create_key("agent")
        print(f"agent key    {key.public_hex()}")
        print("ask a network authority to grant it credits before it can act.")
    return 0


def cmd_node(args: argparse.Namespace) -> int:
    """Run as a node of the network."""
    import signal as signal_module

    from .p2p import Node

    workspace = Workspace(args.workspace)
    chain = workspace.load_chain()
    node_key = (
        workspace.load_key(args.node_key) if args.node_key in workspace.key_names()
        else workspace.create_key(args.node_key)
    )

    node = Node(chain, node_key=node_key, host=args.host, port=args.port,
                label=args.label or workspace.root.name,
                log=lambda message: print(f"  {message}", flush=True))
    node.start()

    print(f"node       {node.node_id}")
    print(f"listening  {node.host}:{node.port}")
    print(f"chain      {chain.genesis_config.chain_id} at height {chain.height}")
    print(f"genesis    {Chain.genesis_commitment(chain.genesis_config)}")

    peers = _parse_peers(args.peer)
    if peers:
        print(f"dialing    {', '.join(f'{h}:{p}' for h, p in peers)}")
        node.connect_all(peers)

    sealer = None
    if args.seal:
        sealer = workspace.load_key(args.seal)
        if sealer.public_hex() not in getattr(chain.consensus, "validators", []):
            raise SystemExit(
                f"'{args.seal}' is not in this chain's validator set, so blocks it "
                "sealed would be refused by every peer"
            )
        print(f"sealing    every {args.seal_interval}s as '{args.seal}'")

    stopping = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal_module, name, None)
        if handler is not None:
            signal_module.signal(handler, lambda *_: stopping.set())

    print("\nCtrl-C to stop.")
    try:
        while not stopping.wait(args.seal_interval if sealer else 1.0):
            if sealer is not None:
                block = node.seal(sealer)
                if block is not None:
                    print(f"  sealed #{block.height} with "
                          f"{len(block.transactions)} tx", flush=True)
    finally:
        print("\nstopping…")
        node.stop()
    return 0


def cmd_remember(args: argparse.Namespace) -> int:
    """Put something into memory by hand."""
    from .memory import MemoryStore

    workspace = Workspace(args.workspace)
    text = sys.stdin.read() if args.text == "-" else args.text
    memory = MemoryStore(workspace.memory_path)
    note = memory.remember(text, kind=args.kind)
    _emit({"id": note.id, "kind": note.kind, "digest": note.digest}, args.json,
          render=[f"remembered {note.id}", f"kind       {note.kind}",
                  f"digest     {note.digest}"])
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    """Search memory the way the agent does before answering."""
    from .memory import MemoryStore

    workspace = Workspace(args.workspace)
    memory = MemoryStore(workspace.memory_path)
    hits = memory.search(args.query, limit=args.limit, kinds=args.kind or None)

    payload = {"query": args.query, "hits": [hit.to_dict() for hit in hits],
               **memory.stats()}
    if not hits:
        lines = ["nothing relevant in memory",
                 f"({memory.stats()['notes']} notes indexed)"]
    else:
        lines = []
        for hit in hits:
            text = " ".join(hit.note.text.split())
            lines.append(f"{hit.score:6.2f}  [{hit.note.kind}] {text[:110]}")
    _emit(payload, args.json, render=lines)
    return 0


def cmd_learning(args: argparse.Namespace) -> int:
    """What the system has collected to learn from."""
    from .memory import FeedbackStore, MemoryStore, build_training_dataset

    workspace = Workspace(args.workspace)
    memory = MemoryStore(workspace.memory_path)
    feedback = FeedbackStore(workspace.feedback_path)
    dataset = build_training_dataset(feedback, rating=args.rating)

    if args.export:
        target = Path(args.export)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for row in dataset:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    stats = feedback.stats()
    payload = {"memory": memory.stats(), "feedback": stats,
               "dataset_rows": len(dataset),
               "exported": str(args.export) if args.export else None}
    approval = stats["approval"]
    lines = [
        f"notes      {memory.stats()['notes']}  ({', '.join(f'{k}: {v}' for k, v in memory.stats()['kinds'].items()) or 'empty'})",
        f"rated      {stats['total']}  (good {stats['good']}, bad {stats['bad']})",
        f"approval   {f'{approval:.0%}' if approval is not None else 'nothing rated yet'}",
        f"dataset    {len(dataset)} rows rated '{args.rating}'",
    ]
    if args.export:
        lines.append(f"written    {args.export}")
    lines += ["", "nothing here trains anything by itself; that is a separate,",
              "deliberate step on hardware of your choosing."]
    _emit(payload, args.json, render=lines)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Score the model against answers a person already approved."""
    from .agent import AgentKernel, LocalLedger
    from .evaluate import EvalReport, Evaluation, load_dataset, split_dataset
    from .models import ModelUnavailable, build_model, describe_model
    from .tools import ToolRegistry

    workspace = Workspace(args.workspace)
    chain = workspace.load_chain()

    if args.dataset:
        rows = load_dataset(args.dataset)
    else:
        from .memory import FeedbackStore, build_training_dataset

        rows = build_training_dataset(FeedbackStore(workspace.feedback_path))
    if not rows:
        raise SystemExit(
            "there is nothing to evaluate. Rate some answers in the chat panel "
            "first, or pass --dataset."
        )

    _, validation = split_dataset(rows, validation_fraction=args.validation_fraction)
    if not validation:
        raise SystemExit("the split left no validation rows; collect more feedback")

    try:
        model = build_model(args.model, workspace=workspace.root)
    except ModelUnavailable as exc:
        raise SystemExit(str(exc)) from exc

    registry = ToolRegistry()
    register_model_tool(registry, model)
    ledger = LocalLedger(chain, workspace.load_key(args.sealer))
    kernel = AgentKernel(workspace.load_key(args.agent), ledger, tools=registry,
                         label=args.agent)
    kernel.register()
    ledger.flush()

    evaluation = Evaluation(kernel, validation, max_tokens=args.max_tokens)
    printed = [0]

    def progress(result) -> None:
        printed[0] += 1
        if not args.json:
            mark = {"scored": f"{result.f1:5.2f}", "refused": "  ref", "failed": "  err"}
            print(f"  {printed[0]:>3}/{len(validation)}  {mark[result.status]}  "
                  f"{' '.join(result.prompt.split())[:60]}", file=sys.stderr)

    report = evaluation.run(model=describe_model(model), label=args.label,
                            limit=args.limit, on_row=progress)
    ledger.flush()

    payload = report.to_dict()
    if args.save:
        report.save(args.save)
        payload["saved"] = str(args.save)

    comparison = None
    if args.compare:
        comparison = report.compare(EvalReport.load(args.compare))
        payload["comparison"] = comparison

    lines = [
        "",
        f"model      {report.model}",
        f"rows       {len(report.results)} ({len(report.scored)} scored, "
        f"{report.refused} refused, {report.failed} failed)",
        f"mean F1    {report.mean_f1:.4f}",
        f"mean ROUGE {report.mean_rouge:.4f}",
        f"cost       {format_credits(report.spent)}",
    ]
    if args.save:
        lines.append(f"saved      {args.save}")
    if comparison is not None:
        lines += ["", "against the baseline:"]
        if not comparison["comparable"]:
            lines.append(f"  {comparison['reason']}")
        else:
            lines += [
                f"  baseline   {comparison['baseline_f1']:.4f}",
                f"  now        {comparison['current_f1']:.4f}",
                f"  delta      {comparison['f1_delta']:+.4f}",
                f"  verdict    {comparison['verdict']}",
            ]
    lines += ["", "the score measures overlap with an approved answer, not quality:",
              "a differently worded but better answer scores lower."]
    _emit(payload, args.json, render=lines)
    return 0


def cmd_runtime(args: argparse.Namespace) -> int:
    """Report what this machine can think with, if anything."""
    from .models import available_runtimes

    workspace = Workspace(args.workspace)
    found = available_runtimes(workspace.root)

    lines: list[str] = []
    own = found["own"]
    if own["available"]:
        lines.append("own       yes — trained here; architecture, vocabulary and "
                     "weights all from this project")
        for entry in own["models"]:
            detail = (f"{entry['parameters'] / 1e6:.1f}M parameters, "
                      f"{entry['layers']}×{entry['dim']}, context {entry['context']}"
                      if "parameters" in entry else entry.get("error", ""))
            lines.append(f"          {entry['name']}  ({detail})")
        lines.append(f"          arithmetic: {own['backend']}")
    else:
        lines.append(f"own       no — {own.get('reason', 'not checked')}")

    embedded = found["embedded"]
    if embedded["available"]:
        lines.append("embedded  yes — open weights, loaded into this process")
        for entry in embedded["models"]:
            lines.append(f"          {entry['name']}  ({entry['size_mb']} MB)")
    else:
        lines.append(f"embedded  no — {embedded.get('reason', 'not checked')}")

    served = found["served"]
    if served["available"]:
        lines.append(f"served    yes — {served['dialect']} at {served['url']}")
        lines.append(f"          {', '.join(served['models']) or '(none loaded yet)'}")
    else:
        lines.append(f"served    no — {served.get('reason', '').splitlines()[0]}")

    lines += ["", "no key, no account, no outbound request."]
    if not found["available"]:
        lines += [
            "",
            "two ways to get one, most self-contained first:",
            "  python3 training/pretrain.py --corpus <your text>   # train its own",
            f"  ...or put an open .gguf file in {workspace.models_dir}",
        ]
    _emit(found, args.json, render=lines)
    return 0 if found["available"] else 1


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

    p = sub.add_parser("join", parents=[shared],
                       help="download an existing chain from a peer and become a node of it")
    p.add_argument("peer", help="a node of the network, as host:port")
    p.add_argument("--genesis-hash",
                   help="the genesis commitment you expect; without it this is "
                        "trust-on-first-use")
    p.add_argument("--force", action="store_true", help="replace an existing workspace")
    p.set_defaults(func=cmd_join)

    p = sub.add_parser("node", parents=[shared], help="run as a node of the network")
    p.add_argument("--host", default="0.0.0.0",
                   help="interface to listen on (default: every interface)")
    p.add_argument("--port", type=int, default=8877)
    p.add_argument("--peer", action="append", metavar="HOST:PORT",
                   help="a node to dial on start, repeatable")
    p.add_argument("--node-key", default="node", help="key name identifying this node")
    p.add_argument("--label", default="", help="a human name for this node")
    p.add_argument("--seal", metavar="KEY",
                   help="also produce blocks with this key (validators only)")
    p.add_argument("--seal-interval", type=float, default=5.0,
                   help="seconds between block attempts when sealing")
    p.set_defaults(func=cmd_node)

    p = sub.add_parser("remember", parents=[shared], help="add something to memory")
    p.add_argument("text", help="what to remember; use - to read it from stdin")
    p.add_argument("--kind", default="note", help="a label, e.g. fact or preference")
    p.set_defaults(func=cmd_remember)

    p = sub.add_parser("recall", parents=[shared],
                       help="search memory the way the agent does")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--kind", action="append", help="restrict to a kind, repeatable")
    p.set_defaults(func=cmd_recall)

    p = sub.add_parser("learning", parents=[shared],
                       help="what has been collected to learn from")
    p.add_argument("--rating", choices=["good", "bad"], default="good")
    p.add_argument("--export", metavar="PATH",
                   help="write the dataset as JSONL for a later fine-tune")
    p.set_defaults(func=cmd_learning)

    p = sub.add_parser("eval", parents=[shared],
                       help="score the model against answers you approved")
    p.add_argument("--dataset", help="a JSONL file; defaults to this workspace's feedback")
    p.add_argument("--agent", default="agent", help="key name that pays for the run")
    p.add_argument("--sealer", default="authority", help="key name that seals blocks")
    p.add_argument("--model",
                   help="a .cmw or .gguf file to load here, or a name a running server offers")
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--limit", type=int, help="stop after this many rows")
    p.add_argument("--validation-fraction", type=float, default=0.2)
    p.add_argument("--label", default="", help="a name for this run")
    p.add_argument("--save", metavar="PATH", help="write the report, to compare against later")
    p.add_argument("--compare", metavar="PATH", help="an earlier report to compare against")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("runtime", parents=[shared],
                       help="show the inference runtime this machine offers")
    p.set_defaults(func=cmd_runtime)

    p = sub.add_parser("keys", parents=[shared], help="list the keys in the workspace")
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser("grant", parents=[shared], help="move credits from the treasury to an agent")
    p.add_argument("beneficiary", help="a key name in the workspace, or a hex address")
    p.add_argument("amount", type=float, help="credits to grant (fractions allowed)")
    p.add_argument("--from-key", default="authority")
    p.add_argument("--memo", default="")
    p.add_argument("--node", metavar="HOST:PORT",
                   help="hand the transaction to a running node instead of sealing "
                        "it here; required while a node holds the ledger")
    p.set_defaults(func=cmd_grant)

    p = sub.add_parser("policy", parents=[shared], help="change prices or quotas (authority only)")
    p.add_argument("--price", action="append", metavar="RESOURCE=MICROCREDITS")
    p.add_argument("--limit", action="append", metavar="RESOURCE=UNITS")
    p.add_argument("--from-key", default="authority")
    p.add_argument("--memo", default="")
    p.add_argument("--node", metavar="HOST:PORT",
                   help="hand the transaction to a running node instead of sealing "
                        "it here; required while a node holds the ledger")
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
    p.add_argument("--model",
                   help="a .cmw or .gguf file to load here, or a name a running server offers")
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
    p.add_argument("--model",
                   help="a .cmw or .gguf file to load here, or a name a running server offers")
    p.add_argument("--chat-max-tokens", type=int, default=4_000,
                   help="ceiling authorised for each answer")
    p.add_argument("--history-turns", type=int, default=20,
                   help="how many past turns are replayed to the model")
    p.add_argument("--recall", type=int, default=4,
                   help="how many remembered notes may be pulled into a turn")
    p.add_argument("--no-memory", action="store_true",
                   help="answer each turn from nothing; remember and rate nothing")
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
