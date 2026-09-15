import json
import tempfile
import unittest
from pathlib import Path

from chainmind.block import Block, BlockHeader
from chainmind.chain import Chain, ChainError, LedgerBusy
from chainmind.consensus import ConsensusError, ProofOfAuthority, ProofOfWork
from chainmind.crypto import SigningKey, merkle_root, sha256_hex
from chainmind.mempool import Mempool
from chainmind.resources import CREDIT
from chainmind.transactions import InvalidTransaction, build_grant, build_registration, build_usage

from support import AGENT, AUTHORITY, OUTSIDER, make_chain, make_genesis

EVIDENCE = sha256_hex("chain-tests")


class ChainTestCase(unittest.TestCase):
    def setUp(self):
        self.chain = make_chain()
        self.agent = AGENT.public_hex()

    def fund(self, amount=1_000_000):
        nonce = self.chain.state.get(AUTHORITY.public_hex()).nonce
        self.chain.seal_block(
            AUTHORITY, [build_grant(AUTHORITY, nonce, beneficiary=self.agent, amount=amount)]
        )

    def usage(self, amount=10, nonce=None, resource="compute_ms"):
        nonce = self.chain.state.get(self.agent).nonce if nonce is None else nonce
        return build_usage(AGENT, nonce, resource=resource, amount=amount, evidence=EVIDENCE)


class GenesisTests(ChainTestCase):
    def test_the_genesis_block_commits_to_its_configuration(self):
        genesis_block = self.chain.block_at(0)
        self.assertEqual(
            genesis_block.header.prev_hash, Chain.genesis_commitment(self.chain.genesis_config)
        )

    def test_a_different_configuration_yields_a_different_commitment(self):
        other = make_genesis(chain_id="other-net")
        self.assertNotEqual(
            Chain.genesis_commitment(other), Chain.genesis_commitment(self.chain.genesis_config)
        )

    def test_the_genesis_state_root_matches_the_configuration(self):
        self.assertEqual(self.chain.block_at(0).header.state_root, self.chain.verify().state_root())


class BlockProductionTests(ChainTestCase):
    def test_sealing_advances_the_height(self):
        self.fund()
        self.assertEqual(self.chain.height, 1)
        self.chain.seal_block(AUTHORITY, [])
        self.assertEqual(self.chain.height, 2)

    def test_an_inapplicable_transaction_is_dropped_not_fatal(self):
        self.fund(50_000)
        good = build_registration(AGENT, 0, role="agent", label="atlas")
        bad = build_usage(OUTSIDER, 0, resource="compute_ms", amount=1, evidence=EVIDENCE)
        result = self.chain.seal_block(AUTHORITY, [good, bad])
        self.assertEqual(result.accepted, 1)
        self.assertEqual(len(result.rejected), 1)
        self.assertIn("unknown account", result.rejected[0][1])
        self.assertEqual(len(result.block.transactions), 1)

    def test_an_over_budget_usage_never_reaches_a_block(self):
        self.fund(10_000)
        result = self.chain.seal_block(AUTHORITY, [self.usage(amount=10 ** 9)])
        self.assertEqual(result.accepted, 0)
        self.assertIn("insufficient credits", result.rejected[0][1])
        self.assertEqual(self.chain.state.balance_of(self.agent), 10_000)

    def test_blocks_link_to_their_parent(self):
        self.fund()
        for _ in range(3):
            self.chain.seal_block(AUTHORITY, [])
        for height in range(1, self.chain.height + 1):
            self.assertEqual(
                self.chain.block_at(height).header.prev_hash, self.chain.block_at(height - 1).hash
            )


class BlockValidationTests(ChainTestCase):
    def test_a_block_whose_state_root_lies_is_refused(self):
        self.fund()
        result = self.chain.propose(AUTHORITY, [self.usage()])
        forged_header = BlockHeader(
            **{**result.block.header.to_dict(), "state_root": sha256_hex("a convenient fiction")}
        )
        forged = Block(forged_header, result.block.transactions).sign(AUTHORITY)
        with self.assertRaises(ChainError) as ctx:
            self.chain.append(forged)
        self.assertIn("state root mismatch", str(ctx.exception))

    def test_a_block_whose_merkle_root_lies_is_refused(self):
        self.fund()
        result = self.chain.propose(AUTHORITY, [self.usage()])
        forged_header = BlockHeader(
            **{**result.block.header.to_dict(), "merkle_root": merkle_root([sha256_hex("nope")])}
        )
        forged = Block(forged_header, result.block.transactions).sign(AUTHORITY)
        with self.assertRaises(ChainError):
            self.chain.append(forged)

    def test_an_unauthorised_proposer_is_refused(self):
        result = self.chain.propose(AUTHORITY, [])
        stranger_header = BlockHeader(
            **{**result.block.header.to_dict(), "proposer": OUTSIDER.public_hex()}
        )
        stranger = Block(stranger_header, ()).sign(OUTSIDER)
        with self.assertRaises(ChainError) as ctx:
            self.chain.append(stranger)
        self.assertIn("not an authorised validator", str(ctx.exception))

    def test_an_unsealed_block_is_refused(self):
        result = self.chain.propose(AUTHORITY, [])
        with self.assertRaises(ChainError):
            self.chain.append(Block(result.block.header, ()))

    def test_a_block_cannot_skip_a_height(self):
        result = self.chain.propose(AUTHORITY, [])
        skipped = Block(
            BlockHeader(**{**result.block.header.to_dict(), "height": 5}), ()
        ).sign(AUTHORITY)
        with self.assertRaises(ChainError):
            self.chain.append(skipped)


class ConsensusEngineTests(unittest.TestCase):
    def test_proof_of_work_seals_to_the_difficulty(self):
        genesis = make_genesis(chain_id="pow-net")
        chain = Chain.create(genesis, AUTHORITY, ProofOfWork(2), timestamp=1_700_000_000)
        self.assertTrue(chain.tip.hash.startswith("00"))
        chain.seal_block(AUTHORITY, [])
        self.assertTrue(chain.tip.hash.startswith("00"))
        chain.verify()

    def test_strict_rotation_rejects_an_out_of_turn_proposer(self):
        second = SigningKey.from_passphrase("second-validator")
        engine = ProofOfAuthority(
            [AUTHORITY.public_hex(), second.public_hex()], strict_rotation=True
        )
        by_address = {AUTHORITY.public_hex(): AUTHORITY, second.public_hex(): second}
        # Height 0 belongs to one validator, so the same key is the wrong one
        # for height 1 -- which is exactly what strict rotation must catch.
        sealer_for_height_0 = by_address[engine.expected_proposer(0)]
        genesis = make_genesis(validators=[second.public_hex()])
        chain = Chain.create(genesis, sealer_for_height_0, engine, timestamp=1_700_000_000)
        wrong = sealer_for_height_0
        result = chain.propose(wrong, [])
        with self.assertRaises(ChainError):
            chain.append(result.block)

    def test_a_key_outside_the_validator_set_cannot_seal(self):
        engine = ProofOfAuthority([AUTHORITY.public_hex()])
        with self.assertRaises(ConsensusError):
            engine.seal(make_chain().propose(AUTHORITY, []).block, OUTSIDER)


class PersistenceTests(ChainTestCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "ledger.jsonl"
        self.chain = Chain.create(
            make_genesis(), AUTHORITY, ProofOfAuthority([AUTHORITY.public_hex()]),
            path=self.path, timestamp=1_700_000_000,
        )

    def populate(self):
        self.fund(500_000)
        self.chain.seal_block(AUTHORITY, [build_registration(AGENT, 0, role="agent", label="atlas")])
        self.chain.seal_block(AUTHORITY, [self.usage(amount=25)])

    def test_a_reloaded_chain_reproduces_the_state(self):
        self.populate()
        reloaded = Chain.load(self.path)
        self.assertEqual(reloaded.height, self.chain.height)
        self.assertEqual(reloaded.state.state_root(), self.chain.state.state_root())
        self.assertEqual(reloaded.state.burned_total, self.chain.state.burned_total)

    def test_loading_does_not_rewrite_the_file(self):
        self.populate()
        before = self.path.read_text()
        Chain.load(self.path)
        self.assertEqual(self.path.read_text(), before)

    def test_editing_a_stored_usage_amount_is_detected(self):
        self.populate()
        lines = self.path.read_text().splitlines()
        block = json.loads(lines[-1])
        block["transactions"][0]["body"]["amount"] = 1
        lines[-1] = json.dumps(block, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n")
        with self.assertRaises((ChainError, InvalidTransaction)):
            Chain.load(self.path)

    def test_deleting_a_block_is_detected(self):
        self.populate()
        lines = self.path.read_text().splitlines()
        del lines[2]
        self.path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(ChainError):
            Chain.load(self.path)

    def test_swapping_the_genesis_configuration_is_detected(self):
        self.populate()
        lines = self.path.read_text().splitlines()
        head = json.loads(lines[0])
        head["genesis"]["authorities"] = {OUTSIDER.public_hex(): 999 * CREDIT}
        lines[0] = json.dumps(head, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(ChainError):
            Chain.load(self.path)


class VerificationTests(ChainTestCase):
    def test_verify_replays_to_the_live_state(self):
        self.fund()
        self.chain.seal_block(AUTHORITY, [build_registration(AGENT, 0, role="agent")])
        self.chain.seal_block(AUTHORITY, [self.usage(amount=7)])
        self.assertEqual(self.chain.verify().state_root(), self.chain.state.state_root())

    def test_verify_catches_an_in_memory_forgery(self):
        self.fund()
        tampered = Block(self.chain.tip.header, ())      # drop the grant, keep the header
        self.chain.blocks[-1] = tampered
        with self.assertRaises(ChainError):
            self.chain.verify()


class MempoolTests(unittest.TestCase):
    def test_the_same_transaction_is_only_stored_once(self):
        pool = Mempool()
        tx = build_registration(AGENT, 0, role="agent")
        self.assertTrue(pool.add(tx))
        self.assertFalse(pool.add(tx))
        self.assertEqual(len(pool), 1)

    def test_a_conflicting_nonce_is_refused(self):
        pool = Mempool()
        pool.add(build_usage(AGENT, 0, resource="compute_ms", amount=1,
                             evidence=EVIDENCE, timestamp=100))
        with self.assertRaises(InvalidTransaction):
            pool.add(build_usage(AGENT, 0, resource="compute_ms", amount=2,
                                 evidence=EVIDENCE, timestamp=50))

    def test_a_newer_transaction_replaces_the_same_slot(self):
        pool = Mempool()
        pool.add(build_usage(AGENT, 0, resource="compute_ms", amount=1,
                             evidence=EVIDENCE, timestamp=100))
        replacement = build_usage(AGENT, 0, resource="compute_ms", amount=2,
                                  evidence=EVIDENCE, timestamp=200)
        pool.add(replacement)
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool.take(1)[0].txid, replacement.txid)

    def test_take_orders_a_senders_nonces(self):
        pool = Mempool()
        for nonce in (2, 0, 1):
            pool.add(build_usage(AGENT, nonce, resource="compute_ms", amount=1, evidence=EVIDENCE))
        self.assertEqual([tx.nonce for tx in pool.take(3)], [0, 1, 2])


if __name__ == "__main__":
    unittest.main()


class WriteLockTests(unittest.TestCase):
    """One ledger file, one writer.

    Two processes appending independently do not merge; they produce two
    blocks at the same height and the file stops loading entirely.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "ledger.jsonl"
        self.first = Chain.create(
            make_genesis(), AUTHORITY, ProofOfAuthority([AUTHORITY.public_hex()]),
            path=self.path, timestamp=1_700_000_000,
        )
        self.addCleanup(self.first.close)

    def test_a_reader_needs_no_lock(self):
        reader = Chain.load(self.path)
        self.assertEqual(reader.height, self.first.height)

    def test_a_second_writer_is_refused(self):
        second = Chain.load(self.path)
        self.addCleanup(second.close)
        with self.assertRaises(LedgerBusy) as ctx:
            second.seal_block(AUTHORITY, [])
        self.assertIn("another process is writing", str(ctx.exception))

    def test_the_refused_writer_does_not_advance_in_memory(self):
        second = Chain.load(self.path)
        self.addCleanup(second.close)
        before = second.height
        with self.assertRaises(LedgerBusy):
            second.seal_block(AUTHORITY, [])
        self.assertEqual(second.height, before)
        self.assertEqual(second.state.state_root(), Chain.load(self.path).state.state_root())

    def test_the_lock_is_released_on_close(self):
        self.first.close()
        second = Chain.load(self.path)
        self.addCleanup(second.close)
        second.seal_block(AUTHORITY, [])
        self.assertEqual(second.height, 1)

    def test_the_file_never_gains_a_duplicate_height(self):
        second = Chain.load(self.path)
        self.addCleanup(second.close)
        with self.assertRaises(LedgerBusy):
            second.seal_block(AUTHORITY, [])
        self.first.seal_block(AUTHORITY, [])
        heights = [
            json.loads(line)["header"]["height"]
            for line in self.path.read_text().splitlines()[1:] if line.strip()
        ]
        self.assertEqual(len(heights), len(set(heights)))
        Chain.load(self.path)          # still loads

    def test_a_chain_works_as_a_context_manager(self):
        self.first.close()
        with Chain.load(self.path) as chain:
            chain.seal_block(AUTHORITY, [])
        with Chain.load(self.path) as other:
            other.seal_block(AUTHORITY, [])     # the first one let go
        self.assertEqual(other.height, 2)
