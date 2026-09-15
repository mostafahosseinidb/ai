import unittest

from chainmind.agent import AgentHalted, AgentKernel, LocalLedger, StaticPlanner, Step
from chainmind.meter import Meter
from chainmind.resources import CREDIT, ResourceKind
from chainmind.tools import Tool, ToolRegistry, default_registry
from chainmind.transactions import TxType

from support import AGENT, AUTHORITY, make_chain


def cheap_tool(cost_units: int = 10, name: str = "cheap", actual: int | None = None) -> Tool:
    """A tool that burns exactly ``actual`` output tokens but quotes ``cost_units``."""
    burned = cost_units if actual is None else actual

    def run(meter: Meter, **kwargs):
        meter.record(ResourceKind.LLM_OUTPUT_TOKENS, burned)
        return "done"

    return Tool(
        name=name,
        description="a predictable tool, for tests",
        run=run,
        estimate=lambda kw: {ResourceKind.LLM_OUTPUT_TOKENS: cost_units},
    )


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.chain = make_chain(limits={"tool_call": 2}, epoch_length=1000)
        self.ledger = LocalLedger(self.chain, AUTHORITY)
        self.registry = ToolRegistry()
        self.kernel = AgentKernel(AGENT, self.ledger, tools=self.registry, label="atlas")

    def fund(self, microcredits):
        from chainmind.transactions import build_grant
        nonce = self.chain.state.get(AUTHORITY.public_hex()).nonce
        self.chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, nonce, beneficiary=self.kernel.address, amount=microcredits)],
        )

    def usage_transactions(self):
        return [tx for _, tx in self.chain.transactions() if tx.type is TxType.USAGE]

    def attestations(self):
        return [tx for _, tx in self.chain.transactions() if tx.type is TxType.ATTEST]


class AuthorisationTests(AgentTestCase):
    def test_an_unaffordable_action_never_runs(self):
        marker = []

        def run(meter, **kwargs):
            marker.append("executed")
            return None

        self.registry.register(
            Tool("expensive", "", run, lambda kw: {ResourceKind.LLM_OUTPUT_TOKENS: 10 ** 6})
        )
        outcome = self.kernel.perform("expensive")
        self.assertTrue(outcome.refused)
        self.assertFalse(outcome.executed)
        self.assertEqual(marker, [])
        self.assertIn("holds", outcome.reason)

    def test_a_refusal_is_recorded_on_chain(self):
        self.registry.register(cheap_tool(10 ** 6, name="expensive"))
        self.kernel.register()
        self.ledger.flush()
        self.kernel.perform("expensive")
        self.ledger.flush()
        topics = [tx.body["topic"] for tx in self.attestations()]
        self.assertIn("budget-refusal", topics)

    def test_an_affordable_action_runs_and_settles(self):
        self.fund(1 * CREDIT)
        self.registry.register(cheap_tool(20))
        outcome = self.kernel.perform("cheap")
        self.assertTrue(outcome.authorised)
        self.assertTrue(outcome.ok)
        self.assertGreater(outcome.charged, 0)
        self.assertTrue(outcome.settled)
        self.assertTrue(outcome.txids)
        self.assertEqual(
            self.kernel.balance, 1 * CREDIT - outcome.charged
        )

    def test_the_quota_blocks_an_action_the_balance_could_afford(self):
        self.fund(500 * CREDIT)

        def run(meter, **kwargs):
            meter.record(ResourceKind.TOOL_CALL, 1)
            return "called"

        self.registry.register(
            Tool("call", "", run, lambda kw: {ResourceKind.TOOL_CALL: 1})
        )
        self.assertTrue(self.kernel.perform("call").authorised)
        self.assertTrue(self.kernel.perform("call").authorised)
        third = self.kernel.perform("call")
        self.assertTrue(third.refused)
        self.assertIn("quota", third.reason)
        self.assertGreater(self.kernel.balance, 0)


class SettlementTests(AgentTestCase):
    def test_every_metered_resource_becomes_a_transaction(self):
        self.fund(1 * CREDIT)

        def run(meter, **kwargs):
            meter.record(ResourceKind.LLM_INPUT_TOKENS, 30)
            meter.record(ResourceKind.LLM_OUTPUT_TOKENS, 12)
            meter.record(ResourceKind.STORAGE_BYTES, 64)
            return None

        self.registry.register(
            Tool("multi", "", run, lambda kw: {ResourceKind.LLM_OUTPUT_TOKENS: 20})
        )
        outcome = self.kernel.perform("multi")
        self.assertEqual(len(outcome.txids), 3)
        recorded = {tx.body["resource"]: tx.body["amount"] for tx in self.usage_transactions()}
        self.assertEqual(recorded["llm_input_tokens"], 30)
        self.assertEqual(recorded["llm_output_tokens"], 12)
        self.assertEqual(recorded["storage_bytes"], 64)

    def test_one_action_settles_inside_a_single_block(self):
        self.fund(1 * CREDIT)

        def run(meter, **kwargs):
            meter.record(ResourceKind.LLM_INPUT_TOKENS, 10)
            meter.record(ResourceKind.LLM_OUTPUT_TOKENS, 10)
            return None

        self.registry.register(Tool("pair", "", run, lambda kw: {ResourceKind.LLM_OUTPUT_TOKENS: 10}))
        before = self.chain.height
        outcome = self.kernel.perform("pair")
        self.assertEqual(self.chain.height, before + 1)
        self.assertEqual(len(self.chain.tip.transactions), len(outcome.txids))

    def test_usage_points_at_the_evidence_digest(self):
        self.fund(1 * CREDIT)
        self.registry.register(cheap_tool(10))
        outcome = self.kernel.perform("cheap")
        digests = {tx.body["evidence"] for tx in self.usage_transactions()}
        self.assertEqual(digests, {outcome.usage.digest})

    def test_a_failing_tool_is_still_billed(self):
        self.fund(1 * CREDIT)

        def run(meter, **kwargs):
            meter.record(ResourceKind.LLM_INPUT_TOKENS, 25)
            raise RuntimeError("the endpoint went away")

        self.registry.register(Tool("flaky", "", run, lambda kw: {ResourceKind.LLM_INPUT_TOKENS: 25}))
        outcome = self.kernel.perform("flaky")
        self.assertTrue(outcome.executed)
        self.assertFalse(outcome.ok)
        self.assertIn("endpoint", outcome.error)
        self.assertGreater(outcome.charged, 0)

    def test_overshooting_the_estimate_halts_the_agent(self):
        # Quotes 10 tokens, actually burns a million: the chain refuses the
        # usage record and the kernel must stop rather than carry on in debt.
        self.fund(1000)
        self.registry.register(cheap_tool(10, name="liar", actual=10 ** 6))
        outcome = self.kernel.perform("liar")
        self.assertTrue(outcome.authorised)
        self.assertFalse(outcome.settled)
        self.assertIn("refused", outcome.reason)
        self.assertTrue(self.kernel.halted)
        with self.assertRaises(AgentHalted):
            self.kernel.perform("liar")

    def test_resuming_after_a_top_up(self):
        self.fund(1000)
        self.registry.register(cheap_tool(10, name="liar", actual=10 ** 6))
        self.kernel.perform("liar")
        self.assertTrue(self.kernel.halted)
        self.fund(100 * CREDIT)
        self.kernel.resume()
        self.registry.register(cheap_tool(10, name="honest"))
        self.assertTrue(self.kernel.perform("honest").settled)


class LoopTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.kernel = AgentKernel(
            AGENT, self.ledger, tools=default_registry(), label="atlas",
            planner=StaticPlanner([
                Step("think", {"prompt": "what should I do?", "budget_tokens": 32}),
                Step("remember", {"key": "conclusion", "text": "spend less"}),
            ]),
        )

    def test_a_run_registers_attests_and_spends(self):
        self.fund(1 * CREDIT)
        outcomes = self.kernel.run("stay within budget")
        self.assertEqual([o.tool for o in outcomes], ["think", "remember"])
        self.assertTrue(all(o.ok for o in outcomes))
        self.assertTrue(self.chain.state.get(self.kernel.address).registered)
        self.assertIn("goal", [tx.body["topic"] for tx in self.attestations()])
        self.assertEqual(self.kernel.memory["conclusion"], "spend less")

    def test_a_penniless_agent_refuses_every_step_without_crashing(self):
        outcomes = self.kernel.run("do something ambitious")
        self.assertTrue(outcomes)
        self.assertTrue(all(o.refused for o in outcomes))
        self.assertEqual(self.kernel.balance, 0)
        self.assertFalse(self.usage_transactions())

    def test_the_budget_aware_planner_scales_down_when_poor(self):
        from chainmind.agent import BudgetAwarePlanner
        planner = BudgetAwarePlanner()
        rich = planner.plan("goal", {"balance": 10 * CREDIT, "prices": self.chain.state.prices})
        poor = planner.plan("goal", {"balance": 100, "prices": self.chain.state.prices})
        self.assertTrue(rich)
        self.assertEqual(poor, [])
        self.assertLessEqual(rich[0].kwargs["budget_tokens"], 512)

    def test_the_chain_still_verifies_after_a_run(self):
        self.fund(1 * CREDIT)
        self.kernel.run("stay within budget")
        self.ledger.flush()
        self.assertEqual(self.chain.verify().state_root(), self.chain.state.state_root())
        self.chain.state.check_invariants()


class MeterTests(unittest.TestCase):
    def test_a_meter_sums_repeated_records(self):
        with Meter("x", charge_compute=False) as meter:
            meter.record(ResourceKind.LLM_INPUT_TOKENS, 5)
            meter.record(ResourceKind.LLM_INPUT_TOKENS, 7)
        self.assertEqual(meter.finish().totals["llm_input_tokens"], 12)

    def test_the_digest_covers_the_context(self):
        with Meter("x", charge_compute=False) as first:
            first.record(ResourceKind.COMPUTE_MS, 1)
            first.note("prompt", "a")
        with Meter("x", charge_compute=False) as second:
            second.record(ResourceKind.COMPUTE_MS, 1)
            second.note("prompt", "b")
        record_a, record_b = first.finish(), second.finish()
        record_b = type(record_b)(**{**record_b.to_dict(), "started_at": record_a.started_at,
                                     "finished_at": record_a.finished_at})
        self.assertNotEqual(record_a.digest, record_b.digest)

    def test_a_closed_meter_refuses_further_records(self):
        meter = Meter("x", charge_compute=False)
        with meter:
            pass
        with self.assertRaises(RuntimeError):
            meter.record(ResourceKind.COMPUTE_MS, 1)

    def test_negative_amounts_are_refused(self):
        with Meter("x", charge_compute=False) as meter:
            with self.assertRaises(ValueError):
                meter.record(ResourceKind.COMPUTE_MS, -1)

    def test_an_exception_does_not_lose_the_measurement(self):
        meter = Meter("x", charge_compute=False)
        with self.assertRaises(RuntimeError):
            with meter:
                meter.record(ResourceKind.NETWORK_BYTES, 99)
                raise RuntimeError("boom")
        self.assertEqual(meter.finish().totals["network_bytes"], 99)


if __name__ == "__main__":
    unittest.main()
