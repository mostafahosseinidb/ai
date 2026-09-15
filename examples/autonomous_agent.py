#!/usr/bin/env python3
"""A walk through the whole idea in one file.

Run it with ``python examples/autonomous_agent.py``.  It needs no network,
no API key and no dependencies beyond the standard library.

The story it tells, in five acts:

1.  A network is founded with a fixed treasury and a quota on tool calls.
2.  An agent is funded and goes to work, paying for everything it consumes.
3.  The agent tries something it cannot afford and is refused *before* acting.
4.  Governance tightens a quota, and the refusal changes shape immediately.
5.  An auditor replays the chain from scratch and reconstructs the bill.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Run straight from a checkout, without installing the package first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chainmind import (
    AgentKernel,
    Chain,
    GenesisConfig,
    LocalLedger,
    ProofOfAuthority,
    ResourceKind,
    SigningKey,
    Step,
    StaticPlanner,
    TxType,
    build_grant,
    build_policy_update,
    format_credits,
)

CREDIT = 1_000_000


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "-" * len(title))


def main() -> int:
    # -- 1. Found the network ------------------------------------------------
    authority = SigningKey.from_passphrase("demo-authority")
    atlas_key = SigningKey.from_passphrase("demo-atlas")

    genesis = GenesisConfig(
        authorities={authority.public_hex(): 1_000 * CREDIT},
        limits={ResourceKind.TOOL_CALL: 2},      # at most two external calls per epoch
        epoch_length=1_000,
        chain_id="atlas-net",
        note="a demonstration network",
    )
    chain = Chain.create(genesis, authority, ProofOfAuthority([authority.public_hex()]))
    ledger = LocalLedger(chain, authority)

    rule("1. the network exists")
    print(f"chain      {genesis.chain_id}")
    print(f"genesis    {chain.tip.hash}")
    print(f"treasury   {format_credits(chain.state.balance_of(authority.public_hex()))}")
    print(f"quota      {genesis.to_dict()['limits']} per epoch")

    # -- 2. Fund an agent and let it work ------------------------------------
    atlas = AgentKernel(
        atlas_key, ledger, label="atlas",
        planner=StaticPlanner([
            Step("think", {"prompt": "how should I ration my own compute?",
                           "budget_tokens": 96},
                 rationale="form a view before spending on anything external"),
            Step("remember", {"key": "policy", "text": "reserve a quarter of the balance"},
                 rationale="persist it so the next epoch inherits the decision"),
        ]),
    )
    chain.seal_block(
        authority,
        [build_grant(authority, chain.state.get(authority.public_hex()).nonce,
                     beneficiary=atlas.address, amount=CREDIT // 100, memo="seed budget")],
    )

    rule("2. the agent works within its means")
    print(f"agent      {atlas.address[:16]}...")
    print(f"funded     {format_credits(atlas.balance)}\n")
    for outcome in atlas.run("draft a rationing policy for myself"):
        print(f"  {outcome.summary()}")
    print(f"\nbalance    {format_credits(atlas.balance)}")

    # -- 3. Ask for more than it can pay for ---------------------------------
    rule("3. the chain refuses before the work happens")
    outcome = atlas.perform("think", prompt="an enormous deliberation", budget_tokens=10 ** 6)
    print(f"  {outcome.summary()}")
    print(f"  executed:  {outcome.executed}   (the tool was never called)")

    # -- 4. Governance moves the line ----------------------------------------
    rule("4. governance tightens the quota, live")
    chain.seal_block(
        authority,
        [build_policy_update(authority, chain.state.get(authority.public_hex()).nonce,
                             limits={ResourceKind.LLM_OUTPUT_TOKENS: 10},
                             memo="rein in verbosity")],
    )
    outcome = atlas.perform("think", prompt="a modest thought", budget_tokens=64)
    print(f"  {outcome.summary()}")
    print(f"  balance is ample ({format_credits(atlas.balance)}) -- the quota is what bound it")

    # -- 5. Audit from nothing but the ledger --------------------------------
    rule("5. an auditor replays the chain")
    replayed = chain.verify()
    replayed.check_invariants()

    usage: dict[str, int] = {}
    spend: dict[str, int] = {}
    refusals = 0
    for _, tx in chain.transactions():
        if tx.type is TxType.USAGE and tx.sender == atlas.address:
            resource, amount = tx.body["resource"], tx.body["amount"]
            usage[resource] = usage.get(resource, 0) + amount
            spend[resource] = spend.get(resource, 0) + replayed.prices.cost(resource, amount)
        elif tx.type is TxType.ATTEST and tx.body["topic"] == "budget-refusal":
            refusals += 1

    print(f"blocks     {len(chain)}")
    print(f"state root {replayed.state_root()}")
    print(f"matches    {replayed.state_root() == chain.state.state_root()}\n")
    for resource in sorted(usage):
        print(f"  {resource:<20} {usage[resource]:>8} units   {format_credits(spend[resource]):>16}")
    print(f"  {'TOTAL':<20} {'':>8}         {format_credits(sum(spend.values())):>16}")
    print(f"\nrefusals recorded on chain: {refusals}")
    print("every credit the agent burned is accounted for, and so is every time it was stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
