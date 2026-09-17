"""Training a decision model.

The gradient check is the important one. A decision head with a wrong sign
still produces a loss curve that goes down -- slowly, to somewhere useless --
and the only way to know is to check the arithmetic against a finite
difference before spending an evening on a training run.

Skipped without NumPy: training is an offline step with its own requirements
file, and running a node never needs it.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from chainmind.decide import Calibration, DecisionModel, DecisionSchema, softmax
from chainmind.nano import Architecture, save_weights
from chainmind.meter import Meter
from chainmind.tokenizer import train_tokenizer

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
ROOT = Path(__file__).resolve().parent.parent


def load(name: str):
    import sys

    if str(ROOT / "training") not in sys.path:
        sys.path.insert(0, str(ROOT / "training"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "training" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OPTIONS = ("refund", "status", "other")
SAMPLES = {
    "refund": ["پولم را پس بدهید", "درخواست مرجوعی دارم", "وجه را برگردانید"],
    "status": ["سفارش من کجاست", "کد رهگیری میخواهم", "کی میرسد بسته"],
    "other": ["امروز هوا خوب است", "پایتخت ژاپن کجاست", "یک شعر بگو"],
}


def make_rows(repeats: int = 40, offset: int = 0) -> list[dict[str, str]]:
    rows = []
    for index in range(offset, offset + repeats):
        for label, texts in SAMPLES.items():
            rows.append({"text": f"{texts[index % len(texts)]} {index}",
                         "label": label})
    return rows


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.decider = load("train_decider")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, lines: list[str]) -> Path:
        path = self.root / "data.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_it_reads_labelled_rows(self):
        path = self.write([
            json.dumps({"text": "سلام", "label": "refund"}, ensure_ascii=False),
            json.dumps({"text": "کجاست", "label": "status"}, ensure_ascii=False),
        ])
        self.assertEqual(len(self.decider.read_dataset(path)), 2)

    def test_malformed_rows_are_counted_rather_than_silently_dropped(self):
        path = self.write([
            "{not json}",
            json.dumps({"text": "fine", "label": "refund"}),
            json.dumps({"text": "", "label": "refund"}),
            json.dumps({"label": "no text"}),
        ])
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rows = self.decider.read_dataset(path)
        self.assertEqual(len(rows), 1)
        self.assertIn("skipped 3", buffer.getvalue())

    def test_a_file_with_nothing_usable_is_refused(self):
        import contextlib
        import io

        path = self.write(["{}", "nonsense"])
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.decider.read_dataset(path)

    def test_the_split_is_stable_when_rows_are_added(self):
        """Adding data later must not reshuffle what was held out.

        Otherwise two runs a month apart are not comparable, and a model
        that looks better may just have been tested on easier rows.
        """
        rows = make_rows(20)
        first = self.decider.split_rows(rows)
        # Genuinely new rows, not a repeat of the first twenty: adding the
        # same text twice would prove nothing.
        more = self.decider.split_rows(rows + make_rows(5, offset=100))
        original = {row["text"] for row in rows}
        for before, after in zip(first, more):
            kept = [row["text"] for row in after if row["text"] in original]
            self.assertEqual(sorted(row["text"] for row in before), sorted(kept))

    def test_the_three_splits_do_not_overlap(self):
        rows = make_rows(30)
        train, calibration, test = self.decider.split_rows(rows)
        self.assertEqual(len(train) + len(calibration) + len(test), len(rows))
        texts = [{row["text"] for row in subset}
                 for subset in (train, calibration, test)]
        self.assertFalse(texts[0] & texts[1])
        self.assertFalse(texts[0] & texts[2])
        self.assertFalse(texts[1] & texts[2])

    def test_a_split_leaving_no_training_rows_is_refused(self):
        with self.assertRaises(SystemExit):
            self.decider.split_rows(make_rows(2), calibration=0.6, test=0.6)

    @unittest.skipUnless(HAVE_NUMPY, "encoding returns arrays")
    def test_rows_are_padded_on_the_right_and_lengths_kept(self):
        """Right-padded, because the decision is read at the last real token.

        Under a causal mask that position sees only what came before it --
        all real. Padding on the left would put pad tokens inside the window
        every real token attends to.
        """
        tokenizer = train_tokenizer(["سلام دنیا refund status"] * 3, vocab_size=300)
        schema = DecisionSchema(name="s", options=OPTIONS)
        inputs, lengths, labels = self.decider.encode_rows(
            [{"text": "سلام", "label": "refund"},
             {"text": "سلام دنیا refund", "label": "status"}],
            tokenizer, schema, context=24,
        )
        pad = tokenizer.special("<|pad|>")
        self.assertEqual(inputs.shape, (2, 24))
        self.assertLess(lengths[0], lengths[1])
        for row, length in zip(inputs, lengths):
            self.assertTrue(all(token == pad for token in row[length:]))
            self.assertTrue(all(token != pad for token in row[:length]))
        self.assertEqual(list(labels), [0, 1])

    @unittest.skipUnless(HAVE_NUMPY, "encoding returns arrays")
    def test_an_empty_text_still_encodes_to_something(self):
        tokenizer = train_tokenizer(["سلام دنیا"] * 3, vocab_size=300)
        schema = DecisionSchema(name="s", options=OPTIONS)
        _inputs, lengths, _labels = self.decider.encode_rows(
            [{"text": "", "label": "refund"}], tokenizer, schema, context=8
        )
        self.assertEqual(int(lengths[0]), 1)


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class GradientTests(unittest.TestCase):
    def setUp(self):
        self.decider = load("train_decider")
        self.pretrain = load("pretrain")
        import numpy as np

        self.np = np
        self.architecture = Architecture(vocab_size=23, dim=8, layers=2, heads=2,
                                         hidden=16, context=6, decisions=3)
        self.params = self.pretrain.initialise(self.architecture, seed=1,
                                               dtype=np.float64)
        rng = np.random.default_rng(0)
        self.inputs = rng.integers(0, 23, size=(3, 6)).astype(np.int64)
        self.lengths = np.array([6, 4, 2], dtype=np.int64)
        self.labels = np.array([0, 2, 1], dtype=np.int64)

    def test_the_head_exists_in_the_parameters(self):
        self.assertIn("decision_head", self.params)
        self.assertEqual(self.params["decision_head"].shape, (3, 8))

    def test_an_untrained_head_starts_at_the_entropy_of_the_options(self):
        loss, _grads, _logits = self.decider.decision_forward_backward(
            self.params, self.architecture, self.inputs, self.lengths, self.labels,
            compute_gradients=False,
        )
        # A randomly initialised head should be exactly as surprised as
        # guessing uniformly between three options. Far from ln(3) means the
        # initialisation or the loss is wrong, not the model.
        self.assertAlmostEqual(loss, float(self.np.log(3)), delta=0.02)

    def test_every_gradient_matches_a_finite_difference(self):
        loss, grads, _logits = self.decider.decision_forward_backward(
            self.params, self.architecture, self.inputs, self.lengths, self.labels
        )
        self.assertTrue(self.np.isfinite(loss))

        rng = self.np.random.default_rng(3)
        epsilon = 1e-5
        for name, tensor in self.params.items():
            with self.subTest(tensor=name):
                for _ in range(3):
                    index = tuple(int(rng.integers(0, size)) for size in tensor.shape)
                    original = float(tensor[index])

                    tensor[index] = original + epsilon
                    up, _, _ = self.decider.decision_forward_backward(
                        self.params, self.architecture, self.inputs, self.lengths,
                        self.labels, compute_gradients=False,
                    )
                    tensor[index] = original - epsilon
                    down, _, _ = self.decider.decision_forward_backward(
                        self.params, self.architecture, self.inputs, self.lengths,
                        self.labels, compute_gradients=False,
                    )
                    tensor[index] = original

                    numeric = (up - down) / (2 * epsilon)
                    analytic = float(grads[name][index])
                    self.assertAlmostEqual(
                        numeric, analytic, delta=1e-3 * abs(analytic) + 1e-8
                    )

    def test_padding_after_the_decision_point_changes_nothing(self):
        """The property that makes right-padding correct.

        Whatever sits past a row's real length is never read, so changing it
        must not move the loss by a hair. If it did, the model would be
        learning from its own padding.
        """
        first, _grads, _logits = self.decider.decision_forward_backward(
            self.params, self.architecture, self.inputs, self.lengths, self.labels,
            compute_gradients=False,
        )
        changed = self.inputs.copy()
        for row, length in enumerate(self.lengths):
            changed[row, length:] = (changed[row, length:] + 5) % 23
        second, _grads, _logits = self.decider.decision_forward_backward(
            self.params, self.architecture, changed, self.lengths, self.labels,
            compute_gradients=False,
        )
        self.assertAlmostEqual(first, second, places=12)


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class CalibrationChoiceTests(unittest.TestCase):
    def setUp(self):
        self.decider = load("train_decider")
        import numpy as np

        self.np = np

    def test_a_temperature_that_would_make_things_worse_is_rejected(self):
        """Never ship a correction that is measurably worse than none.

        Fitting minimises log-likelihood, which is not the same as
        minimising calibration error; on a few hundred rows the two can
        disagree. Measured, not assumed.
        """
        # Well calibrated already: 80% confident, 80% right.
        logits = self.np.array([[1.3863, 0.0]] * 10)      # softmax -> 0.8/0.2
        labels = self.np.array([0] * 8 + [1] * 2)
        messages: list[str] = []
        chosen = self.decider._choose_temperature(
            logits, labels, fitted=12.0, bins=5, report=messages.append
        )
        self.assertEqual(chosen, 1.0)
        self.assertTrue(any("left alone" in message for message in messages))

    def test_a_temperature_that_helps_is_kept(self):
        # Wildly overconfident: 99.9% confident, 70% right.
        logits = self.np.array([[8.0, 0.0]] * 10)
        labels = self.np.array([0] * 7 + [1] * 3)
        chosen = self.decider._choose_temperature(
            logits, labels, fitted=6.0, bins=5, report=lambda _: None
        )
        self.assertEqual(chosen, 6.0)


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.decider = load("train_decider")
        self.pretrain = load("pretrain")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_it_learns_a_separable_task_and_the_result_decides(self):
        """Corpus, vocabulary, training, calibration, weights file, decision.

        The task is easy on purpose. If a model cannot separate three
        obviously different kinds of sentence, something in the trainer is
        broken and no amount of real data would rescue it.
        """
        rows = make_rows(40)
        schema = DecisionSchema(name="support", options=OPTIONS, threshold=0.5)
        tokenizer = train_tokenizer([row["text"] for row in rows], vocab_size=400)
        architecture = Architecture(vocab_size=tokenizer.vocab_size, dim=32,
                                    layers=2, heads=4, hidden=64, context=16,
                                    decisions=3)

        subsets = self.decider.split_rows(rows)
        sets = [self.decider.encode_rows(subset, tokenizer, schema, 16)
                for subset in subsets]
        params, record = self.decider.train(
            *sets, architecture, steps=120, batch=12, learning_rate=2e-3,
            warmup=20, seed=0, report=lambda _: None,
        )

        self.assertLess(record["history"][-1]["loss"],
                        record["history"][0]["loss"] / 2)

        calibration = self.decider.calibrate(params, architecture, sets[1], sets[2],
                                             report=lambda _: None)
        self.assertGreater(calibration.accuracy, 0.8)

        path = self.root / "models" / "router.cmw"
        path.parent.mkdir(parents=True)
        save_weights(path, architecture, tokenizer,
                     {name: value.reshape(-1).tolist()
                      for name, value in params.items()},
                     meta={"decision_schema": schema.to_dict(),
                           "calibration": calibration.to_dict()})

        model = DecisionModel(path=path)
        self.assertTrue(model.calibration.measured)
        decision = model.decide(Meter("x"), "پولم را پس بدهید 3")
        self.assertEqual(decision.option, "refund")

    def test_the_checkpoint_kept_is_the_best_one_seen(self):
        """On a task with no overfitting, the last step legitimately wins.

        What has to hold either way is that the checkpoint kept is one whose
        held-out loss is the lowest recorded -- not that it is an early one.
        """
        rows = make_rows(40)
        schema = DecisionSchema(name="support", options=OPTIONS)
        tokenizer = train_tokenizer([row["text"] for row in rows], vocab_size=400)
        architecture = Architecture(vocab_size=tokenizer.vocab_size, dim=32,
                                    layers=2, heads=4, hidden=64, context=16,
                                    decisions=3)
        sets = [self.decider.encode_rows(subset, tokenizer, schema, 16)
                for subset in self.decider.split_rows(rows)]
        _params, record = self.decider.train(
            *sets, architecture, steps=100, batch=12, learning_rate=5e-3,
            warmup=10, seed=0, report=lambda _: None,
        )
        holdouts = {entry["step"]: entry["holdout"] for entry in record["history"]
                    if "holdout" in entry}
        self.assertEqual(holdouts[record["kept_step"]], min(holdouts.values()))

    def test_an_overfitting_run_stops_at_the_step_that_was_best(self):
        """The case this exists for.

        Contradictory labels on identical text mean the only way to drive
        the training loss down is to memorise which row was which, and the
        held-out loss rises while it happens. The kept checkpoint must be
        from before that.
        """
        # A handful of training rows and a model far too large for them: it
        # memorises, and the held-out loss turns back up once it has.
        rows = make_rows(5)
        schema = DecisionSchema(name="support", options=OPTIONS)
        tokenizer = train_tokenizer([row["text"] for row in rows], vocab_size=400)
        architecture = Architecture(vocab_size=tokenizer.vocab_size, dim=64,
                                    layers=2, heads=4, hidden=128, context=16,
                                    decisions=3)
        sets = [self.decider.encode_rows(subset, tokenizer, schema, 16)
                for subset in self.decider.split_rows(rows)]
        messages: list[str] = []
        _params, record = self.decider.train(
            *sets, architecture, steps=300, batch=8, learning_rate=1e-2,
            warmup=5, seed=0, report=messages.append,
        )
        holdouts = {entry["step"]: entry["holdout"] for entry in record["history"]
                    if "holdout" in entry}
        self.assertEqual(holdouts[record["kept_step"]], min(holdouts.values()))
        self.assertLess(record["kept_step"], 299)
        self.assertTrue(any("overfitting" in message for message in messages))

    def test_a_warm_start_copies_what_the_encoder_already_knows(self):
        rows = make_rows(10)
        tokenizer = train_tokenizer([row["text"] for row in rows], vocab_size=400)
        language = Architecture(vocab_size=tokenizer.vocab_size, dim=32, layers=2,
                                heads=4, hidden=64, context=16)
        import numpy as np

        params = self.pretrain.initialise(language, seed=3)
        source = self.root / "atlas.cmw"
        save_weights(source, language, tokenizer,
                     {name: value.reshape(-1).tolist()
                      for name, value in params.items()})

        decider = Architecture(vocab_size=tokenizer.vocab_size, dim=32, layers=2,
                               heads=4, hidden=64, context=16, decisions=3)
        fresh = self.pretrain.initialise(decider, seed=9)
        warmed, copied = self.decider.warm_start(source, decider, dict(fresh))

        self.assertEqual(copied, len(language.tensor_shapes()))
        self.assertTrue(np.allclose(warmed["tok_emb"], params["tok_emb"]))
        # The head is new, because it did not exist in the source.
        self.assertTrue(np.allclose(warmed["decision_head"],
                                    fresh["decision_head"]))

    def test_a_warm_start_from_a_different_vocabulary_is_refused(self):
        """Copying embeddings across vocabularies would be silently wrong.

        Row 400 means a different token in each, and nothing would raise.
        """
        tokenizer = train_tokenizer(["سلام دنیا"] * 3, vocab_size=300)
        language = Architecture(vocab_size=tokenizer.vocab_size, dim=32, layers=1,
                                heads=4, hidden=64, context=16)
        params = self.pretrain.initialise(language, seed=3)
        source = self.root / "atlas.cmw"
        save_weights(source, language, tokenizer,
                     {name: value.reshape(-1).tolist()
                      for name, value in params.items()})

        other = Architecture(vocab_size=tokenizer.vocab_size + 7, dim=32, layers=1,
                             heads=4, hidden=64, context=16, decisions=3)
        with self.assertRaises(SystemExit) as ctx:
            self.decider.warm_start(source, other, self.pretrain.initialise(other))
        self.assertIn("--reuse-vocab", str(ctx.exception))

    def test_the_unfamiliar_check_reports_how_often_it_abstains(self):
        """The number calibration cannot give you.

        A router trained on three kinds of message will answer a question
        about the weather with one of its three options, confidently,
        because those are the only things it can say.
        """
        rows = make_rows(30)
        schema = DecisionSchema(name="support", options=OPTIONS, threshold=0.9)
        tokenizer = train_tokenizer([row["text"] for row in rows], vocab_size=400)
        architecture = Architecture(vocab_size=tokenizer.vocab_size, dim=32,
                                    layers=1, heads=4, hidden=64, context=16,
                                    decisions=3)
        sets = [self.decider.encode_rows(subset, tokenizer, schema, 16)
                for subset in self.decider.split_rows(rows)]
        params, _record = self.decider.train(
            *sets, architecture, steps=40, batch=12, learning_rate=2e-3,
            warmup=5, seed=0, report=lambda _: None,
        )
        messages: list[str] = []
        result = self.decider.unfamiliar_check(
            params, architecture, ["قیمت طلا چند است", "معادله درجه دو"],
            tokenizer, schema, 16, "", 1.0, 0.9, report=messages.append,
        )
        self.assertEqual(result["rows"], 2)
        self.assertIn("abstained", result)
        self.assertTrue(any("unfamiliar input" in message for message in messages))

    def test_no_unfamiliar_rows_is_no_measurement(self):
        self.assertEqual(
            self.decider.unfamiliar_check(None, None, [], None, None, 0, "", 1.0,
                                          0.9, report=lambda _: None),
            {},
        )


if __name__ == "__main__":
    unittest.main()
