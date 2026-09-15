import unittest
from dataclasses import dataclass, field
from typing import Any

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.models import ClaudeModel, ModelUnavailable, build_model_tool
from chainmind.resources import CREDIT
from chainmind.tools import ToolRegistry
from chainmind.transactions import TxType, build_grant

from support import AGENT, AUTHORITY, make_chain


# --------------------------------------------------------------------------
# A stand-in shaped like the SDK's response, so nothing here touches the
# network or costs money.
# --------------------------------------------------------------------------

@dataclass
class FakeBlock:
    type: str
    text: str = ""


@dataclass
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeDetails:
    category: str = "cyber"
    explanation: str = "declined for safety reasons"


@dataclass
class FakeResponse:
    content: list = field(default_factory=list)
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "end_turn"
    stop_details: Any = None
    model: str = "claude-opus-5"


class FakeMessages:
    def __init__(self, response=None, counted=42, fail=None):
        self.response = response or FakeResponse(
            content=[FakeBlock("text", "the answer")],
            usage=FakeUsage(input_tokens=100, output_tokens=25),
        )
        self.counted = counted
        self.fail = fail
        self.requests: list[dict] = []
        self.count_calls: list[dict] = []

    def count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        if isinstance(self.counted, Exception):
            raise self.counted
        return type("Counted", (), {"input_tokens": self.counted})()

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.fail is not None:
            raise self.fail
        return self.response


class FakeClient:
    def __init__(self, **kwargs):
        self.messages = FakeMessages(**kwargs)


def model(**kwargs) -> ClaudeModel:
    client = FakeClient(**kwargs)
    return ClaudeModel(client=client, model="claude-opus-5")


class RequestShapeTests(unittest.TestCase):
    def test_the_request_names_the_configured_model(self):
        engine = model()
        from chainmind.meter import Meter
        with Meter("ask", charge_compute=False) as meter:
            engine.respond(meter, "hello")
        request = engine.client.messages.requests[0]
        self.assertEqual(request["model"], "claude-opus-5")
        self.assertEqual(request["messages"], [{"role": "user", "content": "hello"}])

    def test_thinking_is_adaptive_and_effort_is_inside_output_config(self):
        engine = model()
        engine.effort = "medium"
        from chainmind.meter import Meter
        with Meter("ask", charge_compute=False) as meter:
            engine.respond(meter, "hello")
        request = engine.client.messages.requests[0]
        self.assertEqual(request["thinking"], {"type": "adaptive"})
        self.assertEqual(request["output_config"], {"effort": "medium"})
        # budget_tokens is rejected by this model family; it must not appear.
        self.assertNotIn("budget_tokens", request.get("thinking", {}))

    def test_max_tokens_can_be_overridden_per_call(self):
        engine = model()
        from chainmind.meter import Meter
        with Meter("ask", charge_compute=False) as meter:
            engine.respond(meter, "hello", max_tokens=512)
        self.assertEqual(engine.client.messages.requests[0]["max_tokens"], 512)


class EstimationTests(unittest.TestCase):
    def test_the_estimate_uses_the_real_token_count(self):
        engine = model(counted=137)
        estimate = engine.estimate("a prompt", max_tokens=400)
        self.assertEqual(estimate["llm_input_tokens"], 137)
        self.assertEqual(estimate["llm_output_tokens"], 400)

    def test_counting_asks_about_the_same_system_prompt(self):
        engine = model()
        engine.estimate("a prompt")
        call = engine.client.messages.count_calls[0]
        self.assertEqual(call["system"], engine.system)
        self.assertEqual(call["messages"], [{"role": "user", "content": "a prompt"}])

    def test_an_unreachable_counter_falls_back_pessimistically(self):
        engine = model(counted=RuntimeError("endpoint down"))
        prompt = "x" * 300
        estimate = engine.estimate(prompt)
        # Never zero, and never below what the text plausibly costs: an
        # under-estimate would let the action slip past its budget check.
        self.assertGreaterEqual(estimate["llm_input_tokens"], len(prompt) // 4)


class UsageTests(unittest.TestCase):
    def meter(self):
        from chainmind.meter import Meter
        return Meter("ask", charge_compute=False)

    def test_tokens_are_recorded_as_provider_reported(self):
        engine = model()
        with self.meter() as meter:
            engine.respond(meter, "hello")
        record = meter.finish()
        self.assertEqual(record.totals["llm_input_tokens"], 100)
        self.assertEqual(record.totals["llm_output_tokens"], 25)
        self.assertEqual(record.source_of("llm_input_tokens"), "provider")
        self.assertEqual(record.source_of("llm_output_tokens"), "provider")

    def test_cached_input_tokens_are_still_input_tokens(self):
        engine = model(response=FakeResponse(
            content=[FakeBlock("text", "hi")],
            usage=FakeUsage(input_tokens=10, output_tokens=5,
                            cache_read_input_tokens=900, cache_creation_input_tokens=90),
        ))
        with self.meter() as meter:
            engine.respond(meter, "hello")
        self.assertEqual(meter.finish().totals["llm_input_tokens"], 1000)

    def test_the_answer_is_the_concatenated_text_blocks(self):
        engine = model(response=FakeResponse(
            content=[FakeBlock("thinking"), FakeBlock("text", "part one "),
                     FakeBlock("text", "part two")],
            usage=FakeUsage(input_tokens=1, output_tokens=1),
        ))
        with self.meter() as meter:
            result = engine.respond(meter, "hello")
        self.assertEqual(result["text"], "part one part two")

    def test_a_refusal_is_reported_and_still_billed(self):
        engine = model(response=FakeResponse(
            content=[], usage=FakeUsage(input_tokens=50, output_tokens=2),
            stop_reason="refusal", stop_details=FakeDetails(),
        ))
        with self.meter() as meter:
            result = engine.respond(meter, "something disallowed")
        self.assertTrue(result["refused"])
        self.assertEqual(result["category"], "cyber")
        self.assertEqual(result["text"], "")
        self.assertEqual(meter.finish().totals["llm_input_tokens"], 50)

    def test_truncation_is_surfaced(self):
        engine = model(response=FakeResponse(
            content=[FakeBlock("text", "cut off here")],
            usage=FakeUsage(input_tokens=5, output_tokens=512),
            stop_reason="max_tokens",
        ))
        with self.meter() as meter:
            result = engine.respond(meter, "write forever")
        self.assertTrue(result["truncated"])

    def test_a_transport_failure_becomes_a_clear_tool_error(self):
        engine = model(fail=ConnectionError("no route to host"))
        with self.assertRaises(ModelUnavailable) as ctx:
            with self.meter() as meter:
                engine.respond(meter, "hello")
        self.assertIn("no route to host", str(ctx.exception))

    def test_the_result_survives_a_json_round_trip(self):
        # It has to: sandboxed tools return through a pipe.
        import json
        engine = model()
        with self.meter() as meter:
            result = engine.respond(meter, "hello")
        self.assertEqual(json.loads(json.dumps(result)), result)


class KernelIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.chain = make_chain()
        self.ledger = LocalLedger(self.chain, AUTHORITY)
        self.registry = ToolRegistry()
        self.engine = model()
        self.registry.register(build_model_tool(self.engine))
        self.kernel = AgentKernel(AGENT, self.ledger, tools=self.registry, label="atlas")

    def fund(self, microcredits):
        nonce = self.chain.state.get(AUTHORITY.public_hex()).nonce
        self.chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, nonce, beneficiary=AGENT.public_hex(), amount=microcredits)],
        )

    def usage_txs(self):
        return [tx for _, tx in self.chain.transactions() if tx.type is TxType.USAGE]

    def test_an_answer_settles_as_provider_measured_usage(self):
        self.fund(5 * CREDIT)
        outcome = self.kernel.perform("ask", prompt="what is the capital of France?")
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.value["text"], "the answer")
        sources = {tx.body["resource"]: tx.body["measured"] for tx in self.usage_txs()}
        self.assertEqual(sources["llm_input_tokens"], "provider")
        self.assertEqual(sources["llm_output_tokens"], "provider")

    def test_a_prompt_the_agent_cannot_afford_never_reaches_the_model(self):
        self.fund(100)                      # far too little
        outcome = self.kernel.perform("ask", prompt="an expensive question")
        self.assertTrue(outcome.refused)
        self.assertFalse(outcome.executed)
        self.assertEqual(self.engine.client.messages.requests, [])   # never called
        self.assertEqual(self.usage_txs(), [])

    def test_authorisation_uses_the_counted_input_tokens(self):
        self.fund(5 * CREDIT)
        self.kernel.perform("ask", prompt="hello")
        self.assertTrue(self.engine.client.messages.count_calls)   # counted before calling

    def test_the_chain_still_verifies_afterwards(self):
        self.fund(5 * CREDIT)
        self.kernel.perform("ask", prompt="hello")
        self.ledger.flush()
        self.assertEqual(self.chain.verify().state_root(), self.chain.state.state_root())
        self.chain.state.check_invariants()

    def test_a_model_failure_is_billed_for_what_it_burned(self):
        self.fund(5 * CREDIT)
        self.engine.client.messages.fail = ConnectionError("upstream down")
        outcome = self.kernel.perform("ask", prompt="hello")
        self.assertFalse(outcome.ok)
        self.assertIn("upstream down", outcome.error)


class ProvenanceReportingTests(unittest.TestCase):
    """The three classes must stay distinguishable all the way to the reports."""

    def setUp(self):
        self.chain = make_chain()
        self.ledger = LocalLedger(self.chain, AUTHORITY)
        registry = ToolRegistry()
        self.engine = model()
        registry.register(build_model_tool(self.engine))
        from chainmind.tools import default_registry
        for tool in default_registry():
            registry.register(tool)
        from chainmind.executors import SandboxExecutor
        self.kernel = AgentKernel(AGENT, self.ledger, tools=registry, label="atlas",
                                  executor=SandboxExecutor())
        nonce = self.chain.state.get(AUTHORITY.public_hex()).nonce
        self.chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, nonce, beneficiary=AGENT.public_hex(), amount=20 * CREDIT)],
        )

    def test_a_chain_can_hold_all_three_sources_at_once(self):
        self.kernel.perform("ask", prompt="a question")        # provider + kernel cpu
        self.kernel.perform("think", prompt="offline", budget_tokens=16)   # declared + kernel
        self.ledger.flush()

        found = {tx.body.get("measured", "declared") for tx in
                 (tx for _, tx in self.chain.transactions() if tx.type is TxType.USAGE)}
        self.assertEqual(found, {"kernel", "provider", "declared"})

    def test_the_server_totals_every_source(self):
        from chainmind.server import build_usage
        self.kernel.perform("ask", prompt="a question")
        self.kernel.perform("think", prompt="offline", budget_tokens=16)
        self.ledger.flush()

        usage = build_usage(self.chain)
        self.assertEqual(sum(usage["by_measurement"].values()), usage["total_cost"])
        for bucket in usage["by_resource"].values():
            self.assertEqual(sum(bucket["by_source"].values()), bucket["cost"])

    def test_a_sandboxed_model_call_does_not_relabel_tokens_as_kernel(self):
        # The tokens are the provider's count; running the call in a child
        # process must not promote them to a kernel measurement.
        self.kernel.perform("ask", prompt="a question")
        self.ledger.flush()
        by_resource = {
            tx.body["resource"]: tx.body["measured"]
            for _, tx in self.chain.transactions() if tx.type is TxType.USAGE
        }
        self.assertEqual(by_resource["llm_input_tokens"], "provider")
        self.assertEqual(by_resource.get("compute_ms"), "kernel")


if __name__ == "__main__":
    unittest.main()
