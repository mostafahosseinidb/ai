import os
import unittest

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.local import LocalModel, LocalRuntimeUnavailable, discover_runtime
from chainmind.meter import Meter
from chainmind.models import ModelUnavailable, build_model, build_model_tool, describe_model
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

    def test_nothing_listening_points_at_the_self_contained_routes(self):
        with self.assertRaises(LocalRuntimeUnavailable) as ctx:
            discover_runtime([("http://127.0.0.1:1", "ollama")])
        message = str(ctx.exception)
        self.assertIn("training/pretrain.py", message)
        self.assertIn(".gguf", message)
        self.assertNotIn("ollama serve", message)

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


class RuntimeSelectionTests(unittest.TestCase):
    """There is one kind of backend, and it is the operator's own machine."""

    def setUp(self):
        for name in ("CHAINMIND_LOCAL_URL", "CHAINMIND_LOCAL_DIALECT", "CHAINMIND_LOCAL_MODEL"):
            os.environ.pop(name, None)
            self.addCleanup(os.environ.pop, name, None)

    def test_a_running_runtime_is_picked_up(self):
        with FakeRuntime("ollama") as runtime:
            os.environ["CHAINMIND_LOCAL_URL"] = runtime.url
            os.environ["CHAINMIND_LOCAL_DIALECT"] = "ollama"
            engine = build_model()
        self.assertIsInstance(engine, LocalModel)
        self.assertEqual(engine.model, "qwen2.5:7b")   # the first one it offers

    def test_a_named_model_wins_over_the_default(self):
        with FakeRuntime("ollama") as runtime:
            os.environ["CHAINMIND_LOCAL_URL"] = runtime.url
            os.environ["CHAINMIND_LOCAL_DIALECT"] = "ollama"
            engine = build_model("llama3.1")
        self.assertEqual(engine.model, "llama3.1")

    def test_nothing_running_says_how_to_get_a_model(self):
        os.environ["CHAINMIND_LOCAL_URL"] = "http://127.0.0.1:1"
        os.environ["CHAINMIND_LOCAL_DIALECT"] = "ollama"
        with self.assertRaises(ModelUnavailable) as ctx:
            build_model()
        self.assertIn("training/pretrain.py", str(ctx.exception))

    def test_the_project_offers_no_hosted_path(self):
        # Guards the promise rather than the implementation: nothing in the
        # package may reach a third-party inference service.
        import chainmind, pathlib as _p
        root = _p.Path(chainmind.__file__).parent
        offenders = [
            path.name for path in root.glob("*.py")
            if "anthropic" in path.read_text() or "api.openai.com" in path.read_text()
        ]
        self.assertEqual(offenders, [])

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


class EstimateCoversEverythingTests(unittest.TestCase):
    """An estimate that misses part of the prompt is a budget bypass."""

    def test_a_longer_system_prompt_costs_more(self):
        with FakeRuntime("openai", prompt_tokens=50, tokenize=False) as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="openai", model="m")
            plain = engine.estimate("سؤال")
            augmented = engine.estimate("سؤال", system=engine.system + "\n" + "خ" * 900)
        self.assertGreater(augmented["llm_input_tokens"], plain["llm_input_tokens"])

    def test_the_tool_passes_the_system_prompt_into_the_estimate(self):
        with FakeRuntime("openai", tokenize=False) as runtime:
            engine = LocalModel(base_url=runtime.url, dialect="openai", model="m")
            tool = build_model_tool(engine)
            plain = tool.estimated_cost(prompt="سؤال")
            augmented = tool.estimated_cost(prompt="سؤال", system="ی" * 1200)
        self.assertGreater(augmented["llm_input_tokens"], plain["llm_input_tokens"])


class DependencyPromiseTests(unittest.TestCase):
    """Guards the promise, not the implementation.

    "Install and become a node" is only true if a node needs nothing but
    Python. A dependency added casually breaks that for every small machine
    the network is supposed to run on, so it has to fail a test rather than
    a deployment.
    """

    #: The allowed exceptions. Each is optional, each has a tested
    #: standard-library fallback or an alternative backend, and each is
    #: imported inside a function so that its absence is a clear message
    #: rather than an import error. All three are asserted below to be absent
    #: from module scope, and the package is imported with all three blocked.
    #: Adding a fourth should require the same argument, which is why this
    #: list is a test and not a comment.
    ALLOWED = {
        ("crypto.py", "cryptography"),      # a faster Ed25519
        ("embedded.py", "llama_cpp"),       # in-process GGUF inference
        ("nano.py", "numpy"),               # arithmetic for the project's own model
    }

    def external_imports(self):
        import ast
        import pathlib
        import sys

        stdlib = set(sys.stdlib_module_names)
        import chainmind

        found = set()
        for path in sorted(pathlib.Path(chainmind.__file__).parent.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top not in stdlib:
                            found.add((path.name, top))
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    top = (node.module or "").split(".")[0]
                    if top and top not in stdlib:
                        found.add((path.name, top))
        return found

    def test_the_package_imports_nothing_outside_the_standard_library(self):
        self.assertEqual(self.external_imports() - self.ALLOWED, set())

    def test_every_optional_dependency_is_really_optional(self):
        """None of them may be imported at module scope.

        A top-level import is the difference between "better when present"
        and "broken when absent". Checked against the syntax tree rather
        than the text, because these modules' own prose names them and
        should be allowed to.
        """
        import ast
        import pathlib

        import chainmind

        for filename, package in sorted(self.ALLOWED):
            with self.subTest(module=filename):
                tree = ast.parse(
                    (pathlib.Path(chainmind.__file__).parent / filename)
                    .read_text(encoding="utf-8")
                )
                top_level = []
                for node in tree.body:
                    if isinstance(node, ast.Import):
                        top_level += [alias.name.split(".")[0] for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        top_level.append((node.module or "").split(".")[0])
                self.assertNotIn(package, top_level)

                # And it must still be reachable, or the code path is dead.
                nested: list[str] = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        nested.append((node.module or "").split(".")[0])
                    elif isinstance(node, ast.Import):
                        nested += [alias.name.split(".")[0] for alias in node.names]
                self.assertIn(package, nested)

    def test_the_package_runs_with_every_optional_dependency_absent(self):
        """A fresh install has none of them, so import the package with none.

        Run in a subprocess with the three blocked at the import hook rather
        than by checking what this container happens to have: a developer who
        installs NumPy to work on the model should not be the reason this
        stops testing the promise.
        """
        import pathlib
        import subprocess
        import sys

        import chainmind

        root = pathlib.Path(chainmind.__file__).resolve().parent.parent
        blocked = sorted(package for _file, package in self.ALLOWED)
        script = (
            "import sys\n"
            f"BLOCKED = {blocked!r}\n"
            "class Blocker:\n"
            "    def find_module(self, name, path=None):\n"
            "        return None\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in BLOCKED:\n"
            "            raise ImportError(name + ' is blocked for this test')\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocker())\n"
            "for name in BLOCKED:\n"
            "    sys.modules.pop(name, None)\n"
            "import chainmind\n"
            "from chainmind import cli, crypto, embedded, local, models, nano, native\n"
            "assert crypto.BACKEND == 'pure-python', crypto.BACKEND\n"
            "assert nano.backend_name() == 'pure-python', nano.backend_name()\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            cwd=str(root),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_training_lives_outside_the_package(self):
        # torch belongs to training, and training is not part of running.
        import pathlib

        import chainmind

        package = pathlib.Path(chainmind.__file__).parent
        self.assertFalse((package / "train_lora.py").exists())
        self.assertTrue((package.parent / "training" / "train_lora.py").exists())
