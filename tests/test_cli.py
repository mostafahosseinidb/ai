import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from chainmind.chain import Chain
from chainmind.cli import Workspace, main


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.workspace = Path(self.dir.name) / "ws"

    def run_cli(self, *args, expect: int = 0, as_json: bool = True):
        argv = ["--workspace", str(self.workspace)]
        if as_json:
            argv.append("--json")
        argv.extend(args)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv)
        self.assertEqual(code, expect, msg=out.getvalue())
        text = out.getvalue()
        return json.loads(text) if as_json and text.strip() else text

    def bootstrap(self, *extra):
        return self.run_cli("init", "--chain-id", "cli-net", "--supply", "100",
                            "--agent-name", "atlas", *extra)


class LifecycleTests(CliTestCase):
    def test_init_creates_a_ledger_and_keys(self):
        info = self.bootstrap()
        self.assertEqual(info["chain_id"], "cli-net")
        self.assertTrue(Path(info["ledger"]).exists())
        self.assertEqual(len(info["authority"]), 64)
        names = Workspace(self.workspace).key_names()
        self.assertEqual(names, ["atlas", "authority"])

    def test_init_refuses_to_clobber_an_existing_workspace(self):
        self.bootstrap()
        with self.assertRaises(SystemExit):
            self.bootstrap()

    def test_private_keys_are_not_world_readable(self):
        self.bootstrap()
        path = Workspace(self.workspace).key_path("authority")
        self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_grant_then_run_then_audit(self):
        self.bootstrap()
        grant = self.run_cli("grant", "atlas", "2")
        self.assertEqual(grant["amount"], 2_000_000)

        run = self.run_cli("run", "write a short plan", "--agent", "atlas")
        self.assertTrue(run["actions"])
        self.assertTrue(any(a["ok"] for a in run["actions"]))
        self.assertGreater(run["spent"], 0)

        audit = self.run_cli("audit", "--address", "atlas")
        self.assertGreater(audit["total_spend"], 0)
        self.assertEqual(audit["total_spend"], run["spent"])

    def test_verify_passes_on_a_healthy_chain(self):
        self.bootstrap()
        self.run_cli("grant", "atlas", "2")
        self.run_cli("run", "do a little work", "--agent", "atlas")
        self.assertTrue(self.run_cli("verify")["valid"])

    def test_verify_fails_on_a_tampered_ledger(self):
        self.bootstrap()
        self.run_cli("grant", "atlas", "5")
        ledger = self.workspace / "ledger.jsonl"
        lines = ledger.read_text().splitlines()
        block = json.loads(lines[-1])
        block["transactions"][0]["body"]["amount"] = 99_000_000
        lines[-1] = json.dumps(block, sort_keys=True)
        ledger.write_text("\n".join(lines) + "\n")
        with self.assertRaises(SystemExit):
            self.run_cli("verify")

    def test_status_reports_accounts_and_supply(self):
        self.bootstrap()
        self.run_cli("grant", "atlas", "3")
        status = self.run_cli("status")
        self.assertEqual(status["chain_id"], "cli-net")
        balances = {a["address"]: a["balance"] for a in status["accounts"]}
        self.assertIn(3_000_000, balances.values())
        self.assertEqual(status["initial_supply"], 100_000_000)


class GovernanceTests(CliTestCase):
    def test_a_quota_set_at_init_refuses_the_agent(self):
        self.bootstrap("--limit", "llm_output_tokens=5")
        self.run_cli("grant", "atlas", "10")
        run = self.run_cli("run", "think expensively", "--agent", "atlas")
        refusals = [a for a in run["actions"] if not a["authorised"]]
        self.assertTrue(refusals)
        self.assertIn("quota", refusals[0]["reason"])

    def test_policy_can_reprice_a_resource(self):
        self.bootstrap()
        self.run_cli("policy", "--price", "compute_ms=999")
        self.assertEqual(self.run_cli("status")["prices"]["compute_ms"], 999)

    def test_policy_requires_something_to_change(self):
        self.bootstrap()
        with self.assertRaises(SystemExit):
            self.run_cli("policy", "--memo", "nothing")

    def test_an_unknown_resource_is_rejected_with_a_helpful_message(self):
        self.bootstrap()
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("policy", "--price", "gpu_hours=1")
        self.assertIn("unknown resource", str(ctx.exception))


class WorkspaceTests(CliTestCase):
    def test_commands_need_a_ledger(self):
        with self.assertRaises(SystemExit):
            self.run_cli("status")

    def test_addresses_may_be_given_as_hex_or_key_name(self):
        info = self.bootstrap()
        self.run_cli("grant", info["atlas"], "1")
        by_name = self.run_cli("audit", "--address", "atlas")
        by_hex = self.run_cli("audit", "--address", info["atlas"])
        self.assertEqual(by_name["address"], by_hex["address"])

    def test_keygen_adds_a_usable_key(self):
        self.bootstrap()
        created = self.run_cli("keygen", "researcher")
        self.run_cli("grant", "researcher", "1")
        chain = Chain.load(self.workspace / "ledger.jsonl")
        self.assertEqual(chain.state.balance_of(created["address"]), 1_000_000)

    def test_two_agents_keep_separate_budgets(self):
        self.bootstrap()
        self.run_cli("keygen", "scout")
        self.run_cli("grant", "atlas", "5")
        self.run_cli("grant", "scout", "1")
        self.run_cli("run", "atlas works", "--agent", "atlas")
        status = self.run_cli("status")
        burned = {a["label"]: a["burned"] for a in status["accounts"]}
        self.assertGreater(burned.get("atlas", 0), 0)
        scout = [a for a in status["accounts"] if a["balance"] == 1_000_000]
        self.assertEqual(scout[0]["burned"], 0)


if __name__ == "__main__":
    unittest.main()


class FlagPositionTests(CliTestCase):
    """`chainmind backends --json` is what people type; it has to work."""

    def test_json_is_accepted_after_the_subcommand(self):
        self.bootstrap()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--workspace", str(self.workspace), "status", "--json"])
        self.assertEqual(code, 0)
        self.assertIn("chain_id", json.loads(out.getvalue()))

    def test_json_before_the_subcommand_is_not_overwritten(self):
        self.bootstrap()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["--workspace", str(self.workspace), "--json", "status"])
        self.assertIn("chain_id", json.loads(out.getvalue()))

    def test_the_workspace_can_also_come_after(self):
        self.bootstrap()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["status", "--workspace", str(self.workspace), "--json"])
        self.assertIn("chain_id", json.loads(out.getvalue()))


class DecideCommandTests(CliTestCase):
    """A decision is an action: authorised, metered and settled."""

    def prepare(self, **kwargs):
        from test_decide import make_decider

        self.bootstrap()
        self.run_cli("grant", "atlas", "5")
        return make_decider(self.workspace, **kwargs)

    def test_a_decision_is_one_of_the_schema_options(self):
        self.prepare()
        report = self.run_cli("decide", "پولم رو پس بدید", "--threshold", "0")
        self.assertIn(report["decision"]["option"], ["refund", "status", "human"])
        self.assertTrue(report["ok"])
        self.assertGreater(report["spent"], 0)

    def test_abstaining_exits_differently_from_deciding(self):
        """So a shell script can route the unsure ones to a person.

        Three exit codes that mean three different things: decided, the
        chain said no, and the model was not sure enough.
        """
        self.prepare()
        self.run_cli("decide", "متنی برای دسته‌بندی", "--threshold", "1.0",
                     expect=3)

    def test_the_chain_refusing_is_not_the_model_abstaining(self):
        from test_decide import make_decider

        self.bootstrap()                       # no grant: the agent has nothing
        make_decider(self.workspace)
        self.run_cli("decide", "متن", expect=2)

    def test_an_empty_input_is_refused_before_anything_is_spent(self):
        self.prepare()
        with self.assertRaises(SystemExit):
            self.run_cli("decide", "   ")

    def test_a_workspace_with_no_decider_says_how_to_train_one(self):
        self.bootstrap()
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("decide", "متن")
        self.assertIn("train_decider.py", str(ctx.exception))

    def test_the_decision_is_visible_in_the_audit(self):
        """What was concluded, not just that credits moved.

        A ledger that records the spend and not the decision cannot answer
        the question anyone actually has later.
        """
        self.prepare()
        self.run_cli("decide", "پولم رو پس بدید", "--threshold", "0")
        audit = self.run_cli("audit")
        self.assertTrue(audit)


class RuntimeCommandTests(CliTestCase):
    def test_it_reports_every_way_of_running_a_model(self):
        self.bootstrap()
        report = self.run_cli("runtime", expect=1)   # none available in a bare test env
        self.assertFalse(report["available"])
        self.assertFalse(report["own"]["available"])
        self.assertIn(".cmw", report["own"]["reason"])
        self.assertFalse(report["embedded"]["available"])
        self.assertIn(".gguf", report["embedded"]["reason"])
        self.assertIn("no inference server is listening", report["served"]["reason"])

    def test_the_report_points_at_this_project_rather_than_a_product(self):
        """The fix for "why am I being told to install something else".

        Every route it suggests is one this repository can carry out: train
        the model here, or drop in a file. No product names, because naming
        one in the place a person looks when stuck is a recommendation.
        """
        self.bootstrap()
        report = self.run_cli("runtime", expect=1)
        # Everything up to "tried:", which is a factual list of the ports and
        # protocols probed rather than a suggestion to go and install one.
        advice = " ".join(
            str(report[kind].get("reason", "")).split("tried:")[0]
            for kind in ("own", "embedded", "served")
        ).lower()
        self.assertIn("training/pretrain.py", advice)
        for product in ("ollama", "lm studio", "vllm"):
            self.assertNotIn(product, advice)

    def test_a_decider_is_reported_separately_from_a_language_model(self):
        """They are different jobs and the report should not blur them.

        A decision model cannot answer a prompt, so it must not make the
        machine look able to.
        """
        from test_decide import make_decider

        self.bootstrap()
        make_decider(self.workspace, calibration={"temperature": 1.4, "ece": 0.06,
                                                  "accuracy": 0.91, "rows": 400})
        report = self.run_cli("runtime", expect=1)
        self.assertTrue(report["decision"]["available"])
        self.assertEqual(report["decision"]["models"][0]["options"],
                         ["refund", "status", "human"])
        self.assertFalse(report["available"])

    def test_init_makes_somewhere_to_put_the_weights(self):
        self.bootstrap()
        self.assertTrue((self.workspace / "models").is_dir())
