"""A fake OpenAI/Anthropic-compatible model server for offline tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockModel:
    def __init__(self, reply: str = "mock reply"):
        self.requests: list[dict] = []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                mock.requests.append({"path": self.path, "body": body,
                                      "auth": self.headers.get("Authorization") or self.headers.get("x-api-key")})
                if self.path.endswith("/messages"):
                    payload = {"id": "m", "type": "message", "role": "assistant", "model": body.get("model"),
                               "content": [{"type": "text", "text": reply}],
                               "usage": {"input_tokens": 11, "output_tokens": 7}}
                else:
                    payload = {"id": "c", "object": "chat.completion", "model": body.get("model"),
                               "choices": [{"index": 0, "finish_reason": "stop",
                                            "message": {"role": "assistant", "content": reply}}],
                               "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()


if __name__ == "__main__":
    import time
    server = MockModel()
    print(server.url, flush=True)
    while True:
        time.sleep(3600)
