"""The agent thinking with weights that were made here.

:mod:`chainmind.embedded` runs someone else's model in this process.  This
runs the agent's own: architecture from :mod:`chainmind.nano`, vocabulary
from :mod:`chainmind.tokenizer`, weights from ``training/pretrain.py`` over
a corpus you chose.  There is no step in that chain where a file arrives
from a company.

It presents the same surface as the other two backends -- ``estimate``,
``count_input_tokens``, ``respond`` -- so the kernel, the chat panel and the
ledger cannot tell which one answered, except that the evidence digest names
the weights.  That sameness is the point: the independence question becomes
a choice about which file to put in ``.chainmind/models/``, not a rewrite.

Two honest notes, because a model you trained yourself is easy to
over-trust:

*   **It is small.**  It knows what was in its corpus and nothing else, and
    it has no idea that it does not know.  Treat what it says the way you
    would treat what a very fast, very well-read intern says.
*   **The token counts are exact.**  The tokenizer is right here, so the
    number the chain authorises is the number that gets spent -- no
    over-estimate, no trusting a remote count.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .meter import Meter
from .nano import SUFFIX, NanoModel, WeightsError, backend_name, read_header
from .resources import ResourceKind

__all__ = [
    "NativeModel",
    "NativeUnavailable",
    "discover_native",
    "discover_weights",
    "is_decider",
    "NATIVE_SUFFIXES",
]

#: What one of our own weight files is called.
NATIVE_SUFFIXES = (SUFFIX,)

#: Shared with the other backends; the directory a workspace keeps models in.
MODELS_DIRNAME = "models"

DEFAULT_MAX_TOKENS = 512
DEFAULT_SYSTEM = (
    "تو یک عامل خودمختار هستی که مصرف منابعت روی یک دفترکل ثبت می‌شود. "
    "مستقیم و کامل جواب بده، و کوتاه بنویس: هر توکن از یک بودجهٔ محدود خرج می‌شود."
)


class NativeUnavailable(RuntimeError):
    """No model of our own is available, with a reason a person can act on."""


def discover_weights(root: Path | str) -> list[Path]:
    """Every one of our weight files in a workspace, largest first."""
    directory = Path(root) / MODELS_DIRNAME
    if not directory.is_dir():
        return []
    found = [
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in NATIVE_SUFFIXES
    ]
    return sorted(found, key=lambda path: (-path.stat().st_size, path.name))


def is_decider(path: Path | str) -> bool:
    """Whether a weights file is a decision model rather than a language one.

    Read from the header, so this costs a few kilobytes rather than the
    whole file.
    """
    try:
        header = read_header(path)
    except WeightsError:
        return False
    architecture = header.get("architecture") or {}
    return bool(architecture.get("decisions")
                and (header.get("meta") or {}).get("decision_schema"))


def discover_native(root: Path | str) -> list[Path]:
    """Language models in a workspace, largest first.

    Decision models are excluded even though they are the same file format.
    They have a language-model head and would load and generate -- fluent
    nonsense, because nothing ever trained that head. Offering one where a
    chat model is expected is the kind of silent substitution this project
    should not contain.
    """
    return [path for path in discover_weights(root) if not is_decider(path)]


@dataclass
class NativeModel:
    """A model this project trained, loaded into this process."""

    path: Path | None = None
    workspace: Path | None = None
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = 0.8
    top_k: int = 40
    seed: int | None = None
    system: str = DEFAULT_SYSTEM
    engine: Any = None                 # injectable, so tests need no real weights
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.engine is None:
            self.engine = self._load()
        if self.path is None:
            self.path = getattr(self.engine, "path", None)

    @property
    def answers_questions(self) -> bool:
        """Whether this model was trained on conversations at all.

        A model pretrained on prose alone has never seen ``<|user|>``, so
        feeding it the chat template puts it off its own distribution and
        the output degrades into noise -- which looks like a broken model
        rather than a corpus that contained no conversations. When there
        were none, the prompt is laid out as plain text instead, which is
        the shape this model *was* trained on.
        """
        meta = getattr(self.engine, "meta", None) or {}
        return bool(meta.get("corpus_conversations"))

    def _load(self) -> NanoModel:
        if self.path is None:
            if self.workspace is None:
                raise NativeUnavailable("no weights file and no workspace to look in")
            candidates = discover_native(self.workspace)
            if not candidates:
                raise NativeUnavailable(
                    f"no {SUFFIX} file in {Path(self.workspace) / MODELS_DIRNAME}.\n"
                    "That directory holds the model this project trains itself. To "
                    "make one:\n"
                    "    python3 training/pretrain.py --corpus <your text> "
                    f"--workspace {Path(self.workspace)}\n"
                    "See training/README.md for what it costs and what to expect."
                )
            self.path = candidates[0]
        if is_decider(self.path):
            raise NativeUnavailable(
                f"{Path(self.path).name} is a decision model, not a language "
                "model: it chooses between typed options and was never trained "
                "to write. Use `chainmind decide` instead."
            )
        try:
            return NanoModel.load(Path(self.path))
        except WeightsError as exc:
            raise NativeUnavailable(str(exc)) from exc

    # -- identity ----------------------------------------------------------

    @property
    def model(self) -> str:
        return Path(self.path).stem if self.path else "native"

    @property
    def fingerprint(self) -> str:
        return str(getattr(self.engine, "fingerprint", ""))

    @property
    def tokenizer(self) -> Any:
        return self.engine.tokenizer

    def describe(self) -> str:
        arch = self.engine.architecture
        shape = "chat" if self.answers_questions else "continuation only"
        return (
            f"{self.model} (own model, {arch.parameters / 1e6:.1f}M parameters, "
            f"{shape}, {backend_name()}, {self.fingerprint[:12]})"
        )

    # -- shared surface ----------------------------------------------------

    @staticmethod
    def conversation(prompt: str, history: Sequence[Mapping[str, str]] | None = None
                     ) -> list[dict[str, str]]:
        messages = [
            {"role": str(entry["role"]), "content": str(entry["content"])}
            for entry in (history or [])
            if entry.get("content")
        ]
        messages.append({"role": "user", "content": prompt})
        return messages

    def _prompt_ids(self, prompt: str,
                    history: Sequence[Mapping[str, str]] | None,
                    system: str | None, max_tokens: int | None = None) -> list[int]:
        """Exactly the ids the model will be fed, so the count is the truth.

        Trimming happens here rather than inside generation, because the
        number the chain authorises has to be the number that gets spent.
        An estimate made against an untrimmed prompt would be an
        *under*-estimate once the answer's reserve is taken out -- and an
        under-estimate is how an action slips past the budget check.
        """
        instructions = self.system if system is None else system
        messages = [{"role": "system", "content": instructions},
                    *self.conversation(prompt, history)]
        if self.answers_questions:
            ids = self.tokenizer.apply_chat_template(
                messages, add_generation_prompt=True
            )
        else:
            # The system text is laid down as prose rather than dropped: a
            # prose model may not follow it, but anything put in front of the
            # model is input the ledger has to charge for. Dropping it would
            # make memory recalled into the system prompt free.
            ids = self.tokenizer.encode(
                "\n".join(message["content"] for message in messages) + "\n"
            )
        return self.engine.fit_prompt(ids, int(max_tokens or self.max_tokens))

    def count_input_tokens(self, prompt: str,
                           history: Sequence[Mapping[str, str]] | None = None,
                           system: str | None = None,
                           max_tokens: int | None = None) -> int:
        return len(self._prompt_ids(prompt, history, system, max_tokens))

    def estimate(self, prompt: str, max_tokens: int | None = None,
                 history: Sequence[Mapping[str, str]] | None = None,
                 system: str | None = None) -> dict[str, int]:
        return {
            ResourceKind.LLM_INPUT_TOKENS.value: self.count_input_tokens(
                prompt, history, system, max_tokens
            ),
            ResourceKind.LLM_OUTPUT_TOKENS.value: int(max_tokens or self.max_tokens),
        }

    # -- the call ----------------------------------------------------------

    def respond(self, meter: Meter, prompt: str, max_tokens: int | None = None,
                system: str | None = None,
                history: Sequence[Mapping[str, str]] | None = None) -> dict[str, Any]:
        limit = int(max_tokens or self.max_tokens)
        ids = self._prompt_ids(prompt, history, system, limit)
        end = self.tokenizer.special("<|end|>")

        started = time.monotonic()
        produced: list[int] = []
        try:
            for token in self.engine.generate(
                ids, max_tokens=limit, temperature=self.temperature,
                top_k=self.top_k, stop=(end,), seed=self.seed, **dict(self.extra),
            ):
                if token == end:
                    break
                produced.append(token)
        except WeightsError:
            raise
        except Exception as exc:
            raise NativeUnavailable(f"{type(exc).__name__}: {exc}") from exc

        stop_reason = "length" if len(produced) >= limit else "stop"
        text = self.tokenizer.decode(produced)

        # Counted by the thing that did the work, with its own tokenizer, in
        # this process -- the same class the other in-process backend reports.
        meter.record(ResourceKind.LLM_INPUT_TOKENS, len(ids), source="provider")
        meter.record(ResourceKind.LLM_OUTPUT_TOKENS, len(produced), source="provider")
        meter.note("model", self.model)
        meter.note("runtime", "native")
        meter.note("weights", self.fingerprint)
        meter.note("stop_reason", stop_reason)

        return {
            "text": text,
            "refused": False,
            "truncated": stop_reason == "length",
            "input_tokens": len(ids),
            "output_tokens": len(produced),
            "stop_reason": stop_reason,
            "model": self.model,
            "weights": self.fingerprint,
            "runtime": "native",
            "seconds": round(time.monotonic() - started, 3),
        }
