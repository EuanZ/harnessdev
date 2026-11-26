"""Minimal chat client the host uses for LLM judges. Goes through the gateway."""

from __future__ import annotations

import time
from typing import Any

import httpx


def chat(base_url: str, model: str, messages: list[dict[str, str]], *, protocol: str = "openai",
         api_key: str = "gateway", max_tokens: int = 2048, retries: int = 5,
         timeout: float = 600.0) -> str:
    """Return the assistant text for one chat call."""
    base = base_url.rstrip("/")
    if protocol == "anthropic":
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens,
                                "messages": [m for m in messages if m["role"] != "system"]}
        if system:
            body["system"] = system
        url, headers = f"{base}/v1/messages", {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
        url, headers = f"{base}/v1/chat/completions", {"Authorization": f"Bearer {api_key}"}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise httpx.HTTPStatusError(resp.text[:300], request=resp.request, response=resp)
            resp.raise_for_status()
            data = resp.json()
            if protocol == "anthropic":
                return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            return data["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last = exc
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"judge call failed after {retries} attempts: {last}")
