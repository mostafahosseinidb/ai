#!/usr/bin/env python3
"""Train a decision model: typed outputs with a confidence that was measured.

Where ``pretrain.py`` teaches a model to continue text, this teaches it to
choose. The output is an index into a fixed list of options, so the model
cannot answer with something that is not on the list -- not because it
behaves, but because there is nothing else for it to return.

Why this is the easy direction
------------------------------

``pretrain.py`` is honest about its limit: a model one person trains from
scratch is far worse at open-ended conversation than any open model they
could have run instead. That limit comes from open-ended-ness. It mostly
goes away here.

A decision between four options over a domain you have labelled data for is
a small problem. A few thousand examples and a few million parameters is a
reasonable model, not a toy, and it will be faster and cheaper than asking a
large language model the same question by orders of magnitude -- one forward
pass over the prompt, no sampling loop, no tokens generated. This is the
shape where "trained from scratch, on my own machine, out of my own data"
and "actually good at the job" stop being in tension.

The dataset
-----------

One JSON object per line::

    {"text": "the customer's message", "label": "refund"}
    {"text": "...", "label": "escalate"}

``--options`` fixes the list and its order; without it the labels present in
the data are used, sorted. Fixing it matters once you retrain: an option
list that reorders turns option 2 into a different meaning without changing
a number, which is the silent kind of wrong.

Three splits, not two
---------------------

Rows are split deterministically into train, calibration and test:

*   **train** fits the weights;
*   **calibration** fits the one temperature that turns scores into
    probabilities;
*   **test** measures accuracy and expected calibration error.

Fitting the temperature and then reporting the calibration error on the same
rows would certify the fit against itself. The number in the weights file is
from rows that were used for neither.

Usage
-----

    pip install numpy

    # optional but much better: start from a model that already read your text
    python3 training/pretrain.py --corpus corpus --workspace .chainmind

    python3 training/train_decider.py --dataset labelled.jsonl \\
        --from .chainmind/models/atlas.cmw --workspace .chainmind

    chainmind decide "the message to classify"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chainmind.decide import (                                   # noqa: E402
    Calibration,
    DecisionSchema,
    expected_calibration_error,
    fit_temperature,
    reliability_table,
    softmax,
)
from chainmind.nano import Architecture, load_weights, save_weights  # noqa: E402
from chainmind.tokenizer import Tokenizer, train_tokenizer         # noqa: E402

try:
    import numpy as np
except ImportError:                                              # pragma: no cover
    raise SystemExit(
        "training needs NumPy:\n    pip install -r training/requirements.txt"
    )

from pretrain import (                                            # noqa: E402
    AdamW,
    backward_stack,
    forward_stack,
    initialise,
    schedule,
)


# --------------------------------------------------------------------------
# The dataset
# --------------------------------------------------------------------------

def read_dataset(path: Path) -> list[dict[str, str]]:
    """Labelled rows, with the malformed ones named rather than skipped.

    A row silently dropped is a row you keep believing you trained on.
    """
    rows: list[dict[str, str]] = []
    problems = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            text, label = str(record["text"]), str(record["label"])
        except (json.JSONDecodeError, KeyError, TypeError):
            problems += 1
            continue
        if text.strip() and label.strip():
            rows.append({"text": text, "label": label})
        else:
            problems += 1
    if problems:
        print(f"  skipped {problems} rows without both a text and a label")
    if not rows:
        raise SystemExit(
            f"no usable rows in {path}. Each line needs "
            '{"text": "...", "label": "..."}'
        )
    return rows


def split_rows(rows: Sequence[dict[str, str]], calibration: float = 0.15,
               test: float = 0.15) -> tuple[list, list, list]:
    """A stable three-way split, keyed on the text rather than on position.

    Hashing the row means adding data later does not reshuffle what was
    already held out, so two runs a month apart stay comparable.
    """
    if not 0 < calibration + test < 1:
        raise SystemExit("the calibration and test shares must leave room to train")

    train_rows, calibration_rows, test_rows = [], [], []
    for row in rows:
        digest = hashlib.sha256(row["text"].encode("utf-8")).digest()
        position = int.from_bytes(digest[:8], "big") / float(1 << 64)
        if position < test:
            test_rows.append(row)
        elif position < test + calibration:
            calibration_rows.append(row)
        else:
            train_rows.append(row)
    return train_rows, calibration_rows, test_rows


def encode_rows(rows: Sequence[dict[str, str]], tokenizer: Tokenizer,
                schema: DecisionSchema, context: int, instructions: str = ""
                ) -> tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
    """Right-padded ids, the real length of each row, and the labels.

    Right-padded rather than left-padded because the decision is read off the
    hidden state at the *last real token*, and under a causal mask that
    position attends only to positions before it -- all of them real. Padding
    on the left would put pad tokens inside the window every real token
    attends to.
    """
    pad = tokenizer.special("<|pad|>")
    encoded, lengths, labels = [], [], []
    for row in rows:
        text = f"{instructions}\n{row['text']}" if instructions else row["text"]
        ids = tokenizer.encode(text)[-context:] or [tokenizer.special("<|end|>")]
        lengths.append(len(ids))
        encoded.append(ids + [pad] * (context - len(ids)))
        labels.append(schema.index(row["label"]))
    return (np.asarray(encoded, dtype=np.int64),
            np.asarray(lengths, dtype=np.int64),
            np.asarray(labels, dtype=np.int64))


# --------------------------------------------------------------------------
# Forward and backward, for the decision head
# --------------------------------------------------------------------------

def decision_forward_backward(params: dict[str, "np.ndarray"],
                              architecture: Architecture, inputs: "np.ndarray",
                              lengths: "np.ndarray", labels: "np.ndarray",
                              compute_gradients: bool = True
                              ) -> tuple[float, dict[str, "np.ndarray"], "np.ndarray"]:
    """Cross-entropy over the options, and every gradient.

    The stack is the one in ``pretrain.py``; only the head differs. Returns
    the logits as well, because calibration needs them and running the model
    twice to get them would be wasteful and, worse, a second place for the
    two to disagree.
    """
    final, state = forward_stack(params, architecture, inputs)

    batch = inputs.shape[0]
    rows = np.arange(batch)
    pooled = final[rows, lengths - 1]                      # (B, d)
    head = params["decision_head"]
    logits = pooled @ head.T                               # (B, options)

    shifted = logits - logits.max(axis=-1, keepdims=True)
    exponent = np.exp(shifted)
    probabilities = exponent / exponent.sum(axis=-1, keepdims=True)
    picked = probabilities[rows, labels]
    loss = float(-np.log(np.maximum(picked, 1e-12)).mean())

    if not compute_gradients:
        return loss, {}, logits

    grads = {name: np.zeros_like(value) for name, value in params.items()}

    d_logits = probabilities.copy()
    d_logits[rows, labels] -= 1.0
    d_logits /= batch

    grads["decision_head"] += d_logits.T @ pooled
    d_pooled = d_logits @ head

    # Only the position the decision was read from carries a gradient; the
    # padding after it never reached the head and must not be pushed on.
    d_final = np.zeros_like(final)
    d_final[rows, lengths - 1] = d_pooled

    backward_stack(params, architecture, state, d_final, grads)
    return loss, grads, logits


def evaluate(params: dict[str, "np.ndarray"], architecture: Architecture,
             inputs: "np.ndarray", lengths: "np.ndarray", labels: "np.ndarray",
             batch: int = 32) -> tuple[float, "np.ndarray"]:
    """Mean loss and the raw logits, in batches so memory stays flat."""
    losses, collected = [], []
    for start in range(0, len(inputs), batch):
        stop = start + batch
        loss, _grads, logits = decision_forward_backward(
            params, architecture, inputs[start:stop], lengths[start:stop],
            labels[start:stop], compute_gradients=False,
        )
        losses.append(loss * (stop - start if stop <= len(inputs)
                              else len(inputs) - start))
        collected.append(logits)
    stacked = np.concatenate(collected) if collected else np.zeros((0, 1))
    return (sum(losses) / max(1, len(inputs))), stacked


# --------------------------------------------------------------------------
# Warm start
# --------------------------------------------------------------------------

def warm_start(path: Path, architecture: Architecture,
               params: dict[str, "np.ndarray"]) -> tuple[dict[str, "np.ndarray"], int]:
    """Copy what a pretrained model already knows into a fresh decider.

    A model that has read your text starts far ahead of random numbers on a
    decision over the same text -- usually the difference between needing
    tens of thousands of labelled rows and needing hundreds. Only tensors
    whose shapes agree are copied, and the decision head is always fresh
    because it did not exist before.
    """
    source_architecture, _tokenizer, tensors, _meta = load_weights(path)
    if source_architecture.vocab_size != architecture.vocab_size:
        raise SystemExit(
            f"{path.name} has a {source_architecture.vocab_size}-token vocabulary "
            f"and this run built one of {architecture.vocab_size}. Pass "
            "--reuse-vocab so the decider keeps the pretrained model's."
        )

    shapes = dict(architecture.tensor_shapes())
    copied = 0
    for name, shape in source_architecture.tensor_shapes():
        if name not in shapes or shapes[name] != shape or name == "decision_head":
            continue
        params[name] = np.asarray(tensors[name], dtype=np.float32).reshape(shape)
        copied += 1
    return params, copied


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

def train(train_set, calibration_set, test_set, architecture: Architecture, *,
          steps: int, batch: int, learning_rate: float, warmup: int, seed: int,
          params: dict[str, "np.ndarray"] | None = None,
          report: Any = print) -> tuple[dict[str, "np.ndarray"], dict[str, Any]]:
    inputs, lengths, labels = train_set
    rng = np.random.default_rng(seed)

    params = initialise(architecture, seed=seed) if params is None else params
    optimiser = AdamW(params, learning_rate=learning_rate)
    history: list[dict[str, float]] = []
    started = time.monotonic()

    # A decision model on a few hundred rows reaches its best held-out loss
    # long before the training loss stops falling, and every step after that
    # makes it more confident about the training set and worse on anything
    # else. Keeping the best checkpoint costs one copy of the weights and
    # removes the need to guess --steps correctly.
    best: dict[str, Any] = {"loss": float("inf"), "step": -1, "params": None}

    for step in range(steps):
        picked = rng.integers(0, len(inputs), size=min(batch, len(inputs)))
        rate = schedule(step, steps, learning_rate, warmup)
        loss, grads, _logits = decision_forward_backward(
            params, architecture, inputs[picked], lengths[picked], labels[picked]
        )
        norm = optimiser.step(params, grads, learning_rate=rate)

        if step % max(1, steps // 20) == 0 or step == steps - 1:
            entry = {"step": step, "loss": round(loss, 4), "lr": round(rate, 6),
                     "grad_norm": round(norm, 3)}
            if len(calibration_set[0]):
                held_loss, held_logits = evaluate(params, architecture, *calibration_set)
                accuracy = float((held_logits.argmax(axis=1)
                                  == calibration_set[2]).mean())
                entry["holdout"] = round(held_loss, 4)
                entry["holdout_accuracy"] = round(accuracy, 4)
                if held_loss < best["loss"]:
                    best = {"loss": held_loss, "step": step,
                            "params": {name: value.copy()
                                       for name, value in params.items()}}
            history.append(entry)
            report(
                f"step {step:>6}/{steps}  loss {entry['loss']:.4f}"
                + (f"  holdout {entry['holdout']:.4f}"
                   f"  acc {entry['holdout_accuracy']:.1%}"
                   if "holdout" in entry else "")
                + f"  lr {rate:.2e}"
            )

    kept = steps - 1
    if best["params"] is not None and best["step"] < steps - 1:
        report(f"\nkeeping step {best['step']} (held-out loss {best['loss']:.4f}); "
               f"training past it was overfitting")
        params, kept = best["params"], best["step"]

    return params, {"steps": steps, "batch": batch, "learning_rate": learning_rate,
                    "seed": seed, "kept_step": kept,
                    "seconds": round(time.monotonic() - started, 1),
                    "history": history}


def unfamiliar_check(params, architecture, rows, tokenizer, schema, context,
                     instructions, temperature, threshold, report=print
                     ) -> dict[str, Any]:
    """How often the model abstains on input unlike anything it was trained on.

    The number that matters most before deploying one of these, and the one
    calibration cannot give you. Temperature scaling is fitted on held-out
    rows that look like the training rows; it says nothing about an input
    from outside the distribution entirely. A router trained on three kinds
    of support message will answer a question about the weather, confidently,
    with one of its three options -- because those are the only things it can
    say.

    "Zero hallucinations" is true here in the sense that matters -- the
    output is always a valid option -- and that is not the same as being
    right, or as knowing when it is out of its depth. Feeding it deliberately
    unrelated text and counting how often it abstains is the honest measure.
    """
    if not rows:
        return {}

    inputs, lengths, labels = encode_rows(
        [{"text": row, "label": schema.options[0]} for row in rows],
        tokenizer, schema, context, instructions,
    )
    _loss, logits = evaluate(params, architecture, inputs, lengths, labels)
    confidences = [max(softmax(row.tolist(), temperature)) for row in logits]
    abstained = sum(1 for confidence in confidences if confidence < threshold)
    rate = abstained / len(rows)

    report(f"\nunfamiliar input ({len(rows)} rows of deliberately unrelated text):")
    report(f"  abstained               {rate:.1%}  ({abstained}/{len(rows)})")
    report(f"  mean confidence         {sum(confidences) / len(confidences):.1%}")
    if rate < 0.5:
        report("  note: this model answers confidently about things it knows "
               "nothing about.\n  Add a catch-all option to --options and label "
               "these rows with it; a decision\n  model can only abstain on what "
               "it was taught to be unsure about.")
    return {"rows": len(rows), "abstained": rate,
            "mean_confidence": sum(confidences) / len(confidences)}


def _choose_temperature(logits, labels, fitted: float, bins: int,
                        report: Any = print) -> float:
    """Scaled or unscaled, whichever is better calibrated on held-out rows."""
    predictions = logits.argmax(axis=1)
    correct = [bool(prediction == label)
               for prediction, label in zip(predictions, labels)]

    def error(temperature: float) -> float:
        confidences = [softmax(row.tolist(), temperature)[int(prediction)]
                       for row, prediction in zip(logits, predictions)]
        return expected_calibration_error(confidences, correct, bins)

    plain, scaled = error(1.0), error(fitted)
    if scaled <= plain:
        return fitted
    report(f"  temperature {fitted:.3f} would make calibration worse "
           f"({plain:.4f} -> {scaled:.4f} on the fitting split), so the scores "
           "are left alone")
    return 1.0


def calibrate(params, architecture, calibration_set, test_set,
              bins: int = 10, report: Any = print) -> Calibration:
    """Fit the temperature on one held-out split; measure on another."""
    if not len(calibration_set[0]) or not len(test_set[0]):
        report("  not enough held-out rows to calibrate; confidences will be "
               "reported as uncalibrated")
        return Calibration()

    _loss, calibration_logits = evaluate(params, architecture, *calibration_set)
    fitted = fit_temperature(calibration_logits.tolist(),
                             calibration_set[2].tolist())

    # Fitting minimises log-likelihood, which is not the same as minimising
    # calibration error, and on a few hundred rows the two can disagree
    # enough that scaling makes things worse. So the choice between "scaled"
    # and "left alone" is made by measuring both -- on the calibration split,
    # not the test one, so the number reported below stays untouched by any
    # decision that went into producing it.
    temperature = _choose_temperature(calibration_logits, calibration_set[2],
                                      fitted, bins, report)

    _loss, test_logits = evaluate(params, architecture, *test_set)
    labels = test_set[2]
    predictions = test_logits.argmax(axis=1)
    confidences, correct = [], []
    for row, prediction, label in zip(test_logits, predictions, labels):
        probabilities = softmax(row.tolist(), temperature)
        confidences.append(probabilities[int(prediction)])
        correct.append(bool(prediction == label))

    accuracy = sum(correct) / len(correct)
    raw = [softmax(row.tolist(), 1.0)[int(prediction)]
           for row, prediction in zip(test_logits, predictions)]
    before = expected_calibration_error(raw, correct, bins)
    after = expected_calibration_error(confidences, correct, bins)

    report(f"\ncalibration (temperature {temperature:.3f}, chosen on "
           f"{len(calibration_set[0])} rows, measured on {len(test_set[0])}):")
    report(f"  accuracy                {accuracy:.1%}")
    report(f"  calibration error       {before:.4f} -> {after:.4f}")
    for row in reliability_table(confidences, correct, bins):
        report(f"    {row['from']:.1f}-{row['to']:.1f}  n={row['count']:<5} "
               f"said {row['confidence']:.1%}  was {row['accuracy']:.1%}")
    if after > 0.1:
        report("  note: a calibration error above about 0.1 means the confidence "
               "should not be\n  trusted as a probability yet. More data is the "
               "usual answer.")
    if accuracy > 0.99 and len(test_set[0]) < 500:
        report("  note: perfect accuracy on a small test set usually means the task "
               "is easier than\n  the real one, or that near-duplicate rows landed "
               "on both sides of the split.\n  A calibration error of zero measured "
               "this way is not evidence of much.")

    return Calibration(temperature=temperature, ece=after, accuracy=accuracy,
                       rows=len(test_set[0]), bins=bins)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train a typed decision model with calibrated confidence.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__,
    )
    parser.add_argument("--dataset", type=Path, required=True,
                        help='JSONL of {"text": ..., "label": ...}')
    parser.add_argument("--workspace", type=Path, default=Path(".chainmind"))
    parser.add_argument("--name", default=None,
                        help="name of the weights file (default: the schema name)")
    parser.add_argument("--schema", default="decision", help="what this decides")
    parser.add_argument("--options", nargs="+", default=None,
                        help="fix the option list and its order")
    parser.add_argument("--describe", default="", help="what the schema means")
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="below this confidence the model abstains")
    parser.add_argument("--instructions", default="",
                        help="text prefixed to every input, at train and decide time")
    parser.add_argument("--unfamiliar", type=Path, default=None,
                        help="a file of unrelated text, one per line, to check how "
                             "often the model abstains on input outside its domain")
    parser.add_argument("--from", dest="warm", type=Path, default=None,
                        help="a pretrained .cmw to start the encoder from")
    parser.add_argument("--reuse-vocab", action="store_true",
                        help="keep the vocabulary of the --from model")
    parser.add_argument("--vocab", type=int, default=2048)
    parser.add_argument("--dim", type=int, default=128)
    parser.add_argument("--layers", type=int, default=4)
    parser.add_argument("--heads", type=int, default=4)
    parser.add_argument("--hidden", type=int, default=None)
    parser.add_argument("--context", type=int, default=256)
    parser.add_argument("--steps", type=int, default=1500)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    rows = read_dataset(args.dataset)
    options = tuple(args.options) if args.options else tuple(
        sorted({row["label"] for row in rows})
    )
    schema = DecisionSchema(name=args.schema, options=options,
                            description=args.describe, threshold=args.threshold)

    unknown = {row["label"] for row in rows} - set(options)
    if unknown:
        raise SystemExit(
            f"these labels are in the data but not in --options: "
            f"{', '.join(sorted(unknown))}"
        )

    counts = {option: sum(1 for row in rows if row["label"] == option)
              for option in options}
    print(f"dataset: {len(rows)} rows over {len(options)} options")
    for option, count in counts.items():
        print(f"  {option:<24} {count:>6}  ({count / len(rows):.1%})")
    if min(counts.values()) == 0:
        raise SystemExit("an option with no examples cannot be learned; drop it "
                         "from --options or add rows")
    if min(counts.values()) < 10:
        print("  note: an option with fewer than about ten examples is guessed at, "
              "not learned.")

    train_rows, calibration_rows, test_rows = split_rows(rows)
    print(f"split: {len(train_rows)} train, {len(calibration_rows)} calibration, "
          f"{len(test_rows)} test")

    if args.warm and args.reuse_vocab:
        _architecture, tokenizer, _tensors, _meta = load_weights(args.warm)
        print(f"vocabulary: reusing {tokenizer.vocab_size} tokens from "
              f"{args.warm.name}")
    else:
        tokenizer = train_tokenizer([row["text"] for row in rows],
                                    vocab_size=args.vocab)
        print(f"vocabulary: trained {tokenizer.vocab_size} tokens from this dataset")

    hidden = args.hidden or (int(args.dim * 8 / 3) // 32 + 1) * 32
    architecture = Architecture(
        vocab_size=tokenizer.vocab_size, dim=args.dim, layers=args.layers,
        heads=args.heads, hidden=hidden, context=args.context,
        decisions=len(options),
    )
    print(f"model: {architecture.parameters / 1e6:.2f}M parameters")

    params = initialise(architecture, seed=args.seed)
    if args.warm:
        params, copied = warm_start(args.warm, architecture, params)
        print(f"warm start: copied {copied} tensors from {args.warm.name}; "
              "the decision head is new")

    sets = [
        encode_rows(subset, tokenizer, schema, args.context, args.instructions)
        for subset in (train_rows, calibration_rows, test_rows)
    ]
    params, record = train(*sets, architecture, steps=args.steps, batch=args.batch,
                           learning_rate=args.lr, warmup=args.warmup,
                           seed=args.seed, params=params)
    calibration = calibrate(params, architecture, sets[1], sets[2])

    unfamiliar: dict[str, Any] = {}
    if args.unfamiliar:
        lines = [line.strip() for line
                 in args.unfamiliar.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        unfamiliar = unfamiliar_check(
            params, architecture, lines, tokenizer, schema, args.context,
            args.instructions, calibration.temperature, schema.threshold,
        )
    else:
        print("\nno --unfamiliar file given, so nothing checked how this model "
              "behaves on input\nunlike its training data. It will answer anyway, "
              "with one of its options.")

    models = Path(args.workspace) / "models"
    models.mkdir(parents=True, exist_ok=True)
    destination = models / f"{args.name or schema.name}.cmw"
    save_weights(
        destination, architecture, tokenizer,
        {name: value.reshape(-1).tolist() for name, value in params.items()},
        meta={
            "trained_from": "warm-start" if args.warm else "scratch",
            "decision_schema": schema.to_dict(),
            "calibration": calibration.to_dict(),
            "instructions": args.instructions,
            "dataset_rows": len(rows),
            "label_counts": counts,
            "unfamiliar": unfamiliar,
            **record,
        },
    )
    print(f"\nwrote {destination} ({destination.stat().st_size / 1e6:.1f} MB)")
    print("try it with:  python3 -m chainmind.cli decide \"<some text>\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
