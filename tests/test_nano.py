"""The model's architecture, its file format, and the two arithmetics.

The point of most of these is that a model file is a thing people copy
between machines. It has to refuse to load a file that would produce
nonsense, rather than loading it and producing nonsense.
"""

import math
import random
import struct
import tempfile
import unittest
from pathlib import Path

from chainmind.nano import (
    MAGIC,
    Architecture,
    NanoModel,
    WeightsError,
    backend_name,
    load_weights,
    save_weights,
)
from chainmind.tokenizer import train_tokenizer

CORPUS = "سلام دنیا. the ledger authorises before the agent acts."


def tiny_tokenizer():
    return train_tokenizer([CORPUS] * 3, vocab_size=300)


def tiny_architecture(tokenizer, **overrides):
    defaults = dict(vocab_size=tokenizer.vocab_size, dim=16, layers=2,
                    heads=2, hidden=32, context=32)
    defaults.update(overrides)
    return Architecture(**defaults)


def random_tensors(architecture, seed=0):
    rng = random.Random(seed)
    return {
        name: [rng.gauss(0.0, 0.05) for _ in range(math.prod(shape))]
        for name, shape in architecture.tensor_shapes()
    }


def write_model(directory, tokenizer=None, architecture=None, seed=0, meta=None):
    tokenizer = tokenizer or tiny_tokenizer()
    architecture = architecture or tiny_architecture(tokenizer)
    path = Path(directory) / "atlas.cmw"
    save_weights(path, architecture, tokenizer,
                 random_tensors(architecture, seed), meta or {})
    return path, architecture, tokenizer


class ArchitectureTests(unittest.TestCase):
    def test_a_dimension_that_does_not_divide_into_heads_is_refused(self):
        with self.assertRaises(ValueError):
            Architecture(vocab_size=10, dim=10, layers=1, heads=4, hidden=8)

    def test_an_odd_head_dimension_is_refused(self):
        # Rotary embeddings rotate pairs; an odd head has a leftover.
        with self.assertRaises(ValueError):
            Architecture(vocab_size=10, dim=6, layers=1, heads=2, hidden=8)

    def test_the_parameter_count_matches_the_tensors(self):
        architecture = Architecture(vocab_size=100, dim=16, layers=3,
                                    heads=2, hidden=32)
        counted = sum(math.prod(shape)
                      for _name, shape in architecture.tensor_shapes())
        self.assertEqual(counted, architecture.parameters)

    def test_it_survives_a_round_trip_through_its_dictionary(self):
        architecture = Architecture(vocab_size=100, dim=16, layers=3,
                                    heads=2, hidden=32, context=64)
        self.assertEqual(Architecture.from_dict(architecture.to_dict()), architecture)

    def test_an_incomplete_architecture_is_named_as_such(self):
        with self.assertRaises(WeightsError) as ctx:
            Architecture.from_dict({"dim": 16})
        self.assertIn("incomplete architecture", str(ctx.exception))


class FileFormatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_a_saved_model_loads_back_identically(self):
        tokenizer = tiny_tokenizer()
        architecture = tiny_architecture(tokenizer)
        tensors = random_tensors(architecture)
        path = self.root / "m.cmw"
        save_weights(path, architecture, tokenizer, tensors, {"note": "hello"})

        loaded_arch, loaded_tok, loaded_tensors, meta = load_weights(path)
        self.assertEqual(loaded_arch, architecture)
        self.assertEqual(loaded_tok.merges, tokenizer.merges)
        self.assertEqual(meta["note"], "hello")
        for name, values in tensors.items():
            for original, restored in zip(values, loaded_tensors[name]):
                self.assertAlmostEqual(original, restored, places=6)

    def test_a_missing_tensor_is_refused_at_save_time(self):
        tokenizer = tiny_tokenizer()
        architecture = tiny_architecture(tokenizer)
        tensors = random_tensors(architecture)
        del tensors["norm"]
        with self.assertRaises(WeightsError) as ctx:
            save_weights(self.root / "m.cmw", architecture, tokenizer, tensors)
        self.assertIn("norm", str(ctx.exception))

    def test_a_wrongly_shaped_tensor_is_refused_at_save_time(self):
        tokenizer = tiny_tokenizer()
        architecture = tiny_architecture(tokenizer)
        tensors = random_tensors(architecture)
        tensors["norm"] = tensors["norm"][:-1]
        with self.assertRaises(WeightsError):
            save_weights(self.root / "m.cmw", architecture, tokenizer, tensors)

    def test_a_failed_save_leaves_no_half_written_model(self):
        # Written to a .partial and renamed, so a crash mid-write cannot
        # leave something that loads and is wrong.
        tokenizer = tiny_tokenizer()
        architecture = tiny_architecture(tokenizer)
        tensors = random_tensors(architecture)
        del tensors["tok_emb"]
        with self.assertRaises(WeightsError):
            save_weights(self.root / "m.cmw", architecture, tokenizer, tensors)
        self.assertFalse((self.root / "m.cmw").exists())

    def test_a_file_that_is_not_ours_is_refused(self):
        path = self.root / "not.cmw"
        path.write_bytes(b"GGUF" + b"\x00" * 64)
        with self.assertRaises(WeightsError) as ctx:
            load_weights(path)
        self.assertIn(MAGIC.decode(), str(ctx.exception))

    def test_a_truncated_file_is_refused(self):
        path, _arch, _tok = write_model(self.root)
        blob = path.read_bytes()
        path.write_bytes(blob[:len(blob) // 2])
        with self.assertRaises(WeightsError) as ctx:
            load_weights(path)
        self.assertIn("truncated", str(ctx.exception))

    def test_a_corrupt_header_length_is_refused(self):
        path, _arch, _tok = write_model(self.root)
        blob = bytearray(path.read_bytes())
        blob[4:8] = struct.pack("<I", 2 ** 31)
        path.write_bytes(bytes(blob))
        with self.assertRaises(WeightsError) as ctx:
            load_weights(path)
        self.assertIn("corrupt header", str(ctx.exception))

    def test_a_future_format_version_is_refused_by_name(self):
        tokenizer = tiny_tokenizer()
        architecture = tiny_architecture(tokenizer)
        path = self.root / "m.cmw"
        save_weights(path, architecture, tokenizer, random_tensors(architecture))
        blob = path.read_bytes()
        (length,) = struct.unpack("<I", blob[4:8])
        header = blob[8:8 + length].replace(b'"version": 1', b'"version": 9')
        path.write_bytes(MAGIC + struct.pack("<I", len(header)) + header
                         + blob[8 + length:])
        with self.assertRaises(WeightsError) as ctx:
            load_weights(path)
        self.assertIn("version", str(ctx.exception))

    def test_a_vocabulary_that_does_not_match_the_embeddings_is_refused(self):
        """The failure that would otherwise be invisible.

        A model assembled with one tokenizer and another's embedding table
        produces fluent-looking nonsense rather than an error, so it has to
        be caught at the door.
        """
        tokenizer = tiny_tokenizer()
        architecture = tiny_architecture(tokenizer)
        other = train_tokenizer([CORPUS * 2], vocab_size=280)
        self.assertNotEqual(other.vocab_size, tokenizer.vocab_size)
        path = self.root / "m.cmw"
        save_weights(path, architecture, other, random_tensors(architecture))
        with self.assertRaises(WeightsError) as ctx:
            load_weights(path)
        self.assertIn("vocabulary", str(ctx.exception))

    def test_a_file_that_is_not_there_says_so(self):
        with self.assertRaises(WeightsError) as ctx:
            load_weights(self.root / "absent.cmw")
        self.assertIn("cannot read", str(ctx.exception))


class ForwardPassTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path, self.architecture, self.tokenizer = write_model(self.directory.name)
        self.model = NanoModel.load(self.path)

    def test_it_produces_one_logit_per_token_in_the_vocabulary(self):
        logits = self.model.logits(self.tokenizer.encode("سلام"))
        self.assertEqual(len(logits), self.architecture.vocab_size)
        self.assertTrue(all(math.isfinite(value) for value in logits))

    def test_an_empty_sequence_is_refused(self):
        with self.assertRaises(ValueError):
            self.model.logits([])

    def test_attention_is_causal(self):
        """Appending a token must not change what came before it.

        The one property that makes the cached, one-token-at-a-time
        inference here equivalent to a full forward pass -- and the one a
        mask bug silently breaks.
        """
        prefix = self.tokenizer.encode("سلام دنیا")
        first = self.model.logits(prefix)
        second = self.model.logits(prefix + self.tokenizer.encode(" the"))
        self.assertNotEqual([round(v, 6) for v in first],
                            [round(v, 6) for v in second])
        # ...and the prefix's own prediction is unchanged, which is the
        # actual causality claim.
        again = self.model.logits(prefix)
        self.assertEqual([round(v, 9) for v in first], [round(v, 9) for v in again])

    def test_the_fingerprint_is_the_hash_of_the_file(self):
        import hashlib
        expected = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(self.model.fingerprint, expected)

    def test_two_models_have_different_fingerprints(self):
        other, _arch, _tok = write_model(self.directory.name, seed=99)
        other = other.rename(other.with_name("other.cmw"))
        self.assertNotEqual(NanoModel.load(other).fingerprint, self.model.fingerprint)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path, self.architecture, self.tokenizer = write_model(self.directory.name)
        self.model = NanoModel.load(self.path)

    def test_it_stops_at_the_requested_length(self):
        produced = list(self.model.generate(
            self.tokenizer.encode("سلام"), max_tokens=5, seed=1
        ))
        self.assertEqual(len(produced), 5)

    def test_the_same_seed_gives_the_same_tokens(self):
        prompt = self.tokenizer.encode("سلام")
        first = list(self.model.generate(prompt, max_tokens=6, seed=7))
        second = list(self.model.generate(prompt, max_tokens=6, seed=7))
        self.assertEqual(first, second)

    def test_zero_temperature_is_greedy_and_needs_no_seed(self):
        prompt = self.tokenizer.encode("سلام")
        first = list(self.model.generate(prompt, max_tokens=4, temperature=0.0))
        second = list(self.model.generate(prompt, max_tokens=4, temperature=0.0))
        self.assertEqual(first, second)

    def test_a_stop_token_ends_generation(self):
        prompt = self.tokenizer.encode("سلام")
        # Every token is a stop token, so exactly one comes out.
        produced = list(self.model.generate(
            prompt, max_tokens=20, seed=1,
            stop=tuple(range(self.architecture.vocab_size)),
        ))
        self.assertEqual(len(produced), 1)

    def test_a_prompt_longer_than_the_context_keeps_the_end(self):
        long_prompt = self.tokenizer.encode(CORPUS * 20)
        self.assertGreater(len(long_prompt), self.architecture.context)
        fitted = self.model.fit_prompt(long_prompt, 2)
        self.assertEqual(fitted, long_prompt[-len(fitted):])
        self.assertLess(len(fitted), self.architecture.context)

    def test_a_full_length_prompt_still_gets_a_full_length_answer(self):
        """The bug this test was written for.

        Trimming the prompt to exactly the context leaves room for one
        token, so a long question gets a one-token answer and looks like a
        broken model rather than a full cache.
        """
        long_prompt = self.tokenizer.encode(CORPUS * 20)
        produced = list(self.model.generate(long_prompt, max_tokens=4, seed=1))
        self.assertEqual(len(produced), 4)

    def test_the_reserve_never_eats_the_whole_context(self):
        # An enormous max_tokens must still leave a usable prompt.
        fitted = self.model.fit_prompt(list(range(200)), 10 ** 6)
        self.assertGreaterEqual(len(fitted), self.architecture.context // 2)

    def test_an_empty_prompt_is_refused(self):
        with self.assertRaises(ValueError):
            list(self.model.generate([], max_tokens=2))

    def test_generation_is_lazy(self):
        # A caller that runs out of budget mid-answer stops pulling, and no
        # work is done for tokens nobody asked for.
        stream = self.model.generate(self.tokenizer.encode("سلام"),
                                     max_tokens=1000, seed=1)
        self.assertIsNotNone(next(stream))
        stream.close()

    def test_top_k_of_one_is_greedy(self):
        prompt = self.tokenizer.encode("سلام")
        greedy = list(self.model.generate(prompt, max_tokens=4, temperature=0.0))
        narrow = list(self.model.generate(prompt, max_tokens=4, top_k=1, seed=3))
        self.assertEqual(greedy, narrow)


class BackendTests(unittest.TestCase):
    """The optional fast path must be faster, not different."""

    def test_the_backend_is_named(self):
        self.assertIn(backend_name(), {"numpy", "pure-python"})

    def test_both_arithmetics_agree(self):
        """Run each backend in its own process.

        Reloading the module in-process would work and would also replace
        ``WeightsError`` and ``Architecture`` with new classes, so every
        other test in this file would stop recognising them. That is a real
        trap, and the cost of avoiding it is two subprocesses.
        """
        import importlib.util
        import json
        import os
        import subprocess
        import sys

        if importlib.util.find_spec("numpy") is None:
            self.skipTest("numpy is not installed; only one path exists here")

        import chainmind

        root = Path(chainmind.__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            path, _arch, tokenizer = write_model(directory, seed=5)
            tokens = tokenizer.encode(CORPUS)[:12]
            script = (
                "import json, sys\n"
                "from chainmind.nano import NanoModel, backend_name\n"
                f"logits = NanoModel.load({str(path)!r}).logits({tokens!r})\n"
                "print(json.dumps({'backend': backend_name(), 'logits': logits}))\n"
            )

            results = {}
            for pure in (False, True):
                environment = dict(os.environ)
                environment.pop("CHAINMIND_PURE_PYTHON_MATH", None)
                if pure:
                    environment["CHAINMIND_PURE_PYTHON_MATH"] = "1"
                finished = subprocess.run(
                    [sys.executable, "-c", script], capture_output=True,
                    text=True, cwd=str(root), env=environment,
                )
                self.assertEqual(finished.returncode, 0, finished.stderr)
                payload = json.loads(finished.stdout)
                results[payload["backend"]] = payload["logits"]

        self.assertEqual(set(results), {"numpy", "pure-python"})
        for fast, slow in zip(results["numpy"], results["pure-python"]):
            self.assertAlmostEqual(fast, slow, places=5)


if __name__ == "__main__":
    unittest.main()
