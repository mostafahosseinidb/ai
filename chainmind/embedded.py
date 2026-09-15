"""Inference inside this process, with no separate program to run.

The HTTP backend talks to a runtime someone else installed and keeps
running -- Ollama, llama.cpp's server, and so on.  That is a runtime, not a
provider: it holds no account and calls nobody.  But it is still a second
program, and an agent whose thinking lives in another process is not quite
the self-contained thing this project is after.

This backend removes it.  The model is a GGUF file sitting in the workspace,
loaded into this process by ``llama-cpp-python``, and the agent owns it the
way it owns its ledger and its keys.  Nothing listens on a port, nothing has
to be started first, and the file can be copied, fine-tuned, or handed to
someone on a USB stick.

Two things get better as a side effect, and both matter here:

*   **Exact token counts.**  The tokenizer is right there, so the estimate
    the chain authorises is the real number rather than a pessimistic guess.
    Ollama's HTTP API offers no counting endpoint at all.
*   **Provenance of the weights.**  The file is hashed, and that hash goes
    into the evidence digest of every usage record.  An auditor can tell
    which model produced which answer -- which becomes the point once you
    start fine-tuning your own.

``llama-cpp-python`` is an optional dependency and the only one inference
needs.  Without it this module explains how to install it rather than
failing obscurely.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .meter import Meter
from .resources import ResourceKind

__all__ = [
    "EmbeddedModel",
    "EmbeddedUnavailable",
    "LlamaEngine",
    "discover_models",
    "model_fingerprint",
    "MODEL_SUFFIXES",
]

#: What a weights file looks like.  GGUF is llama.cpp's format; the older
#: GGML one is not supported and has not been produced for years.
MODEL_SUFFIXES = (".gguf",)

#: Where a workspace keeps its weights.
MODELS_DIRNAME = "models"

DEFAULT_CONTEXT = 8192
DEFAULT_MAX_TOKENS = 2_000


class EmbeddedUnavailable(RuntimeError):
    """The in-process engine cannot be used, with a reason a person can act on."""


class LlamaEngine(Protocol):
    """The slice of ``llama_cpp.Llama`` this module actually uses.

    Declared as a protocol so the wiring around it -- discovery, hashing,
    metering, budget checks -- is testable without a multi-gigabyte download
    and a compiled extension.
    """

    def tokenize(self, text: bytes, add_bos: bool = ..., special: bool = ...) -> list[int]: ...

    def create_chat_completion(self, messages: Sequence[Mapping[str, str]],
                               **kwargs: Any) -> Mapping[str, Any]: ...


# --------------------------------------------------------------------------
# Finding and identifying weights
# --------------------------------------------------------------------------

def discover_models(root: Path | str) -> list[Path]:
    """Weight files in a workspace, largest first.

    Largest first because when someone has both a small test model and the
    real one, the real one is what they meant.
    """
    directory = Path(root) / MODELS_DIRNAME
    if not directory.is_dir():
        return []
    found = [
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in MODEL_SUFFIXES
    ]
    return sorted(found, key=lambda path: (-path.stat().st_size, path.name))


def model_fingerprint(path: Path | str) -> str:
    """A content hash of a weights file, cached beside it.

    Hashing several gigabytes on every start would make the agent slow to
    wake for no benefit, so the result is cached and invalidated by size and
    modification time -- which is the pair that changes when a file is
    replaced or retrained.
    """
    path = Path(path)
    stat = path.stat()
    sidecar = path.with_suffix(path.suffix + ".fingerprint")

    if sidecar.exists():
        try:
            cached = json.loads(sidecar.read_text(encoding="utf-8"))
            if cached.get("size") == stat.st_size and cached.get("mtime") == int(stat.st_mtime):
                return str(cached["sha256"])
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    fingerprint = digest.hexdigest()

    try:
        sidecar.write_text(json.dumps({
            "sha256": fingerprint, "size": stat.st_size, "mtime": int(stat.st_mtime),
        }), encoding="utf-8")
    except OSError:
        pass          # a read-only models directory is not a failure
    return fingerprint


def _load_llama(path: Path, *, context: int, threads: int | None,
                gpu_layers: int) -> LlamaEngine:
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise EmbeddedUnavailable(
            "in-process inference needs llama-cpp-python:\n"
            "    pip install 'chainmind[embedded]'\n"
            "it compiles llama.cpp, so the first install is slow; for GPU support see\n"
            "the CMAKE_ARGS on that project's README."
        ) from exc

    try:
        return Llama(
            model_path=str(path),
            n_ctx=context,
            n_threads=threads or (os.cpu_count() or 4),
            n_gpu_layers=gpu_layers,
            verbose=False,
        )
    except Exception as exc:
        raise EmbeddedUnavailable(f"could not load {path.name}: {exc}") from exc


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------

@dataclass
class EmbeddedModel:
    """A model this process owns outright.

    Presents the same surface as the HTTP-backed one, so the kernel, the chat
    panel and the CLI cannot tell them apart -- only the ledger notices, and
    only because the evidence digest now names the weights.
    """

    path: Path | None = None
    workspace: Path | None = None
    context: int = DEFAULT_CONTEXT
    max_tokens: int = DEFAULT_MAX_TOKENS
    threads: int | None = None
    gpu_layers: int = 0
    temperature: float = 0.7
    system: str = (
        "You are an autonomous agent whose resource consumption is recorded on a "
        "public ledger. Answer the task directly and completely. Prefer being "
        "concise over being exhaustive: every token you produce is paid for out "
        "of a finite budget."
    )
    engine: Any = None                 # injectable; built on first use otherwise
    fingerprint: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path is None:
            if self.workspace is None:
                raise EmbeddedUnavailable("no model file and no workspace to look in")
            candidates = discover_models(self.workspace)
            if not candidates:
                raise EmbeddedUnavailable(
                    f"no .gguf file in {Path(self.workspace) / MODELS_DIRNAME}.\n"
                    "Put one there -- any GGUF build of an open model works, and it is\n"
                    "yours once it is on your disk. Then run `chainmind runtime` again."
                )
            self.path = candidates[0]
        self.path = Path(self.path)
        if not self.path.is_file():
            raise EmbeddedUnavailable(f"{self.path} is not a file")
        if not self.fingerprint:
            self.fingerprint = model_fingerprint(self.path)

    # -- identity ----------------------------------------------------------

    @property
    def model(self) -> str:
        return self.path.stem if self.path else "embedded"

    def describe(self) -> str:
        return f"{self.model} (embedded, {self.fingerprint[:12]})"

    def _engine(self) -> LlamaEngine:
        if self.engine is None:
            self.engine = _load_llama(
                self.path, context=self.context, threads=self.threads,
                gpu_layers=self.gpu_layers,
            )
        return self.engine

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

    @staticmethod
    def approximate_tokens(text: str) -> int:
        return max(1, len(text) // 3)

    def count_input_tokens(self, prompt: str,
                           history: Sequence[Mapping[str, str]] | None = None,
                           system: str | None = None) -> int:
        """The exact number, because the tokenizer is in this process.

        If tokenising fails for any reason the fallback over-estimates on
        purpose: an under-estimate would let the turn slip past the budget
        check the chain is about to make.
        """
        messages = self.conversation(prompt, history)
        instructions = self.system if system is None else system
        text = instructions + "\n" + "\n".join(message["content"] for message in messages)
        try:
            return len(self._engine().tokenize(text.encode("utf-8"), add_bos=True))
        except (EmbeddedUnavailable, Exception):
            return self.approximate_tokens(text)

    def estimate(self, prompt: str, max_tokens: int | None = None,
                 history: Sequence[Mapping[str, str]] | None = None,
                 system: str | None = None) -> dict[str, int]:
        return {
            ResourceKind.LLM_INPUT_TOKENS.value: self.count_input_tokens(
                prompt, history, system
            ),
            ResourceKind.LLM_OUTPUT_TOKENS.value: int(max_tokens or self.max_tokens),
        }

    # -- the call ----------------------------------------------------------

    def respond(self, meter: Meter, prompt: str, max_tokens: int | None = None,
                system: str | None = None,
                history: Sequence[Mapping[str, str]] | None = None) -> dict[str, Any]:
        instructions = system if system is not None else self.system
        messages = [{"role": "system", "content": instructions},
                    *self.conversation(prompt, history)]
        limit = int(max_tokens or self.max_tokens)

        try:
            response = self._engine().create_chat_completion(
                messages=messages,
                max_tokens=limit,
                temperature=self.temperature,
                **dict(self.extra),
            )
        except EmbeddedUnavailable:
            raise
        except Exception as exc:
            raise EmbeddedUnavailable(f"{type(exc).__name__}: {exc}") from exc

        text, input_tokens, output_tokens, stop_reason = self._parse(response)

        # The counts come from the engine's own accounting rather than from
        # this module's guess, so they keep the `provider` provenance class --
        # see chainmind/local.py for why that class means what it means.
        meter.record(ResourceKind.LLM_INPUT_TOKENS, input_tokens, source="provider")
        meter.record(ResourceKind.LLM_OUTPUT_TOKENS, output_tokens, source="provider")
        meter.note("model", self.model)
        meter.note("runtime", "embedded")
        # The weights are part of the evidence: two answers from two different
        # fine-tunes should not look identical to an auditor.
        meter.note("weights", self.fingerprint)
        meter.note("stop_reason", stop_reason)

        return {
            "text": text,
            "refused": False,
            "truncated": stop_reason == "length",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
            "model": self.model,
            "weights": self.fingerprint,
            "runtime": "embedded",
        }

    def _parse(self, response: Mapping[str, Any]) -> tuple[str, int, int, str]:
        choices = response.get("choices") or []
        if not choices:
            raise EmbeddedUnavailable("the engine returned no choices")
        message = choices[0].get("message") or {}
        usage = response.get("usage") or {}
        return (
            str(message.get("content", "")),
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            str(choices[0].get("finish_reason") or "stop"),
        )
