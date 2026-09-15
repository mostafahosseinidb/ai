"""The agent's own tokenizer, trained on the agent's own text.

Every other piece of this project refuses to depend on someone else's
service.  A tokenizer taken from an existing model would be the last place
that dependency survives: it fixes the vocabulary, and the vocabulary
decides what the model can cheaply say.  A vocabulary learned from English
web text spends three or four tokens on a Persian word that deserves one,
and no amount of fine-tuning buys those tokens back.

So this trains one.  Byte-level BPE, which is the same algorithm the large
models use and is short enough to read in one sitting:

1.  Start from the 256 byte values.  Every possible input is representable,
    no ``<unk>`` exists, and nothing has to be normalised away first --
    which matters for Persian, where normalising is where meaning gets lost.
2.  Count adjacent pairs, merge the commonest, repeat.  Each merge adds one
    entry to the vocabulary and shortens the corpus.
3.  Stop at the requested size.

Training is offline and deliberately slow.  Encoding, which happens on
every turn, is the part that had to be quick, so merges are applied through
a rank table and a linked list rather than by rescanning the text.

Standard library only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

__all__ = [
    "Tokenizer",
    "SPECIAL_TOKENS",
    "train_tokenizer",
]

#: Control tokens, given the lowest ids so their numbers never move when the
#: vocabulary grows.  The chat template below is the only thing that writes
#: them, and the model learns their meaning from position alone.
SPECIAL_TOKENS: tuple[str, ...] = (
    "<|pad|>",
    "<|end|>",
    "<|system|>",
    "<|user|>",
    "<|assistant|>",
)

#: Text is split before merging so that a merge can never straddle a word
#: boundary -- otherwise ``"the cat"`` becomes a single token and the model
#: wastes capacity memorising phrases.  The classes are, in order: a run of
#: letters (any script, so Persian and Latin behave the same), a run of
#: digits, a space followed by either, any other single character.
_SPLIT = re.compile(r" ?\w+|\s+|[^\s\w]+", re.UNICODE)


def _pieces(text: str) -> list[bytes]:
    return [piece.encode("utf-8") for piece in _SPLIT.findall(text) if piece]


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------

def _count_pairs(words: Mapping[tuple[int, ...], int]) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = {}
    for symbols, weight in words.items():
        for pair in zip(symbols, symbols[1:]):
            counts[pair] = counts.get(pair, 0) + weight
    return counts


def _merge_word(symbols: tuple[int, ...], pair: tuple[int, int],
                new_id: int) -> tuple[int, ...]:
    out: list[int] = []
    index = 0
    limit = len(symbols) - 1
    while index < len(symbols):
        if index < limit and symbols[index] == pair[0] and symbols[index + 1] == pair[1]:
            out.append(new_id)
            index += 2
        else:
            out.append(symbols[index])
            index += 1
    return tuple(out)


def train_tokenizer(texts: Iterable[str], vocab_size: int = 4096,
                    min_frequency: int = 2) -> "Tokenizer":
    """Learn a vocabulary from a corpus.

    Works on unique words weighted by how often they occur rather than on the
    raw stream, which is what makes this finish on one machine: a corpus of
    millions of words usually has only tens of thousands of distinct ones.
    """
    floor = len(SPECIAL_TOKENS) + 256
    if vocab_size < floor:
        raise ValueError(f"vocab_size must be at least {floor} (specials + bytes)")

    words: dict[tuple[int, ...], int] = {}
    for text in texts:
        for piece in _pieces(text):
            symbols = tuple(byte + len(SPECIAL_TOKENS) for byte in piece)
            words[symbols] = words.get(symbols, 0) + 1
    if not words:
        raise ValueError("nothing to train on")

    merges: list[tuple[int, int]] = []
    next_id = floor
    while next_id < vocab_size:
        counts = _count_pairs(words)
        if not counts:
            break
        pair, frequency = max(counts.items(), key=lambda item: (item[1], item[0]))
        if frequency < min_frequency:
            break
        words = {
            _merge_word(symbols, pair, next_id): weight
            for symbols, weight in words.items()
        }
        merges.append(pair)
        next_id += 1

    return Tokenizer(merges=merges)


# --------------------------------------------------------------------------
# The tokenizer
# --------------------------------------------------------------------------

@dataclass
class Tokenizer:
    """A byte-level BPE vocabulary.

    Fully described by its merge list: the ids below ``256 + len(specials)``
    are fixed, and every id above that is the result of one merge, in order.
    Two tokenizers with the same merges are the same tokenizer, which is what
    lets a weights file carry its own and stay self-contained.
    """

    merges: Sequence[tuple[int, int]] = field(default_factory=list)
    _ranks: dict[tuple[int, int], int] = field(init=False, repr=False)
    _bytes: list[bytes] = field(init=False, repr=False)
    _cache: dict[bytes, tuple[int, ...]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.merges = [tuple(pair) for pair in self.merges]  # type: ignore[misc]
        base = len(SPECIAL_TOKENS)
        self._ranks = {pair: index for index, pair in enumerate(self.merges)}
        self._bytes = [b""] * base + [bytes([value]) for value in range(256)]
        for left, right in self.merges:
            self._bytes.append(self._bytes[left] + self._bytes[right])
        self._cache = {}

    # -- identity ----------------------------------------------------------

    @property
    def vocab_size(self) -> int:
        return len(self._bytes)

    def special(self, name: str) -> int:
        return SPECIAL_TOKENS.index(name)

    # -- encoding ----------------------------------------------------------

    def _encode_piece(self, piece: bytes) -> tuple[int, ...]:
        cached = self._cache.get(piece)
        if cached is not None:
            return cached

        base = len(SPECIAL_TOKENS)
        symbols = [byte + base for byte in piece]
        while len(symbols) > 1:
            best_rank = len(self.merges)
            best_at = -1
            for index in range(len(symbols) - 1):
                rank = self._ranks.get((symbols[index], symbols[index + 1]), best_rank)
                if rank < best_rank:
                    best_rank, best_at = rank, index
            if best_at < 0:
                break
            merged = base + 256 + best_rank
            symbols[best_at:best_at + 2] = [merged]

        result = tuple(symbols)
        if len(self._cache) < 100_000:
            self._cache[piece] = result
        return result

    def encode(self, text: str) -> list[int]:
        ids: list[int] = []
        for piece in _pieces(text):
            ids.extend(self._encode_piece(piece))
        return ids

    def count(self, text: str) -> int:
        return len(self.encode(text))

    # -- decoding ----------------------------------------------------------

    def decode(self, ids: Iterable[int]) -> str:
        """Bytes back to text.

        ``errors="replace"`` because generation can stop in the middle of a
        multi-byte character, and a half-written Persian letter should show as
        one replacement mark rather than raise.
        """
        blob = b"".join(
            self._bytes[token] for token in ids
            if 0 <= token < len(self._bytes)
        )
        return blob.decode("utf-8", errors="replace")

    # -- chat template -----------------------------------------------------

    def apply_chat_template(self, messages: Sequence[Mapping[str, str]], *,
                            add_generation_prompt: bool = True) -> list[int]:
        """Turn a conversation into ids the model was trained to expect.

        The template is part of the model, not of the caller: it lives here so
        that the trainer and the server cannot disagree about it, which is the
        usual way a home-trained model ends up producing nonsense.
        """
        role_token = {
            "system": self.special("<|system|>"),
            "user": self.special("<|user|>"),
            "assistant": self.special("<|assistant|>"),
        }
        ids: list[int] = []
        for message in messages:
            role = str(message.get("role", "user"))
            ids.append(role_token.get(role, role_token["user"]))
            ids.extend(self.encode(str(message.get("content", ""))))
            ids.append(self.special("<|end|>"))
        if add_generation_prompt:
            ids.append(role_token["assistant"])
        return ids

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "byte-bpe",
            "specials": list(SPECIAL_TOKENS),
            "merges": [list(pair) for pair in self.merges],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "Tokenizer":
        specials = list(payload.get("specials") or SPECIAL_TOKENS)
        if tuple(specials) != SPECIAL_TOKENS:
            raise ValueError(
                "this tokenizer was built with different control tokens; the ids "
                "would not line up with the weights"
            )
        merges = [(int(pair[0]), int(pair[1])) for pair in payload.get("merges", [])]  # type: ignore[index]
        return cls(merges=merges)

    def save(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> "Tokenizer":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
