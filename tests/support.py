"""Shared fixtures for the test suite."""

from __future__ import annotations

from chainmind.chain import Chain
from chainmind.consensus import ProofOfAuthority
from chainmind.crypto import SigningKey
from chainmind.resources import CREDIT
from chainmind.state import GenesisConfig
from chainmind.transactions import build_grant

AUTHORITY = SigningKey.from_passphrase("test-authority")
AGENT = SigningKey.from_passphrase("test-agent")
OUTSIDER = SigningKey.from_passphrase("test-outsider")


def make_genesis(**overrides) -> GenesisConfig:
    params = {
        "authorities": {AUTHORITY.public_hex(): 1000 * CREDIT},
        "chain_id": "test-net",
        "epoch_length": 100,
    }
    params.update(overrides)
    return GenesisConfig(**params)


def make_chain(**overrides) -> Chain:
    genesis = make_genesis(**overrides)
    return Chain.create(
        genesis, AUTHORITY, ProofOfAuthority([AUTHORITY.public_hex()]), timestamp=1_700_000_000
    )


def fund(chain: Chain, address: str, microcredits: int) -> None:
    """Move credits from the genesis treasury to ``address``."""
    nonce = chain.state.get(AUTHORITY.public_hex()).nonce
    result = chain.seal_block(
        AUTHORITY, [build_grant(AUTHORITY, nonce, beneficiary=address, amount=microcredits)]
    )
    assert not result.rejected, result.rejected
