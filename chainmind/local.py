"""Running the agent's own model, on the agent's own machine.

The Claude backend made the project useful and made it dependent: no key, no
network, no money -- no thought.  For a project whose premise is an agent
that owns its resources, borrowing its mind from a company is a real hole.

This module closes it.  It talks to an inference runtime already running on
the machine -- Ollama, llama.cpp's server, LM Studio, vLLM -- over plain HTTP
with nothing but the standard library.  The independent path therefore has
*no* third-party dependency at all, while the hosted path needs an SDK.

Two dialects cover essentially every local runtime:

*   **Ollama** (``/api/chat``), which reports ``prompt_eval_count`` and
    ``eval_count``.
*   **OpenAI-compatible** (``/v1/chat/completions``), spoken by llama.cpp's
    server, vLLM, LM Studio and most others, which reports ``usage``.

A word on provenance.  Token counts from a local runtime are recorded as
``provider``, the same class as a hosted API, because the property that
class describes still holds: the number comes from a separate process and
the tool cannot quietly shrink it.  What differs is *whose* process it is --
here the trust is in your own machine rather than in a company.  That is a
better position to be in, not a worse one, but it is not the same thing as
proof, and the ledger should not pretend otherwise.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .meter import Meter
from .resources import ResourceKind

__all__ = [
    "LocalModel",
    "LocalRuntimeUnavailable",
    "discover_runtime",
    "KNOWN_ENDPOINTS",
    "DEFAULT_LOCAL_MODEL",
]

#: Where local runtimes usually listen, in the order we look.
KNOWN_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("http://127.0.0.1:11434", "ollama"),      # Ollama
    ("http://127.0.0.1:8080", "openai"),       # llama.cpp server
    ("http://127.0.0.1:1234", "openai"),       # LM Studio
    ("http://127.0.0.1:8000", "openai"),       # vLLM
)

#: Used when the runtime does not tell us which model to pick.
DEFAULT_LOCAL_MODEL = "llama3.1"

PROBE_TIMEOUT = 1.5
REQUEST_TIMEOUT = 600.0


class LocalRuntimeUnavailable(RuntimeError):
    """No local inference runtime answered, or the one that did failed."""


def _opener() -> urllib.request.OpenerDirector:
    """A plain opener that ignores any configured proxy.

    A loopback request must not be sent to an HTTP proxy; in a container with
    ``HTTPS_PROXY`` set, the default opener would try, and the agent's own
    model would appear to be unreachable.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(url: str, payload: Mapping[str, Any] | None = None, *,
             timeout: float = REQUEST_TIMEOUT) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST" if data else "GET",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with _opener().open(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        raise LocalRuntimeUnavailable(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise LocalRuntimeUnavailable(f"{url} is not reachable: {exc}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise LocalRuntimeUnavailable(f"{url} did not return JSON") from exc


def discover_runtime(endpoints: Sequence[tuple[str, str]] | None = None
                     ) -> tuple[str, str, list[str]]:
    """Find a runtime that is already listening.

    Returns ``(base_url, dialect, model_names)``.  Raises if nothing answers,
    which is the honest outcome: this module starts no servers and downloads
    no weights.
    """
    override = os.environ.get("CHAINMIND_LOCAL_URL")
    candidates: Sequence[tuple[str, str]]
    if override:
        dialect = os.environ.get("CHAINMIND_LOCAL_DIALECT", "")
        candidates = [(override.rstrip("/"), dialect)] if dialect else (
            (override.rstrip("/"), "ollama"), (override.rstrip("/"), "openai"),
        )
    else:
        candidates = endpoints if endpoints is not None else KNOWN_ENDPOINTS

    tried: list[str] = []
    for base, dialect in candidates:
        probe = f"{base}/api/tags" if dialect == "ollama" else f"{base}/v1/models"
        try:
            payload = _request(probe, timeout=PROBE_TIMEOUT)
        except LocalRuntimeUnavailable:
            tried.append(f"{base} ({dialect})")
            continue
        return base, dialect, _model_names(payload, dialect)

    raise LocalRuntimeUnavailable(
        "no local inference runtime is listening. Start one first, for example:\n"
        "    ollama serve && ollama pull llama3.1\n"
        "or point CHAINMIND_LOCAL_URL at an existing server.\n"
        f"tried: {', '.join(tried) if tried else 'nothing'}"
    )


def _model_names(payload: Any, dialect: str) -> list[str]:
    try:
        if dialect == "ollama":
            return [entry["name"] for entry in payload.get("models", []) if entry.get("name")]
        return [entry["id"] for entry in payload.get("data", []) if entry.get("id")]
    except (AttributeError, TypeError):
        return []


@dataclass
class LocalModel:
    """A model the agent runs itself.

    The interface matches :class:`chainmind.models.ClaudeModel` exactly, so
    the kernel, the chat panel and the CLI cannot tell them apart.
    """

    model: str = ""
    base_url: str = ""
    dialect: str = ""
    max_tokens: int = 2_000
    system: str = (
        "You are an autonomous agent whose resource consumption is recorded on a "
        "public ledger. Answer the task directly and completely. Prefer being "
        "concise over being exhaustive: every token you produce is paid for out "
        "of a finite budget."
    )
    temperature: float = 0.7
    effort: str = ""              # accepted and ignored; local runtimes have no effort knob
    extra: Mapping[str, Any] = field(default_factory=dict)
    available_models: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.base_url or not self.dialect:
            base, dialect, names = discover_runtime()
            self.base_url = self.base_url or base
            self.dialect = self.dialect or dialect
            self.available_models = self.available_models or names
        if not self.model:
            self.model = (
                os.environ.get("CHAINMIND_LOCAL_MODEL")
                or (self.available_models[0] if self.available_models else DEFAULT_LOCAL_MODEL)
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

    @staticmethod
    def approximate_tokens(text: str) -> int:
        return max(1, len(text) // 3)

    def count_input_tokens(self, prompt: str,
                           history: Sequence[Mapping[str, str]] | None = None) -> int:
        """Count the turn's input tokens, exactly where the runtime allows it.

        llama.cpp exposes ``/tokenize``; Ollama has no counting endpoint at
        all.  Where an exact count is not available this over-estimates on
        purpose -- an under-estimate would let the agent slip past its own
        budget check.
        """
        messages = self.conversation(prompt, history)
        text = self.system + "\n" + "\n".join(m["content"] for m in messages)
        if self.dialect == "openai":
            try:
                payload = _request(
                    f"{self.base_url}/tokenize", {"content": text}, timeout=PROBE_TIMEOUT
                )
                tokens = payload.get("tokens")
                if isinstance(tokens, list) and tokens:
                    return len(tokens)
            except LocalRuntimeUnavailable:
                pass
        return self.approximate_tokens(text)

    def estimate(self, prompt: str, max_tokens: int | None = None,
                 history: Sequence[Mapping[str, str]] | None = None) -> dict[str, int]:
        return {
            ResourceKind.LLM_INPUT_TOKENS.value: self.count_input_tokens(prompt, history),
            ResourceKind.LLM_OUTPUT_TOKENS.value: int(max_tokens or self.max_tokens),
        }

    # -- the call ----------------------------------------------------------

    def respond(self, meter: Meter, prompt: str, max_tokens: int | None = None,
                system: str | None = None,
                history: Sequence[Mapping[str, str]] | None = None) -> dict[str, Any]:
        messages = self.conversation(prompt, history)
        limit = int(max_tokens or self.max_tokens)
        instructions = system if system is not None else self.system

        if self.dialect == "ollama":
            url = f"{self.base_url}/api/chat"
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [{"role": "system", "content": instructions}, *messages],
                "stream": False,
                "options": {"num_predict": limit, "temperature": self.temperature},
                **dict(self.extra),
            }
        else:
            url = f"{self.base_url}/v1/chat/completions"
            payload = {
                "model": self.model,
                "messages": [{"role": "system", "content": instructions}, *messages],
                "stream": False,
                "max_tokens": limit,
                "temperature": self.temperature,
                **dict(self.extra),
            }

        response = _request(url, payload)
        text, input_tokens, output_tokens, stop_reason = self._parse(response)

        # The runtime's own counts, from a separate process.
        meter.record(ResourceKind.LLM_INPUT_TOKENS, input_tokens, source="provider")
        meter.record(ResourceKind.LLM_OUTPUT_TOKENS, output_tokens, source="provider")
        meter.note("model", self.model)
        meter.note("runtime", f"{self.dialect} @ {self.base_url}")
        meter.note("stop_reason", stop_reason)

        return {
            "text": text,
            "refused": False,
            "truncated": stop_reason in ("length", "max_tokens"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
            "model": self.model,
            "runtime": self.dialect,
        }

    def _parse(self, response: Any) -> tuple[str, int, int, str]:
        if not isinstance(response, Mapping):
            raise LocalRuntimeUnavailable("the runtime returned an unexpected shape")

        if self.dialect == "ollama":
            message = response.get("message") or {}
            text = str(message.get("content", ""))
            return (
                text,
                int(response.get("prompt_eval_count") or 0),
                int(response.get("eval_count") or 0),
                str(response.get("done_reason") or "stop"),
            )

        choices = response.get("choices") or []
        if not choices:
            raise LocalRuntimeUnavailable("the runtime returned no choices")
        message = choices[0].get("message") or {}
        usage = response.get("usage") or {}
        return (
            str(message.get("content", "")),
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            str(choices[0].get("finish_reason") or "stop"),
        )

    def describe(self) -> str:
        return f"{self.model} ({self.dialect} @ {self.base_url})"
