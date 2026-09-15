import json
import tempfile
import unittest
from pathlib import Path

from chainmind.memory import (
    RATINGS,
    FeedbackStore,
    MemoryStore,
    build_training_dataset,
    normalise,
    tokenise,
)


class NormalisationTests(unittest.TestCase):
    """Without this, half a Persian corpus silently fails to match."""

    def test_arabic_and_persian_letters_fold_together(self):
        for arabic, persian in (("كتاب", "کتاب"), ("يک", "یک"), ("علي", "علی")):
            with self.subTest(pair=(arabic, persian)):
                self.assertEqual(normalise(arabic), normalise(persian))

    def test_diacritics_are_dropped(self):
        self.assertEqual(tokenise("کِتابِ بُزرگ"), tokenise("کتاب بزرگ"))

    def test_the_zero_width_joiner_splits_words(self):
        # "می‌رود" is two morphemes; indexing it whole finds neither.
        self.assertIn("رود", tokenise("می‌رود"))

    def test_digits_from_every_script_agree(self):
        self.assertEqual(tokenise("۱۲۳"), tokenise("١٢٣"))
        self.assertEqual(tokenise("۱۲۳"), tokenise("123"))

    def test_stopwords_are_removed_in_both_languages(self):
        self.assertEqual(tokenise("این کتاب از آن من است"), ["کتاب", "من"])
        self.assertEqual(tokenise("this is the book"), ["book"])

    def test_a_stopword_that_normalisation_rewrites_is_still_removed(self):
        # "آن" normalises to "ان"; a raw stop list would miss it.
        self.assertNotIn("ان", tokenise("آن کتاب"))

    def test_punctuation_does_not_become_a_token(self):
        self.assertEqual(tokenise("سلام، دنیا! (۲۰۲۶)"), ["سلام", "دنیا", "2026"])


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "memory.jsonl"
        self.memory = MemoryStore(self.path)

    def seed(self):
        self.memory.remember("بودجهٔ عامل در زنجیره نگه داشته می‌شود و خودش نمی‌تواند تغییرش دهد",
                             kind="fact")
        self.memory.remember("سهمیهٔ دوره‌ای نرخ مصرف را محدود می‌کند، نه هزینهٔ کل", kind="fact")
        self.memory.remember("The ledger refuses an over-budget transaction outright",
                             kind="fact")
        self.memory.remember("قهوه را با شیر دوست دارم", kind="preference")


class SearchTests(MemoryTestCase):
    def test_a_query_finds_the_note_that_answers_it(self):
        self.seed()
        hits = self.memory.search("سهمیه چه چیزی را محدود می‌کند؟")
        self.assertTrue(hits)
        self.assertIn("سهمیه", hits[0].note.text)

    def test_search_works_across_the_two_languages_separately(self):
        self.seed()
        self.assertIn("ledger", self.memory.search("over-budget ledger")[0].note.text)
        self.assertIn("بودجه", self.memory.search("بودجهٔ عامل")[0].note.text)

    def test_an_arabic_spelling_finds_a_persian_note(self):
        self.memory.remember("کتاب راهنمای شبکه")
        self.assertTrue(self.memory.search("كتاب"))

    def test_results_come_back_best_first(self):
        self.seed()
        hits = self.memory.search("سهمیه بودجه", limit=4)
        scores = [hit.score for hit in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_a_query_with_nothing_to_match_returns_nothing(self):
        self.seed()
        self.assertEqual(self.memory.search("زرافه"), [])

    def test_a_query_of_only_stopwords_returns_nothing(self):
        self.seed()
        self.assertEqual(self.memory.search("این از آن است"), [])

    def test_results_can_be_restricted_by_kind(self):
        self.seed()
        hits = self.memory.search("قهوه", kinds=["fact"])
        self.assertEqual(hits, [])
        self.assertTrue(self.memory.search("قهوه", kinds=["preference"]))

    def test_a_rarer_word_outranks_a_common_one(self):
        for i in range(10):
            self.memory.remember(f"گزارش روزانهٔ شمارهٔ {i} دربارهٔ وضعیت شبکه")
        self.memory.remember("گزارش ویژه دربارهٔ رمزنگاری منحنی بیضوی")
        hits = self.memory.search("رمزنگاری گزارش")
        self.assertIn("رمزنگاری", hits[0].note.text)


class RecallTests(MemoryTestCase):
    def test_recall_renders_notes_for_a_prompt(self):
        self.seed()
        context, used = self.memory.recall_context("سهمیه چیست؟")
        self.assertTrue(context.startswith("- "))
        self.assertTrue(used)

    def test_recall_is_bounded_so_it_cannot_run_up_the_bill(self):
        for i in range(40):
            self.memory.remember("سهمیه " + "طولانی " * 60 + str(i))
        context, used = self.memory.recall_context("سهمیه", limit=10, max_characters=400)
        self.assertLessEqual(len(context), 400)
        self.assertLess(len(used), 10)

    def test_recall_on_an_empty_memory_is_empty_not_an_error(self):
        context, used = self.memory.recall_context("هر چیزی")
        self.assertEqual(context, "")
        self.assertEqual(used, [])


class PersistenceTests(MemoryTestCase):
    def test_notes_survive_a_reload(self):
        self.seed()
        reloaded = MemoryStore(self.path)
        self.assertEqual(len(reloaded.notes()), 4)
        self.assertTrue(reloaded.search("سهمیه"))

    def test_forgetting_survives_a_reload(self):
        note = self.memory.remember("چیزی که باید فراموش شود")
        self.assertTrue(self.memory.forget(note.id))
        self.assertEqual(self.memory.search("فراموش"), [])
        self.assertEqual(MemoryStore(self.path).search("فراموش"), [])

    def test_forgetting_something_absent_is_false_not_an_error(self):
        self.assertFalse(self.memory.forget("nope"))

    def test_a_truncated_last_line_does_not_break_loading(self):
        self.seed()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"id": "broken", "text": "cut off')
        reloaded = MemoryStore(self.path)
        self.assertEqual(len(reloaded.notes()), 4)

    def test_an_empty_note_is_refused(self):
        for text in ("", "   ", "\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.memory.remember(text)

    def test_a_note_commits_to_its_own_content(self):
        note = self.memory.remember("چیزی مهم")
        self.assertEqual(len(note.digest), 64)
        other = self.memory.remember("چیزی دیگر")
        self.assertNotEqual(note.digest, other.digest)

    def test_the_store_stops_growing_without_limit(self):
        small = MemoryStore(Path(self.dir.name) / "small.jsonl", max_notes=5)
        for i in range(12):
            small.remember(f"یادداشت شمارهٔ {i}")
        self.assertEqual(len(small.notes()), 5)
        # The oldest went, the newest stayed.
        self.assertTrue(small.search("11"))

    def test_stats_describe_the_store(self):
        self.seed()
        stats = self.memory.stats()
        self.assertEqual(stats["notes"], 4)
        self.assertEqual(stats["kinds"]["fact"], 3)
        self.assertGreater(stats["terms"], 0)


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "feedback.jsonl"
        self.feedback = FeedbackStore(self.path)

    def test_a_rating_is_recorded_and_retrievable(self):
        self.feedback.rate("conv1", 1, "good", prompt="سؤال", answer="پاسخ")
        entry = self.feedback.get("conv1", 1)
        self.assertEqual(entry.rating, "good")
        self.assertEqual(entry.answer, "پاسخ")

    def test_rating_the_same_answer_twice_corrects_it(self):
        self.feedback.rate("conv1", 1, "good", prompt="س", answer="پ")
        self.feedback.rate("conv1", 1, "bad", prompt="س", answer="پ")
        self.assertEqual(self.feedback.get("conv1", 1).rating, "bad")
        self.assertEqual(self.feedback.stats()["total"], 1)

    def test_an_unknown_rating_is_refused(self):
        with self.assertRaises(ValueError):
            self.feedback.rate("conv1", 1, "excellent")

    def test_ratings_survive_a_reload(self):
        self.feedback.rate("conv1", 1, "good", prompt="س", answer="پ")
        self.assertEqual(FeedbackStore(self.path).stats()["good"], 1)

    def test_stats_report_an_approval_rate(self):
        for turn, rating in enumerate(["good", "good", "bad"]):
            self.feedback.rate("conv1", turn, rating, prompt="س", answer="پ")
        stats = self.feedback.stats()
        self.assertEqual(stats["total"], 3)
        self.assertAlmostEqual(stats["approval"], 2 / 3, places=3)

    def test_approval_is_none_rather_than_zero_when_nothing_is_rated(self):
        self.assertIsNone(self.feedback.stats()["approval"])

    def test_every_rating_in_the_vocabulary_is_accepted(self):
        for turn, rating in enumerate(RATINGS):
            self.feedback.rate("conv", turn, rating, prompt="س", answer="پ")
        self.assertEqual(self.feedback.stats()["total"], len(RATINGS))


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.feedback = FeedbackStore(Path(self.dir.name) / "feedback.jsonl")

    def test_a_dataset_holds_only_the_answers_that_were_approved(self):
        self.feedback.rate("c", 1, "good", prompt="سؤال خوب", answer="پاسخ خوب")
        self.feedback.rate("c", 3, "bad", prompt="سؤال بد", answer="پاسخ بد")
        rows = build_training_dataset(self.feedback)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["messages"][0]["content"], "سؤال خوب")
        self.assertEqual(rows[0]["messages"][1]["role"], "assistant")

    def test_the_rejected_answers_can_be_exported_too(self):
        self.feedback.rate("c", 1, "bad", prompt="س", answer="پ")
        self.assertEqual(len(build_training_dataset(self.feedback, rating="bad")), 1)

    def test_a_rating_with_no_content_is_not_a_training_row(self):
        self.feedback.rate("c", 1, "good")      # rated, but nothing recorded
        self.assertEqual(build_training_dataset(self.feedback), [])

    def test_rows_are_json_serialisable(self):
        self.feedback.rate("c", 1, "good", prompt="س", answer="پ")
        rows = build_training_dataset(self.feedback)
        self.assertEqual(json.loads(json.dumps(rows)), rows)


if __name__ == "__main__":
    unittest.main()
