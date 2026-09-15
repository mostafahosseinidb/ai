import json
import tempfile
import unittest
from pathlib import Path

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.executors import InProcessExecutor, SandboxExecutor
from chainmind.resources import CREDIT, ResourceKind
from chainmind.sandbox import SUPPORTED, SandboxLimits, run_sandboxed
from chainmind.tools import Tool, ToolRegistry, default_registry
from chainmind.transactions import TxType, build_grant

from support import AGENT, AUTHORITY, make_chain

requires_fork = unittest.skipUnless(SUPPORTED, "out-of-process metering needs fork and wait4")


def burn_cpu(meter, iterations: int = 400_000):
    """Real work, so the kernel has something to measure."""
    total = 0
    for i in range(iterations):
        total += i * i
    return total


@requires_fork
class MeasurementTests(unittest.TestCase):
    def test_cpu_time_comes_from_the_kernel(self):
        result = run_sandboxed(burn_cpu, {"iterations": 400_000}, label="work")
        self.assertTrue(result.ok)
        self.assertGreater(result.cpu_ms, 0)
        self.assertGreater(result.max_rss_kb, 0)
        self.assertEqual(result.totals[ResourceKind.COMPUTE_MS.value], result.cpu_ms)

    def test_a_tool_cannot_under_report_its_cpu_time(self):
        def liar(meter, iterations):
            meter.record(ResourceKind.COMPUTE_MS, 0)   # "I used nothing"
            burn_cpu(meter, iterations)
            return "cheap, honest"

        result = run_sandboxed(liar, {"iterations": 600_000}, label="liar")
        self.assertTrue(result.ok)
        self.assertEqual(result.declared.get("compute_ms", 0), 0)
        self.assertGreater(result.cpu_ms, 0)
        self.assertEqual(result.totals["compute_ms"], result.cpu_ms)

    def test_declarations_the_kernel_cannot_see_are_preserved(self):
        def mixed(meter, n):
            meter.record(ResourceKind.LLM_OUTPUT_TOKENS, 42)
            meter.record(ResourceKind.NETWORK_BYTES, 1024)
            return burn_cpu(meter, n)

        result = run_sandboxed(mixed, {"n": 50_000}, label="mixed")
        self.assertEqual(result.totals["llm_output_tokens"], 42)
        self.assertEqual(result.totals["network_bytes"], 1024)
        self.assertIn("compute_ms", result.totals)

    def test_the_usage_record_marks_how_it_was_measured(self):
        result = run_sandboxed(burn_cpu, {"iterations": 10_000}, label="work")
        record = result.to_usage_record("work", 1, 2)
        self.assertEqual(record.context["measurement"], "out-of-process")
        self.assertIn("max_rss_kb", record.context)


@requires_fork
class EnforcementTests(unittest.TestCase):
    def test_an_infinite_loop_is_killed_by_the_cpu_limit(self):
        def runaway(meter):
            while True:
                pass

        result = run_sandboxed(
            runaway, {}, limits=SandboxLimits(cpu_seconds=1, wall_seconds=10), label="runaway"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.killed_by, "cpu")
        self.assertGreater(result.cpu_ms, 0)

    def test_the_wall_clock_catches_a_sleeping_child(self):
        def sleeper(meter):
            import time
            time.sleep(30)

        result = run_sandboxed(
            sleeper, {}, limits=SandboxLimits(cpu_seconds=60, wall_seconds=1.0), label="sleeper"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.killed_by, "wall")

    def test_a_memory_limit_is_enforced(self):
        def hog(meter):
            return len(bytearray(512 * 1024 * 1024))

        result = run_sandboxed(
            hog, {}, limits=SandboxLimits(address_space_bytes=64 * 1024 * 1024, wall_seconds=20),
            label="hog",
        )
        self.assertFalse(result.ok)

    def test_a_crashing_child_does_not_take_the_parent_with_it(self):
        def crasher(meter):
            import ctypes
            ctypes.string_at(0)

        result = run_sandboxed(crasher, {}, limits=SandboxLimits(wall_seconds=10), label="crash")
        self.assertFalse(result.ok)
        self.assertIn("SIGSEGV", result.killed_by or result.error)
        self.assertTrue(run_sandboxed(burn_cpu, {"iterations": 1000}).ok)  # still usable

    def test_a_budget_becomes_a_real_limit(self):
        limits = SandboxLimits.from_compute_budget(250)
        self.assertEqual(limits.cpu_seconds, 1)
        self.assertEqual(limits.wall_seconds, 1.0)
        self.assertEqual(SandboxLimits.from_compute_budget(4_500).cpu_seconds, 5)

    def test_a_negative_budget_is_refused(self):
        with self.assertRaises(ValueError):
            SandboxLimits.from_compute_budget(-1)


@requires_fork
class IsolationTests(unittest.TestCase):
    def test_mutations_to_shared_objects_do_not_come_back(self):
        # Documented cost of the isolation, and the reason the reference
        # `remember` tool writes to a file instead of to a dict.
        shared = {}

        def mutate(meter, target):
            target["touched"] = True
            return "done"

        result = run_sandboxed(mutate, {"target": shared}, label="mutate")
        self.assertTrue(result.ok)
        self.assertEqual(shared, {})

    def test_a_file_written_by_the_child_is_visible_to_the_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "memory.json"
            tool = default_registry().get("remember")
            result = SandboxExecutor().run(
                tool, {"store": str(store), "key": "policy", "text": "keep a reserve"}
            )
            self.assertTrue(result.ok)
            self.assertEqual(json.loads(store.read_text())["policy"], "keep a reserve")

    def test_a_result_that_cannot_be_serialised_is_reported_not_swallowed(self):
        def exotic(meter):
            return object()

        result = run_sandboxed(exotic, {}, label="exotic")
        self.assertFalse(result.ok)
        self.assertIn("JSON", result.error)


@requires_fork
class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.chain = make_chain()
        self.ledger = LocalLedger(self.chain, AUTHORITY)
        self.registry = ToolRegistry()

    def fund(self, microcredits):
        nonce = self.chain.state.get(AUTHORITY.public_hex()).nonce
        self.chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, nonce, beneficiary=AGENT.public_hex(), amount=microcredits)],
        )

    def kernel(self, **kwargs):
        return AgentKernel(AGENT, self.ledger, tools=self.registry, label="atlas", **kwargs)

    def usage_by_resource(self):
        found = {}
        for _, tx in self.chain.transactions():
            if tx.type is TxType.USAGE:
                found[tx.body["resource"]] = found.get(tx.body["resource"], 0) + tx.body["amount"]
        return found

    def test_the_limit_is_clamped_by_the_executor_ceiling(self):
        executor = SandboxExecutor(max_compute_ms=2_000)
        self.assertEqual(executor.limits_for(10 ** 9).cpu_seconds, 2)
        self.assertEqual(executor.limits_for(-1).cpu_seconds, 2)   # -1 means unbounded upstream

    def test_the_kernel_derives_its_compute_budget_from_the_chain(self):
        self.fund(1 * CREDIT)
        kernel = self.kernel(executor=SandboxExecutor())
        expected = self.chain.state.affordable_units(kernel.address, "compute_ms")
        self.assertEqual(kernel.compute_budget_ms(), expected)
        self.assertGreater(expected, 0)

    def test_a_sandboxed_action_bills_measured_cpu_on_chain(self):
        self.fund(5 * CREDIT)
        self.registry.register(
            Tool("work", "", burn_cpu, lambda kw: {ResourceKind.COMPUTE_MS: 50})
        )
        kernel = self.kernel(executor=SandboxExecutor())
        outcome = kernel.perform("work", iterations=400_000)
        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.settled)
        self.assertGreater(self.usage_by_resource().get("compute_ms", 0), 0)
        self.assertEqual(
            self.chain.verify().state_root(), self.chain.state.state_root()
        )

    def test_a_tool_killed_by_its_limit_is_still_billed(self):
        self.fund(5 * CREDIT)

        def runaway(meter):
            while True:
                pass

        self.registry.register(
            Tool("runaway", "", runaway, lambda kw: {ResourceKind.COMPUTE_MS: 100})
        )
        kernel = self.kernel(
            executor=SandboxExecutor(max_compute_ms=1_000, wall_seconds=5.0)
        )
        outcome = kernel.perform("runaway")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.executed)
        self.assertGreater(outcome.charged, 0)          # the CPU it burned is paid for
        self.assertGreater(self.usage_by_resource().get("compute_ms", 0), 0)

    def test_the_in_process_executor_remains_the_default(self):
        self.assertIsInstance(self.kernel().executor, InProcessExecutor)

    def test_both_executors_produce_the_same_shape_of_result(self):
        self.fund(5 * CREDIT)
        tool = Tool("work", "", burn_cpu, lambda kw: {ResourceKind.COMPUTE_MS: 10})
        in_process = InProcessExecutor().run(tool, {"iterations": 20_000})
        sandboxed = SandboxExecutor().run(tool, {"iterations": 20_000})
        self.assertEqual(in_process.tool, sandboxed.tool)
        self.assertTrue(in_process.ok and sandboxed.ok)
        self.assertEqual(in_process.value, sandboxed.value)
        self.assertIsNotNone(sandboxed.usage.digest)


@requires_fork
class MemoryFileTests(unittest.TestCase):
    def test_kernel_memory_survives_a_sandboxed_write(self):
        chain = make_chain()
        ledger = LocalLedger(chain, AUTHORITY)
        chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, chain.state.get(AUTHORITY.public_hex()).nonce,
                         beneficiary=AGENT.public_hex(), amount=1 * CREDIT)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            kernel = AgentKernel(
                AGENT, ledger, label="atlas", executor=SandboxExecutor(),
                memory_path=Path(tmp) / "memory.json",
            )
            kernel.run("write something down")
            self.assertIn("conclusion", kernel.memory)

    def test_memory_is_empty_before_anything_is_written(self):
        chain = make_chain()
        kernel = AgentKernel(AGENT, LocalLedger(chain, AUTHORITY), label="atlas")
        self.assertEqual(kernel.memory, {})


if __name__ == "__main__":
    unittest.main()
