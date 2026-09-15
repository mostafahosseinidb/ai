import json
import tempfile
import unittest
from pathlib import Path

from chainmind.agent import AgentKernel, LocalLedger
from chainmind.evaluate import (
    EvalReport,
    Evaluation,
    RowResult,
    load_dataset,
    rouge_l,
    split_dataset,
    token_f1,
)
from chainmind.local import LocalModel
from chainmind.models import build_model_tool
from chainmind.resources import CREDIT
from chainmind.tools import ToolRegistry
from chainmind.transactions import build_grant

from fake_runtime import FakeRuntime
from support import AGENT, AUTHORITY, make_chain


def rows(count: int = 10, answer: str = "the answer"):
    return [
        {"messages": [{"role": "user", "content": f"سؤال شمارهٔ {i}"},
                      {"role": "assistant", "content": answer}],
         "digest": f"{i:064d}"}
        for i in range(count)
    ]


class MetricTests(unittest.TestCase):
    def test_an_identical_answer_scores_one(self):
        text = "بودجه هزینهٔ کل را محدود می‌کند"
        self.assertEqual(token_f1(text, text), 1.0)
        self.assertEqual(rouge_l(text, text), 1.0)

    def test_an_unrelated_answer_scores_zero(self):
        self.assertEqual(token_f1("قهوه با شیر", "بودجه هزینهٔ کل"), 0.0)

    def test_word_order_is_invisible_to_f1_and_visible_to_rouge(self):
        reference = "بودجه نرخ را محدود می‌کند"
        shuffled = "نرخ را محدود می‌کند بودجه"
        self.assertEqual(token_f1(shuffled, reference), 1.0)
        self.assertLess(rouge_l(shuffled, reference), 1.0)

    def test_an_arabic_spelling_still_matches(self):
        self.assertEqual(token_f1("كتاب بزرگ", "کتاب بزرگ"), 1.0)

    def test_a_partial_answer_scores_between(self):
        score = token_f1("بودجه محدود می‌کند", "بودجه هزینهٔ کل را محدود می‌کند")
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    def test_an_empty_answer_scores_zero_against_content(self):
        self.assertEqual(token_f1("", "چیزی"), 0.0)
        self.assertEqual(rouge_l("", "چیزی"), 0.0)

    def test_two_empty_strings_agree(self):
        self.assertEqual(token_f1("", ""), 1.0)

    def test_a_longer_answer_is_penalised_for_padding(self):
        tight = token_f1("بودجه هزینه", "بودجه هزینه")
        padded = token_f1("بودجه هزینه " + "اضافه " * 20, "بودجه هزینه")
        self.assertLess(padded, tight)


class SplitTests(unittest.TestCase):
    def test_the_split_is_the_same_every_time(self):
        data = rows(200)
        first = split_dataset(data)[1]
        second = split_dataset(data)[1]
        self.assertEqual([r["digest"] for r in first], [r["digest"] for r in second])

    def test_a_row_keeps_its_side_as_the_dataset_grows(self):
        small = rows(50)
        grown = small + rows(50)[:0] + [
            {"messages": [{"role": "user", "content": f"تازه {i}"},
                          {"role": "assistant", "content": "پ"}], "digest": f"n{i:063d}"}
            for i in range(50)
        ]
        before = {r["digest"] for r in split_dataset(small)[1]}
        after = {r["digest"] for r in split_dataset(grown)[1]}
        # Every original validation row is still a validation row.
        self.assertTrue(before <= after)

    def test_the_fraction_is_roughly_honoured(self):
        train, validation = split_dataset(rows(1000), validation_fraction=0.2)
        self.assertEqual(len(train) + len(validation), 1000)
        self.assertAlmostEqual(len(validation) / 1000, 0.2, delta=0.05)

    def test_an_impossible_fraction_is_refused(self):
        for fraction in (0.0, 1.0, -0.2, 1.5):
            with self.subTest(fraction=fraction), self.assertRaises(ValueError):
                split_dataset(rows(10), validation_fraction=fraction)

    def test_rows_without_a_digest_still_split_stably(self):
        plain = [{"messages": r["messages"]} for r in rows(100)]
        self.assertEqual(
            [r["messages"] for r in split_dataset(plain)[1]],
            [r["messages"] for r in split_dataset(plain)[1]],
        )


class DatasetLoadingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "dataset.jsonl"

    def test_a_dataset_round_trips(self):
        self.path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows(5)),
            encoding="utf-8")
        self.assertEqual(len(load_dataset(self.path)), 5)

    def test_a_broken_line_is_skipped_not_fatal(self):
        good = json.dumps(rows(1)[0], ensure_ascii=False)
        self.path.write_text(good + "\n{not json\n" + good + "\n", encoding="utf-8")
        self.assertEqual(len(load_dataset(self.path)), 2)

    def test_a_row_without_messages_is_ignored(self):
        self.path.write_text('{"rating": "good"}\n', encoding="utf-8")
        self.assertEqual(load_dataset(self.path), [])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.chain = make_chain()
        self.ledger = LocalLedger(self.chain, AUTHORITY)

    def kernel(self, credits: int, answer: str = "the answer"):
        runtime = FakeRuntime("ollama", answer=answer,
                              prompt_tokens=20, completion_tokens=8).__enter__()
        self.addCleanup(runtime.__exit__, None, None, None)
        self.chain.seal_block(
            AUTHORITY,
            [build_grant(AUTHORITY, self.chain.state.get(AUTHORITY.public_hex()).nonce,
                         beneficiary=AGENT.public_hex(), amount=credits)],
        )
        registry = ToolRegistry()
        registry.register(build_model_tool(
            LocalModel(base_url=runtime.url, dialect="ollama", model="m")))
        return AgentKernel(AGENT, self.ledger, tools=registry, label="atlas")

    def test_a_perfect_model_scores_one(self):
        report = Evaluation(self.kernel(5 * CREDIT), rows(5), max_tokens=64).run()
        self.assertEqual(len(report.scored), 5)
        self.assertAlmostEqual(report.mean_f1, 1.0)

    def test_a_wrong_model_scores_zero(self):
        kernel = self.kernel(5 * CREDIT, answer="something else entirely")
        report = Evaluation(kernel, rows(5), max_tokens=64).run()
        self.assertEqual(report.mean_f1, 0.0)

    def test_evaluation_costs_credits_and_says_how_much(self):
        report = Evaluation(self.kernel(5 * CREDIT), rows(4), max_tokens=64).run()
        self.assertGreater(report.spent, 0)
        self.assertEqual(report.spent, sum(r.cost for r in report.results))

    def test_a_row_the_agent_cannot_afford_is_refused_not_scored_zero(self):
        # The distinction matters: an unaffordable row is a missing
        # measurement, not a bad one.
        report = Evaluation(self.kernel(200), rows(3), max_tokens=64).run()
        self.assertGreater(report.refused, 0)
        self.assertNotIn("refused", [r.status for r in report.scored])

    def test_a_malformed_row_is_reported_as_failed(self):
        bad = [{"messages": [{"role": "user", "content": "سؤال"}]}]   # no reference
        report = Evaluation(self.kernel(5 * CREDIT), bad).run()
        self.assertEqual(report.failed, 1)
        self.assertIn("reference", report.results[0].reason)

    def test_the_limit_stops_the_run_early(self):
        report = Evaluation(self.kernel(5 * CREDIT), rows(10), max_tokens=64).run(limit=3)
        self.assertEqual(len(report.results), 3)

    def test_progress_is_reported_row_by_row(self):
        seen = []
        Evaluation(self.kernel(5 * CREDIT), rows(3), max_tokens=64).run(on_row=seen.append)
        self.assertEqual(len(seen), 3)
        self.assertIsInstance(seen[0], RowResult)


class ComparisonTests(unittest.TestCase):
    def report(self, scores, label=""):
        return EvalReport(label=label, results=[
            RowResult(key=f"k{i}", prompt="س", reference="پ", answer="پ", f1=score,
                      rouge=score)
            for i, score in enumerate(scores)
        ])

    def test_an_improvement_is_reported_as_one(self):
        better = self.report([0.8] * 12).compare(self.report([0.5] * 12))
        self.assertTrue(better["comparable"])
        self.assertGreater(better["f1_delta"], 0)
        self.assertIn("better", better["verdict"])

    def test_a_regression_is_reported_as_one(self):
        worse = self.report([0.3] * 12).compare(self.report([0.7] * 12))
        self.assertLess(worse["f1_delta"], 0)
        self.assertIn("worse", worse["verdict"])

    def test_a_tiny_sample_refuses_to_draw_a_conclusion(self):
        # The most common way an evaluation misleads: three rows and a verdict.
        verdict = self.report([0.9] * 3).compare(self.report([0.1] * 3))["verdict"]
        self.assertIn("too few", verdict)

    def test_no_meaningful_difference_says_so(self):
        verdict = self.report([0.5] * 20).compare(self.report([0.502] * 20))["verdict"]
        self.assertIn("no meaningful difference", verdict)

    def test_runs_with_no_shared_rows_are_not_compared(self):
        mine = EvalReport(results=[RowResult(key="a", prompt="س", reference="پ", f1=0.9)])
        theirs = EvalReport(results=[RowResult(key="b", prompt="س", reference="پ", f1=0.1)])
        comparison = mine.compare(theirs)
        self.assertFalse(comparison["comparable"])
        self.assertIn("share no scored rows", comparison["reason"])

    def test_only_shared_rows_count(self):
        mine = EvalReport(results=[
            RowResult(key="a", prompt="س", reference="پ", f1=0.5),
            RowResult(key="b", prompt="س", reference="پ", f1=1.0),
        ])
        theirs = EvalReport(results=[RowResult(key="a", prompt="س", reference="پ", f1=0.5)])
        comparison = mine.compare(theirs)
        self.assertEqual(comparison["shared"], 1)
        self.assertEqual(comparison["f1_delta"], 0.0)

    def test_a_report_survives_being_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            original = self.report([0.4, 0.6], label="baseline")
            original.save(path)
            loaded = EvalReport.load(path)
        self.assertEqual(loaded.label, "baseline")
        self.assertAlmostEqual(loaded.mean_f1, original.mean_f1)


if __name__ == "__main__":
    unittest.main()
