"""The agent thinking with the model this project trained.

The behaviour that matters is not the quality of the answers -- a model
this small has none -- but that the ledger cannot tell which backend
produced one, except through the weights fingerprint that is supposed to
tell it.
"""

import math
import random
import tempfile
import unittest
from pathlib import Path

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.meter import Meter
from chainmind.models import (
    ModelUnavailable,
    build_model,
    build_model_tool,
    describe_model,
    model_is_path,
)
from chainmind.nano import Architecture, save_weights
from chainmind.native import NativeModel, NativeUnavailable, discover_native
from chainmind.resources import CREDIT, ResourceKind
from chainmind.tokenizer import train_tokenizer
from chainmind.tools import ToolRegistry
from chainmind.transactions import TxType, build_grant

from support import AGENT, AUTHORITY, make_chain

CORPUS = "سلام دنیا. the ledger authorises before the agent acts."


def make_weights(root: Path, name: str = "atlas", seed: int = 0,
                 context: int = 64) -> Path:
    tokenizer = train_tokenizer([CORPUS] * 3, vocab_size=300)
    architecture = Architecture(vocab_size=tokenizer.vocab_size, dim=16, layers=2,
                                heads=2, hidden=32, context=context)
    rng = random.Random(seed)
    tensors = {
        tensor: [rng.gauss(0.0, 0.05) for _ in range(math.prod(shape))]
        for tensor, shape in architecture.tensor_shapes()
    }
    models = root / "models"
    models.mkdir(parents=True, exist_ok=True)
    path = models / f"{name}.cmw"
    save_weights(path, architecture, tokenizer, tensors,
                 {"trained_from": "scratch", "corpus_documents": 3})
    return path


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)


class DiscoveryTests(WorkspaceTestCase):
    def test_an_empty_workspace_finds_nothing(self):
        self.assertEqual(discover_native(self.root), [])

    def test_it_finds_our_own_weights_and_ignores_others(self):
        make_weights(self.root)
        (self.root / "models" / "someone-elses.gguf").write_bytes(b"GGUF" + b"\x00" * 99)
        found = discover_native(self.root)
        self.assertEqual([path.name for path in found], ["atlas.cmw"])

    def test_the_largest_is_first(self):
        # With a test model and the real one present, the real one is meant.
        make_weights(self.root, name="small", context=64)
        make_weights(self.root, name="big", context=4096)
        self.assertEqual(discover_native(self.root)[0].name, "big.cmw")

    def test_an_empty_workspace_says_how_to_make_one(self):
        with self.assertRaises(NativeUnavailable) as ctx:
            NativeModel(workspace=self.root)
        message = str(ctx.exception)
        self.assertIn("training/pretrain.py", message)
        self.assertIn(".cmw", message)

    def test_no_path_and_no_workspace_is_refused(self):
        with self.assertRaises(NativeUnavailable):
            NativeModel()

    def test_a_corrupt_file_is_reported_rather_than_crashed_on(self):
        models = self.root / "models"
        models.mkdir()
        (models / "broken.cmw").write_bytes(b"not ours at all")
        with self.assertRaises(NativeUnavailable) as ctx:
            NativeModel(workspace=self.root)
        self.assertIn("broken.cmw", str(ctx.exception))


class SurfaceTests(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        self.path = make_weights(self.root)
        self.model = NativeModel(workspace=self.root, max_tokens=4, seed=1)

    def test_a_prose_only_model_is_not_fed_a_chat_template(self):
        """The failure this guards against looks like a broken model.

        A model pretrained on prose has never seen the control tokens, so
        prefixing them puts it off its own distribution and the output
        turns to noise. It gets plain text instead, and says so.
        """
        self.assertFalse(self.model.answers_questions)
        self.assertIn("continuation only", self.model.describe())
        ids = self.model._prompt_ids("سلام", None, "دستور")
        controls = {self.model.tokenizer.special(name)
                    for name in ("<|user|>", "<|assistant|>", "<|system|>")}
        self.assertFalse(controls.intersection(ids))

    def test_a_model_trained_on_conversations_gets_the_template(self):
        make_weights(self.root, name="chatty", context=256)
        path = self.root / "models" / "chatty.cmw"
        # Rewrite the header's meta the way the trainer would have.
        from chainmind.nano import load_weights, save_weights

        architecture, tokenizer, tensors, meta = load_weights(path)
        save_weights(path, architecture, tokenizer,
                     {name: list(values) for name, values in tensors.items()},
                     {**meta, "corpus_conversations": 12})

        model = NativeModel(path=path, max_tokens=4)
        self.assertTrue(model.answers_questions)
        self.assertIn("chat", model.describe())
        ids = model._prompt_ids("سلام", None, "دستور")
        self.assertIn(model.tokenizer.special("<|assistant|>"), ids)

    def test_it_describes_itself_as_the_project_s_own(self):
        described = self.model.describe()
        self.assertIn("atlas", described)
        self.assertIn("own model", described)
        self.assertIn("parameters", described)

    def test_the_estimate_counts_exactly_what_will_be_sent(self):
        """No guessing: the tokenizer is in this process.

        The number the chain authorises has to be the number the call
        spends, or the budget check is decorative.
        """
        meter = Meter("x")
        estimate = self.model.estimate("سلام", max_tokens=4)
        result = self.model.respond(meter, "سلام", max_tokens=4)
        self.assertEqual(estimate[ResourceKind.LLM_INPUT_TOKENS.value],
                         result["input_tokens"])

    def test_a_longer_system_prompt_costs_more(self):
        # Memory recalled into the system prompt must be paid for rather
        # than smuggled past the budget check.
        make_weights(self.root, name="roomy", context=4096)
        model = NativeModel(path=self.root / "models" / "roomy.cmw", max_tokens=4)
        plain = model.count_input_tokens("سلام", system="short")
        padded = model.count_input_tokens("سلام", system="short " * 40)
        self.assertGreater(padded, plain)

    def test_history_costs_more_than_no_history(self):
        # A roomy context, so the comparison is about the history rather
        # than about both prompts hitting the same cap.
        make_weights(self.root, name="roomy", context=4096)
        model = NativeModel(path=self.root / "models" / "roomy.cmw", max_tokens=4)
        plain = model.count_input_tokens("سلام")
        with_history = model.count_input_tokens(
            "سلام", history=[{"role": "user", "content": "قبلا چیزی پرسیدم"},
                             {"role": "assistant", "content": "و جوابی گرفتی"}]
        )
        self.assertGreater(with_history, plain)

    def test_empty_history_entries_are_dropped(self):
        conversation = self.model.conversation("hi", [{"role": "user", "content": ""}])
        self.assertEqual(len(conversation), 1)

    def test_it_answers_and_meters_what_it_spent(self):
        meter = Meter("x")
        result = self.model.respond(meter, "سلام", max_tokens=3)
        self.assertEqual(result["runtime"], "native")
        self.assertEqual(result["model"], "atlas")
        self.assertIsInstance(result["text"], str)
        self.assertLessEqual(result["output_tokens"], 3)
        self.assertEqual(meter.totals[ResourceKind.LLM_OUTPUT_TOKENS.value],
                         result["output_tokens"])

    def test_the_weights_fingerprint_is_part_of_the_evidence(self):
        """Two fine-tunes must not look identical to an auditor."""
        meter = Meter("x")
        self.model.respond(meter, "سلام", max_tokens=2)
        self.assertEqual(meter.context["weights"], self.model.fingerprint)
        self.assertEqual(meter.context["runtime"], "native")
        # Counted in this process by this model's own tokenizer.
        self.assertEqual(meter.sources[ResourceKind.LLM_INPUT_TOKENS.value],
                         "provider")

    def test_running_out_of_room_is_reported_as_truncation(self):
        meter = Meter("x")
        result = self.model.respond(meter, "سلام", max_tokens=2)
        if result["output_tokens"] == 2:
            self.assertTrue(result["truncated"])
            self.assertEqual(result["stop_reason"], "length")

    def test_the_same_seed_gives_the_same_answer(self):
        first = NativeModel(workspace=self.root, max_tokens=4, seed=9)
        second = NativeModel(workspace=self.root, max_tokens=4, seed=9)
        self.assertEqual(first.respond(Meter("a"), "سلام")["text"],
                         second.respond(Meter("b"), "سلام")["text"])

    def test_a_failing_engine_is_reported_rather_than_leaking(self):
        class Broken:
            path = Path("broken.cmw")
            fingerprint = "0" * 64
            architecture = self.model.engine.architecture
            tokenizer = self.model.engine.tokenizer

            def fit_prompt(self, tokens, max_tokens=0):
                return list(tokens)[:8]

            def generate(self, *args, **kwargs):
                raise RuntimeError("the arithmetic gave up")

        model = NativeModel(engine=Broken())
        with self.assertRaises(NativeUnavailable) as ctx:
            model.respond(Meter("x"), "سلام")
        self.assertIn("the arithmetic gave up", str(ctx.exception))


class SelectionTests(WorkspaceTestCase):
    def test_a_cmw_path_is_recognised_as_a_file(self):
        self.assertTrue(model_is_path("models/atlas.cmw"))
        self.assertTrue(model_is_path("models/atlas.gguf"))
        self.assertFalse(model_is_path("qwen2.5:7b"))

    def test_our_own_model_is_preferred_over_open_weights(self):
        """Least borrowed first.

        A workspace holding both should use the one whose every part was
        made here, because that is the only one the independence claim
        covers end to end.
        """
        make_weights(self.root)
        (self.root / "models" / "open.gguf").write_bytes(b"GGUF" + b"\x00" * (1 << 20))
        engine = build_model(workspace=self.root)
        self.assertIsInstance(engine, NativeModel)

    def test_an_explicit_weights_file_is_honoured(self):
        path = make_weights(self.root)
        engine = build_model(str(path))
        self.assertIsInstance(engine, NativeModel)
        self.assertIn("atlas", describe_model(engine))

    def test_with_nothing_anywhere_every_route_is_named(self):
        import os
        os.environ["CHAINMIND_LOCAL_URL"] = "http://127.0.0.1:1"
        os.environ["CHAINMIND_LOCAL_DIALECT"] = "openai"
        self.addCleanup(os.environ.pop, "CHAINMIND_LOCAL_URL", None)
        self.addCleanup(os.environ.pop, "CHAINMIND_LOCAL_DIALECT", None)
        with self.assertRaises(ModelUnavailable) as ctx:
            build_model(workspace=self.root)
        message = str(ctx.exception)
        self.assertIn("own model", message)
        self.assertIn("open weights", message)
        self.assertIn("running runtime", message)


class KernelTests(WorkspaceTestCase):
    """The ledger must not be able to tell which backend answered."""

    def setUp(self):
        super().setUp()
        make_weights(self.root)
        self.model = NativeModel(workspace=self.root, max_tokens=3, seed=2)

    def _funded(self, amount: int):
        chain = make_chain()
        ledger = LocalLedger(chain, AUTHORITY)
        chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, chain.state.get(AUTHORITY.public_hex()).nonce,
                         beneficiary=AGENT.public_hex(), amount=amount)],
        )
        registry = ToolRegistry()
        registry.register(build_model_tool(self.model))
        return chain, ledger, AgentKernel(AGENT, ledger, tools=registry, label="atlas")

    def test_an_answer_settles_like_any_other_tool(self):
        chain, ledger, kernel = self._funded(5 * CREDIT)

        outcome = kernel.perform("ask", prompt="سلام", max_tokens=2)
        ledger.flush()

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.value["runtime"], "native")
        measured = {tx.body["resource"]: tx.body["measured"]
                    for _, tx in chain.transactions() if tx.type is TxType.USAGE}
        self.assertEqual(measured["llm_input_tokens"], "provider")
        self.assertEqual(chain.verify().state_root(), chain.state.state_root())

    def test_an_unaffordable_turn_never_reaches_the_model(self):
        chain, _ledger, kernel = self._funded(50)

        outcome = kernel.perform("ask", prompt="سلام", max_tokens=2)

        self.assertTrue(outcome.refused)


if __name__ == "__main__":
    unittest.main()
