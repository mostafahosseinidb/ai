"""What the system keeps, and what it learns from.

Two stores, both plain files, both dependency-free:

*   :class:`MemoryStore` -- everything worth recalling later, searchable by
    relevance.  Before answering, the agent looks here; after answering, it
    writes what happened back.  A conversation therefore builds on the ones
    before it instead of starting from nothing every time.
*   :class:`FeedbackStore` -- whether an answer was any good, as judged by
    the person who received it.  On its own that improves nothing; its
    purpose is to accumulate into a dataset that a later fine-tune can
    actually train on.  This is the honest shape of "learning": collect the
    signal now, train on it deliberately, measure whether it helped.

Retrieval is BM25 over a hand-rolled index.  It is not a neural retriever
and does not pretend to be, but it is genuinely good at short queries over
a personal-scale corpus, it costs microseconds, and it adds no dependency.

The Persian normalisation below is not decoration.  Without it, ``کتاب`` and
``كتاب`` -- Persian and Arabic kaf, visually near-identical -- are different
tokens, and half of a Persian corpus silently fails to match.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .crypto import canonical_bytes, sha256_hex

__all__ = [
    "MemoryStore",
    "FeedbackStore",
    "Note",
    "Hit",
    "Feedback",
    "normalise",
    "tokenise",
    "build_training_dataset",
    "RATINGS",
]

#: What a person can say about an answer.  Deliberately coarse: a five-point
#: scale invites deliberation that nobody actually does.
RATINGS = ("good", "bad")

BM25_K1 = 1.5
BM25_B = 0.75

#: Arabic forms that must fold into their Persian counterparts, plus the
#: presentation forms that copy-paste drags in.
_FOLD = {
    "ي": "ی",   # Arabic yeh    -> Persian yeh
    "ى": "ی",   # alef maksura  -> Persian yeh
    "ك": "ک",   # Arabic kaf    -> Persian keheh
    "ۀ": "ه",   # heh with yeh  -> heh
    "ة": "ه",   # teh marbuta   -> heh
    "أ": "ا",   # alef with hamza above
    "إ": "ا",   # alef with hamza below
    "آ": "ا",   # alef with madda
    "ـ": "",         # tatweel, pure decoration
}

#: Harakat and other combining marks: written rarely, searched never.
_DIACRITICS = re.compile("[ً-ْٰٓ-ٕٖ-ٟ]")

#: Zero-width non-joiner. "می‌رود" is "می" + "رود"; splitting there finds both.
_ZWNJ = "‌"

_DIGITS = {ord(c): str(i % 10) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩")}

_WORD = re.compile(r"[^\W_]+", re.UNICODE)

#: Words too common to carry meaning, in both languages the project uses.
#: Normalised at import, because otherwise a stop word that normalisation
#: rewrites -- "آن" becomes "ان" -- never matches the list and is indexed
#: anyway, which is the failure mode this list exists to prevent.
_STOPWORD_SOURCE = """
the a an and or of to in is are was were be been for on at by with as that this it
از به با در را که این آن و یا هم تا برای هست است بود شد می نمی های ها یک ای
"""


def _build_stopwords() -> frozenset[str]:
    return frozenset(
        word for word in _WORD.findall(_normalise_raw(_STOPWORD_SOURCE)) if word
    )


def _normalise_raw(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_DIGITS)
    for source, target in _FOLD.items():
        text = text.replace(source, target)
    text = _DIACRITICS.sub("", text)
    text = text.replace(_ZWNJ, " ")
    return text.casefold()


def normalise(text: str) -> str:
    """Fold a string into the one form the index stores.

    Applied identically when writing and when searching; a mismatch between
    the two is the classic way a search box quietly returns nothing.
    """
    return _normalise_raw(text)


_STOPWORDS = _build_stopwords()


def tokenise(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Words, normalised, with the noise dropped."""
    tokens = _WORD.findall(normalise(text))
    if keep_stopwords:
        return tokens
    return [token for token in tokens if token not in _STOPWORDS and len(token) > 1]


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class Note:
    """One thing worth remembering."""

    id: str
    text: str
    kind: str = "note"
    created_at: int = field(default_factory=lambda: int(time.time()))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """A commitment to this note, for putting on chain."""
        return sha256_hex(canonical_bytes(
            {"id": self.id, "text": self.text, "kind": self.kind,
             "created_at": self.created_at}
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "kind": self.kind,
            "created_at": self.created_at, "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Note":
        return cls(
            id=str(data["id"]), text=str(data.get("text", "")),
            kind=str(data.get("kind", "note")),
            created_at=int(data.get("created_at", 0)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class Hit:
    note: Note
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {**self.note.to_dict(), "score": round(self.score, 4)}


@dataclass
class Feedback:
    """A person's verdict on one answer."""

    id: str
    conversation_id: str
    turn: int
    rating: str
    prompt: str = ""
    answer: str = ""
    note: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))

    @property
    def digest(self) -> str:
        return sha256_hex(canonical_bytes({
            "conversation": self.conversation_id, "turn": self.turn,
            "rating": self.rating, "prompt": self.prompt, "answer": self.answer,
        }))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "conversation_id": self.conversation_id, "turn": self.turn,
            "rating": self.rating, "prompt": self.prompt, "answer": self.answer,
            "note": self.note, "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Feedback":
        return cls(
            id=str(data["id"]), conversation_id=str(data.get("conversation_id", "")),
            turn=int(data.get("turn", 0)), rating=str(data.get("rating", "")),
            prompt=str(data.get("prompt", "")), answer=str(data.get("answer", "")),
            note=str(data.get("note", "")), created_at=int(data.get("created_at", 0)),
        )


# --------------------------------------------------------------------------
# The index
# --------------------------------------------------------------------------

class _Bm25Index:
    """Okapi BM25 over an in-memory posting list.

    Everything lives in memory and is rebuilt on load.  That is the right
    trade at personal scale -- tens of thousands of notes index in well under
    a second -- and the wrong one at a million, which is documented rather
    than pretended away.
    """

    def __init__(self) -> None:
        self.postings: dict[str, dict[str, int]] = {}
        self.lengths: dict[str, int] = {}
        self._total_length = 0

    def add(self, note_id: str, tokens: Sequence[str]) -> None:
        self.remove(note_id)
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            self.postings.setdefault(token, {})[note_id] = count
        self.lengths[note_id] = len(tokens)
        self._total_length += len(tokens)

    def remove(self, note_id: str) -> None:
        length = self.lengths.pop(note_id, None)
        if length is None:
            return
        self._total_length -= length
        for token, docs in list(self.postings.items()):
            if docs.pop(note_id, None) is not None and not docs:
                del self.postings[token]

    @property
    def size(self) -> int:
        return len(self.lengths)

    @property
    def average_length(self) -> float:
        return (self._total_length / self.size) if self.size else 0.0

    def score(self, query_tokens: Sequence[str]) -> dict[str, float]:
        if not self.size:
            return {}
        average = self.average_length or 1.0
        scores: dict[str, float] = {}
        for token in set(query_tokens):
            docs = self.postings.get(token)
            if not docs:
                continue
            # The +0.5/+1 smoothing keeps a term present in every document
            # from scoring negative, which the textbook formula otherwise does.
            idf = math.log(1 + (self.size - len(docs) + 0.5) / (len(docs) + 0.5))
            for note_id, frequency in docs.items():
                length = self.lengths.get(note_id, 0)
                denominator = frequency + BM25_K1 * (
                    1 - BM25_B + BM25_B * length / average
                )
                scores[note_id] = scores.get(note_id, 0.0) + idf * (
                    frequency * (BM25_K1 + 1) / denominator
                )
        return scores


# --------------------------------------------------------------------------
# Stores
# --------------------------------------------------------------------------

class _JsonlStore:
    """An append-only JSONL file with an in-memory mirror."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._records: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue          # a half-written final line is not a crash
            self._adopt(payload)

    def _adopt(self, payload: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def _append(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def __len__(self) -> int:
        return len(self._records)


class MemoryStore(_JsonlStore):
    """Everything the system can recall, ranked by relevance."""

    def __init__(self, path: Path | str, *, max_notes: int = 50_000) -> None:
        self.index = _Bm25Index()
        self.max_notes = max_notes
        self._notes: dict[str, Note] = {}
        super().__init__(path)

    def _adopt(self, payload: Mapping[str, Any]) -> None:
        if payload.get("deleted"):
            note_id = str(payload.get("id", ""))
            self._notes.pop(note_id, None)
            self.index.remove(note_id)
            return
        try:
            note = Note.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return
        self._notes[note.id] = note
        self.index.add(note.id, tokenise(note.text))

    @property
    def _records(self) -> dict[str, Note]:      # type: ignore[override]
        return self._notes

    @_records.setter
    def _records(self, value: Any) -> None:
        pass          # the notes dict is the record store

    def remember(self, text: str, *, kind: str = "note",
                 metadata: Mapping[str, Any] | None = None,
                 note_id: str | None = None) -> Note:
        """Write something down and make it findable."""
        text = (text or "").strip()
        if not text:
            raise ValueError("there is nothing to remember")
        note = Note(
            id=note_id or uuid.uuid4().hex[:16], text=text, kind=kind,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._notes[note.id] = note
            self.index.add(note.id, tokenise(note.text))
            self._append(note.to_dict())
            self._trim()
        return note

    def _trim(self) -> None:
        """Drop the oldest notes once the store is over its ceiling.

        A memory that only grows eventually makes every turn slower and more
        expensive, which is a strange way to get better at something.
        """
        excess = len(self._notes) - self.max_notes
        if excess <= 0:
            return
        oldest = sorted(self._notes.values(), key=lambda note: note.created_at)[:excess]
        for note in oldest:
            self.forget(note.id)

    def forget(self, note_id: str) -> bool:
        with self._lock:
            if note_id not in self._notes:
                return False
            del self._notes[note_id]
            self.index.remove(note_id)
            self._append({"id": note_id, "deleted": True})
        return True

    def search(self, query: str, *, limit: int = 5,
               kinds: Iterable[str] | None = None,
               min_score: float = 0.0) -> list[Hit]:
        """The most relevant notes for a query, best first."""
        tokens = tokenise(query)
        if not tokens:
            return []
        allowed = set(kinds) if kinds else None
        with self._lock:
            scores = self.index.score(tokens)
            hits = [
                Hit(self._notes[note_id], score)
                for note_id, score in scores.items()
                if note_id in self._notes
                and score > min_score
                and (allowed is None or self._notes[note_id].kind in allowed)
            ]
        hits.sort(key=lambda hit: (-hit.score, -hit.note.created_at))
        return hits[:limit]

    def recall_context(self, query: str, *, limit: int = 4,
                       max_characters: int = 2_000) -> tuple[str, list[Hit]]:
        """Relevant memory rendered for a prompt, and what went into it.

        Bounded on purpose: every recalled character becomes an input token
        the agent pays for, so unbounded recall is a way to make the system
        both slower and poorer.
        """
        hits = self.search(query, limit=limit)
        if not hits:
            return "", []

        lines: list[str] = []
        used: list[Hit] = []
        budget = max_characters
        for hit in hits:
            entry = f"- {hit.note.text.strip()}"
            if len(entry) > budget:
                break
            lines.append(entry)
            used.append(hit)
            budget -= len(entry)
        if not lines:
            return "", []
        return "\n".join(lines), used

    def notes(self, kind: str | None = None) -> list[Note]:
        with self._lock:
            found = list(self._notes.values())
        if kind:
            found = [note for note in found if note.kind == kind]
        return sorted(found, key=lambda note: -note.created_at)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            kinds: dict[str, int] = {}
            for note in self._notes.values():
                kinds[note.kind] = kinds.get(note.kind, 0) + 1
            return {
                "notes": len(self._notes),
                "terms": len(self.index.postings),
                "average_length": round(self.index.average_length, 1),
                "kinds": dict(sorted(kinds.items())),
                "path": str(self.path),
            }

    def __iter__(self) -> Iterator[Note]:
        return iter(self.notes())


class FeedbackStore(_JsonlStore):
    """Verdicts on answers, kept so they can be trained on later."""

    def __init__(self, path: Path | str) -> None:
        self._entries: dict[str, Feedback] = {}
        super().__init__(path)

    def _adopt(self, payload: Mapping[str, Any]) -> None:
        try:
            entry = Feedback.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return
        self._entries[entry.id] = entry

    @property
    def _records(self) -> dict[str, Feedback]:   # type: ignore[override]
        return self._entries

    @_records.setter
    def _records(self, value: Any) -> None:
        pass

    def rate(self, conversation_id: str, turn: int, rating: str, *,
             prompt: str = "", answer: str = "", note: str = "") -> Feedback:
        if rating not in RATINGS:
            raise ValueError(f"rating must be one of {', '.join(RATINGS)}")
        entry = Feedback(
            # Keyed by conversation and turn, so rating the same answer twice
            # corrects the verdict instead of stacking two of them.
            id=f"{conversation_id}:{turn}",
            conversation_id=conversation_id, turn=turn, rating=rating,
            prompt=prompt, answer=answer, note=note,
        )
        with self._lock:
            self._entries[entry.id] = entry
            self._append(entry.to_dict())
        return entry

    def get(self, conversation_id: str, turn: int) -> Feedback | None:
        return self._entries.get(f"{conversation_id}:{turn}")

    def entries(self, rating: str | None = None) -> list[Feedback]:
        with self._lock:
            found = list(self._entries.values())
        if rating:
            found = [entry for entry in found if entry.rating == rating]
        return sorted(found, key=lambda entry: -entry.created_at)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            counts = {rating: 0 for rating in RATINGS}
            for entry in self._entries.values():
                counts[entry.rating] = counts.get(entry.rating, 0) + 1
        total = sum(counts.values())
        return {
            "total": total,
            **counts,
            "approval": round(counts.get("good", 0) / total, 3) if total else None,
            "path": str(self.path),
        }


def build_training_dataset(feedback: FeedbackStore, *,
                           rating: str = "good") -> list[dict[str, Any]]:
    """Turn collected verdicts into rows a fine-tune can consume.

    The shape is the usual chat-format one: a list of messages ending in the
    assistant turn being judged.  Nothing here trains anything -- that is a
    separate, deliberate step on hardware of your choosing, which is the
    point.  This is the part that has to exist first.
    """
    rows: list[dict[str, Any]] = []
    for entry in sorted(feedback.entries(rating), key=lambda item: item.created_at):
        if not entry.prompt or not entry.answer:
            continue
        rows.append({
            "messages": [
                {"role": "user", "content": entry.prompt},
                {"role": "assistant", "content": entry.answer},
            ],
            "rating": entry.rating,
            "conversation": entry.conversation_id,
            "turn": entry.turn,
            "digest": entry.digest,
        })
    return rows
