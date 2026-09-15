import os
import unittest

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.local import LocalModel, LocalRuntimeUnavailable, discover_runtime
from chainmind.meter import Meter
from chainmind.models import BACKENDS, ModelUnavailable, build_model, build_model_tool, describe_model
from chainmind.resources import CREDIT
from chainmind.tools import ToolRegistry
from chainmind.transactions import TxType, build_grant

from fake_runtime import FakeRuntime
from support import AGENT, AUTHORITY, make_chain


class DiscoveryTests(unittest.TestCase):
    def test_a_listening_ollama_is_found(self):
        with FakeRuntime("ollama") as runtime:
            base, dialect, models = discover_runtime([(runtime.url, "ollama")])
        self.assertEqual(base, runtime.url)
        self.assertEqual(dialect, "ollama")
        self.assertIn("qwen2.5:7b", models)

    def test_a_listening_openai_compatible_server_is_found(self):
        with FakeRuntime("openai") as runtime:
            base, dialect, models = discover_runtime([(runtime.url, "openai")])
        self.assertEqual(dialect, "openai")
        self.assertIn("llama3.1", models)

    def test_nothing_listening_says_how_to_start_one(self):
        with self.assertRaises(LocalRuntimeUnavailable) as ctx:
            discover_runtime([("http://127.0.0.1:1", "ollama")])
        self.assertIn("ollama serve", str(ctx.exception))

    def test_an_explicit_url_overrides_the_search(self):
        with FakeRuntime("ollama") as runtime:
            os.environ["CHAINMIND_LOCAL_URL"] = runtime.url
            os.environ["CHAINMIND_LOCAL_DIALECT"] = "ollama"
            self.addCleanup(os.environ.pop, "CHAINMIND_LOCAL_URL", None)
            self.addCleanup(os.environ.pop, "CHAINMIND_LOCAL_DIALECT", None)
            base, dialect, _ = discover_runtime()
        self.assertEqual(base, runtime.url)


class OllamaDialectTests(unittest.TestCase):
    def test_a_turn_reaches_the_runtime_and_comes_back(self):
        with FakeRuntime("ollama", answer="سلام از مدل محلی") as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="ollama", model="qwen2.5:7b")
            with Meter("ask", charge_compute=False) as meter:
                result = engine.respond(meter, "سلام")
            self.assertEqual(result["text"], "سلام از مدل محلی")
            self.assertEqual(runtime.requests[-1]["model"], "qwen2.5:7b")
            self.assertFalse(runtime.requests[-1]["stream"])

    def test_the_runtime_counts_are_recorded_as_provider_reported(self):
        with FakeRuntime("ollama", prompt_tokens=222, completion_tokens=33) as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="ollama", model="m")
            with Meter("ask", charge_compute=False) as meter:
                engine.respond(meter, "سلام")
            record = meter.finish()
        self.assertEqual(record.totals["llm_input_tokens"], 222)
        self.assertEqual(record.totals["llm_output_tokens"], 33)
        self.assertEqual(record.source_of("llm_input_tokens"), "provider")

    def test_history_is_sent_with_a_system_turn_in_front(self):
        with FakeRuntime("ollama") as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="ollama", model="m")
            with Meter("ask", charge_compute=False) as meter:
                engine.respond(meter, "دوم", history=[
                    {"role": "user", "content": "اول"},
                    {"role": "assistant", "content": "پاسخ اول"},
                ])
            roles = [m["role"] for m in runtime.requests[-1]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])

    def test_the_output_ceiling_is_passed_through(self):
        with FakeRuntime("ollama") as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="ollama", model="m")
            with Meter("ask", charge_compute=False) as meter:
                engine.respond(meter, "سلام", max_tokens=321)
        self.assertEqual(runtime.requests[-1]["options"]["num_predict"], 321)


class OpenAiDialectTests(unittest.TestCase):
    def test_a_turn_works_against_an_openai_compatible_server(self):
        with FakeRuntime("openai", answer="local answer",
                         prompt_tokens=90, completion_tokens=12) as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="openai", model="llama3.1")
            with Meter("ask", charge_compute=False) as meter:
                result = engine.respond(meter, "hello")
            record = meter.finish()
        self.assertEqual(result["text"], "local answer")
        self.assertEqual(record.totals["llm_input_tokens"], 90)
        self.assertEqual(runtime.requests[-1]["max_tokens"], engine.max_tokens)

    def test_an_exact_token_count_is_used_when_the_runtime_offers_one(self):
        with FakeRuntime("openai", prompt_tokens=137, tokenize=True) as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="openai", model="m")
            estimate = engine.estimate("a prompt", max_tokens=50)
        self.assertEqual(estimate["llm_input_tokens"], 137)
        self.assertIn("/tokenize", runtime.paths)

    def test_without_a_tokenizer_the_estimate_errs_high(self):
        with FakeRuntime("openai", tokenize=False) as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="openai", model="m")
            prompt = "x" * 600
            estimate = engine.estimate(prompt)
        # Never below what the text plausibly costs: an under-estimate would
        # let the action slip past its own budget check.
        self.assertGreaterEqual(estimate["llm_input_tokens"], len(prompt) // 4)


class FailureTests(unittest.TestCase):
    def test_a_dead_runtime_is_reported_clearly(self):
        engine = LocalModel(base_url="http://127.0.0.1:1", dialect="ollama", model="m")
        with self.assertRaises(LocalRuntimeUnavailable) as ctx:
            with Meter("ask", charge_compute=False) as meter:
                engine.respond(meter, "سلام")
        self.assertIn("not reachable", str(ctx.exception))

    def test_an_empty_choices_list_is_an_error_not_an_empty_answer(self):
        engine = LocalModel(base_url="http://x", dialect="openai", model="m")
        with self.assertRaises(LocalRuntimeUnavailable):
            engine._parse({"choices": []})


class BackendSelectionTests(unittest.TestCase):
    def setUp(self):
        for name in ("CHAINMIND_BACKEND", "CHAINMIND_LOCAL_URL", "CHAINMIND_LOCAL_DIALECT"):
            os.environ.pop(name, None)
            self.addCleanup(os.environ.pop, name, None)

    def test_auto_prefers_a_local_runtime(self):
        with FakeRuntime("ollama") as runtime:
            os.environ["CHAINMIND_LOCAL_URL"] = runtime.url
            os.environ["CHAINMIND_LOCAL_DIALECT"] = "ollama"
            engine = build_model("auto")
        self.assertIsInstance(engine, LocalModel)
        self.assertEqual(engine.model, "qwen2.5:7b")   # first one the runtime offers

    def test_local_is_refused_clearly_when_nothing_is_running(self):
        os.environ["CHAINMIND_LOCAL_URL"] = "http://127.0.0.1:1"
        os.environ["CHAINMIND_LOCAL_DIALECT"] = "ollama"
        with self.assertRaises(ModelUnavailable) as ctx:
            build_model("local")
        self.assertIn("ollama serve", str(ctx.exception))

    def test_auto_with_nothing_available_names_both_paths(self):
        os.environ["CHAINMIND_LOCAL_URL"] = "http://127.0.0.1:1"
        os.environ["CHAINMIND_LOCAL_DIALECT"] = "ollama"
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            os.environ.pop(key, None)
        os.environ["ANTHROPIC_CONFIG_DIR"] = "/nonexistent-for-tests"
        self.addCleanup(os.environ.pop, "ANTHROPIC_CONFIG_DIR", None)
        with self.assertRaises(ModelUnavailable) as ctx:
            build_model("auto")
        message = str(ctx.exception)
        self.assertIn("ollama", message)
        self.assertIn("ANTHROPIC_API_KEY", message)

    def test_an_unknown_backend_is_refused(self):
        with self.assertRaises(ValueError):
            build_model("telepathy")

    def test_every_backend_name_is_documented(self):
        self.assertEqual(set(BACKENDS), {"auto", "local", "claude"})

    def test_a_local_model_describes_where_it_runs(self):
        with FakeRuntime("ollama") as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="ollama", model="m")
        self.assertIn(runtime.url, describe_model(engine))


class KernelIntegrationTests(unittest.TestCase):
    """The ledger must not be able to tell the two backends apart."""

    def test_a_local_answer_settles_on_chain_like_any_other(self):
        chain = make_chain()
        ledger = LocalLedger(chain, AUTHORITY)
        chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, chain.state.get(AUTHORITY.public_hex()).nonce,
                         beneficiary=AGENT.public_hex(), amount=5 * CREDIT)],
        )
        with FakeRuntime("ollama", answer="جواب محلی", prompt_tokens=60,
                         completion_tokens=20) as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="ollama", model="qwen2.5:7b")
            registry = ToolRegistry()
            registry.register(build_model_tool(engine))
            kernel = AgentKernel(AGENT, ledger, tools=registry, label="atlas")
            outcome = kernel.perform("ask", prompt="سلام")
            ledger.flush()

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.value["text"], "جواب محلی")
        measured = {tx.body["resource"]: tx.body["measured"]
                    for _, tx in chain.transactions() if tx.type is TxType.USAGE}
        self.assertEqual(measured["llm_input_tokens"], "provider")
        self.assertEqual(chain.verify().state_root(), chain.state.state_root())

    def test_an_unaffordable_local_turn_never_reaches_the_runtime(self):
        chain = make_chain()
        ledger = LocalLedger(chain, AUTHORITY)
        chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, chain.state.get(AUTHORITY.public_hex()).nonce,
                         beneficiary=AGENT.public_hex(), amount=100)],
        )
        with FakeRuntime("ollama") as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="ollama", model="m")
            registry = ToolRegistry()
            registry.register(build_model_tool(engine))
            kernel = AgentKernel(AGENT, ledger, tools=registry, label="atlas")
            outcome = kernel.perform("ask", prompt="یک سؤال")
            chat_requests = [p for p in runtime.paths if "chat" in p or "completions" in p]

        self.assertTrue(outcome.refused)
        self.assertEqual(chat_requests, [])


if __name__ == "__main__":
    unittest.main()
