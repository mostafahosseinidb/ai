#!/usr/bin/env python3
"""Train ChainMind's own model from random numbers.

Not a fine-tune.  This starts from noise, learns a vocabulary from your
corpus, and writes a ``.cmw`` file that :mod:`chainmind.native` loads.  No
weights are downloaded, nothing is adapted from another model, and the only
third-party thing involved is NumPy -- a numerical library, not an AI
service.

Read this part before spending a night on it
--------------------------------------------

A model one person can train from scratch is *small*.  With a laptop CPU and
a corpus of a few megabytes you are in the range of a few million
parameters and a few million tokens, and what comes out will:

*   write text in the shape and register of your corpus,
*   answer in the style of your own recorded conversations,
*   and be **far worse at general knowledge and reasoning than any open
    model you could have run instead**.

That trade is the whole decision.  If you want the most capable assistant
your hardware can run, put a ``.gguf`` file in ``.chainmind/models/`` and
use the embedded backend.  If you want a system with no outside parts --
every weight traceable to your own corpus and your own machine -- train this
and accept a weaker model.  Both are supported and the rest of ChainMind
cannot tell the difference.

Scaling, roughly: useful models need about twenty tokens of training data
per parameter.  Ten million parameters wants two hundred million tokens,
which is more Persian text than most people have.  Below that ratio the
model memorises rather than generalises, which is fine if what you want is
a model that talks like your own archive -- and it is worth being clear-eyed
that this is what you are getting.

What it does
------------

Standard decoder-only pretraining: sample a window of tokens, predict each
next token, backpropagate, AdamW, cosine schedule with warmup.  The forward
pass mirrors :mod:`chainmind.nano` exactly, and the test suite checks every
gradient here against finite differences, because a transformer with one
wrong sign trains to a plausible-looking loss and produces nothing.

Usage
-----

    pip install -r training/requirements.txt

    python3 training/pretrain.py --corpus ./my-texts \\
        --workspace .chainmind --dim 256 --layers 6 --steps 4000

    chainmind runtime          # should now report your own model
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chainmind.nano import Architecture, save_weights            # noqa: E402
from chainmind.tokenizer import SPECIAL_TOKENS, Tokenizer, train_tokenizer  # noqa: E402

try:
    import numpy as np
except ImportError:                                              # pragma: no cover
    raise SystemExit(
        "pretraining needs NumPy:\n    pip install -r training/requirements.txt"
    )

TEXT_SUFFIXES = {".txt", ".md", ".jsonl", ".json"}


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------

def read_corpus(paths: Sequence[Path]) -> list[str]:
    """Every text file under the given paths, as strings.

    ``.jsonl`` is read as one record per line and understood two ways: a
    ``{"text": ...}`` record, or a ``{"prompt": ..., "response": ...}`` pair
    like the one ``chainmind.memory.build_training_dataset`` writes.  The
    second is rendered through the chat template later, so the control
    tokens mean something to the trained model.
    """
    documents: list[str] = []
    for path in paths:
        files = sorted(p for p in path.rglob("*") if p.suffix.lower() in TEXT_SUFFIXES) \
            if path.is_dir() else [path]
        for file in files:
            try:
                raw = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if file.suffix.lower() in {".jsonl", ".json"}:
                documents.extend(_from_records(raw))
            elif raw.strip():
                documents.append(raw)
    return documents


def _from_records(raw: str) -> list[str]:
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("text"):
            out.append(str(record["text"]))
        elif record.get("prompt") and record.get("response"):
            out.append(f"<<CHAT>>{record['prompt']}\n<<REPLY>>{record['response']}")
    return out


def is_conversation(document: str) -> bool:
    """Whether a document is a recorded exchange rather than prose."""
    return document.startswith("<<CHAT>>") and "\n<<REPLY>>" in document


def pack_tokens(tokenizer: Tokenizer, documents: Iterable[str]) -> "np.ndarray":
    """One flat stream of ids, documents separated by ``<|end|>``.

    Conversation records keep their roles: a model that never sees
    ``<|user|>`` during training will not know what to do with it at chat
    time, which is the commonest way a home-trained model comes out mute.
    """
    end = tokenizer.special("<|end|>")
    user = tokenizer.special("<|user|>")
    assistant = tokenizer.special("<|assistant|>")

    stream: list[int] = []
    for document in documents:
        if is_conversation(document):
            prompt, response = document[len("<<CHAT>>"):].split("\n<<REPLY>>", 1)
            stream.append(user)
            stream.extend(tokenizer.encode(prompt))
            stream.append(end)
            stream.append(assistant)
            stream.extend(tokenizer.encode(response))
        else:
            stream.extend(tokenizer.encode(document))
        stream.append(end)
    return np.asarray(stream, dtype=np.int32)


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------

def initialise(architecture: Architecture, seed: int = 0,
               dtype: Any = None) -> dict[str, "np.ndarray"]:
    """Random weights, scaled so the residual stream does not explode.

    Projections that write *into* the residual stream (``wo`` and ``w2``) are
    damped by the depth, which is what keeps a deep stack trainable without a
    learning-rate search.

    ``dtype`` exists for the gradient check: in float32 a finite-difference
    estimate of a small gradient is mostly rounding noise, so the test runs
    the same code in double precision.
    """
    rng = np.random.default_rng(seed)
    dtype = np.float32 if dtype is None else dtype
    scale = 0.02
    depth = 1.0 / math.sqrt(2 * max(architecture.layers, 1))

    params: dict[str, np.ndarray] = {}
    for name, shape in architecture.tensor_shapes():
        if name.endswith("norm"):
            params[name] = np.ones(shape, dtype=dtype)
        else:
            spread = scale * (depth if name.endswith((".wo", ".w2")) else 1.0)
            params[name] = rng.normal(0.0, spread, size=shape).astype(dtype)
    return params


# --------------------------------------------------------------------------
# Forward and backward
# --------------------------------------------------------------------------

def _rope_tables(positions: "np.ndarray", head_dim: int, theta: float,
                 dtype: Any) -> tuple["np.ndarray", "np.ndarray"]:
    offsets = np.arange(head_dim // 2, dtype=np.float64)
    angles = positions[:, None] / (theta ** (2.0 * offsets[None, :] / head_dim))
    return np.cos(angles).astype(dtype), np.sin(angles).astype(dtype)


def _rope_apply(x: "np.ndarray", cos: "np.ndarray", sin: "np.ndarray") -> "np.ndarray":
    """``x`` is (batch, time, heads, head_dim); tables are (time, head_dim/2)."""
    half = x.shape[-1] // 2
    first, second = x[..., :half], x[..., half:]
    c, s = cos[None, :, None, :], sin[None, :, None, :]
    return np.concatenate([first * c - second * s, first * s + second * c], axis=-1)


def _rope_backward(grad: "np.ndarray", cos: "np.ndarray", sin: "np.ndarray"
                   ) -> "np.ndarray":
    half = grad.shape[-1] // 2
    first, second = grad[..., :half], grad[..., half:]
    c, s = cos[None, :, None, :], sin[None, :, None, :]
    return np.concatenate([first * c + second * s, -first * s + second * c], axis=-1)


def _rmsnorm(x: "np.ndarray", gain: "np.ndarray", eps: float
             ) -> tuple["np.ndarray", "np.ndarray"]:
    inverse = 1.0 / np.sqrt((x * x).mean(axis=-1, keepdims=True) + eps)
    hat = x * inverse
    return hat * gain, inverse


def _rmsnorm_backward(grad: "np.ndarray", x: "np.ndarray", gain: "np.ndarray",
                      inverse: "np.ndarray") -> tuple["np.ndarray", "np.ndarray"]:
    hat = x * inverse
    grad_gain = (grad * hat).reshape(-1, x.shape[-1]).sum(axis=0)
    grad_hat = grad * gain
    correction = (grad_hat * hat).mean(axis=-1, keepdims=True)
    return (grad_hat - hat * correction) * inverse, grad_gain


def _softmax(x: "np.ndarray") -> "np.ndarray":
    shifted = x - x.max(axis=-1, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / exponent.sum(axis=-1, keepdims=True)


def forward_stack(params: dict[str, "np.ndarray"], architecture: Architecture,
                  inputs: "np.ndarray") -> tuple["np.ndarray", dict[str, Any]]:
    """Run the transformer and stop before the head.

    Returns the normalised hidden state for every position, plus everything
    the backward pass needs. Split out from the loss because two different
    heads read the same stack: the language-model head in this file predicts
    the next token, and the decision head in ``train_decider.py`` chooses
    between typed options. Sharing the implementation is what stops the two
    trainers -- and the inference code in ``chainmind/nano.py`` -- from
    drifting apart.
    """
    batch, time_steps = inputs.shape
    dim, heads = architecture.dim, architecture.heads
    head_dim = architecture.head_dim
    eps = architecture.norm_eps

    embedding = params["tok_emb"]
    dtype = embedding.dtype
    positions = np.arange(time_steps, dtype=np.float64)
    cos, sin = _rope_tables(positions, head_dim, architecture.rope_theta, dtype)
    mask = np.triu(np.full((time_steps, time_steps), -1e9, dtype=dtype), 1)

    x = embedding[inputs]                               # (B, T, d)
    tape: list[dict[str, Any]] = []

    for index in range(architecture.layers):
        prefix = f"layer.{index}"
        step: dict[str, Any] = {"x": x}

        normed, inverse = _rmsnorm(x, params[f"{prefix}.attn_norm"], eps)
        step["attn_normed"], step["attn_inverse"] = normed, inverse

        q = (normed @ params[f"{prefix}.wq"].T).reshape(batch, time_steps, heads, head_dim)
        k = (normed @ params[f"{prefix}.wk"].T).reshape(batch, time_steps, heads, head_dim)
        v = (normed @ params[f"{prefix}.wv"].T).reshape(batch, time_steps, heads, head_dim)
        q_rot, k_rot = _rope_apply(q, cos, sin), _rope_apply(k, cos, sin)

        scores = np.einsum("bqhd,bkhd->bhqk", q_rot, k_rot) / math.sqrt(head_dim)
        probabilities = _softmax(scores + mask[None, None, :, :])
        attended = np.einsum("bhqk,bkhd->bqhd", probabilities, v)
        flat = attended.reshape(batch, time_steps, dim)
        step.update(q_rot=q_rot, k_rot=k_rot, v=v, probabilities=probabilities, flat=flat)

        x = x + flat @ params[f"{prefix}.wo"].T

        ffn_normed, ffn_inverse = _rmsnorm(x, params[f"{prefix}.ffn_norm"], eps)
        gate = ffn_normed @ params[f"{prefix}.w1"].T
        up = ffn_normed @ params[f"{prefix}.w3"].T
        sigmoid = 1.0 / (1.0 + np.exp(-gate))
        activated = gate * sigmoid
        hidden = activated * up
        step.update(residual=x, ffn_normed=ffn_normed, ffn_inverse=ffn_inverse,
                    gate=gate, up=up, sigmoid=sigmoid, activated=activated,
                    hidden=hidden)

        x = x + hidden @ params[f"{prefix}.w2"].T
        tape.append(step)

    final, final_inverse = _rmsnorm(x, params["norm"], eps)
    state = {"tape": tape, "x": x, "final_inverse": final_inverse,
             "cos": cos, "sin": sin, "inputs": inputs}
    return final, state


def backward_stack(params: dict[str, "np.ndarray"], architecture: Architecture,
                   state: dict[str, Any], d_final: "np.ndarray",
                   grads: dict[str, "np.ndarray"]) -> None:
    """Propagate a gradient on the final hidden state through the stack.

    ``d_final`` is the gradient with respect to what :func:`forward_stack`
    returned. Accumulates into ``grads`` in place, embeddings included.
    """
    dim, heads = architecture.dim, architecture.heads
    head_dim = architecture.head_dim
    tape, cos, sin = state["tape"], state["cos"], state["sin"]
    batch, time_steps = state["inputs"].shape

    d_x, grad_gain = _rmsnorm_backward(d_final, state["x"], params["norm"],
                                       state["final_inverse"])
    grads["norm"] += grad_gain

    for index in reversed(range(architecture.layers)):
        prefix = f"layer.{index}"
        step = tape[index]

        d_hidden = d_x @ params[f"{prefix}.w2"]
        grads[f"{prefix}.w2"] += np.einsum("btd,bth->dh", d_x, step["hidden"])

        d_activated = d_hidden * step["up"]
        d_up = d_hidden * step["activated"]
        d_gate = d_activated * step["sigmoid"] * (
            1.0 + step["gate"] * (1.0 - step["sigmoid"])
        )

        grads[f"{prefix}.w1"] += np.einsum("bth,btd->hd", d_gate, step["ffn_normed"])
        grads[f"{prefix}.w3"] += np.einsum("bth,btd->hd", d_up, step["ffn_normed"])
        d_ffn_normed = d_gate @ params[f"{prefix}.w1"] + d_up @ params[f"{prefix}.w3"]

        d_residual, grad_gain = _rmsnorm_backward(
            d_ffn_normed, step["residual"], params[f"{prefix}.ffn_norm"],
            step["ffn_inverse"],
        )
        grads[f"{prefix}.ffn_norm"] += grad_gain
        d_x = d_x + d_residual

        d_flat = d_x @ params[f"{prefix}.wo"]
        grads[f"{prefix}.wo"] += np.einsum("btd,bte->de", d_x, step["flat"])

        d_attended = d_flat.reshape(batch, time_steps, heads, head_dim)
        d_probabilities = np.einsum("bqhd,bkhd->bhqk", d_attended, step["v"])
        d_v = np.einsum("bhqk,bqhd->bkhd", step["probabilities"], d_attended)

        probabilities = step["probabilities"]
        d_scores = probabilities * (
            d_probabilities
            - (d_probabilities * probabilities).sum(axis=-1, keepdims=True)
        )
        d_scores /= math.sqrt(head_dim)

        d_q_rot = np.einsum("bhqk,bkhd->bqhd", d_scores, step["k_rot"])
        d_k_rot = np.einsum("bhqk,bqhd->bkhd", d_scores, step["q_rot"])
        d_q = _rope_backward(d_q_rot, cos, sin).reshape(batch, time_steps, dim)
        d_k = _rope_backward(d_k_rot, cos, sin).reshape(batch, time_steps, dim)
        d_v = d_v.reshape(batch, time_steps, dim)

        normed = step["attn_normed"]
        grads[f"{prefix}.wq"] += np.einsum("bte,btd->ed", d_q, normed)
        grads[f"{prefix}.wk"] += np.einsum("bte,btd->ed", d_k, normed)
        grads[f"{prefix}.wv"] += np.einsum("bte,btd->ed", d_v, normed)
        d_normed = (d_q @ params[f"{prefix}.wq"] + d_k @ params[f"{prefix}.wk"]
                    + d_v @ params[f"{prefix}.wv"])

        d_input, grad_gain = _rmsnorm_backward(
            d_normed, step["x"], params[f"{prefix}.attn_norm"], step["attn_inverse"],
        )
        grads[f"{prefix}.attn_norm"] += grad_gain
        d_x = d_x + d_input

    np.add.at(grads["tok_emb"], state["inputs"].reshape(-1), d_x.reshape(-1, dim))


def forward_backward(params: dict[str, "np.ndarray"], architecture: Architecture,
                     inputs: "np.ndarray", targets: "np.ndarray",
                     compute_gradients: bool = True
                     ) -> tuple[float, dict[str, "np.ndarray"]]:
    """Mean cross-entropy over a batch, and the gradient of every parameter.

    Written out rather than delegated to an autograd framework so that the
    only dependency is an array library, and so that what the model does is
    readable in one file next to the inference code it has to match.
    """
    embedding = params["tok_emb"]
    final, state = forward_stack(params, architecture, inputs)

    logits = final @ embedding.T
    probabilities = _softmax(logits)

    batch, time_steps = inputs.shape
    count = batch * time_steps
    flat_probabilities = probabilities.reshape(count, -1)
    flat_targets = targets.reshape(count)
    picked = flat_probabilities[np.arange(count), flat_targets]
    loss = float(-np.log(np.maximum(picked, 1e-12)).mean())

    if not compute_gradients:
        return loss, {}

    grads: dict[str, np.ndarray] = {
        name: np.zeros_like(value) for name, value in params.items()
    }

    d_logits = probabilities.copy()
    d_logits.reshape(count, -1)[np.arange(count), flat_targets] -= 1.0
    d_logits /= count

    grads["tok_emb"] += np.einsum("btv,btd->vd", d_logits, final)
    d_final = d_logits @ embedding

    backward_stack(params, architecture, state, d_final, grads)
    return loss, grads


# --------------------------------------------------------------------------
# Optimiser
# --------------------------------------------------------------------------

class AdamW:
    """Adam with decoupled weight decay, which is the standard for this.

    Norm gains are excluded from decay: shrinking them toward zero fights
    the normalisation itself and shows up as a loss that stalls.
    """

    def __init__(self, params: dict[str, "np.ndarray"], learning_rate: float = 3e-4,
                 betas: tuple[float, float] = (0.9, 0.95), eps: float = 1e-8,
                 weight_decay: float = 0.1) -> None:
        self.learning_rate = learning_rate
        self.beta1, self.beta2 = betas
        self.eps = eps
        self.weight_decay = weight_decay
        self.step_count = 0
        self.moment1 = {name: np.zeros_like(value) for name, value in params.items()}
        self.moment2 = {name: np.zeros_like(value) for name, value in params.items()}

    def step(self, params: dict[str, "np.ndarray"], grads: dict[str, "np.ndarray"],
             learning_rate: float | None = None, clip: float = 1.0) -> float:
        self.step_count += 1
        rate = self.learning_rate if learning_rate is None else learning_rate

        norm = math.sqrt(sum(float((g * g).sum()) for g in grads.values()))
        factor = min(1.0, clip / (norm + 1e-12)) if clip else 1.0

        correction1 = 1.0 - self.beta1 ** self.step_count
        correction2 = 1.0 - self.beta2 ** self.step_count

        for name, value in params.items():
            grad = grads[name] * factor
            self.moment1[name] = self.beta1 * self.moment1[name] + (1 - self.beta1) * grad
            self.moment2[name] = (self.beta2 * self.moment2[name]
                                  + (1 - self.beta2) * grad * grad)
            hat1 = self.moment1[name] / correction1
            hat2 = self.moment2[name] / correction2
            update = hat1 / (np.sqrt(hat2) + self.eps)
            if self.weight_decay and not name.endswith("norm"):
                update = update + self.weight_decay * value
            params[name] = (value - rate * update).astype(value.dtype)
        return norm


def schedule(step: int, total: int, peak: float, warmup: int,
             floor_ratio: float = 0.1) -> float:
    """Linear warmup then cosine decay -- the usual, and it matters here.

    Without warmup a small model on a small corpus diverges in the first
    dozen steps, which looks like a bug in the gradients and is not.
    """
    if warmup and step < warmup:
        return peak * (step + 1) / warmup
    if total <= warmup:
        return peak
    progress = (step - warmup) / max(1, total - warmup)
    cosine = 0.5 * (1.0 + math.cos(math.pi * min(1.0, progress)))
    return peak * (floor_ratio + (1.0 - floor_ratio) * cosine)


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

def sample_batch(stream: "np.ndarray", batch: int, length: int, rng: Any
                 ) -> tuple["np.ndarray", "np.ndarray"]:
    starts = rng.integers(0, len(stream) - length - 1, size=batch)
    inputs = np.stack([stream[s:s + length] for s in starts]).astype(np.int64)
    targets = np.stack([stream[s + 1:s + length + 1] for s in starts]).astype(np.int64)
    return inputs, targets


def train(stream: "np.ndarray", architecture: Architecture, *, steps: int,
          batch: int, length: int, learning_rate: float, warmup: int,
          seed: int, holdout: float = 0.05, report: Any = print
          ) -> tuple[dict[str, "np.ndarray"], dict[str, Any]]:
    """Pretrain, and report the held-out loss so overfitting is visible.

    The split is by position, not by sample: the tail of the corpus is never
    trained on.  With a corpus this small the model will memorise, and the
    gap between the two numbers is the only honest way to see it happening.
    """
    if len(stream) < length + 2:
        raise SystemExit(
            f"corpus is {len(stream)} tokens; need more than {length + 2}. "
            "Point --corpus at more text."
        )

    cut = max(length + 2, int(len(stream) * (1.0 - holdout)))
    train_stream, holdout_stream = stream[:cut], stream[cut:]
    rng = np.random.default_rng(seed)

    params = initialise(architecture, seed=seed)
    optimiser = AdamW(params, learning_rate=learning_rate)
    history: list[dict[str, float]] = []
    started = time.monotonic()

    for step in range(steps):
        inputs, targets = sample_batch(train_stream, batch, length, rng)
        rate = schedule(step, steps, learning_rate, warmup)
        loss, grads = forward_backward(params, architecture, inputs, targets)
        norm = optimiser.step(params, grads, learning_rate=rate)

        if step % max(1, steps // 20) == 0 or step == steps - 1:
            entry = {"step": step, "loss": round(loss, 4),
                     "lr": round(rate, 6), "grad_norm": round(norm, 3)}
            if len(holdout_stream) > length + 2:
                check_inputs, check_targets = sample_batch(
                    holdout_stream, min(batch, 4), length, np.random.default_rng(0)
                )
                entry["holdout"] = round(forward_backward(
                    params, architecture, check_inputs, check_targets,
                    compute_gradients=False,
                )[0], 4)
            history.append(entry)
            report(
                f"step {step:>6}/{steps}  loss {entry['loss']:.4f}"
                + (f"  holdout {entry['holdout']:.4f}" if "holdout" in entry else "")
                + f"  lr {rate:.2e}  |g| {norm:.2f}"
            )

    return params, {
        "steps": steps, "batch": batch, "length": length,
        "learning_rate": learning_rate, "seed": seed,
        "train_tokens": int(len(train_stream)),
        "seconds": round(time.monotonic() - started, 1),
        "history": history,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train ChainMind's own model from scratch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--corpus", type=Path, nargs="+", required=True,
                        help="text files or directories to learn from")
    parser.add_argument("--workspace", type=Path, default=Path(".chainmind"),
                        help="where the finished model is written")
    parser.add_argument("--name", default="atlas", help="name of the weights file")
    parser.add_argument("--vocab", type=int, default=4096)
    parser.add_argument("--dim", type=int, default=256)
    parser.add_argument("--layers", type=int, default=6)
    parser.add_argument("--heads", type=int, default=8)
    parser.add_argument("--hidden", type=int, default=None,
                        help="feed-forward width (default: 8/3 x dim, rounded)")
    parser.add_argument("--context", type=int, default=512)
    parser.add_argument("--steps", type=int, default=2000)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--length", type=int, default=None,
                        help="training window (default: the context length)")
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    documents = read_corpus(args.corpus)
    if not documents:
        raise SystemExit(f"no readable text under {', '.join(map(str, args.corpus))}")
    characters = sum(len(document) for document in documents)
    conversations = sum(1 for document in documents if is_conversation(document))
    print(f"corpus: {len(documents)} documents ({conversations} recorded "
          f"conversations), {characters:,} characters")
    if not conversations:
        print("  note: nothing in this corpus is a conversation, so the model will "
              "continue text\n  rather than answer questions. Add the dataset from "
              "`chainmind learning --export` to change that.")

    print(f"training a {args.vocab}-token vocabulary (this is the slow, quiet part)")
    tokenizer = train_tokenizer(documents, vocab_size=args.vocab)
    stream = pack_tokens(tokenizer, documents)
    print(f"tokenizer: {tokenizer.vocab_size} tokens, corpus packs to {len(stream):,} "
          f"ids ({characters / max(1, len(stream)):.2f} characters per token)")

    hidden = args.hidden or (int(args.dim * 8 / 3) // 32 + 1) * 32
    architecture = Architecture(
        vocab_size=tokenizer.vocab_size, dim=args.dim, layers=args.layers,
        heads=args.heads, hidden=hidden, context=args.context,
    )
    length = min(args.length or args.context, args.context)
    ratio = len(stream) / max(1, architecture.parameters)
    print(f"model: {architecture.parameters / 1e6:.2f}M parameters, "
          f"{ratio:.1f} tokens per parameter")
    if ratio < 5:
        print("  note: under about 20 tokens per parameter the model memorises more "
              "than it generalises.\n  Either bring more text or make the model "
              "smaller (--dim, --layers).")

    params, record = train(
        stream, architecture, steps=args.steps, batch=args.batch, length=length,
        learning_rate=args.lr, warmup=args.warmup, seed=args.seed,
    )

    models = Path(args.workspace) / "models"
    models.mkdir(parents=True, exist_ok=True)
    destination = models / f"{args.name}.cmw"
    save_weights(
        destination, architecture, tokenizer,
        {name: value.reshape(-1).tolist() for name, value in params.items()},
        meta={
            "trained_from": "scratch",
            "corpus_documents": len(documents),
            "corpus_conversations": conversations,
            "corpus_characters": characters,
            "specials": list(SPECIAL_TOKENS),
            **record,
        },
    )
    print(f"\nwrote {destination} ({destination.stat().st_size / 1e6:.1f} MB)")
    print("check it with:  python3 -m chainmind.cli runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
