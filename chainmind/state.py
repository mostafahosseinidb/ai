"""The world state: accounts, balances, quotas and the rules that move them.

This is where the project's central claim is enforced.  An agent does not
*report* its consumption to a passive log -- it must pay for it out of a
balance the chain granted, within a per-epoch quota the chain set.  A usage
transaction that exceeds either is not "flagged"; it is **invalid**, and an
invalid transaction never enters a block.

Design constraints:

*   Deterministic.  Same genesis plus same transactions gives the same
    ``state_root`` on every node, so all arithmetic is integer arithmetic and
    every iteration over a mapping is sorted.
*   Conservative.  Credits are never conjured.  Genesis fixes the supply, a
    grant moves credits between accounts, and usage *burns* them.  The
    invariant ``circulating + burned == initial_supply`` is checkable at any
    height, and :meth:`WorldState.check_invariants` does exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .crypto import canonical_bytes, sha256_hex
from .resources import DEFAULT_PRICES, PriceTable, ResourceKind
from .transactions import Transaction, TxType

__all__ = ["Account", "WorldState", "StateError", "Receipt", "GenesisConfig"]


class StateError(Exception):
    """A transaction is well-formed but not applicable to this state."""


@dataclass
class Account:
    """One identity on the chain."""

    address: str
    role: str = "agent"
    label: str = ""
    balance: int = 0           # microcredits available to spend
    nonce: int = 0             # next expected transaction nonce
    burned: int = 0            # microcredits this account has consumed
    epoch: int = 0             # epoch the quota counters below refer to
    epoch_usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    registered: bool = False   # False while the account exists only to hold credits

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "role": self.role,
            "label": self.label,
            "balance": self.balance,
            "nonce": self.nonce,
            "burned": self.burned,
            "epoch": self.epoch,
            "epoch_usage": dict(sorted(self.epoch_usage.items())),
            "metadata": self.metadata,
            "registered": self.registered,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Account":
        return cls(
            address=data["address"],
            role=data.get("role", "agent"),
            label=data.get("label", ""),
            balance=data.get("balance", 0),
            nonce=data.get("nonce", 0),
            burned=data.get("burned", 0),
            epoch=data.get("epoch", 0),
            epoch_usage=dict(data.get("epoch_usage", {})),
            metadata=dict(data.get("metadata", {})),
            registered=data.get("registered", False),
        )

    def copy(self) -> "Account":
        return Account.from_dict(self.to_dict())


@dataclass(frozen=True)
class Receipt:
    """What applying a transaction actually did."""

    txid: str
    type: str
    sender: str
    charged: int = 0
    resource: str | None = None
    amount: int = 0
    balance_after: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "txid": self.txid,
            "type": self.type,
            "sender": self.sender,
            "charged": self.charged,
            "resource": self.resource,
            "amount": self.amount,
            "balance_after": self.balance_after,
            "note": self.note,
        }


@dataclass
class GenesisConfig:
    """The only moment authorities and initial supply are declared.

    Roles other than ``agent`` exist only here.  A later transaction can never
    promote an account to authority or validator, which keeps the trusted set
    auditable: read the genesis block and you know who can grant credits and
    set prices, forever.
    """

    authorities: Mapping[str, int]                     # address -> initial treasury
    validators: Iterable[str] = ()
    prices: PriceTable = DEFAULT_PRICES
    limits: Mapping[str, int] = field(default_factory=dict)
    epoch_length: int = 100                            # blocks per quota window
    chain_id: str = "chainmind-dev"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "authorities": dict(sorted(self.authorities.items())),
            "validators": sorted(self.validators),
            "prices": self.prices.to_dict(),
            # Keys are normalised to their wire strings rather than left as
            # whatever the caller passed (an enum member or a str), so the
            # genesis commitment does not depend on a serialiser's choices.
            "limits": {
                k: int(v) for k, v in sorted(
                    (ResourceKind.parse(rk.value if isinstance(rk, ResourceKind) else rk).value, rv)
                    for rk, rv in dict(self.limits).items()
                )
            },
            "epoch_length": self.epoch_length,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GenesisConfig":
        return cls(
            authorities=dict(data["authorities"]),
            validators=list(data.get("validators", [])),
            prices=PriceTable.from_dict(data.get("prices", DEFAULT_PRICES.to_dict())),
            limits=dict(data.get("limits", {})),
            epoch_length=data.get("epoch_length", 100),
            chain_id=data.get("chain_id", "chainmind-dev"),
            note=data.get("note", ""),
        )


class WorldState:
    """The replicated state machine."""

    def __init__(self, genesis: GenesisConfig) -> None:
        self.genesis = genesis
        self.chain_id = genesis.chain_id
        self.epoch_length = max(1, int(genesis.epoch_length))
        self.prices = genesis.prices
        self.limits: dict[str, int] = {
            ResourceKind.parse(k).value: int(v) for k, v in genesis.limits.items()
        }
        self.accounts: dict[str, Account] = {}
        self.burned_total = 0
        self.height = 0

        for address, treasury in sorted(genesis.authorities.items()):
            if treasury < 0:
                raise ValueError("a genesis treasury must not be negative")
            self.accounts[address] = Account(
                address=address, role="authority", label="genesis-authority",
                balance=int(treasury), registered=True,
            )
        for address in sorted(set(genesis.validators)):
            if address in self.accounts:
                continue
            self.accounts[address] = Account(
                address=address, role="validator", label="genesis-validator", registered=True
            )

        self.initial_supply = sum(a.balance for a in self.accounts.values())

    # -- queries -----------------------------------------------------------

    def get(self, address: str) -> Account | None:
        return self.accounts.get(address)

    def require(self, address: str) -> Account:
        account = self.accounts.get(address)
        if account is None:
            raise StateError(f"unknown account {address[:12]}...")
        return account

    def balance_of(self, address: str) -> int:
        account = self.accounts.get(address)
        return account.balance if account else 0

    def is_authority(self, address: str) -> bool:
        account = self.accounts.get(address)
        return account is not None and account.role == "authority"

    def epoch_at(self, height: int) -> int:
        return height // self.epoch_length

    def quota_remaining(self, address: str, kind: ResourceKind | str, height: int | None = None) -> int | None:
        """Units of ``kind`` still allowed this epoch, or ``None`` if uncapped."""
        resolved = ResourceKind.parse(kind.value if isinstance(kind, ResourceKind) else kind)
        limit = self.limits.get(resolved.value)
        if limit is None:
            return None
        account = self.accounts.get(address)
        if account is None:
            return limit
        epoch = self.epoch_at(self.height if height is None else height)
        used = account.epoch_usage.get(resolved.value, 0) if account.epoch == epoch else 0
        return max(0, limit - used)

    def affordable_units(self, address: str, kind: ResourceKind | str,
                         height: int | None = None) -> int:
        """How many units of ``kind`` this account may still consume.

        The answer folds together the two independent ceilings -- the credit
        balance and the epoch quota -- so callers (notably the agent kernel)
        can ask one question before deciding whether an action is possible.
        """
        resolved = ResourceKind.parse(kind.value if isinstance(kind, ResourceKind) else kind)
        price = self.prices.prices[resolved.value]
        quota = self.quota_remaining(address, resolved, height)

        if price == 0:
            # A free resource is bounded only by its quota; without one it is
            # genuinely unlimited, which we report as -1 rather than a made-up
            # ceiling the caller might mistake for a real number.
            return -1 if quota is None else quota

        by_credit = self.balance_of(address) // price
        return by_credit if quota is None else min(by_credit, quota)

    # -- mutation ----------------------------------------------------------

    def apply(self, tx: Transaction, height: int | None = None) -> Receipt:
        """Apply one transaction or raise :class:`StateError`.

        The caller is responsible for rolling back on failure; block
        application does that by working on a copy of the state.
        """
        at_height = self.height if height is None else height
        tx.validate()  # structural checks; cheap and refuses unsigned input

        if tx.type is TxType.REGISTER:
            return self._apply_register(tx)

        account = self.require(tx.sender)
        if tx.nonce != account.nonce:
            raise StateError(
                f"nonce mismatch for {tx.sender[:12]}...: expected {account.nonce}, got {tx.nonce}"
            )

        handler = {
            TxType.GRANT: self._apply_grant,
            TxType.USAGE: self._apply_usage,
            TxType.POLICY: self._apply_policy,
            TxType.ATTEST: self._apply_attest,
        }[tx.type]
        receipt = handler(tx, account, at_height)
        account.nonce += 1
        return receipt

    def _apply_register(self, tx: Transaction) -> Receipt:
        """Claim an identity.

        An account may already exist without ever having registered, because
        a grant creates one for any well-formed address (see
        :meth:`_apply_grant`).  Registration then *claims* that account and
        attaches a label to it.  Only the holder of the key can do so, which
        is why this is the one transaction type whose sender need not exist
        yet -- the signature is the proof of ownership.
        """
        existing = self.accounts.get(tx.sender)
        if existing is not None and existing.registered:
            raise StateError("account is already registered")
        if tx.nonce != 0:
            raise StateError("a registration must use nonce 0")
        if existing is not None and existing.nonce != 0:
            raise StateError("this account has already sent transactions")

        role = tx.body["role"]
        if role != "agent":
            raise StateError(
                f"role {role!r} can only be established in the genesis block; "
                "self-registration is limited to agents"
            )

        account = existing or Account(address=tx.sender, role="agent")
        account.label = tx.body.get("label", "")
        account.metadata = dict(tx.body.get("metadata", {}))
        account.registered = True
        account.nonce = 1  # the registration itself consumed nonce 0
        self.accounts[tx.sender] = account
        return Receipt(
            txid=tx.txid, type=tx.type.value, sender=tx.sender, balance_after=account.balance,
            note=f"registered agent {account.label or tx.sender[:12]}",
        )

    def _apply_grant(self, tx: Transaction, account: Account, height: int) -> Receipt:
        if account.role != "authority":
            raise StateError("only a genesis authority may grant credits")
        beneficiary_address = tx.body["beneficiary"]
        amount = tx.body["amount"]
        if beneficiary_address == tx.sender:
            raise StateError("an authority cannot grant credits to itself")
        beneficiary = self.accounts.get(beneficiary_address)
        if beneficiary is None:
            # Funding an address brings it into existence, unregistered.  This
            # avoids a deadlock where an agent cannot register because it has
            # no credits and cannot be funded because it has not registered.
            beneficiary = Account(address=beneficiary_address, role="agent")
            self.accounts[beneficiary_address] = beneficiary
        if account.balance < amount:
            raise StateError(
                f"treasury too small: holds {account.balance} mc, tried to grant {amount} mc"
            )
        account.balance -= amount
        beneficiary.balance += amount
        return Receipt(
            txid=tx.txid, type=tx.type.value, sender=tx.sender, charged=amount,
            balance_after=account.balance,
            note=f"granted {amount} mc to {beneficiary_address[:12]}",
        )

    def _apply_usage(self, tx: Transaction, account: Account, height: int) -> Receipt:
        resource = ResourceKind.parse(tx.body["resource"]).value
        amount = tx.body["amount"]
        cost = self.prices.cost(resource, amount)

        if account.balance < cost:
            raise StateError(
                f"insufficient credits: {resource} x{amount} costs {cost} mc, "
                f"balance is {account.balance} mc"
            )

        epoch = self.epoch_at(height)
        if account.epoch != epoch:
            account.epoch = epoch
            account.epoch_usage = {}

        limit = self.limits.get(resource)
        if limit is not None:
            used = account.epoch_usage.get(resource, 0)
            if used + amount > limit:
                raise StateError(
                    f"quota exceeded for {resource}: {used} + {amount} > {limit} per epoch"
                )

        account.balance -= cost
        account.burned += cost
        self.burned_total += cost
        account.epoch_usage[resource] = account.epoch_usage.get(resource, 0) + amount
        return Receipt(
            txid=tx.txid, type=tx.type.value, sender=tx.sender, charged=cost,
            resource=resource, amount=amount, balance_after=account.balance,
            note=tx.body.get("tool", "") or tx.body.get("note", ""),
        )

    def _apply_policy(self, tx: Transaction, account: Account, height: int) -> Receipt:
        if account.role != "authority":
            raise StateError("only a genesis authority may change policy")
        if "prices" in tx.body:
            self.prices = self.prices.replace(tx.body["prices"])
        if "limits" in tx.body:
            for resource, value in tx.body["limits"].items():
                self.limits[ResourceKind.parse(resource).value] = int(value)
        return Receipt(
            txid=tx.txid, type=tx.type.value, sender=tx.sender,
            balance_after=account.balance, note=tx.body.get("memo", "policy updated"),
        )

    def _apply_attest(self, tx: Transaction, account: Account, height: int) -> Receipt:
        # Attestations are free on purpose: the chain should never make an
        # agent choose between paying and being transparent about what it did.
        return Receipt(
            txid=tx.txid, type=tx.type.value, sender=tx.sender,
            balance_after=account.balance, note=tx.body["topic"],
        )

    # -- integrity ---------------------------------------------------------

    def state_root(self) -> str:
        """A single hash committing to the entire state."""
        return sha256_hex(canonical_bytes(self.snapshot()))

    def snapshot(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "height": self.height,
            "accounts": [self.accounts[a].to_dict() for a in sorted(self.accounts)],
            "prices": self.prices.to_dict(),
            "limits": {k: v for k, v in sorted(self.limits.items())},
            "epoch_length": self.epoch_length,
            "burned_total": self.burned_total,
            "initial_supply": self.initial_supply,
        }

    def check_invariants(self) -> None:
        """Raise if the ledger has lost track of credits."""
        circulating = sum(a.balance for a in self.accounts.values())
        if circulating + self.burned_total != self.initial_supply:
            raise StateError(
                "supply invariant violated: "
                f"{circulating} circulating + {self.burned_total} burned "
                f"!= {self.initial_supply} initial"
            )
        if any(a.balance < 0 for a in self.accounts.values()):
            raise StateError("an account holds a negative balance")

    def copy(self) -> "WorldState":
        """A deep-enough copy for speculative block execution."""
        clone = WorldState.__new__(WorldState)
        clone.genesis = self.genesis
        clone.chain_id = self.chain_id
        clone.epoch_length = self.epoch_length
        clone.prices = PriceTable.from_dict(self.prices.to_dict())
        clone.limits = dict(self.limits)
        clone.accounts = {address: account.copy() for address, account in self.accounts.items()}
        clone.burned_total = self.burned_total
        clone.height = self.height
        clone.initial_supply = self.initial_supply
        return clone
