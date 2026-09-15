"""A transformer this project owns from the first weight to the last.

The embedded backend loads a ``.gguf`` file: your file, on your disk, with
nobody to ask -- but somebody else's model.  The architecture, the
tokenizer and the training corpus all came from elsewhere.  That is
independence from a *provider*, which is most of what matters in practice
and is not the same as independence.

This module closes the last gap.  The architecture is here, the tokenizer is
in :mod:`chainmind.tokenizer`, the trainer is in ``training/pretrain.py``,
and the weights come out of your own corpus.  Nothing in the pipeline was
downloaded.

**Say the cost out loud, because it is large.**  A model one person can
train from scratch is small -- a few million to a few hundred million
parameters over a corpus measured in millions of tokens, not trillions.  It
will write fluent-looking text in the register of what you fed it and it
will be far worse at general conversation than any open model you could run
instead.  That is not a defect of this code; it is what the arithmetic of
pretraining costs.  Which one you want depends on whether you are after a
capable assistant or a system with no outside parts, and the project
supports both: see ``chainmind runtime``.

The design is the standard decoder-only stack -- pre-norm, RMSNorm, rotary
position embeddings, SwiGLU feed-forward, tied embeddings, no biases.
Standard on purpose: an unusual architecture would be a second thing that
could be wrong while debugging a model that is already hard to evaluate.

Arithmetic runs on NumPy when it is installed and on the standard library
when it is not, the same arrangement :mod:`chainmind.crypto` uses for
signatures.  The two paths are checked against each other by the tests, so
the fallback is a slower answer rather than a different one.
"""

from __future__ import annotations

import array
import hashlib
import json
import math
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .tokenizer import Tokenizer

__all__ = [
    "Architecture",
    "NanoModel",
    "WeightsError",
    "MAGIC",
    "SUFFIX",
    "backend_name",
    "load_weights",
    "save_weights",
]

MAGIC = b"CMW1"
SUFFIX = ".cmw"
_HEADER_LIMIT = 64 * 1024 * 1024      # a header this big is a corrupt file


class WeightsError(RuntimeError):
    """A weights file cannot be used, with a reason a person can act on."""


# --------------------------------------------------------------------------
# Architecture
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Architecture:
    """The shape of the network, and the whole of what a reader must know.

    Stored in the file next to the numbers, so a weights file never depends
    on the version of this package that happens to be installed.
    """

    vocab_size: int
    dim: int
    layers: int
    heads: int
    hidden: int
    context: int = 512
    norm_eps: float = 1e-5
    rope_theta: float = 10_000.0

    def __post_init__(self) -> None:
        if self.dim % self.heads:
            raise ValueError(f"dim {self.dim} is not divisible by heads {self.heads}")
        if self.head_dim % 2:
            raise ValueError("head_dim must be even for rotary embeddings")

    @property
    def head_dim(self) -> int:
        return self.dim // self.heads

    @property
    def parameters(self) -> int:
        per_layer = 4 * self.dim * self.dim + 3 * self.dim * self.hidden + 2 * self.dim
        return self.vocab_size * self.dim + self.layers * per_layer + self.dim

    def tensor_shapes(self) -> list[tuple[str, tuple[int, ...]]]:
        """Every tensor in the model, in the order the file stores them."""
        shapes: list[tuple[str, tuple[int, ...]]] = [
            ("tok_emb", (self.vocab_size, self.dim)),
        ]
        for index in range(self.layers):
            prefix = f"layer.{index}"
            shapes += [
                (f"{prefix}.attn_norm", (self.dim,)),
                (f"{prefix}.wq", (self.dim, self.dim)),
                (f"{prefix}.wk", (self.dim, self.dim)),
                (f"{prefix}.wv", (self.dim, self.dim)),
                (f"{prefix}.wo", (self.dim, self.dim)),
                (f"{prefix}.ffn_norm", (self.dim,)),
                (f"{prefix}.w1", (self.hidden, self.dim)),
                (f"{prefix}.w3", (self.hidden, self.dim)),
                (f"{prefix}.w2", (self.dim, self.hidden)),
            ]
        shapes.append(("norm", (self.dim,)))
        return shapes

    def to_dict(self) -> dict[str, Any]:
        return {
            "vocab_size": self.vocab_size, "dim": self.dim, "layers": self.layers,
            "heads": self.heads, "hidden": self.hidden, "context": self.context,
            "norm_eps": self.norm_eps, "rope_theta": self.rope_theta,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Architecture":
        known = {field_name: payload[field_name] for field_name in
                 ("vocab_size", "dim", "layers", "heads", "hidden")
                 if field_name in payload}
        for optional in ("context", "norm_eps", "rope_theta"):
            if optional in payload:
                known[optional] = payload[optional]
        try:
            return cls(**known)   # type: ignore[arg-type]
        except TypeError as exc:
            raise WeightsError(f"incomplete architecture in weights file: {exc}") from exc


# --------------------------------------------------------------------------
# Arithmetic, twice
# --------------------------------------------------------------------------

class _PureOps:
    """Float arithmetic in the standard library.

    Slow -- seconds per token rather than milliseconds -- and here so that a
    model can be loaded and checked on a machine with nothing installed.  It
    is also the reference the NumPy path is tested against.
    """

    name = "pure-python"

    @staticmethod
    def matrix(flat: Sequence[float], rows: int, cols: int) -> list[list[float]]:
        return [list(flat[row * cols:(row + 1) * cols]) for row in range(rows)]

    @staticmethod
    def vector(values: Sequence[float]) -> list[float]:
        return list(values)

    @staticmethod
    def to_list(vector: Any) -> list[float]:
        return list(vector)

    @staticmethod
    def row(matrix: Any, index: int) -> list[float]:
        return list(matrix[index])

    @staticmethod
    def matvec(matrix: Any, vector: Any) -> list[float]:
        return [sum(w * x for w, x in zip(row, vector)) for row in matrix]

    @staticmethod
    def add(left: Any, right: Any) -> list[float]:
        return [a + b for a, b in zip(left, right)]

    @staticmethod
    def rmsnorm(vector: Any, gain: Any, eps: float) -> list[float]:
        scale = 1.0 / math.sqrt(sum(x * x for x in vector) / len(vector) + eps)
        return [x * scale * g for x, g in zip(vector, gain)]

    @staticmethod
    def swiglu(gate: Any, up: Any) -> list[float]:
        return [(g / (1.0 + math.exp(-g))) * u for g, u in zip(gate, up)]

    @staticmethod
    def rope(vector: Any, position: int, head_dim: int, theta: float) -> list[float]:
        out = list(vector)
        half = head_dim // 2
        for start in range(0, len(out), head_dim):
            for offset in range(half):
                angle = position / (theta ** (2.0 * offset / head_dim))
                cos, sin = math.cos(angle), math.sin(angle)
                a, b = out[start + offset], out[start + half + offset]
                out[start + offset] = a * cos - b * sin
                out[start + half + offset] = a * sin + b * cos
        return out

    @staticmethod
    def attend(query: Any, keys: Sequence[Any], values: Sequence[Any],
               heads: int, head_dim: int) -> list[float]:
        out = [0.0] * (heads * head_dim)
        scale = 1.0 / math.sqrt(head_dim)
        for head in range(heads):
            lo = head * head_dim
            hi = lo + head_dim
            scores = [
                sum(q * k for q, k in zip(query[lo:hi], key[lo:hi])) * scale
                for key in keys
            ]
            top = max(scores)
            weights = [math.exp(score - top) for score in scores]
            total = sum(weights)
            for weight, value in zip(weights, values):
                share = weight / total
                for index in range(lo, hi):
                    out[index] += share * value[index]
        return out


class _NumpyOps:
    """The same arithmetic, vectorised.

    NumPy is optional and is the only thing that makes this model usable at
    conversational speed.  It is not an AI service and holds no account: it
    is a numerical library, and the project works without it.
    """

    name = "numpy"

    def __init__(self, numpy: Any) -> None:
        self.np = numpy

    def matrix(self, flat: Sequence[float], rows: int, cols: int) -> Any:
        return self.np.asarray(flat, dtype=self.np.float32).reshape(rows, cols)

    def vector(self, values: Sequence[float]) -> Any:
        return self.np.asarray(values, dtype=self.np.float32)

    @staticmethod
    def to_list(vector: Any) -> list[float]:
        return [float(value) for value in vector]

    @staticmethod
    def row(matrix: Any, index: int) -> Any:
        return matrix[index]

    @staticmethod
    def matvec(matrix: Any, vector: Any) -> Any:
        return matrix @ vector

    @staticmethod
    def add(left: Any, right: Any) -> Any:
        return left + right

    def rmsnorm(self, vector: Any, gain: Any, eps: float) -> Any:
        scale = 1.0 / self.np.sqrt(float((vector * vector).mean()) + eps)
        return vector * scale * gain

    def swiglu(self, gate: Any, up: Any) -> Any:
        return (gate / (1.0 + self.np.exp(-gate))) * up

    def rope(self, vector: Any, position: int, head_dim: int, theta: float) -> Any:
        np = self.np
        half = head_dim // 2
        offsets = np.arange(half, dtype=np.float32)
        angle = position / (theta ** (2.0 * offsets / head_dim))
        cos, sin = np.cos(angle).astype(np.float32), np.sin(angle).astype(np.float32)
        view = vector.reshape(-1, head_dim)
        first, second = view[:, :half], view[:, half:]
        return np.concatenate(
            [first * cos - second * sin, first * sin + second * cos], axis=1
        ).reshape(-1)

    def attend(self, query: Any, keys: Sequence[Any], values: Sequence[Any],
               heads: int, head_dim: int) -> Any:
        np = self.np
        k = np.stack(keys).reshape(len(keys), heads, head_dim)
        v = np.stack(values).reshape(len(values), heads, head_dim)
        q = query.reshape(heads, head_dim)
        scores = np.einsum("hd,thd->ht", q, k) / math.sqrt(head_dim)
        scores -= scores.max(axis=1, keepdims=True)
        weights = np.exp(scores)
        weights /= weights.sum(axis=1, keepdims=True)
        return np.einsum("ht,thd->hd", weights, v).reshape(-1)


def _select_ops() -> Any:
    """Prefer NumPy, but never let a broken install take the model down.

    Guarded as widely as the crypto backend is, and for the same reason: a
    half-built native extension raises things that are not ``ImportError``,
    and a slow model beats a model that will not load.
    """
    if not os.environ.get("CHAINMIND_PURE_PYTHON_MATH"):
        try:
            import numpy                                      # noqa: PLC0415

            ops = _NumpyOps(numpy)
            probe = ops.matvec(ops.matrix([1.0, 2.0, 3.0, 4.0], 2, 2),
                               ops.vector([1.0, 1.0]))
            if [round(value, 6) for value in ops.to_list(probe)] != [3.0, 7.0]:
                raise ArithmeticError("numpy produced the wrong answer")
            return ops
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            pass
    return _PureOps()


_OPS = _select_ops()


def backend_name() -> str:
    """Which arithmetic backend is in use -- reported by ``chainmind runtime``."""
    return _OPS.name


# --------------------------------------------------------------------------
# The file format
# --------------------------------------------------------------------------

def _pack(values: Sequence[float]) -> bytes:
    buffer = array.array("f", values)
    if sys.byteorder != "little":
        buffer.byteswap()
    return buffer.tobytes()


def _unpack(blob: bytes) -> array.array:
    buffer = array.array("f")
    buffer.frombytes(blob)
    if sys.byteorder != "little":
        buffer.byteswap()
    return buffer


def save_weights(path: Path | str, architecture: Architecture,
                 tokenizer: Tokenizer, tensors: Mapping[str, Sequence[float]],
                 meta: Mapping[str, Any] | None = None) -> Path:
    """Write a ``.cmw`` file: one JSON header, then float32 in header order.

    Deliberately not pickle.  A weights file gets copied between machines and
    handed to strangers, and a format that can execute code on load would
    undo every other guarantee in this project.
    """
    path = Path(path)
    shapes = architecture.tensor_shapes()

    entries = []
    offset = 0
    for name, shape in shapes:
        expected = math.prod(shape)
        values = tensors.get(name)
        if values is None:
            raise WeightsError(f"missing tensor {name}")
        if len(values) != expected:
            raise WeightsError(
                f"tensor {name} has {len(values)} values, expected {expected}"
            )
        entries.append({"name": name, "shape": list(shape),
                        "dtype": "f4", "offset": offset})
        offset += expected * 4

    header = json.dumps({
        "format": "chainmind-weights",
        "version": 1,
        "architecture": architecture.to_dict(),
        "tokenizer": tokenizer.to_dict(),
        "tensors": entries,
        "meta": dict(meta or {}),
    }, ensure_ascii=False).encode("utf-8")

    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("wb") as handle:
        handle.write(MAGIC)
        handle.write(struct.pack("<I", len(header)))
        handle.write(header)
        for name, _shape in shapes:
            handle.write(_pack(tensors[name]))
    temporary.replace(path)
    return path


def load_weights(path: Path | str) -> tuple[Architecture, Tokenizer,
                                            dict[str, array.array], dict[str, Any]]:
    """Read a ``.cmw`` file, checking the parts that could be lies."""
    path = Path(path)
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise WeightsError(f"cannot read {path}: {exc}") from exc

    if len(blob) < 8 or blob[:4] != MAGIC:
        raise WeightsError(
            f"{path.name} is not a ChainMind weights file (expected magic "
            f"{MAGIC.decode()})"
        )
    (header_length,) = struct.unpack("<I", blob[4:8])
    if header_length > _HEADER_LIMIT or 8 + header_length > len(blob):
        raise WeightsError(f"{path.name} has a corrupt header length")

    try:
        header = json.loads(blob[8:8 + header_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WeightsError(f"{path.name} has an unreadable header: {exc}") from exc

    if header.get("version") != 1:
        raise WeightsError(
            f"{path.name} is format version {header.get('version')}; this build "
            "reads version 1"
        )

    architecture = Architecture.from_dict(header.get("architecture") or {})
    tokenizer = Tokenizer.from_dict(header.get("tokenizer") or {})
    if tokenizer.vocab_size != architecture.vocab_size:
        raise WeightsError(
            f"{path.name} carries a {tokenizer.vocab_size}-token vocabulary but "
            f"embeddings for {architecture.vocab_size}; it was assembled wrongly"
        )

    body = blob[8 + header_length:]
    tensors: dict[str, array.array] = {}
    for entry in header.get("tensors", []):
        name = str(entry["name"])
        count = math.prod(int(value) for value in entry["shape"])
        start = int(entry["offset"])
        end = start + count * 4
        if end > len(body):
            raise WeightsError(f"{path.name} is truncated at tensor {name}")
        tensors[name] = _unpack(body[start:end])

    missing = [name for name, _ in architecture.tensor_shapes() if name not in tensors]
    if missing:
        raise WeightsError(f"{path.name} is missing tensors: {', '.join(missing[:4])}")

    return architecture, tokenizer, tensors, dict(header.get("meta") or {})


def weights_fingerprint(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------

@dataclass
class _Layer:
    attn_norm: Any
    wq: Any
    wk: Any
    wv: Any
    wo: Any
    ffn_norm: Any
    w1: Any
    w3: Any
    w2: Any


@dataclass
class NanoModel:
    """A loaded model: weights, tokenizer, and a way to continue a sequence."""

    architecture: Architecture
    tokenizer: Tokenizer
    meta: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None
    fingerprint: str = ""
    _tok_emb: Any = field(default=None, repr=False)
    _layers: list[_Layer] = field(default_factory=list, repr=False)
    _norm: Any = field(default=None, repr=False)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_tensors(cls, architecture: Architecture, tokenizer: Tokenizer,
                     tensors: Mapping[str, Sequence[float]],
                     meta: Mapping[str, Any] | None = None,
                     path: Path | None = None,
                     fingerprint: str = "") -> "NanoModel":
        shapes = dict(architecture.tensor_shapes())

        def take(name: str) -> Any:
            shape = shapes[name]
            values = tensors[name]
            if len(shape) == 1:
                return _OPS.vector(values)
            return _OPS.matrix(values, shape[0], shape[1])

        model = cls(architecture=architecture, tokenizer=tokenizer,
                    meta=dict(meta or {}), path=path, fingerprint=fingerprint)
        model._tok_emb = take("tok_emb")
        model._norm = take("norm")
        model._layers = [
            _Layer(
                attn_norm=take(f"layer.{index}.attn_norm"),
                wq=take(f"layer.{index}.wq"), wk=take(f"layer.{index}.wk"),
                wv=take(f"layer.{index}.wv"), wo=take(f"layer.{index}.wo"),
                ffn_norm=take(f"layer.{index}.ffn_norm"),
                w1=take(f"layer.{index}.w1"), w3=take(f"layer.{index}.w3"),
                w2=take(f"layer.{index}.w2"),
            )
            for index in range(architecture.layers)
        ]
        return model

    @classmethod
    def load(cls, path: Path | str) -> "NanoModel":
        path = Path(path)
        architecture, tokenizer, tensors, meta = load_weights(path)
        return cls.from_tensors(architecture, tokenizer, tensors, meta,
                                path=path, fingerprint=weights_fingerprint(path))

    # -- the forward pass --------------------------------------------------

    def _forward(self, token: int, position: int,
                 cache: list[tuple[list[Any], list[Any]]]) -> Any:
        """One token in, one row of logits out, cache extended in place."""
        arch = self.architecture
        head_dim = arch.head_dim
        x = _OPS.row(self._tok_emb, token)

        for layer, (keys, values) in zip(self._layers, cache):
            h = _OPS.rmsnorm(x, layer.attn_norm, arch.norm_eps)
            q = _OPS.rope(_OPS.matvec(layer.wq, h), position, head_dim, arch.rope_theta)
            k = _OPS.rope(_OPS.matvec(layer.wk, h), position, head_dim, arch.rope_theta)
            keys.append(k)
            values.append(_OPS.matvec(layer.wv, h))
            attended = _OPS.attend(q, keys, values, arch.heads, head_dim)
            x = _OPS.add(x, _OPS.matvec(layer.wo, attended))

            h = _OPS.rmsnorm(x, layer.ffn_norm, arch.norm_eps)
            gated = _OPS.swiglu(_OPS.matvec(layer.w1, h), _OPS.matvec(layer.w3, h))
            x = _OPS.add(x, _OPS.matvec(layer.w2, gated))

        return _OPS.matvec(self._tok_emb, _OPS.rmsnorm(x, self._norm, arch.norm_eps))

    def logits(self, tokens: Sequence[int]) -> list[float]:
        """Logits for the token after ``tokens``.  Mostly here for the tests."""
        cache: list[tuple[list[Any], list[Any]]] = [([], []) for _ in self._layers]
        output: Any = None
        for position, token in enumerate(tokens):
            output = self._forward(token, position, cache)
        if output is None:
            raise ValueError("need at least one token")
        return _OPS.to_list(output)

    # -- generation --------------------------------------------------------

    def fit_prompt(self, tokens: Sequence[int], max_tokens: int = 0) -> list[int]:
        """Trim a prompt so the answer still fits inside the context.

        Rotary embeddings are trained up to ``context`` positions and the
        cache grows by one per token, so prompt and answer share one budget.
        Filling it entirely with prompt would leave room for a single token --
        which is how a model that works on short questions appears to go
        mute on long ones.

        The end is kept: the newest turn is what the answer depends on.
        """
        reserve = max(1, min(int(max_tokens), self.architecture.context // 2))
        room = max(1, self.architecture.context - reserve)
        return list(tokens)[-room:]

    def generate(self, tokens: Sequence[int], *, max_tokens: int = 128,
                 temperature: float = 0.8, top_k: int = 40,
                 stop: Sequence[int] = (), seed: int | None = None
                 ) -> Iterator[int]:
        """Continue a sequence, one token at a time.

        A generator because the chat panel should be able to stream, and
        because a caller that runs out of budget mid-answer can simply stop
        pulling -- no work is done for tokens nobody asked for.
        """
        import random                                          # noqa: PLC0415

        arch = self.architecture
        if not tokens:
            raise ValueError("need at least one token to continue from")
        window = self.fit_prompt(tokens, max_tokens)
        rng = random.Random(seed)
        stops = set(stop)

        cache: list[tuple[list[Any], list[Any]]] = [([], []) for _ in self._layers]
        logits: Any = None
        for position, token in enumerate(window):
            logits = self._forward(token, position, cache)
        position = len(window)

        for _ in range(max_tokens):
            token = self._sample(_OPS.to_list(logits), temperature, top_k, rng)
            yield token
            if token in stops or position >= arch.context:
                return
            logits = self._forward(token, position, cache)
            position += 1

    @staticmethod
    def _sample(logits: Sequence[float], temperature: float, top_k: int,
                rng: Any) -> int:
        if temperature <= 0:
            return max(range(len(logits)), key=logits.__getitem__)

        scaled = [value / temperature for value in logits]
        if 0 < top_k < len(scaled):
            ranked = sorted(range(len(scaled)), key=scaled.__getitem__, reverse=True)
            candidates = ranked[:top_k]
        else:
            candidates = list(range(len(scaled)))

        top = max(scaled[index] for index in candidates)
        weights = [math.exp(scaled[index] - top) for index in candidates]
        total = sum(weights)
        threshold = rng.random() * total
        running = 0.0
        for index, weight in zip(candidates, weights):
            running += weight
            if running >= threshold:
                return index
        return candidates[-1]
