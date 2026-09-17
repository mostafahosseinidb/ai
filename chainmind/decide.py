"""Decisions instead of prose, with a confidence that has been measured.

A chat model answers with a string, and a string is the wrong thing to hand
to software.  It can name an option that does not exist, phrase the same
answer three ways, or sound certain about a guess -- and the program on the
other end has to parse it and hope.  Generating one is also expensive: a
question whose answer is one of four things should not cost hundreds of
sampled tokens.

This module is the other shape.  The model scores a fixed list of options
and returns the index of one, so:

*   **The output cannot be invalid.**  Not because the model is well behaved
    but because there is no code path that produces anything except an index
    into the schema's list.  The output type is the guarantee, and
    ``tests/test_decide.py`` asserts it against a deliberately deranged model.
*   **It is one forward pass.**  No sampling loop; the cost is the prompt
    and nothing else.  A small model doing this is orders of magnitude
    cheaper than a large one writing a sentence about it.
*   **The confidence is a number you can check.**  Which is the part that
    actually takes work, and the part this module refuses to fake.

On confidence, precisely
------------------------

A softmax over logits produces numbers between 0 and 1, and an untrained
instinct reads them as probabilities.  They are not.  A network trained with
cross-entropy is systematically overconfident: it will say 0.99 on a batch
where it is right 80% of the time.  Reported as a probability, that is worse
than useless -- software that trusts it will route confidently into its own
mistakes.

So a confidence here is only meaningful after two steps, both of which live
in this file and neither of which is optional:

1.  **Temperature scaling** on a validation set the model never trained on.
    One scalar, fitted by minimising the negative log-likelihood, which
    leaves every decision unchanged and only moves the confidences.
2.  **Expected calibration error**, measured on that same held-out set and
    stored in the weights file.  Every decision carries it, so a caller can
    see how much the number is worth.

A model with no calibration record reports its confidence as uncalibrated
and says so in the result.  It does not quietly pass a softmax off as a
probability.

Abstention
----------

Below a threshold the model returns no option at all.  This is the useful
half of a calibrated confidence: a system that knows which 5% of its
decisions to hand to a person is worth much more than one that is 95%
accurate and silent about which 5%.  An abstention is a recorded outcome on
the ledger, distinguishable from both a decision and a refusal.

Standard library only; the arithmetic here is small enough not to need more.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .meter import Meter
from .nano import NanoModel, WeightsError, backend_name
from .resources import ResourceKind

__all__ = [
    "Decision",
    "DecisionModel",
    "DecisionSchema",
    "DecisionUnavailable",
    "Calibration",
    "discover_deciders",
    "expected_calibration_error",
    "fit_temperature",
    "reliability_table",
    "softmax",
]

#: Below this, a decision is handed back to whoever asked instead of taken.
#: Not a tuned constant -- the right value depends on what a wrong decision
#: costs, so it lives on the schema and this is only the default.
DEFAULT_THRESHOLD = 0.75


class DecisionUnavailable(RuntimeError):
    """No decision model is available, with a reason a person can act on."""


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class DecisionSchema:
    """The fixed list of things a model is allowed to answer.

    Frozen and carried inside the weights file. A schema that could drift
    away from the head it was trained against would turn option 2 into a
    different meaning without changing a single number -- the silent kind of
    wrong that this whole design exists to rule out.
    """

    name: str
    options: tuple[str, ...]
    description: str = ""
    threshold: float = DEFAULT_THRESHOLD

    def __post_init__(self) -> None:
        if len(self.options) < 2:
            raise ValueError(f"{self.name}: a decision needs at least two options")
        if len(set(self.options)) != len(self.options):
            raise ValueError(f"{self.name}: option names must be unique")
        if any(not str(option).strip() for option in self.options):
            raise ValueError(f"{self.name}: an option cannot be blank")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"{self.name}: threshold must be between 0 and 1")

    def index(self, option: str) -> int:
        try:
            return self.options.index(option)
        except ValueError as exc:
            raise ValueError(
                f"{option!r} is not one of {self.name}'s options: "
                f"{', '.join(self.options)}"
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "options": list(self.options),
                "description": self.description, "threshold": self.threshold}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DecisionSchema":
        try:
            return cls(
                name=str(payload["name"]),
                options=tuple(str(option) for option in payload["options"]),
                description=str(payload.get("description", "")),
                threshold=float(payload.get("threshold", DEFAULT_THRESHOLD)),
            )
        except KeyError as exc:
            raise ValueError(f"decision schema is missing {exc}") from exc


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

def softmax(logits: Sequence[float], temperature: float = 1.0) -> list[float]:
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    scaled = [value / temperature for value in logits]
    top = max(scaled)
    weights = [math.exp(value - top) for value in scaled]
    total = sum(weights)
    return [weight / total for weight in weights]


def expected_calibration_error(confidences: Sequence[float],
                               correct: Sequence[bool], bins: int = 10) -> float:
    """How far the stated confidence is from the observed accuracy.

    Decisions are bucketed by confidence; in each bucket the gap between the
    average confidence and the fraction actually right is measured, and the
    buckets are averaged by weight. Zero means the number means what it says.

    This is the honest single number for "can I trust the confidence", and
    it is the one thing a model claiming calibration has to publish.
    """
    if len(confidences) != len(correct):
        raise ValueError("confidences and outcomes must line up")
    if not confidences:
        return 0.0

    total = len(confidences)
    error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        # The last bucket is closed so a confidence of exactly 1.0 lands in it.
        members = [
            (confidence, hit) for confidence, hit in zip(confidences, correct)
            if (low < confidence <= high) or (index == 0 and confidence <= low)
        ]
        if not members:
            continue
        mean_confidence = sum(confidence for confidence, _ in members) / len(members)
        accuracy = sum(1 for _, hit in members if hit) / len(members)
        error += (len(members) / total) * abs(mean_confidence - accuracy)
    return error


def reliability_table(confidences: Sequence[float], correct: Sequence[bool],
                      bins: int = 10) -> list[dict[str, Any]]:
    """The same measurement, bucket by bucket, for a person to look at.

    The aggregate can hide the shape: a model over-confident at the top and
    under-confident at the bottom averages out to a decent number and is
    still wrong in the place that matters.
    """
    rows: list[dict[str, Any]] = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [
            (confidence, hit) for confidence, hit in zip(confidences, correct)
            if (low < confidence <= high) or (index == 0 and confidence <= low)
        ]
        if not members:
            continue
        rows.append({
            "from": round(low, 3), "to": round(high, 3), "count": len(members),
            "confidence": round(sum(c for c, _ in members) / len(members), 4),
            "accuracy": round(sum(1 for _, h in members if h) / len(members), 4),
        })
    return rows


def _negative_log_likelihood(logits: Sequence[Sequence[float]],
                             labels: Sequence[int], temperature: float) -> float:
    total = 0.0
    for row, label in zip(logits, labels):
        probabilities = softmax(row, temperature)
        total -= math.log(max(probabilities[label], 1e-12))
    return total / max(1, len(labels))


def fit_temperature(logits: Sequence[Sequence[float]], labels: Sequence[int],
                    low: float = 0.05, high: float = 20.0,
                    iterations: int = 60) -> float:
    """The one scalar that turns scores into probabilities.

    Dividing every logit by a single temperature cannot change which option
    wins, so accuracy is untouched and only the confidences move. Fitted by
    golden-section search on the held-out negative log-likelihood, which
    needs no gradients and cannot overshoot -- the function is unimodal in
    this parameter.

    Fitted on data the model never trained on. Fitting it on the training set
    would find a temperature near 1 and certify the overconfidence rather
    than correct it.
    """
    if not logits:
        raise ValueError("nothing to calibrate on")
    if len(logits) != len(labels):
        raise ValueError("logits and labels must line up")

    invphi = (math.sqrt(5.0) - 1.0) / 2.0
    left, right = low, high
    probe_left = right - invphi * (right - left)
    probe_right = left + invphi * (right - left)
    value_left = _negative_log_likelihood(logits, labels, probe_left)
    value_right = _negative_log_likelihood(logits, labels, probe_right)

    for _ in range(iterations):
        if value_left < value_right:
            right, probe_right, value_right = probe_right, probe_left, value_left
            probe_left = right - invphi * (right - left)
            value_left = _negative_log_likelihood(logits, labels, probe_left)
        else:
            left, probe_left, value_left = probe_left, probe_right, value_right
            probe_right = left + invphi * (right - left)
            value_right = _negative_log_likelihood(logits, labels, probe_right)
    return (left + right) / 2.0


@dataclass(frozen=True)
class Calibration:
    """What was measured, on how much, and when.

    Stored in the weights file and reported with every decision. A
    confidence without this attached is a number from a softmax, and this
    module labels it as such rather than letting it pass for a probability.
    """

    temperature: float = 1.0
    ece: float | None = None
    accuracy: float | None = None
    rows: int = 0
    bins: int = 10

    @property
    def measured(self) -> bool:
        return self.rows > 0 and self.ece is not None

    def to_dict(self) -> dict[str, Any]:
        return {"temperature": self.temperature, "ece": self.ece,
                "accuracy": self.accuracy, "rows": self.rows, "bins": self.bins}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "Calibration":
        if not payload:
            return cls()
        return cls(
            temperature=float(payload.get("temperature", 1.0) or 1.0),
            ece=(None if payload.get("ece") is None else float(payload["ece"])),
            accuracy=(None if payload.get("accuracy") is None
                      else float(payload["accuracy"])),
            rows=int(payload.get("rows", 0) or 0),
            bins=int(payload.get("bins", 10) or 10),
        )


# --------------------------------------------------------------------------
# A decision
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """What came back: one option or none, and how sure, and how much that means."""

    schema: str
    option: str | None
    confidence: float
    distribution: dict[str, float]
    abstained: bool
    margin: float
    calibrated: bool
    ece: float | None
    model: str
    weights: str
    seconds: float = 0.0
    input_tokens: int = 0

    @property
    def taken(self) -> bool:
        return self.option is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "option": self.option,
            "confidence": round(self.confidence, 6),
            "distribution": {k: round(v, 6) for k, v in self.distribution.items()},
            "abstained": self.abstained, "margin": round(self.margin, 6),
            "calibrated": self.calibrated,
            "ece": None if self.ece is None else round(self.ece, 6),
            "model": self.model, "weights": self.weights,
            "seconds": self.seconds, "input_tokens": self.input_tokens,
        }

    def __str__(self) -> str:
        if self.abstained:
            return (f"abstained ({self.confidence:.1%} on "
                    f"{self.best()}, below threshold)")
        qualifier = "" if self.calibrated else " (uncalibrated)"
        return f"{self.option} at {self.confidence:.1%}{qualifier}"

    def best(self) -> str:
        return max(self.distribution, key=self.distribution.__getitem__)


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------

@dataclass
class DecisionModel:
    """A typed decision maker, metered and settled like any other tool."""

    path: Path | None = None
    workspace: Path | None = None
    schema: DecisionSchema | None = None
    calibration: Calibration | None = None
    threshold: float | None = None
    engine: Any = None
    instructions: str = ""

    def __post_init__(self) -> None:
        if self.engine is None:
            self.engine = self._load()
        if self.path is None:
            self.path = getattr(self.engine, "path", None)

        meta = getattr(self.engine, "meta", None) or {}
        if self.schema is None:
            payload = meta.get("decision_schema")
            if not payload:
                raise DecisionUnavailable(
                    f"{self.name} has no decision schema in it; it is a language "
                    "model. Train a decider with training/train_decider.py."
                )
            self.schema = DecisionSchema.from_dict(payload)
        if self.calibration is None:
            self.calibration = Calibration.from_dict(meta.get("calibration"))

        options = self.engine.architecture.decisions
        if options != len(self.schema.options):
            raise DecisionUnavailable(
                f"{self.name} has a head for {options} options but a schema naming "
                f"{len(self.schema.options)}; it was assembled wrongly and every "
                "answer it gave would be mislabelled."
            )

    def _load(self) -> NanoModel:
        if self.path is None:
            if self.workspace is None:
                raise DecisionUnavailable("no weights file and no workspace to look in")
            candidates = discover_deciders(self.workspace)
            if not candidates:
                raise DecisionUnavailable(
                    f"no decision model in {Path(self.workspace) / 'models'}.\n"
                    "Train one from labelled examples:\n"
                    "    python3 training/train_decider.py --dataset labelled.jsonl \\\n"
                    "        --workspace .chainmind\n"
                    "See training/README.md for the shape of that file."
                )
            self.path = candidates[0]
        try:
            return NanoModel.load(Path(self.path))
        except WeightsError as exc:
            raise DecisionUnavailable(str(exc)) from exc

    # -- identity ----------------------------------------------------------

    @property
    def name(self) -> str:
        return Path(self.path).stem if self.path else "decider"

    @property
    def model(self) -> str:
        return self.name

    @property
    def fingerprint(self) -> str:
        return str(getattr(self.engine, "fingerprint", ""))

    @property
    def cutoff(self) -> float:
        if self.threshold is not None:
            return self.threshold
        return self.schema.threshold if self.schema else DEFAULT_THRESHOLD

    def describe(self) -> str:
        assert self.schema is not None and self.calibration is not None
        arch = self.engine.architecture
        quality = (
            f"ECE {self.calibration.ece:.3f} over {self.calibration.rows} rows"
            if self.calibration.measured else "uncalibrated"
        )
        return (
            f"{self.name} (decision: {self.schema.name}, "
            f"{len(self.schema.options)} options, "
            f"{arch.parameters / 1e6:.1f}M parameters, {quality}, {backend_name()})"
        )

    # -- counting ----------------------------------------------------------

    def _ids(self, text: str) -> list[int]:
        prompt = f"{self.instructions}\n{text}" if self.instructions else text
        ids = self.engine.tokenizer.encode(prompt)
        if not ids:
            # An empty input still costs one token to look at, and a model
            # asked to decide about nothing should abstain rather than raise.
            ids = [self.engine.tokenizer.special("<|end|>")]
        return ids[-self.engine.architecture.context:]

    def count_input_tokens(self, text: str) -> int:
        return len(self._ids(text))

    def estimate(self, text: str) -> dict[str, int]:
        """One forward pass over the prompt, and nothing generated.

        The reason this is cheap enough to put in a loop: the cost does not
        depend on how long the answer is, because there is no answer to
        write.
        """
        return {
            ResourceKind.LLM_INPUT_TOKENS.value: self.count_input_tokens(text),
            ResourceKind.TOOL_CALL.value: 1,
        }

    # -- the call ----------------------------------------------------------

    def decide(self, meter: Meter, text: str,
               threshold: float | None = None) -> Decision:
        assert self.schema is not None and self.calibration is not None
        ids = self._ids(text)
        cutoff = self.cutoff if threshold is None else float(threshold)

        started = time.monotonic()
        try:
            logits = self.engine.decision_logits(ids)
        except WeightsError as exc:
            raise DecisionUnavailable(str(exc)) from exc
        except Exception as exc:
            raise DecisionUnavailable(f"{type(exc).__name__}: {exc}") from exc

        probabilities = softmax(logits, self.calibration.temperature)
        ranked = sorted(range(len(probabilities)),
                        key=probabilities.__getitem__, reverse=True)
        winner, runner_up = ranked[0], ranked[1]
        confidence = probabilities[winner]
        abstained = confidence < cutoff

        decision = Decision(
            schema=self.schema.name,
            option=None if abstained else self.schema.options[winner],
            confidence=confidence,
            distribution={option: probability for option, probability
                          in zip(self.schema.options, probabilities)},
            abstained=abstained,
            margin=confidence - probabilities[runner_up],
            calibrated=self.calibration.measured,
            ece=self.calibration.ece,
            model=self.name,
            weights=self.fingerprint,
            seconds=round(time.monotonic() - started, 4),
            input_tokens=len(ids),
        )

        meter.record(ResourceKind.LLM_INPUT_TOKENS, len(ids), source="provider")
        meter.record(ResourceKind.TOOL_CALL, 1, source="provider")
        meter.note("model", self.name)
        meter.note("runtime", "decision")
        meter.note("weights", self.fingerprint)
        meter.note("schema", self.schema.name)
        # The decision and how sure it was are part of the evidence: an
        # auditor reading the chain later should be able to see not just that
        # the agent acted but what it concluded and how close the call was.
        meter.note("decision", decision.option or "(abstained)")
        meter.note("confidence", round(confidence, 4))
        meter.note("calibrated", decision.calibrated)
        return decision

    # -- the tool surface --------------------------------------------------

    def respond(self, meter: Meter, prompt: str, max_tokens: int | None = None,
                system: str | None = None,
                history: Sequence[Mapping[str, str]] | None = None) -> dict[str, Any]:
        """The shape the rest of the program expects, so this drops in anywhere.

        ``text`` is the whole of the input: history and a system prompt are
        accepted and ignored, because a decision is a function of the thing
        being decided about and pretending otherwise would make the cost
        depend on conversation length for no benefit.
        """
        decision = self.decide(meter, prompt)
        return {
            "text": str(decision),
            "decision": decision.to_dict(),
            "refused": decision.abstained,
            "truncated": False,
            "input_tokens": decision.input_tokens,
            "output_tokens": 0,
            "stop_reason": "abstained" if decision.abstained else "decided",
            "model": self.name,
            "weights": self.fingerprint,
            "runtime": "decision",
            "seconds": decision.seconds,
        }


def discover_deciders(root: Path | str) -> list[Path]:
    """Decision models in a workspace, largest first."""
    from .native import discover_weights, is_decider           # noqa: PLC0415

    return [path for path in discover_weights(root) if is_decider(path)]
