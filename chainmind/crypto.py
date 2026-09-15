"""Cryptographic primitives for the ChainMind ledger.

Everything the ledger signs or hashes goes through this module so that the
byte-level encoding is defined in exactly one place.  Two things matter here:

*   **Canonical encoding.**  Two nodes must derive the same bytes from the same
    logical object, otherwise their hashes -- and therefore their chains --
    diverge.  We use JSON with sorted keys, no insignificant whitespace and
    no non-ASCII escaping ambiguity.
*   **Deterministic signatures.**  Ed25519 is deterministic, so a node can
    re-sign a payload and compare it byte-for-byte while auditing.

The module prefers ``cryptography`` when it is installed and otherwise falls
back to a pure-Python Ed25519 implementation, which keeps the core of the
project dependency-free at the cost of speed.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from typing import Any, Iterable, Sequence

__all__ = [
    "canonical_bytes",
    "canonical_json",
    "sha256_hex",
    "double_sha256_hex",
    "merkle_root",
    "SigningKey",
    "VerifyingKey",
    "verify_signature",
    "BACKEND",
]


# --------------------------------------------------------------------------
# Canonical encoding and hashing
# --------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """Serialise ``obj`` to the one JSON string every node must agree on."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(obj: Any) -> bytes:
    """UTF-8 encoding of :func:`canonical_json`."""
    return canonical_json(obj).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def double_sha256_hex(data: bytes | str) -> str:
    """SHA-256 applied twice, used for block identifiers."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(hashlib.sha256(data).digest()).hexdigest()


ZERO_HASH = "0" * 64


def _leaf_hash(leaf_hex: str) -> bytes:
    return hashlib.sha256(b"\x00" + bytes.fromhex(leaf_hex)).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(count: int) -> int:
    """The largest power of two strictly below ``count``."""
    return 1 << ((count - 1).bit_length() - 1)


def _tree_hash(leaves: Sequence[bytes]) -> bytes:
    if len(leaves) == 1:
        return leaves[0]
    k = _split(len(leaves))
    return _node_hash(_tree_hash(leaves[:k]), _tree_hash(leaves[k:]))


def merkle_root(leaves: Sequence[str]) -> str:
    """Merkle root over a sequence of hex digests, following RFC 6962.

    The obvious construction -- pair up, duplicate the odd one out, repeat --
    is unsafe: a three-leaf tree and the same tree with its last leaf repeated
    produce the same root, so a block's transaction list can be padded without
    changing its header.  (Tagging leaves and internal nodes differently, as we
    do, closes a *different* hole and not this one.)  RFC 6962 never
    duplicates: it splits at the largest power of two below the leaf count, so
    every leaf count yields a distinct shape.
    """
    if not leaves:
        return ZERO_HASH
    return _tree_hash([_leaf_hash(leaf) for leaf in leaves]).hex()


def merkle_proof(leaves: Sequence[str], index: int) -> list[tuple[str, str]]:
    """Inclusion proof for ``leaves[index]`` as ``(side, sibling_hex)`` pairs.

    ``side`` says where the sibling sits, so a verifier can rebuild the root
    without knowing the tree's shape.
    """
    if not 0 <= index < len(leaves):
        raise IndexError("leaf index out of range")

    hashed = [_leaf_hash(leaf) for leaf in leaves]
    proof: list[tuple[str, str]] = []

    def walk(nodes: Sequence[bytes], target: int) -> None:
        if len(nodes) == 1:
            return
        k = _split(len(nodes))
        if target < k:
            walk(nodes[:k], target)
            proof.append(("right", _tree_hash(nodes[k:]).hex()))
        else:
            walk(nodes[k:], target - k)
            proof.append(("left", _tree_hash(nodes[:k]).hex()))

    walk(hashed, index)
    return proof


def verify_merkle_proof(leaf: str, proof: Iterable[tuple[str, str]], root: str) -> bool:
    """Check a proof produced by :func:`merkle_proof`."""
    acc = _leaf_hash(leaf)
    for side, sibling_hex in proof:
        sibling = bytes.fromhex(sibling_hex)
        acc = _node_hash(sibling, acc) if side == "left" else _node_hash(acc, sibling)
    return acc.hex() == root


# --------------------------------------------------------------------------
# Ed25519 -- pure-Python fallback (RFC 8032 reference construction)
# --------------------------------------------------------------------------

_P = 2 ** 255 - 19                                              # field prime
_Q = 2 ** 252 + 27742317777372353535851937790883648493          # group order
_D = -121665 * pow(121666, _P - 2, _P) % _P                     # curve constant
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _sha512_modq(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little") % _Q


def _point_add(p1, p2):
    a = (p1[1] - p1[0]) * (p2[1] - p2[0]) % _P
    b = (p1[1] + p1[0]) * (p2[1] + p2[0]) % _P
    c = 2 * p1[3] * p2[3] * _D % _P
    d = 2 * p1[2] * p2[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _point_mul(scalar: int, point):
    result = (0, 1, 1, 0)  # neutral element
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _point_equal(p1, p2) -> bool:
    if (p1[0] * p2[2] - p2[0] * p1[2]) % _P != 0:
        return False
    return (p1[1] * p2[2] - p2[1] * p1[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_G_Y = 4 * pow(5, _P - 2, _P) % _P
_G_X = _recover_x(_G_Y, 0)
_G = (_G_X, _G_Y, 1, _G_X * _G_Y % _P)


def _point_compress(point) -> bytes:
    zinv = pow(point[2], _P - 2, _P)
    x = point[0] * zinv % _P
    y = point[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _point_decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _secret_expand(secret: bytes) -> tuple[int, bytes]:
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _py_public_key(secret: bytes) -> bytes:
    a, _ = _secret_expand(secret)
    return _point_compress(_point_mul(a, _G))


def _py_sign(secret: bytes, message: bytes) -> bytes:
    a, prefix = _secret_expand(secret)
    public = _point_compress(_point_mul(a, _G))
    r = _sha512_modq(prefix + message)
    rs = _point_compress(_point_mul(r, _G))
    h = _sha512_modq(rs + public + message)
    s = (r + h * a) % _Q
    return rs + int.to_bytes(s, 32, "little")


def _py_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    point_a = _point_decompress(public)
    if point_a is None:
        return False
    rs = signature[:32]
    point_r = _point_decompress(rs)
    if point_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = _sha512_modq(rs + public + message)
    return _point_equal(_point_mul(s, _G), _point_add(point_r, _point_mul(h, point_a)))


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------

class _suppress_stderr:
    """Silence fd 2 for the duration of a block.

    A broken compiled extension can print a Rust panic trace straight to the
    file descriptor, bypassing Python entirely, and we do not want that noise
    on every import when the fallback handles it cleanly.
    """

    def __enter__(self):
        self._saved = os.dup(2)
        self._devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self._devnull, 2)
        return self

    def __exit__(self, *exc_info):
        os.dup2(self._saved, 2)
        os.close(self._devnull)
        os.close(self._saved)
        return False


def _select_backend():
    """Pick the fastest working Ed25519 backend.

    ``cryptography`` is preferred, but a present-yet-broken install must not
    take the whole ledger down with it: distro packages regularly ship the
    Python half without its compiled half, and the failure surfaces as
    anything from ``ImportError`` to a Rust ``PanicException`` (which derives
    from ``BaseException``, so a bare ``except Exception`` would miss it).
    We therefore guard the import broadly and, more importantly, prove the
    backend actually signs and verifies before trusting it.
    """
    if not os.environ.get("CHAINMIND_PURE_PYTHON_CRYPTO"):
        try:
            with _suppress_stderr():
                from cryptography.hazmat.primitives.asymmetric import ed25519
                from cryptography.hazmat.primitives import serialization
                from cryptography.exceptions import InvalidSignature

            def public_key(secret: bytes) -> bytes:
                key = ed25519.Ed25519PrivateKey.from_private_bytes(secret)
                return key.public_key().public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )

            def sign(secret: bytes, message: bytes) -> bytes:
                return ed25519.Ed25519PrivateKey.from_private_bytes(secret).sign(message)

            def verify(public: bytes, message: bytes, signature: bytes) -> bool:
                try:
                    ed25519.Ed25519PublicKey.from_public_bytes(public).verify(
                        signature, message
                    )
                except (InvalidSignature, ValueError):
                    return False
                return True

            _self_test(public_key, sign, verify)
            return "cryptography", public_key, sign, verify
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            pass  # fall through to the pure-Python implementation

    return "pure-python", _py_public_key, _py_sign, _py_verify


_PROBE_SEED = bytes(range(32))
# RFC 8032 has no test vector for this seed, so the expected public key is
# pinned from the pure-Python implementation, which *is* the reference
# construction.  Any backend that disagrees is not interoperable with it.
_PROBE_MESSAGE = b"chainmind backend self-test"


def _self_test(public_key, sign, verify) -> None:
    """Raise unless the candidate backend agrees with the reference code."""
    expected_public = _py_public_key(_PROBE_SEED)
    if public_key(_PROBE_SEED) != expected_public:
        raise RuntimeError("backend derives a different public key")
    signature = sign(_PROBE_SEED, _PROBE_MESSAGE)
    if signature != _py_sign(_PROBE_SEED, _PROBE_MESSAGE):
        raise RuntimeError("backend produces a different signature")
    if not verify(expected_public, _PROBE_MESSAGE, signature):
        raise RuntimeError("backend rejects its own signature")
    if verify(expected_public, b"tampered", signature):
        raise RuntimeError("backend accepts a forged signature")


BACKEND, _public_key, _sign, _verify = _select_backend()


# --------------------------------------------------------------------------
# Key objects
# --------------------------------------------------------------------------

class VerifyingKey:
    """A public identity on the chain, rendered as 64 hex characters."""

    __slots__ = ("_raw",)

    def __init__(self, raw: bytes) -> None:
        if len(raw) != 32:
            raise ValueError("an Ed25519 public key must be 32 bytes")
        self._raw = bytes(raw)

    @classmethod
    def from_hex(cls, value: str) -> "VerifyingKey":
        return cls(bytes.fromhex(value))

    @property
    def raw(self) -> bytes:
        return self._raw

    def to_hex(self) -> str:
        return self._raw.hex()

    def verify(self, message: bytes, signature_hex: str) -> bool:
        try:
            signature = bytes.fromhex(signature_hex)
        except ValueError:
            return False
        return _verify(self._raw, message, signature)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, VerifyingKey) and other._raw == self._raw

    def __hash__(self) -> int:
        return hash(self._raw)

    def __repr__(self) -> str:
        return f"VerifyingKey({self.to_hex()[:16]}...)"


class SigningKey:
    """A private key.  Only agents and authorities hold one."""

    __slots__ = ("_seed", "_public")

    def __init__(self, seed: bytes) -> None:
        if len(seed) != 32:
            raise ValueError("an Ed25519 seed must be 32 bytes")
        self._seed = bytes(seed)
        self._public = VerifyingKey(_public_key(self._seed))

    @classmethod
    def generate(cls) -> "SigningKey":
        return cls(secrets.token_bytes(32))

    @classmethod
    def from_hex(cls, value: str) -> "SigningKey":
        return cls(bytes.fromhex(value))

    @classmethod
    def from_passphrase(cls, passphrase: str, *, salt: str = "chainmind") -> "SigningKey":
        """Derive a reproducible key.  Useful for tests and local demos."""
        seed = hashlib.pbkdf2_hmac(
            "sha256", passphrase.encode("utf-8"), salt.encode("utf-8"), 200_000, dklen=32
        )
        return cls(seed)

    @property
    def verifying_key(self) -> VerifyingKey:
        return self._public

    def to_hex(self) -> str:
        return self._seed.hex()

    def public_hex(self) -> str:
        return self._public.to_hex()

    def sign(self, message: bytes) -> str:
        return _sign(self._seed, message).hex()

    def __repr__(self) -> str:
        return f"SigningKey(public={self.public_hex()[:16]}...)"


def verify_signature(public_hex: str, message: bytes, signature_hex: str) -> bool:
    """Convenience wrapper that never raises on malformed input."""
    try:
        return VerifyingKey.from_hex(public_hex).verify(message, signature_hex)
    except ValueError:
        return False
