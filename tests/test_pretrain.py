"""The from-scratch trainer.

A transformer with one wrong sign in its backward pass still trains to a
plausible-looking loss curve and produces nothing usable, and you find out
after the training run rather than during it. So every gradient here is
checked against a finite difference, and the whole pipeline -- corpus,
vocabulary, training, weights file, generation -- is run end to end on
something small enough to finish in seconds.

Skipped entirely without NumPy: training is an offline step with its own
requirements file, and running a node never needs it.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from chainmind.nano import Architecture, NanoModel
from chainmind.tokenizer import train_tokenizer

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None


def load_pretrain():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "pretrain", root / "training" / "pretrain.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SENTENCE = (
    "سلام. من یک عامل مستقل هستم و مصرف منابعم روی زنجیره ثبت می‌شود. "
    "هر کاری که می‌کنم اول برآورد می‌شود، بعد اجازه می‌گیرد، بعد اجرا می‌شود. "
)


class LayoutTests(unittest.TestCase):
    """True with or without NumPy: training is not part of running."""

    def test_the_trainer_lives_outside_the_package(self):
        import chainmind

        package = Path(chainmind.__file__).parent
        self.assertFalse((package / "pretrain.py").exists())
        self.assertTrue((package.parent / "training" / "pretrain.py").exists())

    def test_numpy_is_declared_where_training_declares_its_needs(self):
        root = Path(__file__).resolve().parent.parent
        requirements = (root / "training" / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("numpy", requirements.lower())


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.pretrain = load_pretrain()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_it_reads_plain_text_and_markdown(self):
        (self.root / "a.txt").write_text("سلام", encoding="utf-8")
        (self.root / "b.md").write_text("# عنوان", encoding="utf-8")
        (self.root / "c.png").write_bytes(b"\x89PNG")
        documents = self.pretrain.read_corpus([self.root])
        self.assertEqual(len(documents), 2)

    def test_it_reads_the_dataset_the_agent_writes_for_itself(self):
        """The loop closes here.

        ``chainmind.memory.build_training_dataset`` writes prompt/response
        rows from conversations people approved; those rows are what makes
        the next model better than this one.
        """
        (self.root / "d.jsonl").write_text(
            json.dumps({"prompt": "سلام", "response": "درود"}, ensure_ascii=False)
            + "\n" + json.dumps({"text": "متن ساده"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        documents = self.pretrain.read_corpus([self.root])
        self.assertEqual(len(documents), 2)

    def test_a_broken_line_is_skipped_rather_than_fatal(self):
        (self.root / "d.jsonl").write_text(
            "{not json}\n" + json.dumps({"text": "fine"}) + "\n", encoding="utf-8"
        )
        self.assertEqual(self.pretrain.read_corpus([self.root]), ["fine"])

    def test_conversations_keep_their_roles_in_the_token_stream(self):
        # A model that never sees <|user|> during training does not know
        # what to do with it at chat time.
        tokenizer = train_tokenizer([SENTENCE], vocab_size=400)
        stream = self.pretrain.pack_tokens(
            tokenizer, ["<<CHAT>>سلام\n<<REPLY>>درود"]
        )
        self.assertIn(tokenizer.special("<|user|>"), stream.tolist())
        self.assertIn(tokenizer.special("<|assistant|>"), stream.tolist())

    def test_documents_are_separated(self):
        tokenizer = train_tokenizer([SENTENCE], vocab_size=400)
        stream = self.pretrain.pack_tokens(tokenizer, ["یک", "دو"])
        self.assertEqual(stream.tolist().count(tokenizer.special("<|end|>")), 2)


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class GradientTests(unittest.TestCase):
    """Every parameter, against a finite difference.

    In float32 a central difference of a small gradient is mostly rounding
    noise, so the same code runs in double precision here. That the forward
    pass is dtype-agnostic is the only concession the trainer makes to being
    testable, and it is a cheap one.
    """

    def setUp(self):
        self.pretrain = load_pretrain()
        import numpy as np

        self.np = np
        self.architecture = Architecture(vocab_size=23, dim=8, layers=2, heads=2,
                                         hidden=16, context=8)
        self.params = self.pretrain.initialise(self.architecture, seed=1,
                                               dtype=np.float64)
        rng = np.random.default_rng(0)
        self.inputs = rng.integers(0, 23, size=(2, 5)).astype(np.int64)
        self.targets = rng.integers(0, 23, size=(2, 5)).astype(np.int64)

    def test_an_untrained_model_starts_at_the_entropy_of_the_vocabulary(self):
        """The first number to check on any training run.

        A randomly initialised model should be exactly as surprised as
        guessing uniformly. A first loss far from ln(V) means the
        initialisation or the loss is wrong, not the model.
        """
        loss, _grads = self.pretrain.forward_backward(
            self.params, self.architecture, self.inputs, self.targets,
            compute_gradients=False,
        )
        self.assertAlmostEqual(loss, float(self.np.log(23)), places=2)

    def test_every_gradient_matches_a_finite_difference(self):
        loss, grads = self.pretrain.forward_backward(
            self.params, self.architecture, self.inputs, self.targets
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
                    up, _ = self.pretrain.forward_backward(
                        self.params, self.architecture, self.inputs, self.targets,
                        compute_gradients=False,
                    )
                    tensor[index] = original - epsilon
                    down, _ = self.pretrain.forward_backward(
                        self.params, self.architecture, self.inputs, self.targets,
                        compute_gradients=False,
                    )
                    tensor[index] = original

                    numeric = (up - down) / (2 * epsilon)
                    analytic = float(grads[name][index])
                    # Relative, with an absolute floor: a gradient of 1e-7 is
                    # below the precision of the difference that measures it.
                    self.assertAlmostEqual(
                        numeric, analytic, delta=1e-3 * abs(analytic) + 1e-8,
                    )

    def test_the_causal_mask_hides_the_future(self):
        """Changing a later token must not change an earlier prediction."""
        import numpy as np

        first, _ = self.pretrain.forward_backward(
            self.params, self.architecture, self.inputs, self.targets,
            compute_gradients=False,
        )
        changed = self.inputs.copy()
        changed[:, -1] = (changed[:, -1] + 1) % 23
        second, _ = self.pretrain.forward_backward(
            self.params, self.architecture, changed, self.targets,
            compute_gradients=False,
        )
        # Only the last position's prediction may move, so the mean loss
        # over five positions moves a little -- but not by nothing, and not
        # by everything.
        self.assertNotAlmostEqual(first, second, places=9)
        self.assertLess(abs(first - second), 0.5)
        self.assertTrue(np.isfinite(second))


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class OptimiserTests(unittest.TestCase):
    def setUp(self):
        self.pretrain = load_pretrain()
        import numpy as np

        self.np = np

    def test_it_walks_downhill_on_something_with_a_known_answer(self):
        # Minimise (x - 3)^2: the optimiser, on its own, away from the model.
        params = {"x": self.np.zeros(1, dtype=self.np.float32)}
        optimiser = self.pretrain.AdamW(params, learning_rate=0.1, weight_decay=0.0)
        for _ in range(400):
            grad = {"x": 2 * (params["x"] - 3.0)}
            optimiser.step(params, grad, clip=0.0)
        self.assertAlmostEqual(float(params["x"][0]), 3.0, places=2)

    def test_gradient_clipping_bounds_a_huge_step(self):
        params = {"x": self.np.zeros(1, dtype=self.np.float32)}
        optimiser = self.pretrain.AdamW(params, learning_rate=0.1)
        norm = optimiser.step(params, {"x": self.np.full(1, 1e6, dtype=self.np.float32)})
        self.assertAlmostEqual(norm, 1e6, places=0)
        self.assertLess(abs(float(params["x"][0])), 1.0)

    def test_norm_gains_are_not_decayed(self):
        """Decaying a normalisation gain toward zero fights the norm itself.

        It shows up as a loss that stalls rather than as an error, so it is
        worth an explicit test.
        """
        params = {"norm": self.np.ones(1, dtype=self.np.float32),
                  "w": self.np.ones(1, dtype=self.np.float32)}
        optimiser = self.pretrain.AdamW(params, learning_rate=0.0, weight_decay=0.5)
        zeros = {name: self.np.zeros(1, dtype=self.np.float32) for name in params}
        optimiser.step(params, zeros, learning_rate=0.1)
        self.assertEqual(float(params["norm"][0]), 1.0)
        self.assertLess(float(params["w"][0]), 1.0)

    def test_the_schedule_warms_up_and_decays(self):
        peak, total, warmup = 1.0, 100, 10
        self.assertLess(self.pretrain.schedule(0, total, peak, warmup), peak)
        self.assertAlmostEqual(self.pretrain.schedule(warmup - 1, total, peak, warmup),
                               peak)
        self.assertLess(self.pretrain.schedule(99, total, peak, warmup), peak)
        self.assertGreater(self.pretrain.schedule(99, total, peak, warmup), 0.0)


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.pretrain = load_pretrain()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_a_corpus_too_small_to_train_on_says_so(self):
        import numpy as np

        architecture = Architecture(vocab_size=20, dim=8, layers=1, heads=2, hidden=16)
        with self.assertRaises(SystemExit) as ctx:
            self.pretrain.train(
                np.arange(4, dtype=np.int32), architecture, steps=1, batch=1,
                length=32, learning_rate=1e-3, warmup=0, seed=0, report=lambda _: None,
            )
        self.assertIn("corpus", str(ctx.exception))

    def test_it_learns_something_and_the_result_generates(self):
        """The whole pipeline, end to end, on something tiny.

        The corpus is one sentence repeated, so a model this size will
        memorise it rather than learn language -- which is the point: if the
        loss does not collapse on a corpus this easy, something in the
        trainer is broken, and no amount of real data would rescue it.
        """
        tokenizer = train_tokenizer([SENTENCE * 40], vocab_size=500)
        stream = self.pretrain.pack_tokens(tokenizer, [SENTENCE * 40])
        architecture = Architecture(vocab_size=tokenizer.vocab_size, dim=32,
                                    layers=2, heads=4, hidden=64, context=32)

        params, record = self.pretrain.train(
            stream, architecture, steps=120, batch=4, length=32,
            learning_rate=3e-3, warmup=20, seed=0, report=lambda _: None,
        )

        first = record["history"][0]["loss"]
        last = record["history"][-1]["loss"]
        self.assertLess(last, first / 2)
        self.assertLess(last, 1.0)

        # ...and the weights it produced load and generate through the same
        # code path a node uses.
        from chainmind.nano import save_weights

        path = self.root / "atlas.cmw"
        save_weights(path, architecture, tokenizer,
                     {name: value.reshape(-1).tolist()
                      for name, value in params.items()},
                     meta={"trained_from": "scratch", **record})
        model = NanoModel.load(path)
        self.assertEqual(model.meta["trained_from"], "scratch")

        produced = list(model.generate(tokenizer.encode("سلام. من یک"),
                                       max_tokens=12, temperature=0.0))
        text = tokenizer.decode(produced)
        self.assertTrue(text.strip())
        # It memorised one sentence; it should be able to continue it.
        self.assertIn(text.strip()[:6], SENTENCE)

    def test_the_holdout_is_never_trained_on(self):
        import numpy as np

        tokenizer = train_tokenizer([SENTENCE * 10], vocab_size=400)
        stream = self.pretrain.pack_tokens(tokenizer, [SENTENCE * 10])
        architecture = Architecture(vocab_size=tokenizer.vocab_size, dim=16,
                                    layers=1, heads=2, hidden=32, context=16)
        _params, record = self.pretrain.train(
            stream, architecture, steps=4, batch=2, length=16, learning_rate=1e-3,
            warmup=1, seed=0, holdout=0.2, report=lambda _: None,
        )
        self.assertLess(record["train_tokens"], len(stream))
        self.assertIn("holdout", record["history"][0])
        self.assertTrue(np.isfinite(record["history"][0]["holdout"]))


if __name__ == "__main__":
    unittest.main()
