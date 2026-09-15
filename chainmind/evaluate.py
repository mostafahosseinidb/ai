"""Measuring whether a change to the model actually helped.

Collecting feedback is the easy half.  The half that decides whether any of
it was worth doing is this one: run the model against answers a person
already approved, score the overlap, and compare that number before and
after a change.  Without it, "we fine-tuned it" is a feeling.

Two things keep this honest:

*   **The split is deterministic.**  Validation rows are chosen by hashing
    each row, not by shuffling, so the held-out set does not drift between
    runs and yesterday's score is comparable to today's.
*   **Evaluation is metered like everything else.**  It runs through the
    agent kernel, so it costs credits, appears on the chain, and can be
    refused when the budget will not cover it.  A measurement that quietly
    exceeds its budget is the same problem as an action that does.

What the score is **not** is a judgement of quality.  It measures overlap
with an answer someone approved, which is a proxy -- a differently worded
but better answer scores lower.  It is useful for detecting regression and
for comparing two versions of the same model on the same rows, and it is
misleading for anything else.  An LLM judge would be less crude and also
circular: the thing being measured would be grading itself.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .agent import AgentKernel
from .crypto import sha256_hex
from .memory import tokenise
from .resources import format_credits

__all__ = [
    "Evaluation",
    "EvalReport",
    "RowResult",
    "split_dataset",
    "token_f1",
    "rouge_l",
    "load_dataset",
]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def token_f1(candidate: str, reference: str) -> float:
    """Overlap of words, regardless of order.

    The SQuAD metric, on tokens normalised the same way the memory index
    normalises them -- so Persian and Arabic spellings of the same word do
    not count as a miss.
    """
    candidate_tokens = tokenise(candidate)
    reference_tokens = tokenise(reference)
    if not candidate_tokens or not reference_tokens:
        return 1.0 if candidate_tokens == reference_tokens else 0.0

    shared = Counter(candidate_tokens) & Counter(reference_tokens)
    overlap = sum(shared.values())
    if not overlap:
        return 0.0
    precision = overlap / len(candidate_tokens)
    recall = overlap / len(reference_tokens)
    return 2 * precision * recall / (precision + recall)


def rouge_l(candidate: str, reference: str) -> float:
    """Overlap that respects order, via the longest common subsequence.

    Word-bag F1 cannot tell "the budget limits the rate" from "the rate
    limits the budget"; this can.  Reported alongside rather than instead,
    because each is blind to something the other sees.
    """
    candidate_tokens = tokenise(candidate)
    reference_tokens = tokenise(reference)
    if not candidate_tokens or not reference_tokens:
        return 1.0 if candidate_tokens == reference_tokens else 0.0

    # Two rows of the LCS table is all that is needed for the length.
    previous = [0] * (len(reference_tokens) + 1)
    for candidate_token in candidate_tokens:
        current = [0]
        for index, reference_token in enumerate(reference_tokens):
            if candidate_token == reference_token:
                current.append(previous[index] + 1)
            else:
                current.append(max(current[index], previous[index + 1]))
        previous = current

    length = previous[-1]
    if not length:
        return 0.0
    precision = length / len(candidate_tokens)
    recall = length / len(reference_tokens)
    return 2 * precision * recall / (precision + recall)


# --------------------------------------------------------------------------
# Dataset handling
# --------------------------------------------------------------------------

def load_dataset(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL dataset written by ``chainmind learning --export``."""
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("messages"):
            rows.append(row)
    return rows


def _row_key(row: Mapping[str, Any]) -> str:
    """A stable identity for a row, independent of its position in the file."""
    digest = row.get("digest")
    if isinstance(digest, str) and digest:
        return digest
    return sha256_hex(json.dumps(row.get("messages"), sort_keys=True, ensure_ascii=False))


def split_dataset(rows: Sequence[Mapping[str, Any]], *, validation_fraction: float = 0.2,
                  salt: str = "chainmind-eval") -> tuple[list[dict], list[dict]]:
    """Split into (train, validation) the same way every time.

    Hashing rather than shuffling means a row stays on the side it started
    on even as the dataset grows, so a score from last week still measures
    the same thing.  A random split would quietly move rows across the line
    and make every comparison meaningless.
    """
    if not 0.0 < validation_fraction < 1.0:
        raise ValueError("the validation fraction must be between 0 and 1")

    threshold = int(validation_fraction * (1 << 32))
    train: list[dict] = []
    validation: list[dict] = []
    for row in rows:
        bucket = int(sha256_hex(salt + _row_key(row))[:8], 16)
        (validation if bucket < threshold else train).append(dict(row))
    return train, validation


def _messages_to_pair(row: Mapping[str, Any]) -> tuple[str, str]:
    prompt = ""
    reference = ""
    for message in row.get("messages", []):
        if message.get("role") == "user":
            prompt = str(message.get("content", ""))
        elif message.get("role") == "assistant":
            reference = str(message.get("content", ""))
    return prompt, reference


# --------------------------------------------------------------------------
# Running an evaluation
# --------------------------------------------------------------------------

@dataclass
class RowResult:
    key: str
    prompt: str
    reference: str
    answer: str = ""
    f1: float = 0.0
    rouge: float = 0.0
    cost: int = 0
    status: str = "scored"      # scored | refused | failed
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "prompt": self.prompt[:400], "reference": self.reference[:400],
            "answer": self.answer[:400], "f1": round(self.f1, 4),
            "rouge": round(self.rouge, 4), "cost": self.cost,
            "status": self.status, "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RowResult":
        return cls(
            key=str(data.get("key", "")), prompt=str(data.get("prompt", "")),
            reference=str(data.get("reference", "")), answer=str(data.get("answer", "")),
            f1=float(data.get("f1", 0.0)), rouge=float(data.get("rouge", 0.0)),
            cost=int(data.get("cost", 0)), status=str(data.get("status", "scored")),
            reason=str(data.get("reason", "")),
        )


@dataclass
class EvalReport:
    """One run, and enough of it to compare against another."""

    model: str = ""
    label: str = ""
    at: int = field(default_factory=lambda: int(time.time()))
    results: list[RowResult] = field(default_factory=list)

    # -- aggregates --------------------------------------------------------

    @property
    def scored(self) -> list[RowResult]:
        return [result for result in self.results if result.status == "scored"]

    @property
    def refused(self) -> int:
        return sum(1 for result in self.results if result.status == "refused")

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status == "failed")

    @property
    def mean_f1(self) -> float:
        scored = self.scored
        return statistics.fmean(result.f1 for result in scored) if scored else 0.0

    @property
    def mean_rouge(self) -> float:
        scored = self.scored
        return statistics.fmean(result.rouge for result in scored) if scored else 0.0

    @property
    def spent(self) -> int:
        return sum(result.cost for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model, "label": self.label, "at": self.at,
            "rows": len(self.results), "scored": len(self.scored),
            "refused": self.refused, "failed": self.failed,
            "mean_f1": round(self.mean_f1, 4), "mean_rouge": round(self.mean_rouge, 4),
            "spent": self.spent, "spent_text": format_credits(self.spent),
            "results": [result.to_dict() for result in self.results],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalReport":
        return cls(
            model=str(data.get("model", "")), label=str(data.get("label", "")),
            at=int(data.get("at", 0)),
            results=[RowResult.from_dict(row) for row in data.get("results", [])],
        )

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | str) -> "EvalReport":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- comparison --------------------------------------------------------

    def compare(self, baseline: "EvalReport") -> dict[str, Any]:
        """This run against an earlier one, on the rows they share.

        Only shared rows are compared.  Averaging over different row sets and
        calling the difference an improvement is the most common way an
        evaluation lies to the person running it.
        """
        mine = {result.key: result for result in self.scored}
        theirs = {result.key: result for result in baseline.scored}
        shared = sorted(set(mine) & set(theirs))

        if not shared:
            return {
                "comparable": False,
                "reason": "the two runs share no scored rows, so nothing can be compared",
                "shared": 0, "only_here": len(set(mine) - set(theirs)),
                "only_there": len(set(theirs) - set(mine)),
            }

        f1_delta = statistics.fmean(mine[key].f1 - theirs[key].f1 for key in shared)
        rouge_delta = statistics.fmean(mine[key].rouge - theirs[key].rouge for key in shared)
        better = [key for key in shared if mine[key].f1 > theirs[key].f1 + 1e-9]
        worse = [key for key in shared if mine[key].f1 < theirs[key].f1 - 1e-9]

        return {
            "comparable": True,
            "shared": len(shared),
            "baseline_f1": round(statistics.fmean(theirs[key].f1 for key in shared), 4),
            "current_f1": round(statistics.fmean(mine[key].f1 for key in shared), 4),
            "f1_delta": round(f1_delta, 4),
            "rouge_delta": round(rouge_delta, 4),
            "better": len(better),
            "worse": len(worse),
            "unchanged": len(shared) - len(better) - len(worse),
            "verdict": _verdict(f1_delta, len(better), len(worse), len(shared)),
        }


def _verdict(f1_delta: float, better: int, worse: int, shared: int) -> str:
    """A sentence a person can act on, with the sample size front and centre."""
    if shared < 10:
        return (
            f"{shared} shared rows is too few to conclude anything; "
            "collect more feedback before trusting a difference"
        )
    if abs(f1_delta) < 0.01:
        return "no meaningful difference"
    direction = "better" if f1_delta > 0 else "worse"
    return (
        f"{direction} by {abs(f1_delta):.3f} mean F1 "
        f"({better} rows improved, {worse} regressed of {shared})"
    )


class Evaluation:
    """Runs a dataset past the model and scores what comes back."""

    def __init__(self, kernel: AgentKernel, rows: Iterable[Mapping[str, Any]], *,
                 max_tokens: int = 512, tool: str = "ask") -> None:
        self.kernel = kernel
        self.rows = [dict(row) for row in rows]
        self.max_tokens = max_tokens
        self.tool = tool

    def run(self, *, model: str = "", label: str = "",
            limit: int | None = None,
            on_row: Any = None) -> EvalReport:
        """Score every row, stopping early if the agent runs out of money."""
        report = EvalReport(model=model, label=label)
        for row in self.rows[:limit]:
            prompt, reference = _messages_to_pair(row)
            result = RowResult(key=_row_key(row), prompt=prompt, reference=reference)

            if not prompt or not reference:
                result.status = "failed"
                result.reason = "the row has no prompt or no reference answer"
            elif self.kernel.halted:
                result.status = "refused"
                result.reason = self.kernel.halt_reason() or "the agent is halted"
            else:
                before = self.kernel.balance
                # No memory is injected here on purpose: the point is to
                # measure the model, and a recall that differs between runs
                # would move the thing being measured.
                outcome = self.kernel.perform(
                    self.tool, prompt=prompt, max_tokens=self.max_tokens
                )
                result.cost = before - self.kernel.balance
                if outcome.refused:
                    result.status = "refused"
                    result.reason = outcome.reason
                elif not outcome.ok:
                    result.status = "failed"
                    result.reason = outcome.error
                else:
                    answer = outcome.value.get("text", "") if isinstance(outcome.value, dict) else ""
                    result.answer = answer
                    result.f1 = token_f1(answer, reference)
                    result.rouge = rouge_l(answer, reference)

            report.results.append(result)
            if on_row is not None:
                on_row(result)
        return report
