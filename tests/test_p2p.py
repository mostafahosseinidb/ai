import tempfile
import time
import unittest
from pathlib import Path

from chainmind.chain import Chain
from chainmind.consensus import ProofOfAuthority
from chainmind.crypto import SigningKey, sha256_hex
from chainmind.p2p import Node, NodeError, join_network
from chainmind.resources import CREDIT
from chainmind.state import GenesisConfig
from chainmind.transactions import build_grant, build_registration

from support import AGENT, AUTHORITY, OUTSIDER, make_genesis


def wait_for(predicate, timeout=10.0, interval=0.02):
    """Poll until a condition holds. Networking is asynchronous; tests are not."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class NodeTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.nodes: list[Node] = []

    def make_chain(self, name: str, genesis: GenesisConfig | None = None) -> Chain:
        return Chain.create(
            genesis or make_genesis(), AUTHORITY,
            ProofOfAuthority([AUTHORITY.public_hex()]),
            path=Path(self.dir.name) / f"{name}.jsonl", timestamp=1_700_000_000,
        )

    def make_node(self, chain: Chain, **kwargs) -> Node:
        node = Node(chain, host="127.0.0.1", port=0,
                    node_key=SigningKey.generate(), **kwargs).start()
        self.nodes.append(node)
        self.addCleanup(node.stop)
        return node

    def joined_pair(self):
        """Two nodes on the same genesis, connected."""
        genesis = make_genesis()
        first = self.make_node(self.make_chain("a", genesis), label="a")
        second = self.make_node(self.make_chain("b", genesis), label="b")
        second.connect(*first.address)
        self.assertTrue(wait_for(lambda: first.peers() and second.peers()))
        return first, second


class HandshakeTests(NodeTestCase):
    def test_two_nodes_on_the_same_genesis_connect(self):
        first, second = self.joined_pair()
        self.assertEqual(second.peers()[0].node_id, first.node_id)
        self.assertEqual(first.peers()[0].node_id, second.node_id)

    def test_a_different_chain_id_is_refused_at_the_door(self):
        first = self.make_node(self.make_chain("a", make_genesis(chain_id="one")))
        second = self.make_node(self.make_chain("b", make_genesis(chain_id="two")))
        with self.assertRaises(NodeError) as ctx:
            second.connect(*first.address)
        self.assertIn("chain", str(ctx.exception))

    def test_the_same_name_with_a_different_genesis_is_refused(self):
        # The dangerous case: it looks like our network but every block it
        # sends would fail to validate.
        ours = make_genesis(chain_id="same", authorities={AUTHORITY.public_hex(): 10 * CREDIT})
        theirs = make_genesis(chain_id="same", authorities={OUTSIDER.public_hex(): 10 * CREDIT})
        first = self.make_node(self.make_chain("a", ours))
        second = self.make_node(self.make_chain("b", theirs))
        with self.assertRaises(NodeError) as ctx:
            second.connect(*first.address)
        self.assertIn("genesis", str(ctx.exception))

    def test_a_node_refuses_to_connect_to_itself(self):
        node = self.make_node(self.make_chain("a"))
        with self.assertRaises(NodeError):
            node.connect(*node.address)

    def test_an_unsigned_greeting_is_refused(self):
        import socket
        from chainmind.p2p import PROTOCOL_VERSION, Wire
        node = self.make_node(self.make_chain("a"))
        wire = Wire(socket.create_connection(node.address, timeout=5))
        self.addCleanup(wire.close)
        wire.send("hello", {
            "version": PROTOCOL_VERSION, "node_id": "ab" * 32, "listen_port": 1,
            "chain_id": node.chain.genesis_config.chain_id,
            "genesis_hash": Chain.genesis_commitment(node.chain.genesis_config),
            "height": 0, "tip_hash": "", "signature": "00" * 64,
        })
        kind, payload = wire.receive(5)
        self.assertEqual(kind, "error")
        self.assertIn("signed", payload["message"])


class SyncTests(NodeTestCase):
    def test_a_late_joiner_catches_up(self):
        genesis = make_genesis()
        ahead_chain = self.make_chain("a", genesis)
        for _ in range(5):
            ahead_chain.seal_block(AUTHORITY, [])
        ahead = self.make_node(ahead_chain, label="ahead")
        behind = self.make_node(self.make_chain("b", genesis), label="behind")

        behind.connect(*ahead.address)
        self.assertTrue(wait_for(lambda: behind.chain.height == 5))
        self.assertEqual(behind.chain.tip.hash, ahead.chain.tip.hash)
        behind.chain.verify()

    def test_syncing_crosses_more_than_one_batch(self):
        from chainmind.p2p import SYNC_BATCH
        genesis = make_genesis()
        ahead_chain = self.make_chain("a", genesis)
        total = SYNC_BATCH + 7
        for _ in range(total):
            ahead_chain.seal_block(AUTHORITY, [])
        ahead = self.make_node(ahead_chain)
        behind = self.make_node(self.make_chain("b", genesis))

        behind.connect(*ahead.address)
        self.assertTrue(wait_for(lambda: behind.chain.height == total, timeout=25))

    def test_a_node_that_is_ahead_pushes_nothing_it_was_not_asked_for(self):
        genesis = make_genesis()
        ahead_chain = self.make_chain("a", genesis)
        ahead_chain.seal_block(AUTHORITY, [])
        ahead = self.make_node(ahead_chain)
        behind = self.make_node(self.make_chain("b", genesis))
        ahead.connect(*behind.address)      # the ahead node dials out
        # It still ends in step, because the behind node asks on handshake.
        self.assertTrue(wait_for(lambda: behind.chain.height == 1))


class GossipTests(NodeTestCase):
    def test_a_sealed_block_reaches_the_other_node(self):
        first, second = self.joined_pair()
        first.seal(AUTHORITY) or first.chain.seal_block(AUTHORITY, [])
        first.announce_block(first.chain.tip)
        self.assertTrue(wait_for(lambda: second.chain.height == first.chain.height))
        self.assertEqual(second.chain.tip.hash, first.chain.tip.hash)

    def test_a_transaction_reaches_the_pool_of_every_peer(self):
        first, second = self.joined_pair()
        tx = build_registration(AGENT, 0, role="agent", label="atlas")
        first.submit(tx)
        self.assertTrue(wait_for(lambda: len(second.mempool) == 1))
        self.assertIn(tx.txid, second.mempool)

    def test_a_block_relays_across_three_nodes(self):
        genesis = make_genesis()
        a = self.make_node(self.make_chain("a", genesis), label="a")
        b = self.make_node(self.make_chain("b", genesis), label="b")
        c = self.make_node(self.make_chain("c", genesis), label="c")
        b.connect(*a.address)
        c.connect(*b.address)                  # c only knows b
        self.assertTrue(wait_for(lambda: a.peers() and c.peers()))

        a.chain.seal_block(AUTHORITY, [])
        a.announce_block(a.chain.tip)
        # b applies it and passes it on to c without c ever talking to a.
        self.assertTrue(wait_for(lambda: c.chain.height == 1, timeout=15))
        self.assertEqual(c.chain.tip.hash, a.chain.tip.hash)

    def test_gossip_does_not_loop_forever(self):
        first, second = self.joined_pair()
        tx = build_registration(AGENT, 0, role="agent")
        first.submit(tx)
        self.assertTrue(wait_for(lambda: len(second.mempool) == 1))
        time.sleep(0.4)
        # A relay that did not stop would keep re-adding it.
        self.assertEqual(len(first.mempool), 1)
        self.assertEqual(len(second.mempool), 1)

    def test_an_end_to_end_transfer_settles_on_both_nodes(self):
        first, second = self.joined_pair()
        first.submit(build_registration(AGENT, 0, role="agent", label="atlas"))
        first.submit(build_grant(AUTHORITY, 0, beneficiary=AGENT.public_hex(),
                                 amount=1 * CREDIT))
        self.assertTrue(wait_for(lambda: len(second.mempool) == 2))

        first.seal(AUTHORITY)
        self.assertTrue(wait_for(lambda: second.chain.height == first.chain.height))
        self.assertEqual(second.chain.state.balance_of(AGENT.public_hex()), 1 * CREDIT)
        second.chain.verify()


class ValidationTests(NodeTestCase):
    def test_an_invalid_block_is_not_accepted_just_because_a_peer_sent_it(self):
        from chainmind.block import Block, BlockHeader
        first, second = self.joined_pair()
        header = BlockHeader(
            height=1, prev_hash=first.chain.tip.hash, merkle_root="0" * 64,
            state_root=sha256_hex("a convenient fiction"), timestamp=1_700_000_100,
            proposer=AUTHORITY.public_hex(), chain_id=first.chain.genesis_config.chain_id,
        )
        forged = Block(header, ()).sign(AUTHORITY)
        first._relay("block", {"block": forged.to_dict()})
        time.sleep(0.5)
        self.assertEqual(second.chain.height, 0)

    def test_a_block_from_an_unauthorised_proposer_is_rejected(self):
        first, second = self.joined_pair()
        result = first.chain.propose(AUTHORITY, [])
        from chainmind.block import Block, BlockHeader
        stranger = Block(
            BlockHeader(**{**result.block.header.to_dict(), "proposer": OUTSIDER.public_hex()}),
            (),
        ).sign(OUTSIDER)
        first._relay("block", {"block": stranger.to_dict()})
        time.sleep(0.5)
        self.assertEqual(second.chain.height, 0)

    def test_a_competing_branch_is_reported_not_adopted(self):
        genesis = make_genesis()
        a = self.make_node(self.make_chain("a", genesis), label="a")
        b = self.make_node(self.make_chain("b", genesis), label="b")
        # Both seal their own height-1 block before they ever meet.
        a.chain.seal_block(AUTHORITY, [], timestamp=1_700_000_100)
        b.chain.seal_block(AUTHORITY, [], timestamp=1_700_000_200)
        self.assertNotEqual(a.chain.tip.hash, b.chain.tip.hash)

        b.connect(*a.address)
        a.announce_block(a.chain.tip)
        self.assertTrue(wait_for(lambda: b.forks_seen or a.forks_seen, timeout=10))
        # Neither rewrote its history.
        self.assertEqual(b.chain.height, 1)
        b.chain.verify()
        a.chain.verify()


class JoinTests(NodeTestCase):
    def test_a_fresh_install_can_download_the_whole_chain(self):
        genesis = make_genesis()
        seed_chain = self.make_chain("seed", genesis)
        seed_chain.seal_block(AUTHORITY, [build_registration(AGENT, 0, role="agent",
                                                             label="atlas")])
        seed_chain.seal_block(AUTHORITY, [build_grant(AUTHORITY, 0,
                                                      beneficiary=AGENT.public_hex(),
                                                      amount=2 * CREDIT)])
        seed = self.make_node(seed_chain, label="seed")

        joined = join_network(*seed.address, Path(self.dir.name) / "joined.jsonl")
        self.addCleanup(joined.close)

        self.assertEqual(joined.height, seed.chain.height)
        self.assertEqual(joined.state.state_root(), seed.chain.state.state_root())
        self.assertEqual(joined.state.balance_of(AGENT.public_hex()), 2 * CREDIT)

    def test_joining_can_pin_the_genesis_hash(self):
        seed = self.make_node(self.make_chain("seed"))
        expected = Chain.genesis_commitment(seed.chain.genesis_config)
        joined = join_network(*seed.address, Path(self.dir.name) / "ok.jsonl",
                              expected_genesis=expected)
        self.addCleanup(joined.close)
        self.assertEqual(joined.height, seed.chain.height)

    def test_a_peer_offering_a_different_genesis_is_refused(self):
        seed = self.make_node(self.make_chain("seed"))
        with self.assertRaises(NodeError) as ctx:
            join_network(*seed.address, Path(self.dir.name) / "no.jsonl",
                         expected_genesis="ff" * 32)
        self.assertIn("you expected", str(ctx.exception))

    def test_a_joined_ledger_is_a_real_ledger(self):
        seed_chain = self.make_chain("seed")
        seed_chain.seal_block(AUTHORITY, [])
        seed = self.make_node(seed_chain)
        path = Path(self.dir.name) / "joined.jsonl"
        joined = join_network(*seed.address, path)
        joined.close()
        # It reloads from disk and verifies on its own, with no peer present.
        reloaded = Chain.load(path)
        self.addCleanup(reloaded.close)
        self.assertEqual(reloaded.verify().state_root(), seed_chain.state.state_root())


class StatusTests(NodeTestCase):
    def test_status_reports_the_network_position(self):
        first, second = self.joined_pair()
        status = second.status()
        self.assertEqual(status["height"], 0)
        self.assertEqual(len(status["peers"]), 1)
        self.assertEqual(status["peers"][0]["node_id"], first.node_id)
        self.assertIn("genesis_hash", status)

    def test_peers_are_exchanged(self):
        genesis = make_genesis()
        a = self.make_node(self.make_chain("a", genesis))
        b = self.make_node(self.make_chain("b", genesis))
        c = self.make_node(self.make_chain("c", genesis))
        b.connect(*a.address)
        c.connect(*a.address)
        # a knows both; when c asks a for peers it learns about b.
        self.assertTrue(wait_for(lambda: len(c.peers()) >= 2, timeout=10))


if __name__ == "__main__":
    unittest.main()


class LedgerOwnershipTests(NodeTestCase):
    """A running node owns its ledger file from the moment it starts."""

    def test_starting_a_node_claims_the_write_lock(self):
        from chainmind.chain import LedgerBusy
        path = Path(self.dir.name) / "owned.jsonl"
        chain = Chain.create(make_genesis(), AUTHORITY,
                             ProofOfAuthority([AUTHORITY.public_hex()]),
                             path=path, timestamp=1_700_000_000)
        chain.close()

        node = self.make_node(Chain.load(path))
        other = Chain.load(path)
        self.addCleanup(other.close)
        with self.assertRaises(LedgerBusy):
            other.seal_block(AUTHORITY, [])
        self.assertEqual(node.chain.height, 0)

    def test_the_lock_is_released_when_the_node_stops(self):
        path = Path(self.dir.name) / "released.jsonl"
        chain = Chain.create(make_genesis(), AUTHORITY,
                             ProofOfAuthority([AUTHORITY.public_hex()]),
                             path=path, timestamp=1_700_000_000)
        chain.close()
        node = Node(Chain.load(path), host="127.0.0.1", port=0,
                    node_key=SigningKey.generate()).start()
        node.stop()

        after = Chain.load(path)
        self.addCleanup(after.close)
        after.seal_block(AUTHORITY, [])
        self.assertEqual(after.height, 1)


class SubmissionTests(NodeTestCase):
    def test_a_transaction_can_be_handed_to_a_running_node(self):
        from chainmind.p2p import submit_to_node
        node = self.make_node(self.make_chain("a"))
        tx = build_registration(AGENT, 0, role="agent", label="atlas")
        submit_to_node(*node.address, tx, chain=node.chain)
        self.assertTrue(wait_for(lambda: tx.txid in node.mempool))

    def test_a_handed_transaction_reaches_every_peer(self):
        from chainmind.p2p import submit_to_node
        first, second = self.joined_pair()
        tx = build_registration(AGENT, 0, role="agent")
        submit_to_node(*first.address, tx, chain=first.chain)
        self.assertTrue(wait_for(lambda: tx.txid in second.mempool))

    def test_a_handed_transaction_is_sealed_into_a_block(self):
        from chainmind.p2p import submit_to_node
        node = self.make_node(self.make_chain("a"))
        submit_to_node(*node.address,
                       build_registration(AGENT, 0, role="agent", label="atlas"),
                       chain=node.chain)
        self.assertTrue(wait_for(lambda: len(node.mempool) == 1))
        block = node.seal(AUTHORITY)
        self.assertIsNotNone(block)
        self.assertEqual(node.chain.state.get(AGENT.public_hex()).label, "atlas")
