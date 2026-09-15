"""Transactions: the only way anything in the world state ever changes.

A transaction is an envelope -- version, type, sender, nonce, timestamp, body --
plus a detached Ed25519 signature over the canonical encoding of that envelope.
The identifier of a transaction is the hash of the *signed payload*, so it is
fixed before the signature exists and cannot be reshaped by a third party.

Structural validity (does this body have the right shape?) lives here.
Semantic validity (can this account afford it? is the nonce next?) lives in
:mod:`chainmind.state`, because it needs the rest of the world to answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from .crypto import SigningKey, canonical_bytes, sha256_hex, verify_signature
from .resources import ResourceKind

__all__ = [
    "MEASUREMENT_SOURCES",
    "TxType",
    "Transaction",
    "InvalidTransaction",
    "build_registration",
    "build_grant",
    "build_usage",
    "build_policy_update",
    "build_attestation",
]

PROTOCOL_VERSION = 1


class InvalidTransaction(ValueError):
    """Raised when a transaction can never be valid, whatever the state."""


class TxType(str, Enum):
    REGISTER = "register"
    GRANT = "grant"
    USAGE = "usage"
    POLICY = "policy"
    ATTEST = "attest"


# --------------------------------------------------------------------------
# Small validation helpers -- deliberately strict, because a permissive parser
# on one node and a strict one on another is a consensus split waiting to
# happen.
# --------------------------------------------------------------------------

def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidTransaction(message)


def _require_hex(value: Any, length: int, label: str) -> str:
    _require(isinstance(value, str), f"{label} must be a string")
    _require(len(value) == length, f"{label} must be {length} hex characters")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise InvalidTransaction(f"{label} must be hexadecimal") from exc
    return value.lower()


def _require_positive_int(value: Any, label: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{label} must be an integer")
    _require(value > 0, f"{label} must be positive")
    return value


def _require_text(value: Any, label: str, *, max_length: int = 256) -> str:
    _require(isinstance(value, str), f"{label} must be a string")
    _require(len(value) <= max_length, f"{label} must be at most {max_length} characters")
    return value


@dataclass(frozen=True)
class Transaction:
    """A signed, self-describing state transition."""

    type: TxType
    sender: str
    nonce: int
    body: Mapping[str, Any]
    timestamp: int = field(default_factory=lambda: int(time.time()))
    signature: str | None = None
    version: int = PROTOCOL_VERSION

    # -- identity ----------------------------------------------------------

    def signing_payload(self) -> bytes:
        """The exact bytes that get signed, and that the txid hashes."""
        return canonical_bytes(
            {
                "version": self.version,
                "type": self.type.value,
                "sender": self.sender,
                "nonce": self.nonce,
                "timestamp": self.timestamp,
                "body": dict(self.body),
            }
        )

    @property
    def txid(self) -> str:
        return sha256_hex(self.signing_payload())

    # -- signing -----------------------------------------------------------

    def sign(self, key: SigningKey) -> "Transaction":
        """Return a signed copy.  The key must match the declared sender."""
        if key.public_hex() != self.sender:
            raise InvalidTransaction(
                "refusing to sign: the key does not match the transaction sender"
            )
        return Transaction(
            type=self.type,
            sender=self.sender,
            nonce=self.nonce,
            body=dict(self.body),
            timestamp=self.timestamp,
            signature=key.sign(self.signing_payload()),
            version=self.version,
        )

    def verify(self) -> bool:
        if not self.signature:
            return False
        return verify_signature(self.sender, self.signing_payload(), self.signature)

    # -- structural validation --------------------------------------------

    def validate(self) -> None:
        """Check everything that can be checked without the world state."""
        _require(self.version == PROTOCOL_VERSION, f"unsupported protocol version {self.version}")
        _require_hex(self.sender, 64, "sender")
        _require(
            isinstance(self.nonce, int) and not isinstance(self.nonce, bool) and self.nonce >= 0,
            "nonce must be a non-negative integer",
        )
        _require(
            isinstance(self.timestamp, int) and not isinstance(self.timestamp, bool),
            "timestamp must be an integer",
        )
        _require(self.timestamp > 0, "timestamp must be positive")
        _require(isinstance(self.body, Mapping), "body must be a mapping")
        _require(self.verify(), "signature does not verify against the sender key")
        _BODY_VALIDATORS[self.type](dict(self.body))

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type.value,
            "sender": self.sender,
            "nonce": self.nonce,
            "timestamp": self.timestamp,
            "body": dict(self.body),
            "signature": self.signature,
            "txid": self.txid,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Transaction":
        try:
            tx = cls(
                type=TxType(data["type"]),
                sender=data["sender"],
                nonce=data["nonce"],
                body=data["body"],
                timestamp=data["timestamp"],
                signature=data.get("signature"),
                version=data.get("version", PROTOCOL_VERSION),
            )
        except (KeyError, ValueError) as exc:
            raise InvalidTransaction(f"malformed transaction: {exc}") from exc
        declared = data.get("txid")
        if declared is not None and declared != tx.txid:
            raise InvalidTransaction("declared txid does not match the payload")
        return tx

    def summary(self) -> str:
        """One line, for logs and the CLI."""
        if self.type is TxType.USAGE:
            detail = f"{self.body['amount']} {self.body['resource']}"
        elif self.type is TxType.GRANT:
            detail = f"{self.body['amount']} mc -> {self.body['beneficiary'][:12]}"
        elif self.type is TxType.REGISTER:
            detail = f"{self.body['role']} '{self.body['label']}'"
        elif self.type is TxType.POLICY:
            detail = ", ".join(sorted(self.body.get("prices", {}))) or "limits"
        else:
            detail = self.body.get("topic", "")
        return f"{self.txid[:10]} {self.type.value:<8} {self.sender[:10]} {detail}"


# --------------------------------------------------------------------------
# Per-type body validators
# --------------------------------------------------------------------------

def _validate_register(body: dict[str, Any]) -> None:
    _require(set(body) <= {"role", "label", "metadata"}, "unexpected fields in register body")
    _require(body.get("role") in {"agent", "authority", "validator"}, "role must be agent, authority or validator")
    _require_text(body.get("label", ""), "label", max_length=64)
    _require(isinstance(body.get("metadata", {}), Mapping), "metadata must be a mapping")


def _validate_grant(body: dict[str, Any]) -> None:
    _require(set(body) <= {"beneficiary", "amount", "memo"}, "unexpected fields in grant body")
    _require_hex(body.get("beneficiary"), 64, "beneficiary")
    _require_positive_int(body.get("amount"), "amount")
    _require_text(body.get("memo", ""), "memo")


#: How a usage figure was obtained.  ``kernel`` means an operating system
#: measured it from outside the code being billed; ``declared`` means the tool
#: reported its own consumption and is believed.  An auditor reading the chain
#: should be able to tell these apart without trusting a side channel.
MEASUREMENT_SOURCES = frozenset({"declared", "kernel"})


def _validate_usage(body: dict[str, Any]) -> None:
    _require(
        set(body) <= {"resource", "amount", "evidence", "tool", "note", "measured"},
        "unexpected fields in usage body",
    )
    _require(
        body.get("measured", "declared") in MEASUREMENT_SOURCES,
        f"measured must be one of {sorted(MEASUREMENT_SOURCES)}",
    )
    try:
        ResourceKind.parse(body.get("resource", ""))
    except ValueError as exc:
        raise InvalidTransaction(str(exc)) from exc
    _require_positive_int(body.get("amount"), "amount")
    _require_hex(body.get("evidence"), 64, "evidence")
    _require_text(body.get("tool", ""), "tool", max_length=64)
    _require_text(body.get("note", ""), "note")


def _validate_policy(body: dict[str, Any]) -> None:
    _require(set(body) <= {"prices", "limits", "memo"}, "unexpected fields in policy body")
    _require(body.keys() & {"prices", "limits"}, "a policy update must change prices or limits")
    for key in ("prices", "limits"):
        table = body.get(key, {})
        _require(isinstance(table, Mapping), f"{key} must be a mapping")
        for resource, value in table.items():
            try:
                ResourceKind.parse(resource)
            except ValueError as exc:
                raise InvalidTransaction(str(exc)) from exc
            _require(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0,
                f"{key}[{resource}] must be a non-negative integer",
            )
    _require_text(body.get("memo", ""), "memo")


def _validate_attest(body: dict[str, Any]) -> None:
    _require(set(body) <= {"topic", "digest", "summary"}, "unexpected fields in attest body")
    _require_text(body.get("topic"), "topic", max_length=64)
    _require(body.get("topic"), "topic must not be empty")
    _require_hex(body.get("digest"), 64, "digest")
    _require_text(body.get("summary", ""), "summary", max_length=1024)


_BODY_VALIDATORS: dict[TxType, Callable[[dict[str, Any]], None]] = {
    TxType.REGISTER: _validate_register,
    TxType.GRANT: _validate_grant,
    TxType.USAGE: _validate_usage,
    TxType.POLICY: _validate_policy,
    TxType.ATTEST: _validate_attest,
}


# --------------------------------------------------------------------------
# Builders -- the ergonomic way to create a signed transaction
# --------------------------------------------------------------------------

def _build(key: SigningKey, tx_type: TxType, nonce: int, body: dict[str, Any],
           timestamp: int | None = None) -> Transaction:
    tx = Transaction(
        type=tx_type,
        sender=key.public_hex(),
        nonce=nonce,
        body=body,
        timestamp=timestamp if timestamp is not None else int(time.time()),
    ).sign(key)
    tx.validate()
    return tx


def build_registration(key: SigningKey, nonce: int, *, role: str, label: str = "",
                       metadata: Mapping[str, Any] | None = None,
                       timestamp: int | None = None) -> Transaction:
    body: dict[str, Any] = {"role": role, "label": label}
    if metadata:
        body["metadata"] = dict(metadata)
    return _build(key, TxType.REGISTER, nonce, body, timestamp)


def build_grant(key: SigningKey, nonce: int, *, beneficiary: str, amount: int,
                memo: str = "", timestamp: int | None = None) -> Transaction:
    return _build(
        key, TxType.GRANT, nonce,
        {"beneficiary": beneficiary, "amount": amount, "memo": memo}, timestamp,
    )


def build_usage(key: SigningKey, nonce: int, *, resource: ResourceKind | str, amount: int,
                evidence: str, tool: str = "", note: str = "", measured: str = "declared",
                timestamp: int | None = None) -> Transaction:
    kind = ResourceKind.parse(resource.value if isinstance(resource, ResourceKind) else resource)
    return _build(
        key, TxType.USAGE, nonce,
        {"resource": kind.value, "amount": amount, "evidence": evidence, "tool": tool,
         "note": note, "measured": measured},
        timestamp,
    )


def build_policy_update(key: SigningKey, nonce: int, *, prices: Mapping[str, int] | None = None,
                        limits: Mapping[str, int] | None = None, memo: str = "",
                        timestamp: int | None = None) -> Transaction:
    body: dict[str, Any] = {"memo": memo}
    if prices:
        body["prices"] = {ResourceKind.parse(k).value: v for k, v in prices.items()}
    if limits:
        body["limits"] = {ResourceKind.parse(k).value: v for k, v in limits.items()}
    return _build(key, TxType.POLICY, nonce, body, timestamp)


def build_attestation(key: SigningKey, nonce: int, *, topic: str, digest: str,
                      summary: str = "", timestamp: int | None = None) -> Transaction:
    return _build(
        key, TxType.ATTEST, nonce,
        {"topic": topic, "digest": digest, "summary": summary}, timestamp,
    )
