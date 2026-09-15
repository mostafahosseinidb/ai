import unittest

from chainmind.crypto import SigningKey, sha256_hex
from chainmind.transactions import (
    InvalidTransaction,
    Transaction,
    TxType,
    build_attestation,
    build_grant,
    build_policy_update,
    build_registration,
    build_usage,
)

KEY = SigningKey.from_passphrase("tx-tests")
OTHER = SigningKey.from_passphrase("tx-tests-other")
EVIDENCE = sha256_hex("evidence")


class SigningTests(unittest.TestCase):
    def test_a_built_transaction_verifies(self):
        tx = build_usage(KEY, 0, resource="compute_ms", amount=120, evidence=EVIDENCE)
        self.assertTrue(tx.verify())
        tx.validate()

    def test_txid_is_stable_across_a_round_trip(self):
        tx = build_grant(KEY, 3, beneficiary=OTHER.public_hex(), amount=500)
        self.assertEqual(Transaction.from_dict(tx.to_dict()).txid, tx.txid)

    def test_signing_with_the_wrong_key_is_refused(self):
        tx = Transaction(type=TxType.ATTEST, sender=KEY.public_hex(), nonce=0,
                         body={"topic": "t", "digest": EVIDENCE}, timestamp=1)
        with self.assertRaises(InvalidTransaction):
            tx.sign(OTHER)

    def test_editing_the_body_breaks_the_signature(self):
        tx = build_usage(KEY, 0, resource="compute_ms", amount=120, evidence=EVIDENCE)
        forged = Transaction(type=tx.type, sender=tx.sender, nonce=tx.nonce,
                             body={**tx.body, "amount": 1}, timestamp=tx.timestamp,
                             signature=tx.signature)
        self.assertFalse(forged.verify())
        with self.assertRaises(InvalidTransaction):
            forged.validate()

    def test_a_declared_txid_must_match(self):
        tx = build_usage(KEY, 0, resource="compute_ms", amount=5, evidence=EVIDENCE)
        payload = tx.to_dict()
        payload["body"] = {**payload["body"], "amount": 6}
        with self.assertRaises(InvalidTransaction):
            Transaction.from_dict(payload)

    def test_an_unsigned_transaction_never_validates(self):
        tx = Transaction(type=TxType.ATTEST, sender=KEY.public_hex(), nonce=0,
                         body={"topic": "t", "digest": EVIDENCE}, timestamp=1)
        self.assertFalse(tx.verify())
        with self.assertRaises(InvalidTransaction):
            tx.validate()


class BodyValidationTests(unittest.TestCase):
    def test_usage_amount_must_be_a_positive_integer(self):
        for amount in (0, -1, 1.5, True):
            with self.subTest(amount=amount), self.assertRaises((InvalidTransaction, TypeError)):
                build_usage(KEY, 0, resource="compute_ms", amount=amount, evidence=EVIDENCE)

    def test_usage_resource_must_be_known(self):
        with self.assertRaises(ValueError):
            build_usage(KEY, 0, resource="gpu_hours", amount=1, evidence=EVIDENCE)

    def test_evidence_must_be_a_digest(self):
        with self.assertRaises(InvalidTransaction):
            build_usage(KEY, 0, resource="compute_ms", amount=1, evidence="short")

    def test_registration_role_is_constrained(self):
        with self.assertRaises(InvalidTransaction):
            build_registration(KEY, 0, role="overlord")

    def test_grant_beneficiary_must_be_an_address(self):
        with self.assertRaises(InvalidTransaction):
            build_grant(KEY, 0, beneficiary="not-an-address", amount=1)

    def test_policy_must_change_something(self):
        with self.assertRaises(InvalidTransaction):
            build_policy_update(KEY, 0, memo="no-op")

    def test_policy_values_must_be_non_negative_integers(self):
        with self.assertRaises(InvalidTransaction):
            build_policy_update(KEY, 0, prices={"compute_ms": -1})

    def test_unexpected_body_fields_are_refused(self):
        tx = Transaction(type=TxType.ATTEST, sender=KEY.public_hex(), nonce=0,
                         body={"topic": "t", "digest": EVIDENCE, "extra": 1},
                         timestamp=1).sign(KEY)
        with self.assertRaises(InvalidTransaction):
            tx.validate()

    def test_attestation_round_trips(self):
        tx = build_attestation(KEY, 1, topic="goal", digest=EVIDENCE, summary="stay solvent")
        tx.validate()
        self.assertEqual(Transaction.from_dict(tx.to_dict()).body["topic"], "goal")


if __name__ == "__main__":
    unittest.main()
