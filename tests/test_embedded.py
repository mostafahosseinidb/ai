import json
import tempfile
import unittest
from pathlib import Path

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.embedded import (
    EmbeddedModel,
    EmbeddedUnavailable,
    discover_models,
    model_fingerprint,
)
from chainmind.meter import Meter
from chainmind.models import ModelUnavailable, build_model, build_model_tool, model_is_path
from chainmind.resources import CREDIT
from chainmind.tools import ToolRegistry
from chainmind.transactions import TxType, build_grant

from support import AGENT, AUTHORITY, make_chain


class FakeLlama:
    """The slice of llama_cpp.Llama this project uses, and nothing else."""

    def __init__(self, answer: str = "the answer", prompt_tokens: int = 0,
                 completion_tokens: int = 7, finish_reason: str = "stop",
                 fail: Exception | None = None) -> None:
        self.answer = answer
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.finish_reason = finish_reason
        self.fail = fail
        self.calls: list[dict] = []
        self.tokenised: list[bytes] = []

    def tokenize(self, text: bytes, add_bos: bool = True, special: bool = False):
        self.tokenised.append(text)
        # One token per word is wrong for a real tokenizer and exactly right
        # for asserting that the count comes from here rather than a guess.
        return list(range(len(text.decode("utf-8").split())))

    def create_chat_completion(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), **kwargs})
        if self.fail is not None:
            raise self.fail
        return {
            "choices": [{"message": {"role": "assistant", "content": self.answer},
                         "finish_reason": self.finish_reason}],
            "usage": {"prompt_tokens": self.prompt_tokens or len(messages) * 10,
                      "completion_tokens": self.completion_tokens},
        }


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        (self.root / "models").mkdir()

    def weights(self, name: str = "atlas.gguf", content: bytes = b"GGUF fake weights") -> Path:
        path = self.root / "models" / name
        path.write_bytes(content)
        return path

    def model(self, **kwargs) -> EmbeddedModel:
        engine = kwargs.pop("engine", None) or FakeLlama()
        return EmbeddedModel(workspace=self.root, engine=engine, **kwargs)


class DiscoveryTests(WorkspaceTestCase):
    def test_a_gguf_in_the_workspace_is_found(self):
        path = self.weights()
        self.assertEqual(discover_models(self.root), [path])

    def test_other_files_are_ignored(self):
        self.weights()
        (self.root / "models" / "notes.txt").write_text("not weights")
        (self.root / "models" / "old.bin").write_bytes(b"x")
        self.assertEqual([p.name for p in discover_models(self.root)], ["atlas.gguf"])

    def test_the_largest_file_comes_first(self):
        # Someone with a test model and a real one meant the real one.
        self.weights("small.gguf", b"x" * 10)
        self.weights("large.gguf", b"x" * 1000)
        self.assertEqual([p.name for p in discover_models(self.root)],
                         ["large.gguf", "small.gguf"])

    def test_a_workspace_with_no_models_directory_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as bare:
            self.assertEqual(discover_models(bare), [])

    def test_a_missing_model_says_where_to_put_one(self):
        with self.assertRaises(EmbeddedUnavailable) as ctx:
            EmbeddedModel(workspace=self.root)
        self.assertIn("models", str(ctx.exception))
        self.assertIn(".gguf", str(ctx.exception))


class FingerprintTests(WorkspaceTestCase):
    def test_the_fingerprint_is_the_content_hash(self):
        import hashlib
        path = self.weights(content=b"specific bytes")
        self.assertEqual(model_fingerprint(path),
                         hashlib.sha256(b"specific bytes").hexdigest())

    def test_different_weights_fingerprint_differently(self):
        first = self.weights("a.gguf", b"one")
        second = self.weights("b.gguf", b"two")
        self.assertNotEqual(model_fingerprint(first), model_fingerprint(second))

    def test_the_hash_is_cached_beside_the_file(self):
        path = self.weights()
        model_fingerprint(path)
        sidecar = path.with_suffix(path.suffix + ".fingerprint")
        self.assertTrue(sidecar.exists())
        self.assertEqual(json.loads(sidecar.read_text())["size"], path.stat().st_size)

    def test_replacing_the_weights_invalidates_the_cache(self):
        path = self.weights(content=b"first")
        before = model_fingerprint(path)
        import os
        import time
        time.sleep(1.1)          # the cache key includes whole-second mtime
        path.write_bytes(b"second")
        self.assertNotEqual(model_fingerprint(path), before)

    def test_a_corrupt_cache_is_recomputed_not_trusted(self):
        path = self.weights()
        expected = model_fingerprint(path)
        path.with_suffix(path.suffix + ".fingerprint").write_text("{ not json")
        self.assertEqual(model_fingerprint(path), expected)


class EstimateTests(WorkspaceTestCase):
    def test_the_count_comes_from_the_tokenizer(self):
        self.weights()
        engine = FakeLlama()
        model = self.model(engine=engine)
        estimate = model.estimate("یک دو سه", max_tokens=100)
        self.assertTrue(engine.tokenised)          # it really asked
        self.assertEqual(estimate["llm_output_tokens"], 100)

    def test_a_longer_prompt_costs_more(self):
        self.weights()
        model = self.model()
        short = model.estimate("یک")["llm_input_tokens"]
        long = model.estimate(" ".join(["کلمه"] * 200))["llm_input_tokens"]
        self.assertGreater(long, short)

    def test_the_system_prompt_is_counted(self):
        self.weights()
        model = self.model()
        plain = model.estimate("سؤال")["llm_input_tokens"]
        augmented = model.estimate("سؤال", system="ی " * 400)["llm_input_tokens"]
        self.assertGreater(augmented, plain)

    def test_a_broken_tokenizer_falls_back_upward(self):
        class Broken(FakeLlama):
            def tokenize(self, *args, **kwargs):
                raise RuntimeError("tokenizer unavailable")

        self.weights()
        model = self.model(engine=Broken())
        prompt = "x" * 600
        # Never below what the text plausibly costs: under-estimating would
        # let the turn slip past the budget check.
        self.assertGreaterEqual(model.estimate(prompt)["llm_input_tokens"], len(prompt) // 4)


class ResponseTests(WorkspaceTestCase):
    def meter(self):
        return Meter("ask", charge_compute=False)

    def test_an_answer_comes_back_with_its_counts(self):
        self.weights()
        model = self.model(engine=FakeLlama(answer="پاسخ محلی", prompt_tokens=42,
                                            completion_tokens=9))
        with self.meter() as meter:
            result = model.respond(meter, "سلام")
        record = meter.finish()
        self.assertEqual(result["text"], "پاسخ محلی")
        self.assertEqual(record.totals["llm_input_tokens"], 42)
        self.assertEqual(record.source_of("llm_output_tokens"), "provider")

    def test_the_conversation_is_sent_with_a_system_turn_first(self):
        self.weights()
        engine = FakeLlama()
        model = self.model(engine=engine)
        with self.meter() as meter:
            model.respond(meter, "دوم", history=[
                {"role": "user", "content": "اول"},
                {"role": "assistant", "content": "پاسخ اول"},
            ])
        self.assertEqual([m["role"] for m in engine.calls[-1]["messages"]],
                         ["system", "user", "assistant", "user"])

    def test_the_weights_are_part_of_the_evidence(self):
        # Two fine-tunes must not produce indistinguishable records.
        self.weights(content=b"version one")
        model = self.model()
        with self.meter() as meter:
            model.respond(meter, "سلام")
        record = meter.finish()
        self.assertEqual(record.context["weights"], model.fingerprint)
        self.assertEqual(record.context["runtime"], "embedded")

    def test_two_different_weights_give_different_evidence(self):
        self.weights("a.gguf", b"one" * 100)
        self.weights("b.gguf", b"two" * 50)
        first = EmbeddedModel(path=self.root / "models" / "a.gguf", engine=FakeLlama())
        second = EmbeddedModel(path=self.root / "models" / "b.gguf", engine=FakeLlama())
        digests = []
        for model in (first, second):
            with self.meter() as meter:
                model.respond(meter, "سلام")
            digests.append(meter.finish().digest)
        self.assertNotEqual(*digests)

    def test_truncation_is_reported(self):
        self.weights()
        model = self.model(engine=FakeLlama(finish_reason="length"))
        with self.meter() as meter:
            result = model.respond(meter, "بنویس")
        self.assertTrue(result["truncated"])

    def test_an_engine_failure_becomes_a_clear_error(self):
        self.weights()
        model = self.model(engine=FakeLlama(fail=RuntimeError("out of memory")))
        with self.assertRaises(EmbeddedUnavailable) as ctx:
            with self.meter() as meter:
                model.respond(meter, "سلام")
        self.assertIn("out of memory", str(ctx.exception))

    def test_the_result_survives_a_json_round_trip(self):
        self.weights()
        model = self.model()
        with self.meter() as meter:
            result = model.respond(meter, "سلام")
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_the_description_names_the_weights(self):
        self.weights("atlas.gguf")
        model = self.model()
        self.assertIn("atlas", model.describe())
        self.assertIn(model.fingerprint[:12], model.describe())


class SelectionTests(WorkspaceTestCase):
    def test_a_gguf_path_is_recognised_as_a_file(self):
        self.assertTrue(model_is_path("models/atlas.gguf"))
        self.assertFalse(model_is_path("qwen2.5:7b"))
        self.assertFalse(model_is_path(None))

    def test_a_workspace_with_no_model_and_no_runtime_names_every_route(self):
        import os
        os.environ["CHAINMIND_LOCAL_URL"] = "http://127.0.0.1:1"
        os.environ["CHAINMIND_LOCAL_DIALECT"] = "ollama"
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

    def test_an_embedded_answer_settles_like_any_other(self):
        self.weights()
        chain = make_chain()
        ledger = LocalLedger(chain, AUTHORITY)
        chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, chain.state.get(AUTHORITY.public_hex()).nonce,
                         beneficiary=AGENT.public_hex(), amount=5 * CREDIT)],
        )
        registry = ToolRegistry()
        registry.register(build_model_tool(self.model(engine=FakeLlama(answer="جواب"))))
        kernel = AgentKernel(AGENT, ledger, tools=registry, label="atlas")

        outcome = kernel.perform("ask", prompt="سؤال")
        ledger.flush()

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.value["text"], "جواب")
        measured = {tx.body["resource"]: tx.body["measured"]
                    for _, tx in chain.transactions() if tx.type is TxType.USAGE}
        self.assertEqual(measured["llm_input_tokens"], "provider")
        self.assertEqual(chain.verify().state_root(), chain.state.state_root())

    def test_an_unaffordable_turn_never_reaches_the_engine(self):
        self.weights()
        chain = make_chain()
        ledger = LocalLedger(chain, AUTHORITY)
        chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, chain.state.get(AUTHORITY.public_hex()).nonce,
                         beneficiary=AGENT.public_hex(), amount=50)],
        )
        engine = FakeLlama()
        registry = ToolRegistry()
        registry.register(build_model_tool(self.model(engine=engine)))
        kernel = AgentKernel(AGENT, ledger, tools=registry, label="atlas")

        outcome = kernel.perform("ask", prompt="یک سؤال")
        self.assertTrue(outcome.refused)
        self.assertEqual(engine.calls, [])


if __name__ == "__main__":
    unittest.main()
