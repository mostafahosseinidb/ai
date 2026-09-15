"""A stand-in for Ollama / llama.cpp, so the local path is tested over real HTTP."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeRuntime:
    """Serves the two dialects the local backend speaks."""

    def __init__(self, dialect: str = "ollama", *, answer: str = "پاسخ محلی",
                 prompt_tokens: int = 120, completion_tokens: int = 40,
                 models: list[str] | None = None, tokenize: bool = False) -> None:
        self.dialect = dialect
        self.answer = answer
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.models = models if models is not None else ["qwen2.5:7b", "llama3.1"]
        self.tokenize = tokenize
        self.requests: list[dict] = []
        self.paths: list[str] = []
        runtime = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, payload, status=200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                runtime.paths.append(self.path)
                if runtime.dialect == "ollama" and self.path == "/api/tags":
                    self._send({"models": [{"name": name} for name in runtime.models]})
                elif runtime.dialect == "openai" and self.path == "/v1/models":
                    self._send({"data": [{"id": name} for name in runtime.models]})
                else:
                    self._send({"error": "not found"}, 404)

            def do_POST(self):
                runtime.paths.append(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                runtime.requests.append(body)

                if self.path == "/tokenize":
                    if not runtime.tokenize:
                        self._send({"error": "not found"}, 404)
                        return
                    self._send({"tokens": list(range(runtime.prompt_tokens))})
                elif self.path == "/api/chat":
                    self._send({
                        "model": body.get("model"),
                        "message": {"role": "assistant", "content": runtime.answer},
                        "prompt_eval_count": runtime.prompt_tokens,
                        "eval_count": runtime.completion_tokens,
                        "done_reason": "stop",
                    })
                elif self.path == "/v1/chat/completions":
                    self._send({
                        "model": body.get("model"),
                        "choices": [{
                            "message": {"role": "assistant", "content": runtime.answer},
                            "finish_reason": "stop",
                        }],
                        "usage": {
                            "prompt_tokens": runtime.prompt_tokens,
                            "completion_tokens": runtime.completion_tokens,
                        },
                    })
                else:
                    self._send({"error": "not found"}, 404)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeRuntime":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"
