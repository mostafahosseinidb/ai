import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.chain import Chain
from chainmind.chat import ChatBusy, ChatRejected, ChatService, MAX_PROMPT_BYTES
from chainmind.consensus import ProofOfAuthority
from chainmind.local import LocalModel
from chainmind.models import build_model_tool, describe_model
from chainmind.resources import CREDIT
from chainmind.server import CHAT_HEADER, serve
from chainmind.tools import ToolRegistry
from chainmind.transactions import TxType, build_grant

from fake_runtime import FakeRuntime
from support import AGENT, AUTHORITY, make_genesis


def build_service(path: Path, credits: int = 20, runtime: FakeRuntime | None = None, **kwargs):
    """A chat service wired to a fake runtime, optionally with memory."""
    chain = Chain.create(make_genesis(), AUTHORITY, ProofOfAuthority([AUTHORITY.public_hex()]),
                         path=path, timestamp=1_700_000_000)
    ledger = LocalLedger(chain, AUTHORITY)
    if credits:
        # A zero grant is not a poor agent, it is an invalid transaction --
        # an unfunded agent simply never receives one.
        chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, chain.state.get(AUTHORITY.public_hex()).nonce,
                         beneficiary=AGENT.public_hex(), amount=credits * CREDIT)],
        )
    engine = LocalModel(base_url=runtime.url, dialect="ollama", model="qwen2.5:7b")
    registry = ToolRegistry()
    registry.register(build_model_tool(engine))
    kernel = AgentKernel(AGENT, ledger, tools=registry, label="atlas")
    kernel.register()
    ledger.flush()
    service = ChatService(kernel, ledger, model_name=describe_model(engine), **kwargs)
    return service, chain, engine


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.runtime = FakeRuntime("ollama", answer="the answer").__enter__()
        self.addCleanup(self.runtime.__exit__, None, None, None)
        self.path = Path(self.dir.name) / "ledger.jsonl"
        self.service, self.chain, self.engine = build_service(self.path, runtime=self.runtime)

    def test_a_turn_returns_an_answer_and_a_receipt(self):
        result = self.service.send("سلام")
        self.assertEqual(result["answer"], "the answer")
        self.assertGreater(result["cost"], 0)
        self.assertEqual(result["sources"]["llm_output_tokens"], "provider")
        self.assertTrue(result["txids"])

    def test_history_is_replayed_to_the_model(self):
        first = self.service.send("سؤال اول")
        self.service.send("سؤال دوم", conversation_id=first["conversation_id"])
        sent = self.runtime.requests[-1]["messages"]
        self.assertEqual([m["role"] for m in sent], ["system", "user", "assistant", "user"])
        self.assertEqual(sent[1]["content"], "سؤال اول")

    def test_each_conversation_keeps_its_own_history(self):
        a = self.service.send("گفتگوی الف")
        b = self.service.send("گفتگوی ب")
        self.assertNotEqual(a["conversation_id"], b["conversation_id"])
        sent = self.runtime.requests[-1]["messages"]
        self.assertEqual(len(sent), 2)   # system + the new prompt; b started fresh

    def test_history_is_bounded(self):
        service, _, engine = build_service(
            Path(self.dir.name) / "bounded.jsonl", runtime=self.runtime, history_turns=2)
        conversation = None
        for i in range(5):
            result = service.send(f"turn {i}", conversation_id=conversation)
            conversation = result["conversation_id"]
        sent = self.runtime.requests[-1]["messages"]
        # system + 2 turns of history (4 messages) + the new prompt.
        self.assertLessEqual(len(sent), 6)

    def test_the_conversation_digest_is_attested(self):
        self.service.send("چیزی بپرس")
        topics = [tx.body["topic"] for _, tx in self.chain.transactions()
                  if tx.type is TxType.ATTEST]
        self.assertIn("chat", topics)

    def test_a_refused_turn_is_not_remembered(self):
        service, _, engine = build_service(Path(self.dir.name) / "poor.jsonl",
                                           runtime=self.runtime, credits=0)
        with self.assertRaises(ChatRejected):
            service.send("این را نمی‌توانم بپردازم")
        self.assertEqual(
            [p for p in self.runtime.paths if "chat" in p or "completions" in p], [])
        self.assertEqual(service.listing()[0]["turns"], 0)

    def test_an_empty_prompt_is_refused(self):
        for prompt in ("", "   ", "\n"):
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                self.service.send(prompt)

    def test_an_oversized_prompt_is_refused(self):
        with self.assertRaises(ValueError):
            self.service.send("x" * (MAX_PROMPT_BYTES + 1))

    def test_only_one_turn_runs_at_a_time(self):
        self.service._lock.acquire()
        self.addCleanup(self.service._lock.release)
        with self.assertRaises(ChatBusy):
            self.service.send("همزمان")

    def test_the_chain_still_verifies_after_a_conversation(self):
        first = self.service.send("یک")
        self.service.send("دو", conversation_id=first["conversation_id"])
        self.assertEqual(self.chain.verify().state_root(), self.chain.state.state_root())
        self.chain.state.check_invariants()


class ReadOnlyByDefaultTests(unittest.TestCase):
    """Without --chat the server must keep its original guarantee."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.runtime = FakeRuntime("ollama").__enter__()
        path = Path(cls.dir.name) / "ledger.jsonl"
        build_service(path, runtime=cls.runtime)
        cls.server = serve(path, host="127.0.0.1", port=0)          # no chat
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.runtime.__exit__(None, None, None)
        cls.dir.cleanup()

    def post(self, path, body=b"{}", headers=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method="POST",
            headers=headers or {},
        )
        return urllib.request.urlopen(request, timeout=10)

    def test_posting_to_chat_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/chat", headers={CHAT_HEADER: "1"})
        self.assertEqual(ctx.exception.code, 405)

    def test_reading_chat_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/chat", timeout=10)
        self.assertEqual(ctx.exception.code, 404)


class ChatEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.runtime = FakeRuntime("ollama", answer="the answer").__enter__()
        path = Path(cls.dir.name) / "ledger.jsonl"
        cls.service, cls.chain, cls.engine = build_service(path, runtime=cls.runtime)
        cls.server = serve(path, host="127.0.0.1", port=0, chat=cls.service)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.runtime.__exit__(None, None, None)
        cls.dir.cleanup()

    def url(self, path=""):
        return f"http://127.0.0.1:{self.port}{path}"

    def send(self, payload, headers=None, expect=200):
        merged = {"Content-Type": "application/json", CHAT_HEADER: "1"}
        merged.update(headers or {})
        request = urllib.request.Request(
            self.url("/api/chat"), data=json.dumps(payload).encode(), method="POST",
            headers=merged,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                self.assertEqual(response.status, expect)
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()          # a response body can only be read once
            self.assertEqual(exc.code, expect, msg=body[:300])
            return json.loads(body or b"{}")

    def test_a_prompt_comes_back_as_an_answer(self):
        payload = self.send({"prompt": "سلام"})
        self.assertEqual(payload["answer"], "the answer")
        self.assertIn("cost_text", payload)

    def test_the_status_route_describes_the_agent(self):
        with urllib.request.urlopen(self.url("/api/chat"), timeout=10) as response:
            payload = json.loads(response.read())
        self.assertTrue(payload["enabled"])
        self.assertIn("qwen2.5:7b", payload["model"])
        self.assertEqual(payload["label"], "atlas")

    def test_a_request_without_the_header_is_forbidden(self):
        request = urllib.request.Request(
            self.url("/api/chat"), data=b'{"prompt":"hi"}', method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 403)

    def test_a_cross_origin_request_is_forbidden(self):
        payload = self.send({"prompt": "hi"}, headers={"Origin": "https://evil.example"},
                            expect=403)
        self.assertIn("dashboard", payload["error"])

    def test_an_empty_prompt_is_a_400(self):
        self.send({"prompt": "   "}, expect=400)

    def test_a_non_json_body_is_a_400(self):
        request = urllib.request.Request(
            self.url("/api/chat"), data=b"not json", method="POST",
            headers={"Content-Type": "application/json", CHAT_HEADER: "1"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_another_write_route_is_still_refused(self):
        request = urllib.request.Request(
            self.url("/api/overview"), data=b"{}", method="POST",
            headers={CHAT_HEADER: "1"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 405)

    def test_a_conversation_can_be_read_back(self):
        payload = self.send({"prompt": "به خاطر بسپار"})
        with urllib.request.urlopen(
            self.url(f"/api/chat?conversation={payload['conversation_id']}"), timeout=10
        ) as response:
            conversation = json.loads(response.read())
        self.assertEqual(conversation["turns"][0]["role"], "user")
        self.assertEqual(conversation["turns"][1]["role"], "assistant")

    def test_an_unknown_conversation_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.url("/api/chat?conversation=nope"), timeout=10)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()


class MemoryTests(unittest.TestCase):
    """A conversation that builds on earlier ones, and pays for doing so."""

    def setUp(self):
        from chainmind.memory import FeedbackStore, MemoryStore

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.runtime = FakeRuntime("ollama", answer="the answer").__enter__()
        self.addCleanup(self.runtime.__exit__, None, None, None)
        self.memory = MemoryStore(Path(self.dir.name) / "notes.jsonl")
        self.feedback = FeedbackStore(Path(self.dir.name) / "feedback.jsonl")
        self.service, self.chain, _ = build_service(
            Path(self.dir.name) / "ledger.jsonl", runtime=self.runtime,
            memory=self.memory, feedback=self.feedback,
        )

    def system_sent(self) -> str:
        return self.runtime.requests[-1]["messages"][0]["content"]

    def test_a_relevant_note_reaches_the_model(self):
        self.memory.remember("رمز عبور پایگاه داده در Vault نگهداری می‌شود", kind="fact")
        result = self.service.send("رمز عبور پایگاه داده کجاست؟")
        self.assertIn("Vault", self.system_sent())
        self.assertTrue(result["recalled"])

    def test_an_irrelevant_note_is_left_out(self):
        self.memory.remember("قهوه را با شیر دوست دارم", kind="preference")
        result = self.service.send("پروتکل شبکه چگونه کار می‌کند؟")
        self.assertNotIn("قهوه", self.system_sent())
        self.assertEqual(result["recalled"], [])

    def test_a_turn_is_remembered_for_the_next_one(self):
        self.service.send("پایتخت فرانسه کجاست؟")
        turns = [note for note in self.memory.notes() if note.kind == "turn"]
        self.assertEqual(len(turns), 1)
        self.assertIn("فرانسه", turns[0].text)

    def test_what_was_recalled_is_reported_back(self):
        note = self.memory.remember("شمارهٔ پرواز ۷۷۴ است", kind="fact")
        result = self.service.send("شمارهٔ پرواز چند بود؟")
        self.assertEqual([hit["id"] for hit in result["recalled"]], [note.id])

    def test_recall_is_paid_for_rather_than_smuggled_in(self):
        # A recalled note lengthens the system prompt, so the authorised
        # estimate has to grow with it.
        plain = self.service.kernel.tools.get("ask").estimated_cost(prompt="سؤال")
        self.memory.remember("حقیقت " * 200, kind="fact")
        context, _ = self.memory.recall_context("حقیقت")
        with_memory = self.service.kernel.tools.get("ask").estimated_cost(
            prompt="سؤال", system=context
        )
        self.assertGreater(with_memory["llm_input_tokens"], plain["llm_input_tokens"])

    def test_memory_can_be_turned_off_entirely(self):
        plain, _, _ = build_service(Path(self.dir.name) / "plain.jsonl",
                                    runtime=self.runtime)
        result = plain.send("سؤالی بپرس")
        self.assertEqual(result["recalled"], [])
        self.assertIsNone(plain.status()["memory"])


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        from chainmind.memory import FeedbackStore, MemoryStore

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.runtime = FakeRuntime("ollama", answer="the answer").__enter__()
        self.addCleanup(self.runtime.__exit__, None, None, None)
        self.feedback = FeedbackStore(Path(self.dir.name) / "feedback.jsonl")
        self.service, self.chain, _ = build_service(
            Path(self.dir.name) / "ledger.jsonl", runtime=self.runtime,
            memory=MemoryStore(Path(self.dir.name) / "notes.jsonl"),
            feedback=self.feedback,
        )

    def test_an_answer_can_be_rated(self):
        sent = self.service.send("سؤال")
        result = self.service.rate(sent["conversation_id"], sent["turn"], "good")
        self.assertEqual(result["good"], 1)
        entry = self.feedback.get(sent["conversation_id"], sent["turn"])
        self.assertEqual(entry.prompt, "سؤال")
        self.assertEqual(entry.answer, "the answer")

    def test_a_rating_is_committed_to_the_chain(self):
        sent = self.service.send("سؤال")
        self.service.rate(sent["conversation_id"], sent["turn"], "bad")
        topics = [tx.body["topic"] for _, tx in self.chain.transactions()
                  if tx.type is TxType.ATTEST]
        self.assertIn("feedback", topics)

    def test_a_question_cannot_be_rated_only_an_answer(self):
        sent = self.service.send("سؤال")
        with self.assertRaises(ValueError):
            self.service.rate(sent["conversation_id"], sent["turn"] - 1, "good")

    def test_an_unknown_rating_is_refused(self):
        sent = self.service.send("سؤال")
        with self.assertRaises(ValueError):
            self.service.rate(sent["conversation_id"], sent["turn"], "brilliant")

    def test_an_unknown_conversation_is_refused(self):
        with self.assertRaises(ValueError):
            self.service.rate("nope", 0, "good")

    def test_ratings_accumulate_into_a_dataset(self):
        from chainmind.memory import build_training_dataset

        for prompt in ("سؤال اول", "سؤال دوم"):
            sent = self.service.send(prompt)
            self.service.rate(sent["conversation_id"], sent["turn"], "good")
        rows = build_training_dataset(self.feedback)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["messages"][1]["content"], "the answer")


class ReloadFidelityTests(unittest.TestCase):
    """A reloaded conversation must show what the live one showed."""

    def setUp(self):
        from chainmind.memory import FeedbackStore, MemoryStore

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.runtime = FakeRuntime("ollama", answer="the answer").__enter__()
        self.addCleanup(self.runtime.__exit__, None, None, None)
        self.memory = MemoryStore(Path(self.dir.name) / "notes.jsonl")
        self.service, _, _ = build_service(
            Path(self.dir.name) / "ledger.jsonl", runtime=self.runtime,
            memory=self.memory,
            feedback=FeedbackStore(Path(self.dir.name) / "feedback.jsonl"),
        )

    def test_a_stored_turn_keeps_the_text_it_was_given(self):
        self.memory.remember("پورت پیش‌فرض شبکه ۸۸۷۷ است", kind="fact")
        sent = self.service.send("پورت پیش‌فرض چند است؟")
        stored = self.service.get(sent["conversation_id"]).to_dict()["turns"][sent["turn"]]
        self.assertTrue(stored["recalled"])
        self.assertIn("۸۸۷۷", stored["recalled"][0]["text"])

    def test_a_recorded_rating_comes_back_with_the_conversation(self):
        sent = self.service.send("سؤال")
        self.service.rate(sent["conversation_id"], sent["turn"], "good")
        turns = self.service.get(sent["conversation_id"]).to_dict()["turns"]
        self.assertEqual(turns[sent["turn"]]["rating"], "good")
        self.assertIsNone(turns[sent["turn"] - 1]["rating"])

    def test_every_turn_reports_its_own_index(self):
        first = self.service.send("یک")
        self.service.send("دو", conversation_id=first["conversation_id"])
        turns = self.service.get(first["conversation_id"]).to_dict()["turns"]
        self.assertEqual([turn["turn"] for turn in turns], [0, 1, 2, 3])
