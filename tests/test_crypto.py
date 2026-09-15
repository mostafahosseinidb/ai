import unittest

from chainmind.crypto import (
    BACKEND,
    SigningKey,
    canonical_json,
    merkle_proof,
    merkle_root,
    sha256_hex,
    verify_merkle_proof,
    verify_signature,
)

# RFC 8032, section 7.1.  If the implementation ever stops matching these,
# signatures produced here are not Ed25519 signatures.
RFC_8032_VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a3"
        "3bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15"
        "996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


class Ed25519Tests(unittest.TestCase):
    def test_matches_rfc_8032_vectors(self):
        for seed, public, message, signature in RFC_8032_VECTORS:
            with self.subTest(seed=seed[:8], backend=BACKEND):
                key = SigningKey.from_hex(seed)
                payload = bytes.fromhex(message)
                self.assertEqual(key.public_hex(), public)
                self.assertEqual(key.sign(payload), signature)
                self.assertTrue(verify_signature(public, payload, signature))

    def test_rejects_a_tampered_message(self):
        key = SigningKey.from_passphrase("crypto")
        signature = key.sign(b"the original")
        self.assertFalse(key.verifying_key.verify(b"the forgery", signature))

    def test_rejects_malformed_signatures(self):
        key = SigningKey.from_passphrase("crypto")
        self.assertFalse(verify_signature(key.public_hex(), b"x", "not hex"))
        self.assertFalse(verify_signature(key.public_hex(), b"x", "ab" * 32))

    def test_signing_is_deterministic(self):
        key = SigningKey.from_passphrase("crypto")
        self.assertEqual(key.sign(b"same input"), key.sign(b"same input"))

    def test_seeds_must_be_32_bytes(self):
        with self.assertRaises(ValueError):
            SigningKey(b"too short")


class CanonicalEncodingTests(unittest.TestCase):
    def test_key_order_does_not_change_the_encoding(self):
        self.assertEqual(
            canonical_json({"b": 1, "a": 2}), canonical_json({"a": 2, "b": 1})
        )

    def test_encoding_is_compact(self):
        self.assertEqual(canonical_json({"a": [1, 2]}), '{"a":[1,2]}')

    def test_nan_is_refused(self):
        with self.assertRaises(ValueError):
            canonical_json({"x": float("nan")})


class MerkleTests(unittest.TestCase):
    def leaves(self, count):
        return [sha256_hex(f"leaf-{i}") for i in range(count)]

    def test_empty_tree_has_a_defined_root(self):
        self.assertEqual(merkle_root([]), "0" * 64)

    def test_root_changes_when_a_leaf_changes(self):
        leaves = self.leaves(5)
        original = merkle_root(leaves)
        leaves[2] = sha256_hex("tampered")
        self.assertNotEqual(merkle_root(leaves), original)

    def test_order_matters(self):
        leaves = self.leaves(4)
        self.assertNotEqual(merkle_root(leaves), merkle_root(list(reversed(leaves))))

    def test_proofs_verify_for_every_leaf(self):
        for size in (1, 2, 3, 5, 8, 9):
            leaves = self.leaves(size)
            root = merkle_root(leaves)
            for index, leaf in enumerate(leaves):
                with self.subTest(size=size, index=index):
                    self.assertTrue(verify_merkle_proof(leaf, merkle_proof(leaves, index), root))

    def test_a_proof_for_the_wrong_leaf_fails(self):
        leaves = self.leaves(6)
        root = merkle_root(leaves)
        self.assertFalse(verify_merkle_proof(leaves[0], merkle_proof(leaves, 3), root))

    def test_duplicating_the_last_leaf_does_not_collide(self):
        # Internal nodes are domain-separated from leaves, so an odd tree and
        # the same tree with its tail repeated must not share a root.
        leaves = self.leaves(3)
        self.assertNotEqual(merkle_root(leaves), merkle_root(leaves + [leaves[-1]]))


if __name__ == "__main__":
    unittest.main()
