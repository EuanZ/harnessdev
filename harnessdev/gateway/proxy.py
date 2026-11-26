"""Model gateway.

Runs on the host. Sandboxes hold a per-role route token; the gateway holds the
provider credentials and forwards each request to the route's upstream.
Every request is appended to ``llm_requests.jsonl`` with its token usage.

URL scheme seen from a sandbox::

    http://<host>:<port>/r/<route_token>/v1/messages          (Anthropic)
    http://<host>:<port>/r/<route_token>/v1/chat/completions  (OpenAI)
    http://<host>:<port>/r/<route_token>/v1/responses         (OpenAI Responses)
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

HOP_HEADERS = {
    "host", "content-length", "connection", "keep-alive", "transfer-encoding",
    "authorization", "x-api-key", "accept-encoding", "proxy-authorization",
}


@dataclass
class Route:
    """One model role (creator, executor, judge) bound to one upstream."""

    role: str
    base_url: str
    api_key: str
    model: str | None = None          # when set, overrides the request's model
    auth: str = "bearer"              # "bearer" | "x-api-key"
    extra_headers: dict[str, str] = field(default_factory=dict)
    token_budget: int | None = None   # total tokens allowed on this route
    tag: str = ""                     # free-form label written to the usage log
    token: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    used_tokens: int = 0
    requests: int = 0


def _usage_from_json(obj: Any) -> dict[str, int]:
    """Extract token counts from an Anthropic or OpenAI style payload."""
    out = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    if not isinstance(obj, dict):
        return out
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        for key in ("message", "response"):
            inner = obj.get(key)
            if isinstance(inner, dict) and isinstance(inner.get("usage"), dict):
                usage = inner["usage"]
                break
    if not isinstance(usage, dict):
        return out
    out["input_tokens"] = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    out["output_tokens"] = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    out["cache_read_tokens"] = int(usage.get("cache_read_input_tokens") or 0)
    out["cache_write_tokens"] = int(usage.get("cache_creation_input_tokens") or 0)
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    if isinstance(details, dict):
        out["cache_read_tokens"] = int(details.get("cached_tokens") or out["cache_read_tokens"])
    return out


def _merge_usage(total: dict[str, int], part: dict[str, int]) -> None:
    # Streaming APIs repeat cumulative counters, so keep the maximum seen.
    for key, value in part.items():
        if value > total.get(key, 0):
            total[key] = value


class Gateway:
    """Threaded reverse proxy with credential injection and usage accounting."""

    def __init__(self, log_path: Path, host: str = "0.0.0.0", port: int = 0, timeout: float = 1800.0):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._routes: dict[str, Route] = {}
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0), follow_redirects=False)
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence default stderr logging
                return

            def do_POST(self) -> None:
                gateway._handle(self)

            def do_GET(self) -> None:
                gateway._handle(self)

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # ------------------------------------------------------------------ routes
    def add_route(self, route: Route) -> Route:
        with self._lock:
            self._routes[route.token] = route
        return route

    def remove_route(self, route: Route) -> None:
        with self._lock:
            self._routes.pop(route.token, None)

    def url_for(self, route: Route, advertise_host: str = "host.docker.internal") -> str:
        return f"http://{advertise_host}:{self.port}/r/{route.token}"

    def usage(self, role: str | None = None, tag: str | None = None) -> dict[str, int]:
        """Aggregate the usage log, optionally filtered by role or tag."""
        total = {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                 "cache_read_tokens": 0, "cache_write_tokens": 0}
        if not self.log_path.exists():
            return total
        for line in self.log_path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if role and row.get("role") != role:
                continue
            if tag and row.get("tag") != tag:
                continue
            total["requests"] += 1
            for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                total[key] += int(row.get(key) or 0)
        return total

    # --------------------------------------------------------------- lifecycle
    def start(self) -> "Gateway":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._client.close()

    # ---------------------------------------------------------------- handling
    def _send_json(self, handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        parts = handler.path.split("/", 3)
        if len(parts) < 4 or parts[1] != "r":
            self._send_json(handler, 404, {"error": {"message": "unknown gateway path"}})
            return
        route = self._routes.get(parts[2])
        if route is None:
            self._send_json(handler, 401, {"error": {"message": "unknown route token"}})
            return
        if route.token_budget is not None and route.used_tokens >= route.token_budget:
            self._send_json(handler, 429, {"error": {
                "type": "budget_exhausted",
                "message": f"token budget for role '{route.role}' is exhausted"}})
            return

        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else b""
        requested_model = None
        if body and route.model or body:
            try:
                payload = json.loads(body)
                requested_model = payload.get("model")
                if route.model:
                    payload["model"] = route.model
                    body = json.dumps(payload).encode()
            except (json.JSONDecodeError, AttributeError):
                pass

        headers = {k: v for k, v in handler.headers.items() if k.lower() not in HOP_HEADERS}
        if route.auth == "x-api-key":
            headers["x-api-key"] = route.api_key
        else:
            headers["Authorization"] = f"Bearer {route.api_key}"
        headers.update(route.extra_headers)
        upstream_path = "/" + parts[3]
        base = route.base_url.rstrip("/")
        # Accept upstream base URLs given with or without a trailing /v1.
        if base.endswith("/v1") and upstream_path.startswith("/v1/"):
            upstream_path = upstream_path[3:]
        url = base + upstream_path

        started = time.time()
        usage: dict[str, int] = {}
        status = 502
        try:
            with self._client.stream(handler.command, url, headers=headers, content=body) as resp:
                status = resp.status_code
                handler.send_response(status)
                ctype = resp.headers.get("content-type", "application/json")
                handler.send_header("Content-Type", ctype)
                handler.send_header("Transfer-Encoding", "chunked")
                handler.end_headers()
                buffer = b""
                is_sse = "text/event-stream" in ctype
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    handler.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    handler.wfile.flush()
                    buffer += chunk
                    if is_sse:
                        *lines, buffer = buffer.split(b"\n")
                        for line in lines:
                            if line.startswith(b"data:"):
                                data = line[5:].strip()
                                if data and data != b"[DONE]":
                                    try:
                                        _merge_usage(usage, _usage_from_json(json.loads(data)))
                                    except json.JSONDecodeError:
                                        pass
                handler.wfile.write(b"0\r\n\r\n")
                if not is_sse:
                    try:
                        _merge_usage(usage, _usage_from_json(json.loads(buffer)))
                    except json.JSONDecodeError:
                        pass
        except (httpx.HTTPError, ConnectionError, BrokenPipeError) as exc:
            if status == 502:
                try:
                    self._send_json(handler, 502, {"error": {"message": f"upstream error: {exc}"}})
                except Exception:
                    pass
        finally:
            spent = sum(usage.get(k, 0) for k in ("input_tokens", "output_tokens"))
            row = {
                "ts": started, "duration_s": round(time.time() - started, 3),
                "role": route.role, "tag": route.tag, "status": status,
                "path": upstream_path, "requested_model": requested_model,
                "model": route.model or requested_model, **usage,
            }
            with self._lock:
                route.used_tokens += spent
                route.requests += 1
                with self.log_path.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
