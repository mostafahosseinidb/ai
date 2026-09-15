"""The vocabulary the model thinks in.

A tokenizer bug is uniquely nasty: nothing raises, the model simply learns
less than it should and nobody finds out for a week of training. So the
properties that matter -- round-tripping, stable ids, Persian costing what
it should -- are asserted rather than assumed.
"""

import json
import tempfile
import unittest
from pathlib import Path

from chainmind.tokenizer import SPECIAL_TOKENS, Tokenizer, train_tokenizer

PERSIAN = (
    "سلام. من یک عامل مستقل هستم و مصرف منابعم روی زنجیره ثبت می‌شود. "
    "هر کاری که می‌کنم اول برآورد می‌شود، بعد اجازه می‌گیرد، بعد اجرا می‌شود."
)
MIXED = PERSIAN + " The ledger authorises before the agent acts. 42 tokens."


def small(vocab_size: int = 600) -> Tokenizer:
    return train_tokenizer([MIXED] * 4, vocab_size=vocab_size)


class TrainingTests(unittest.TestCase):
    def test_it_learns_merges_and_stays_within_the_budget(self):
        tokenizer = small(500)
        self.assertLessEqual(tokenizer.vocab_size, 500)
        self.assertGreater(len(tokenizer.merges), 0)

    def test_the_first_ids_are_the_control_tokens(self):
        # Fixed forever: a weights file records ids, not names, so moving
        # these would silently repurpose every embedding row above them.
        tokenizer = small()
        for index, name in enumerate(SPECIAL_TOKENS):
            self.assertEqual(tokenizer.special(name), index)

    def test_a_vocabulary_smaller_than_the_bytes_is_refused(self):
        with self.assertRaises(ValueError):
            train_tokenizer([MIXED], vocab_size=100)

    def test_an_empty_corpus_is_refused(self):
        with self.assertRaises(ValueError):
            train_tokenizer([], vocab_size=400)

    def test_training_is_deterministic(self):
        # Two runs must give the same ids, or a tokenizer and a checkpoint
        # trained on different days stop matching.
        self.assertEqual(small().merges, small().merges)


class RoundTripTests(unittest.TestCase):
    def test_persian_survives_encoding_and_decoding(self):
        tokenizer = small()
        self.assertEqual(tokenizer.decode(tokenizer.encode(PERSIAN)), PERSIAN)

    def test_text_it_never_saw_survives_too(self):
        # Byte level means there is no such thing as an unknown token.
        tokenizer = small()
        unseen = "日本語 ✅ emoji 🙂 and ǆ"
        self.assertEqual(tokenizer.decode(tokenizer.encode(unseen)), unseen)

    def test_zero_width_non_joiners_are_preserved(self):
        # می‌شود is one word with a ZWNJ in it; losing it changes the word.
        tokenizer = small()
        self.assertEqual(tokenizer.decode(tokenizer.encode("می‌شود")), "می‌شود")

    def test_training_on_persian_makes_persian_cheap(self):
        """The reason not to borrow somebody else's vocabulary.

        Trained here, a Persian sentence costs far fewer tokens than its
        bytes -- which is exactly the capacity a foreign vocabulary spends
        and cannot be fine-tuned back.
        """
        tokenizer = train_tokenizer([PERSIAN] * 8, vocab_size=1000)
        self.assertLess(len(tokenizer.encode(PERSIAN)), len(PERSIAN.encode("utf-8")) / 3)

    def test_decoding_a_truncated_sequence_does_not_raise(self):
        tokenizer = small()
        ids = tokenizer.encode("سلام")
        # Chop the last id: a generation cut short mid-character must show a
        # replacement mark, not take the server down.
        self.assertIsInstance(tokenizer.decode(ids[:-1]), str)

    def test_ids_outside_the_vocabulary_are_ignored(self):
        tokenizer = small()
        self.assertEqual(tokenizer.decode([10 ** 9, -4]), "")


class ChatTemplateTests(unittest.TestCase):
    def test_roles_become_control_tokens(self):
        tokenizer = small()
        ids = tokenizer.apply_chat_template([
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ])
        self.assertEqual(ids[0], tokenizer.special("<|system|>"))
        self.assertIn(tokenizer.special("<|user|>"), ids)
        self.assertEqual(ids[-1], tokenizer.special("<|assistant|>"))

    def test_the_generation_prompt_can_be_left_off(self):
        tokenizer = small()
        ids = tokenizer.apply_chat_template(
            [{"role": "user", "content": "u"}], add_generation_prompt=False
        )
        self.assertEqual(ids[-1], tokenizer.special("<|end|>"))

    def test_an_unknown_role_is_treated_as_the_user(self):
        tokenizer = small()
        ids = tokenizer.apply_chat_template(
            [{"role": "wizard", "content": "u"}], add_generation_prompt=False
        )
        self.assertEqual(ids[0], tokenizer.special("<|user|>"))


class PersistenceTests(unittest.TestCase):
    def test_a_tokenizer_survives_a_round_trip_through_json(self):
        tokenizer = small()
        restored = Tokenizer.from_dict(json.loads(json.dumps(tokenizer.to_dict())))
        self.assertEqual(restored.encode(PERSIAN), tokenizer.encode(PERSIAN))
        self.assertEqual(restored.vocab_size, tokenizer.vocab_size)

    def test_it_saves_and_loads_from_a_file(self):
        tokenizer = small()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vocab.json"
            tokenizer.save(path)
            self.assertEqual(Tokenizer.load(path).merges, tokenizer.merges)

    def test_different_control_tokens_are_refused(self):
        # Loading these would line the weights up against the wrong ids and
        # produce a model that is subtly, unfixably wrong.
        payload = small().to_dict()
        payload["specials"] = ["<|pad|>"]
        with self.assertRaises(ValueError):
            Tokenizer.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
