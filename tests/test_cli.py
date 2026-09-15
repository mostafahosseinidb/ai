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
