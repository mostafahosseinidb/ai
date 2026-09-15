import unittest

from chainmind.crypto import sha256_hex
from chainmind.resources import CREDIT, DEFAULT_PRICES
from chainmind.state import StateError, WorldState
from chainmind.transactions import (
    build_attestation,
    build_grant,
    build_policy_update,
    build_registration,
    build_usage,
)

from support import AGENT, AUTHORITY, OUTSIDER, make_genesis

EVIDENCE = sha256_hex("state-tests")


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self.state = WorldState(make_genesis(limits={"tool_call": 3}, epoch_length=10))
        self.authority = AUTHORITY.public_hex()
        self.agent = AGENT.public_hex()

    def register_agent(self):
        self.state.apply(build_registration(AGENT, 0, role="agent", label="atlas"))

    def fund(self, amount=1_000_000, nonce=None):
        nonce = self.state.get(self.authority).nonce if nonce is None else nonce
        self.state.apply(build_grant(AUTHORITY, nonce, beneficiary=self.agent, amount=amount))

    def spend(self, resource="compute_ms", amount=10, nonce=None, height=0):
        nonce = self.state.get(self.agent).nonce if nonce is None else nonce
        return self.state.apply(
            build_usage(AGENT, nonce, resource=resource, amount=amount, evidence=EVIDENCE), height
        )


class RegistrationTests(StateTestCase):
    def test_an_agent_can_register_itself(self):
        self.register_agent()
        account = self.state.get(self.agent)
        self.assertEqual(account.label, "atlas")
        self.assertTrue(account.registered)
        self.assertEqual(account.balance, 0)

    def test_registering_twice_is_refused(self):
        self.register_agent()
        with self.assertRaises(StateError):
            self.state.apply(build_registration(AGENT, 0, role="agent"))

    def test_registration_must_use_nonce_zero(self):
        with self.assertRaises(StateError):
            self.state.apply(build_registration(AGENT, 7, role="agent"))

    def test_nobody_can_promote_themselves_to_authority(self):
        with self.assertRaises(StateError):
            self.state.apply(build_registration(OUTSIDER, 0, role="authority"))

    def test_a_grant_creates_an_unregistered_account_that_can_be_claimed(self):
        self.fund(50_000)
        account = self.state.get(self.agent)
        self.assertFalse(account.registered)
        self.assertEqual(account.balance, 50_000)
        self.register_agent()
        self.assertTrue(self.state.get(self.agent).registered)
        self.assertEqual(self.state.get(self.agent).balance, 50_000)


class GrantTests(StateTestCase):
    def test_only_an_authority_may_grant(self):
        self.register_agent()
        self.fund(100_000)
        with self.assertRaises(StateError):
            self.state.apply(
                build_grant(AGENT, self.state.get(self.agent).nonce,
                            beneficiary=OUTSIDER.public_hex(), amount=1)
            )

    def test_a_grant_cannot_exceed_the_treasury(self):
        with self.assertRaises(StateError):
            self.fund(10_000 * CREDIT)

    def test_a_grant_moves_credits_rather_than_creating_them(self):
        before = self.state.balance_of(self.authority)
        self.fund(250_000)
        self.assertEqual(self.state.balance_of(self.authority), before - 250_000)
        self.assertEqual(self.state.balance_of(self.agent), 250_000)
        self.state.check_invariants()

    def test_an_authority_cannot_grant_to_itself(self):
        with self.assertRaises(StateError):
            self.state.apply(
                build_grant(AUTHORITY, self.state.get(self.authority).nonce,
                            beneficiary=self.authority, amount=1)
            )


class UsageTests(StateTestCase):
    def setUp(self):
        super().setUp()
        self.register_agent()
        self.fund(1_000_000)

    def test_usage_burns_exactly_the_quoted_price(self):
        expected = DEFAULT_PRICES.cost("compute_ms", 40)
        before = self.state.balance_of(self.agent)
        receipt = self.spend("compute_ms", 40)
        self.assertEqual(receipt.charged, expected)
        self.assertEqual(self.state.balance_of(self.agent), before - expected)
        self.assertEqual(self.state.burned_total, expected)
        self.state.check_invariants()

    def test_usage_beyond_the_balance_is_refused(self):
        with self.assertRaises(StateError) as ctx:
            self.spend("llm_output_tokens", 10 ** 9)
        self.assertIn("insufficient credits", str(ctx.exception))

    def test_a_refused_usage_changes_nothing(self):
        before = self.state.snapshot()
        with self.assertRaises(StateError):
            self.spend("llm_output_tokens", 10 ** 9)
        self.assertEqual(self.state.snapshot(), before)

    def test_the_epoch_quota_caps_usage_even_with_credits_to_spare(self):
        for i in range(3):
            self.spend("tool_call", 1)
        self.assertEqual(self.state.quota_remaining(self.agent, "tool_call"), 0)
        with self.assertRaises(StateError) as ctx:
            self.spend("tool_call", 1)
        self.assertIn("quota exceeded", str(ctx.exception))
        self.assertGreater(self.state.balance_of(self.agent), 0)

    def test_the_quota_resets_in_the_next_epoch(self):
        for _ in range(3):
            self.spend("tool_call", 1, height=0)
        receipt = self.spend("tool_call", 1, height=10)   # epoch_length is 10
        self.assertEqual(receipt.amount, 1)
        self.assertEqual(self.state.quota_remaining(self.agent, "tool_call", 10), 2)

    def test_nonces_must_not_be_replayed(self):
        nonce = self.state.get(self.agent).nonce
        self.spend("compute_ms", 1, nonce=nonce)
        with self.assertRaises(StateError) as ctx:
            self.spend("compute_ms", 1, nonce=nonce)
        self.assertIn("nonce mismatch", str(ctx.exception))

    def test_an_unknown_account_cannot_spend(self):
        with self.assertRaises(StateError):
            self.state.apply(
                build_usage(OUTSIDER, 0, resource="compute_ms", amount=1, evidence=EVIDENCE)
            )

    def test_affordable_units_respects_both_ceilings(self):
        self.assertEqual(self.state.affordable_units(self.agent, "tool_call"), 3)  # quota binds
        self.assertEqual(
            self.state.affordable_units(self.agent, "compute_ms"),
            self.state.balance_of(self.agent) // DEFAULT_PRICES.prices["compute_ms"],
        )

    def test_attestations_are_free(self):
        before = self.state.balance_of(self.agent)
        self.state.apply(
            build_attestation(AGENT, self.state.get(self.agent).nonce,
                              topic="decision", digest=EVIDENCE)
        )
        self.assertEqual(self.state.balance_of(self.agent), before)


class PolicyTests(StateTestCase):
    def setUp(self):
        super().setUp()
        self.register_agent()
        self.fund(1_000_000)

    def test_an_authority_can_reprice_a_resource(self):
        self.state.apply(
            build_policy_update(AUTHORITY, self.state.get(self.authority).nonce,
                                prices={"compute_ms": 100})
        )
        self.assertEqual(self.state.prices.prices["compute_ms"], 100)
        self.assertEqual(self.spend("compute_ms", 10).charged, 1000)

    def test_an_agent_cannot_change_policy(self):
        with self.assertRaises(StateError):
            self.state.apply(
                build_policy_update(AGENT, self.state.get(self.agent).nonce,
                                    limits={"tool_call": 10 ** 9})
            )

    def test_tightening_a_quota_takes_effect_immediately(self):
        self.state.apply(
            build_policy_update(AUTHORITY, self.state.get(self.authority).nonce,
                                limits={"compute_ms": 5})
        )
        self.spend("compute_ms", 5)
        with self.assertRaises(StateError):
            self.spend("compute_ms", 1)


class IntegrityTests(StateTestCase):
    def test_independent_transactions_commute(self):
        # Registering then funding must land on exactly the same state as
        # funding then registering: the root commits to balances and nonces,
        # not to the order two unrelated senders happened to arrive in.
        self.register_agent()
        self.fund(500_000)
        root = self.state.state_root()

        twin = WorldState(make_genesis(limits={"tool_call": 3}, epoch_length=10))
        twin.apply(build_grant(AUTHORITY, 0, beneficiary=self.agent, amount=500_000))
        twin.apply(build_registration(AGENT, 0, role="agent", label="atlas"))
        self.assertEqual(twin.state_root(), root)

    def test_the_state_root_tracks_every_spend(self):
        self.register_agent()
        self.fund(500_000)
        roots = {self.state.state_root()}
        for _ in range(5):
            self.spend("compute_ms", 1)
            roots.add(self.state.state_root())
        self.assertEqual(len(roots), 6)

    def test_a_copy_is_independent(self):
        self.register_agent()
        self.fund(500_000)
        clone = self.state.copy()
        self.spend("compute_ms", 10)
        self.assertNotEqual(clone.state_root(), self.state.state_root())
        self.assertEqual(clone.balance_of(self.agent), 500_000)

    def test_supply_invariant_holds_through_a_busy_run(self):
        self.register_agent()
        self.fund(1_000_000)
        for _ in range(20):
            self.spend("compute_ms", 3)
        self.state.check_invariants()
        circulating = sum(a.balance for a in self.state.accounts.values())
        self.assertEqual(circulating + self.state.burned_total, self.state.initial_supply)


if __name__ == "__main__":
    unittest.main()


class GenesisSerialisationTests(unittest.TestCase):
    def test_limit_keys_normalise_regardless_of_how_they_were_written(self):
        from chainmind.chain import Chain
        from chainmind.resources import ResourceKind

        enum_keyed = make_genesis(limits={ResourceKind.TOOL_CALL: 2})
        string_keyed = make_genesis(limits={"tool_call": 2})
        self.assertEqual(enum_keyed.to_dict(), string_keyed.to_dict())
        self.assertEqual(
            Chain.genesis_commitment(enum_keyed), Chain.genesis_commitment(string_keyed)
        )
        self.assertEqual(list(enum_keyed.to_dict()["limits"]), ["tool_call"])

    def test_an_unknown_limit_key_is_refused_at_serialisation(self):
        with self.assertRaises(ValueError):
            make_genesis(limits={"gpu_hours": 1}).to_dict()
