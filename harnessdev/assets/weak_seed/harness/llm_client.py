"""Runtime LLM access. Reads the endpoint from the model-config file."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def load_model_config(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def is_configured(config: dict[str, Any]) -> bool:
    llm = config.get("llm") or {}
    return bool(llm.get("base_url") and llm.get("model"))


def _post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: int, retries: int) -> dict[str, Any]:
    data = json.dumps(body).encode()
    last = ""
    for attempt in range(retries):
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}"
            if exc.code < 500 and exc.code != 429:
                break
        except Exception as exc:  # network errors are retried
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(last)


def chat_once(messages: list[dict[str, Any]], config: dict[str, Any], *, tools: list[dict[str, Any]] | None = None,
              max_tokens: int = 8192, timeout: int = 1800, retries: int = 6) -> dict[str, Any]:
    """One model call. Returns {"ok", "content", "tool_calls", "usage", "raw", "error"}.

    Messages and tools use the OpenAI chat format; they are translated when the
    provider is ``anthropic_messages``.
    """
    llm = config.get("llm") or {}
    base = str(llm.get("base_url", "")).rstrip("/")
    try:
        if llm.get("provider") == "anthropic_messages":
            system = "\n\n".join(str(m["content"]) for m in messages if m["role"] == "system")
            body: dict[str, Any] = {"model": llm["model"], "max_tokens": max_tokens,
                                    "messages": [m for m in messages if m["role"] != "system"]}
            if system:
                body["system"] = system
            if tools:
                body["tools"] = [{"name": t["function"]["name"],
                                  "description": t["function"].get("description", ""),
                                  "input_schema": t["function"].get("parameters", {"type": "object"})}
                                 for t in tools]
            raw = _post(f"{base}/v1/messages", {"x-api-key": llm.get("api_key", ""),
                                               "anthropic-version": "2023-06-01"}, body, timeout, retries)
            blocks = raw.get("content") or []
            content = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls = [{"id": b["id"], "name": b["name"], "arguments": b.get("input", {})}
                     for b in blocks if b.get("type") == "tool_use"]
            usage = raw.get("usage") or {}
            usage = {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}
        else:
            body = {"model": llm["model"], "messages": messages, "max_tokens": max_tokens}
            if tools:
                body["tools"] = tools
            raw = _post(f"{base}/v1/chat/completions", {"Authorization": f"Bearer {llm.get('api_key', '')}"},
                        body, timeout, retries)
            message = raw["choices"][0]["message"]
            content = message.get("content") or ""
            calls = [{"id": c["id"], "name": c["function"]["name"],
                      "arguments": json.loads(c["function"].get("arguments") or "{}")}
                     for c in message.get("tool_calls") or []]
            usage = raw.get("usage") or {}
            usage = {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}
        return {"ok": True, "content": content, "tool_calls": calls, "usage": usage, "raw": raw, "error": ""}
    except Exception as exc:
        return {"ok": False, "content": "", "tool_calls": [], "usage": {}, "raw": None, "error": str(exc)}
