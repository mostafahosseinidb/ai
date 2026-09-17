"""Typed decisions, and the confidence attached to them.

Two things are being guarded here and they are not the same thing:

*   that the output is **structurally** valid -- an option from the schema or
    nothing, whatever the model does;
*   that the confidence is **honestly labelled** -- a measured probability
    where one was measured, and marked as a raw score where one was not.

The second is the one a project can quietly get wrong while looking fine.
"""

import math
import random
import tempfile
import unittest
from pathlib import Path

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.decide import (
    DEFAULT_THRESHOLD,
    Calibration,
    Decision,
    DecisionModel,
    DecisionSchema,
    DecisionUnavailable,
    discover_deciders,
    expected_calibration_error,
    fit_temperature,
    reliability_table,
    softmax,
)
from chainmind.meter import Meter
from chainmind.models import build_decider
from chainmind.nano import Architecture, save_weights
from chainmind.resources import CREDIT, ResourceKind
from chainmind.tokenizer import train_tokenizer
from chainmind.tools import Tool, ToolRegistry
from chainmind.transactions import TxType, build_grant

from support import AGENT, AUTHORITY, make_chain

CORPUS = "سلام دنیا. the ledger authorises before the agent acts. refund status"
OPTIONS = ("refund", "status", "human")


def make_decider(root: Path, name: str = "router", options=OPTIONS,
                 head_size: int | None = None, calibration: dict | None = None,
                 schema_name: str = "support", threshold: float = DEFAULT_THRESHOLD,
                 seed: int = 0) -> Path:
    """A real weights file with a decision head, small enough to be instant."""
    tokenizer = train_tokenizer([CORPUS] * 3, vocab_size=300)
    architecture = Architecture(
        vocab_size=tokenizer.vocab_size, dim=16, layers=2, heads=2, hidden=32,
        context=32, decisions=len(options) if head_size is None else head_size,
    )
    rng = random.Random(seed)
    tensors = {
        tensor: [rng.gauss(0.0, 0.05) for _ in range(math.prod(shape))]
        for tensor, shape in architecture.tensor_shapes()
    }
    models = root / "models"
    models.mkdir(parents=True, exist_ok=True)
    path = models / f"{name}.cmw"
    meta = {
        "trained_from": "scratch",
        "decision_schema": DecisionSchema(
            name=schema_name, options=tuple(options), threshold=threshold
        ).to_dict(),
    }
    if calibration is not None:
        meta["calibration"] = calibration
    save_weights(path, architecture, tokenizer, tensors, meta)
    return path


class FakeEngine:
    """A model that returns whatever logits a test wants, including nonsense."""

    def __init__(self, logits, options=len(OPTIONS), meta=None, fail=None):
        self.logits = logits
        self.fail = fail
        self.path = Path("fake.cmw")
        self.fingerprint = "f" * 64
        self.meta = meta if meta is not None else {
            "decision_schema": DecisionSchema(name="support",
                                              options=OPTIONS).to_dict()
        }
        self.architecture = Architecture(vocab_size=300, dim=16, layers=1, heads=2,
                                         hidden=32, context=32, decisions=options)
        self.tokenizer = train_tokenizer([CORPUS] * 3, vocab_size=300)
        self.seen: list[list[int]] = []

    def decision_logits(self, tokens):
        if self.fail:
            raise self.fail
        self.seen.append(list(tokens))
        return list(self.logits)


class SchemaTests(unittest.TestCase):
    def test_one_option_is_not_a_decision(self):
        with self.assertRaises(ValueError):
            DecisionSchema(name="x", options=("only",))

    def test_duplicate_options_are_refused(self):
        # Two options with the same name would make the answer ambiguous in
        # exactly the way a typed output is supposed to prevent.
        with self.assertRaises(ValueError):
            DecisionSchema(name="x", options=("a", "b", "a"))

    def test_a_blank_option_is_refused(self):
        with self.assertRaises(ValueError):
            DecisionSchema(name="x", options=("a", "  "))

    def test_a_threshold_outside_zero_to_one_is_refused(self):
        with self.assertRaises(ValueError):
            DecisionSchema(name="x", options=("a", "b"), threshold=1.5)

    def test_an_unknown_option_names_the_ones_that_exist(self):
        schema = DecisionSchema(name="support", options=OPTIONS)
        with self.assertRaises(ValueError) as ctx:
            schema.index("refnud")
        self.assertIn("refund", str(ctx.exception))

    def test_it_survives_a_round_trip(self):
        schema = DecisionSchema(name="s", options=OPTIONS, description="d",
                                threshold=0.8)
        self.assertEqual(DecisionSchema.from_dict(schema.to_dict()), schema)

    def test_a_schema_missing_its_options_says_so(self):
        with self.assertRaises(ValueError) as ctx:
            DecisionSchema.from_dict({"name": "s"})
        self.assertIn("options", str(ctx.exception))


class CalibrationMathsTests(unittest.TestCase):
    def test_softmax_sums_to_one(self):
        self.assertAlmostEqual(sum(softmax([1.0, 2.0, 3.0])), 1.0)

    def test_temperature_leaves_the_ranking_alone(self):
        """The property that makes temperature scaling safe.

        Dividing every logit by one number cannot change which is largest,
        so calibration never costs accuracy. If it could, nobody should use
        it.
        """
        logits = [2.0, -1.0, 0.5, 3.3]
        cold = softmax(logits, 0.1)
        hot = softmax(logits, 9.0)
        self.assertEqual(max(range(4), key=cold.__getitem__),
                         max(range(4), key=hot.__getitem__))
        self.assertGreater(max(cold), max(hot))

    def test_a_non_positive_temperature_is_refused(self):
        with self.assertRaises(ValueError):
            softmax([1.0, 2.0], 0.0)

    def test_a_perfectly_calibrated_model_scores_zero(self):
        # Ninety claims at 90%, nine of ten right.
        confidences = [0.9] * 100
        correct = [True] * 90 + [False] * 10
        self.assertAlmostEqual(expected_calibration_error(confidences, correct), 0.0,
                               places=6)

    def test_overconfidence_is_measured(self):
        confidences = [0.99] * 100
        correct = [True] * 70 + [False] * 30
        self.assertAlmostEqual(expected_calibration_error(confidences, correct), 0.29,
                               places=2)

    def test_it_refuses_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            expected_calibration_error([0.9], [True, False])

    def test_no_decisions_is_no_error(self):
        self.assertEqual(expected_calibration_error([], []), 0.0)

    def test_fitting_cools_an_overconfident_model(self):
        """The whole point: logits that are too sharp get softened.

        Four confident predictions, one wrong. A temperature above 1 is the
        correction; below 1 would make it worse.
        """
        logits = [[6.0, 0.0], [6.0, 0.0], [6.0, 0.0], [6.0, 0.0]]
        labels = [0, 0, 0, 1]
        self.assertGreater(fit_temperature(logits, labels), 1.0)

    def test_fitting_sharpens_an_underconfident_model(self):
        logits = [[0.2, 0.0]] * 8
        labels = [0] * 8
        self.assertLess(fit_temperature(logits, labels), 1.0)

    def test_fitting_lowers_the_held_out_loss(self):
        logits = [[5.0, 0.0]] * 8 + [[0.0, 5.0]] * 2
        labels = [0] * 8 + [0, 0]
        temperature = fit_temperature(logits, labels)
        before = -sum(math.log(softmax(row, 1.0)[label])
                      for row, label in zip(logits, labels))
        after = -sum(math.log(softmax(row, temperature)[label])
                     for row, label in zip(logits, labels))
        self.assertLess(after, before)

    def test_fitting_on_nothing_is_refused(self):
        with self.assertRaises(ValueError):
            fit_temperature([], [])

    def test_the_reliability_table_shows_the_shape(self):
        rows = reliability_table([0.15, 0.95, 0.95], [False, True, False])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["count"], 2)
        self.assertAlmostEqual(rows[-1]["accuracy"], 0.5)

    def test_calibration_survives_a_round_trip(self):
        original = Calibration(temperature=1.7, ece=0.04, accuracy=0.9, rows=200)
        restored = Calibration.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertTrue(restored.measured)

    def test_an_absent_record_is_not_measured(self):
        self.assertFalse(Calibration.from_dict(None).measured)
        self.assertFalse(Calibration.from_dict({"temperature": 2.0}).measured)
        self.assertEqual(Calibration.from_dict(None).temperature, 1.0)


class TypedOutputTests(unittest.TestCase):
    """The structural claim, tested against a model doing its worst."""

    def test_the_answer_is_always_an_option_or_nothing(self):
        for logits in ([50.0, 0.0, 0.0], [-1e9, -1e9, -1e9], [0.0, 0.0, 0.0],
                       [1e300, 1.0, 2.0]):
            with self.subTest(logits=logits):
                model = DecisionModel(engine=FakeEngine(logits))
                decision = model.decide(Meter("x"), "anything", threshold=0.0)
                self.assertIn(decision.option, OPTIONS)

    def test_the_distribution_covers_exactly_the_schema(self):
        model = DecisionModel(engine=FakeEngine([1.0, 2.0, 3.0]))
        decision = model.decide(Meter("x"), "text", threshold=0.0)
        self.assertEqual(tuple(decision.distribution), OPTIONS)
        self.assertAlmostEqual(sum(decision.distribution.values()), 1.0)

    def test_a_head_that_disagrees_with_the_schema_is_refused_at_load(self):
        """The mislabelling that would otherwise be invisible.

        A head for four options under a schema naming three would return
        option 3 as option 2's name and nothing would raise.
        """
        with self.assertRaises(DecisionUnavailable) as ctx:
            DecisionModel(engine=FakeEngine([1.0] * 4, options=4))
        self.assertIn("assembled wrongly", str(ctx.exception))

    def test_a_language_model_is_not_a_decider(self):
        with self.assertRaises(DecisionUnavailable) as ctx:
            DecisionModel(engine=FakeEngine([1.0, 2.0, 3.0], meta={}))
        self.assertIn("train_decider.py", str(ctx.exception))

    def test_a_failing_engine_is_reported_rather_than_leaking(self):
        model = DecisionModel(engine=FakeEngine([1.0], fail=RuntimeError("boom")))
        with self.assertRaises(DecisionUnavailable) as ctx:
            model.decide(Meter("x"), "text")
        self.assertIn("boom", str(ctx.exception))

    def test_empty_input_still_decides_rather_than_raising(self):
        model = DecisionModel(engine=FakeEngine([1.0, 2.0, 3.0]))
        decision = model.decide(Meter("x"), "", threshold=0.0)
        self.assertIn(decision.option, OPTIONS)
        self.assertGreaterEqual(decision.input_tokens, 1)


class AbstentionTests(unittest.TestCase):
    def test_a_close_call_abstains(self):
        model = DecisionModel(engine=FakeEngine([1.0, 0.99, 0.98]))
        decision = model.decide(Meter("x"), "text", threshold=0.9)
        self.assertTrue(decision.abstained)
        self.assertIsNone(decision.option)
        self.assertFalse(decision.taken)
        # ...but it still says what it would have guessed.
        self.assertIn(decision.best(), OPTIONS)

    def test_a_clear_call_is_taken(self):
        model = DecisionModel(engine=FakeEngine([20.0, 0.0, 0.0]))
        decision = model.decide(Meter("x"), "text", threshold=0.9)
        self.assertFalse(decision.abstained)
        self.assertEqual(decision.option, "refund")
        self.assertGreater(decision.margin, 0.9)

    def test_the_threshold_comes_from_the_schema_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_decider(root, threshold=0.99)
            model = build_decider(workspace=root)
            self.assertEqual(model.cutoff, 0.99)

    def test_an_explicit_threshold_overrides_the_schema(self):
        model = DecisionModel(engine=FakeEngine([1.0, 0.0, 0.0]), threshold=0.0)
        self.assertEqual(model.cutoff, 0.0)
        self.assertFalse(model.decide(Meter("x"), "t").abstained)

    def test_abstaining_is_not_the_same_as_failing(self):
        model = DecisionModel(engine=FakeEngine([1.0, 0.99, 0.98]))
        result = model.respond(Meter("x"), "text")
        self.assertTrue(result["refused"])
        self.assertEqual(result["stop_reason"], "abstained")
        self.assertEqual(result["output_tokens"], 0)


class HonestConfidenceTests(unittest.TestCase):
    """A number that was not measured must not be presented as if it were."""

    def test_an_uncalibrated_model_says_so(self):
        model = DecisionModel(engine=FakeEngine([9.0, 0.0, 0.0]))
        decision = model.decide(Meter("x"), "text")
        self.assertFalse(decision.calibrated)
        self.assertIsNone(decision.ece)
        self.assertIn("uncalibrated", str(decision))
        self.assertIn("uncalibrated", model.describe())

    def test_a_calibrated_model_carries_its_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_decider(root, calibration={"temperature": 2.0, "ece": 0.031,
                                            "accuracy": 0.9, "rows": 300})
            model = build_decider(workspace=root)
            decision = model.decide(Meter("x"), "text", threshold=0.0)
            self.assertTrue(decision.calibrated)
            self.assertAlmostEqual(decision.ece, 0.031)
            self.assertIn("ECE 0.031", model.describe())
            self.assertNotIn("uncalibrated", str(decision))

    def test_the_stored_temperature_is_actually_applied(self):
        engine = FakeEngine([4.0, 0.0, 0.0])
        sharp = DecisionModel(engine=engine, calibration=Calibration(temperature=1.0))
        soft = DecisionModel(engine=engine, calibration=Calibration(temperature=8.0))
        self.assertGreater(sharp.decide(Meter("a"), "t", threshold=0.0).confidence,
                           soft.decide(Meter("b"), "t", threshold=0.0).confidence)

    def test_temperature_never_changes_which_option_wins(self):
        engine = FakeEngine([4.0, 1.0, 0.0])
        options = {
            DecisionModel(engine=engine,
                          calibration=Calibration(temperature=temperature)
                          ).decide(Meter("x"), "t", threshold=0.0).option
            for temperature in (0.2, 1.0, 5.0, 50.0)
        }
        self.assertEqual(options, {"refund"})


class MeteringTests(unittest.TestCase):
    def test_the_cost_does_not_depend_on_the_answer(self):
        """Why this is cheap: there is no answer to write.

        A language model's cost grows with how much it says. Here it is the
        prompt and one forward pass, full stop.
        """
        model = DecisionModel(engine=FakeEngine([5.0, 0.0, 0.0]))
        short = model.estimate("hi")
        long = model.estimate("hi " * 40)
        self.assertEqual(short[ResourceKind.TOOL_CALL.value], 1)
        self.assertEqual(long[ResourceKind.TOOL_CALL.value], 1)
        self.assertGreater(long[ResourceKind.LLM_INPUT_TOKENS.value],
                           short[ResourceKind.LLM_INPUT_TOKENS.value])

    def test_the_estimate_matches_what_is_spent(self):
        model = DecisionModel(engine=FakeEngine([5.0, 0.0, 0.0]))
        meter = Meter("x")
        estimate = model.estimate("some message to route")
        model.decide(meter, "some message to route")
        self.assertEqual(meter.totals[ResourceKind.LLM_INPUT_TOKENS.value],
                         estimate[ResourceKind.LLM_INPUT_TOKENS.value])

    def test_the_decision_itself_is_part_of_the_evidence(self):
        """An auditor should see what was concluded, not just that it was.

        A ledger that records "the agent spent four tokens" and not "it
        decided to refund, at 91%" cannot answer the question anyone
        actually has later.
        """
        model = DecisionModel(engine=FakeEngine([9.0, 0.0, 0.0]))
        meter = Meter("x")
        model.decide(meter, "text", threshold=0.0)
        self.assertEqual(meter.context["decision"], "refund")
        self.assertEqual(meter.context["schema"], "support")
        self.assertEqual(meter.context["runtime"], "decision")
        self.assertIn("confidence", meter.context)
        self.assertFalse(meter.context["calibrated"])

    def test_an_abstention_is_recorded_as_one(self):
        model = DecisionModel(engine=FakeEngine([1.0, 0.99, 0.98]))
        meter = Meter("x")
        model.decide(meter, "text", threshold=0.99)
        self.assertEqual(meter.context["decision"], "(abstained)")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_an_empty_workspace_says_how_to_make_one(self):
        with self.assertRaises(DecisionUnavailable) as ctx:
            build_decider(workspace=self.root)
        self.assertIn("train_decider.py", str(ctx.exception))

    def test_a_language_model_is_not_offered_as_a_decider(self):
        from test_native import make_weights

        make_weights(self.root, name="atlas")
        self.assertEqual(discover_deciders(self.root), [])

    def test_a_decider_is_found(self):
        make_decider(self.root)
        self.assertEqual([p.name for p in discover_deciders(self.root)],
                         ["router.cmw"])

    def test_a_corrupt_file_is_skipped_rather_than_fatal(self):
        make_decider(self.root)
        (self.root / "models" / "broken.cmw").write_bytes(b"junk")
        self.assertEqual([p.name for p in discover_deciders(self.root)],
                         ["router.cmw"])

    def test_a_decider_is_not_offered_as_a_language_model(self):
        """They share a file format and are not interchangeable.

        A decision model has an untrained language-model head, so it would
        load and generate fluent nonsense rather than fail.
        """
        from chainmind.native import NativeModel, NativeUnavailable, discover_native

        path = make_decider(self.root)
        self.assertEqual(discover_native(self.root), [])
        with self.assertRaises(NativeUnavailable) as ctx:
            NativeModel(path=path)
        self.assertIn("chainmind decide", str(ctx.exception))

    def test_both_kinds_can_live_in_one_workspace(self):
        from test_native import make_weights
        from chainmind.native import discover_native

        make_weights(self.root, name="atlas")
        make_decider(self.root, name="router")
        self.assertEqual([p.name for p in discover_native(self.root)],
                         ["atlas.cmw"])
        self.assertEqual([p.name for p in discover_deciders(self.root)],
                         ["router.cmw"])

    def test_the_schema_travels_inside_the_weights(self):
        path = make_decider(self.root, options=("a", "b"), schema_name="ab")
        model = build_decider(str(path))
        self.assertEqual(model.schema.options, ("a", "b"))
        self.assertEqual(model.schema.name, "ab")


class KernelTests(unittest.TestCase):
    """A decision is an action, and the ledger treats it like one."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        make_decider(self.root)
        self.model = build_decider(workspace=self.root, threshold=0.0)

    def _kernel(self, amount: int):
        chain = make_chain()
        ledger = LocalLedger(chain, AUTHORITY)
        chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, chain.state.get(AUTHORITY.public_hex()).nonce,
                         beneficiary=AGENT.public_hex(), amount=amount)],
        )
        registry = ToolRegistry()
        registry.register(Tool(
            name="decide",
            description="choose",
            run=lambda meter, text: self.model.decide(meter, text).to_dict(),
            estimate=lambda kw: self.model.estimate(str(kw.get("text", ""))),
        ))
        return chain, ledger, AgentKernel(AGENT, ledger, tools=registry,
                                          label="atlas")

    def test_a_decision_settles_on_chain(self):
        chain, ledger, kernel = self._kernel(5 * CREDIT)
        outcome = kernel.perform("decide", text="پولم رو پس بدید")
        ledger.flush()

        self.assertTrue(outcome.ok)
        self.assertIn(outcome.value["option"], OPTIONS)
        measured = {tx.body["resource"]: tx.body["measured"]
                    for _, tx in chain.transactions() if tx.type is TxType.USAGE}
        self.assertEqual(measured["llm_input_tokens"], "provider")
        self.assertEqual(chain.verify().state_root(), chain.state.state_root())

    def test_an_agent_with_no_credit_never_reaches_the_model(self):
        _chain, _ledger, kernel = self._kernel(1)
        outcome = kernel.perform("decide", text="پولم رو پس بدید")
        self.assertTrue(outcome.refused)


if __name__ == "__main__":
    unittest.main()
