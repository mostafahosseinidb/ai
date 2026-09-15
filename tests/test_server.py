import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.chain import Chain
from chainmind.consensus import ProofOfAuthority
from chainmind.resources import CREDIT
from chainmind.server import (
    LedgerView,
    build_events,
    build_overview,
    build_usage,
    build_verification,
    serve,
)
from chainmind.transactions import build_grant

from support import AGENT, AUTHORITY, make_genesis


def populated_chain(path: Path) -> Chain:
    chain = Chain.create(
        make_genesis(limits={"llm_output_tokens": 40}), AUTHORITY,
        ProofOfAuthority([AUTHORITY.public_hex()]), path=path, timestamp=1_700_000_000,
    )
    ledger = LocalLedger(chain, AUTHORITY)
    chain.seal_block(
        AUTHORITY,
        [build_grant(AUTHORITY, chain.state.get(AUTHORITY.public_hex()).nonce,
                     beneficiary=AGENT.public_hex(), amount=2 * CREDIT)],
    )
    kernel = AgentKernel(AGENT, ledger, label="atlas",
                         memory_path=path.parent / "memory.json")
    kernel.run("do a little measurable work")
    kernel.perform("think", prompt="something far too large", budget_tokens=10 ** 6)
    ledger.flush()
    return chain


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.path = Path(cls.dir.name) / "ledger.jsonl"
        cls.chain = populated_chain(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_overview_describes_the_chain(self):
        overview = build_overview(self.chain)
        self.assertEqual(overview["chain_id"], "test-net")
        self.assertEqual(overview["height"], self.chain.height)
        self.assertEqual(
            overview["circulating"] + overview["burned_total"], overview["initial_supply"]
        )
        self.assertTrue(any(a["label"] == "atlas" for a in overview["accounts"]))

    def test_overview_reports_remaining_quota_per_account(self):
        overview = build_overview(self.chain)
        atlas = next(a for a in overview["accounts"] if a["label"] == "atlas")
        self.assertIn("llm_output_tokens", atlas["quotas"])
        self.assertLessEqual(atlas["quotas"]["llm_output_tokens"], 40)

    def test_usage_totals_match_what_was_burned(self):
        usage = build_usage(self.chain)
        self.assertEqual(usage["total_cost"], self.chain.state.burned_total)
        self.assertEqual(
            usage["by_measurement"]["kernel"] + usage["by_measurement"]["declared"],
            usage["total_cost"],
        )

    def test_the_cumulative_series_ends_at_the_total(self):
        usage = build_usage(self.chain)
        self.assertEqual(len(usage["series"]), self.chain.height + 1)
        self.assertEqual(usage["series"][-1]["cumulative"], usage["total_cost"])
        cumulative = [point["cumulative"] for point in usage["series"]]
        self.assertEqual(cumulative, sorted(cumulative))   # never decreases

    def test_every_usage_row_names_its_measurement_source(self):
        for row in build_usage(self.chain)["rows"]:
            self.assertIn(row["measured"], {"kernel", "declared"})

    def test_events_separate_goals_from_refusals(self):
        events = build_events(self.chain)
        topics = {event["topic"] for event in events["events"]}
        self.assertIn("goal", topics)
        self.assertIn("budget-refusal", topics)
        self.assertGreaterEqual(events["refusals"], 1)

    def test_verification_reports_a_healthy_chain(self):
        result = build_verification(self.chain)
        self.assertTrue(result["valid"])
        self.assertTrue(result["matches_live_state"])


class LedgerViewTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "ledger.jsonl"

    def test_a_view_reloads_when_the_file_grows(self):
        chain = populated_chain(self.path)
        view = LedgerView(self.path)
        self.assertEqual(view.chain().height, chain.height)

        chain.seal_block(AUTHORITY, [])
        reloaded = view.chain()
        self.assertEqual(reloaded.height, chain.height)

    def test_a_missing_ledger_is_reported_not_crashed(self):
        view = LedgerView(self.path)
        with self.assertRaises(Exception):
            view.chain()

    def test_a_tampered_ledger_stops_being_served(self):
        populated_chain(self.path)
        view = LedgerView(self.path)
        view.chain()
        lines = self.path.read_text().splitlines()
        block = json.loads(lines[-1])
        block["header"]["state_root"] = "0" * 64
        lines[-1] = json.dumps(block, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(Exception):
            view.chain()


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.path = Path(cls.dir.name) / "ledger.jsonl"
        populated_chain(cls.path)
        cls.server = serve(cls.path, host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.dir.cleanup()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=10) as response:
            return response.status, response.read(), response.headers

    def test_every_api_route_answers_with_json(self):
        for path in ("/api/overview", "/api/usage", "/api/blocks", "/api/events", "/api/verify"):
            with self.subTest(path=path):
                status, body, headers = self.get(path)
                self.assertEqual(status, 200)
                self.assertIn("application/json", headers["Content-Type"])
                self.assertIsInstance(json.loads(body), dict)

    def test_the_dashboard_is_served_at_the_root(self):
        status, body, headers = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"ChainMind", body)
        self.assertIn(b'dir="rtl"', body)

    def test_responses_carry_hardening_headers(self):
        _, _, headers = self.get("/api/overview")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_an_unknown_route_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/nonsense")
        self.assertEqual(ctx.exception.code, 404)

    def test_the_server_refuses_to_write(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                request = urllib.request.Request(
                    self.url("/api/overview"), data=b"{}", method=method
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(request, timeout=10)
                self.assertEqual(ctx.exception.code, 405)

    def test_the_block_limit_is_bounded(self):
        _, body, _ = self.get("/api/blocks?limit=999999")
        self.assertLessEqual(len(json.loads(body)["blocks"]), 1000)
        _, body, _ = self.get("/api/blocks?limit=nonsense")
        self.assertTrue(json.loads(body)["blocks"])

    def test_the_bundled_font_is_served(self):
        status, body, headers = self.get("/fonts/Vazirmatn-Regular.woff2")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "font/woff2")
        self.assertEqual(body[:4], b"wOF2")
        self.assertIn("immutable", headers["Cache-Control"])

    def test_the_font_licence_travels_with_the_font(self):
        status, body, _ = self.get("/fonts/Vazirmatn-OFL.txt")
        self.assertEqual(status, 200)
        self.assertIn(b"SIL Open Font License", body)

    def test_the_font_route_cannot_be_walked_out_of(self):
        for name in ("..%2fledger.jsonl", "%2e%2e%2f%2e%2e%2fcli.py", "evil.woff2",
                     "subdir%2fVazirmatn-Regular.woff2"):
            with self.subTest(name=name), self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/fonts/" + name)
            self.assertEqual(ctx.exception.code, 404)

    def test_the_verify_route_replays_from_genesis(self):
        _, body, _ = self.get("/api/verify")
        payload = json.loads(body)
        self.assertTrue(payload["valid"])
        self.assertTrue(payload["matches_live_state"])


if __name__ == "__main__":
    unittest.main()
