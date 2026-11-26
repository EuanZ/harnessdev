"""HTTP API between the creator sandbox and the controller.

The sandbox holds a bearer token and the controller URL; the ``hd`` tool is a
thin client of this API.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


class ControlServer:
    def __init__(self, controller: Any, host: str = "0.0.0.0", port: int = 0):
        self.controller = controller
        self.token = secrets.token_urlsafe(24)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def _reply(self, status: int, payload: Any) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _serve(self, method: str) -> None:
                if self.headers.get("Authorization") != f"Bearer {server.token}":
                    self._reply(401, {"ok": False, "reason": "unauthorized"})
                    return
                url = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                body = {}
                if method == "POST":
                    raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                    body = json.loads(raw or b"{}")
                try:
                    self._reply(200, server.dispatch(method, url.path, query, body))
                except KeyError:
                    self._reply(404, {"ok": False, "reason": "unknown_endpoint"})
                except Exception as exc:
                    self._reply(500, {"ok": False, "reason": "controller_error", "detail": repr(exc)})

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

        self._http = ThreadingHTTPServer((host, port), Handler)
        self._http.daemon_threads = True
        self.port = self._http.server_address[1]

    def start(self) -> "ControlServer":
        threading.Thread(target=self._http.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._http.shutdown()
        self._http.server_close()

    def dispatch(self, method: str, path: str, query: dict[str, str], body: dict[str, Any]) -> Any:
        c = self.controller
        routes = {
            ("GET", "/budget"): lambda: c.budget(),
            ("GET", "/evals"): lambda: c.list_evals(),
            ("GET", "/eval"): lambda: c.state["evals"].get(query.get("id", ""), {"ok": False, "reason": "unknown_eval"}),
            ("GET", "/events"): lambda: c.events_since(int(query.get("since", 0))),
            ("GET", "/wait"): lambda: c.wait_events(int(query.get("since", 0)),
                                                    float(query.get("timeout", 3600))),
            ("POST", "/submit"): lambda: c.submit(body["kind"], body.get("commit", "HEAD"),
                                                  bool(body.get("dry_run"))),
            ("POST", "/final"): lambda: c.declare_final(body.get("commit", "HEAD")),
            ("POST", "/selftest"): lambda: c.selftest_ticket(body["benchmark"], list(body["tasks"])),
        }
        return routes[(method, path)]()
