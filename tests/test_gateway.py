import json
import time

import httpx

from harnessdev.gateway.proxy import Gateway, Route
from tests.mock_model import MockModel


def test_gateway_injects_credentials_and_logs_usage(tmp_path):
    mock = MockModel()
    gateway = Gateway(tmp_path / "llm_requests.jsonl", host="127.0.0.1").start()
    route = gateway.add_route(Route(role="executor", base_url=mock.url, api_key="sk-real",
                                    model="served-model", tag="ev001"))
    url = gateway.url_for(route, "127.0.0.1")
    resp = httpx.post(f"{url}/v1/chat/completions", headers={"Authorization": "Bearer gateway"},
                      json={"model": "whatever", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "mock reply"
    seen = mock.requests[0]
    assert seen["auth"] == "Bearer sk-real"
    assert seen["body"]["model"] == "served-model"
    log = tmp_path / "llm_requests.jsonl"
    for _ in range(50):          # the row is written right after the response is flushed
        if log.exists():
            break
        time.sleep(0.05)
    row = json.loads(log.read_text().splitlines()[0])
    assert (row["role"], row["tag"], row["input_tokens"], row["output_tokens"]) == ("executor", "ev001", 11, 7)
    assert gateway.usage(tag="ev001")["requests"] == 1
    gateway.stop()
    mock.stop()


def test_gateway_rejects_unknown_token_and_enforces_budget(tmp_path):
    mock = MockModel()
    gateway = Gateway(tmp_path / "log.jsonl", host="127.0.0.1").start()
    base = f"http://127.0.0.1:{gateway.port}"
    assert httpx.post(f"{base}/r/nope/v1/messages", json={}).status_code == 401
    route = gateway.add_route(Route(role="creator", base_url=mock.url, api_key="k", auth="x-api-key",
                                    token_budget=10))
    url = gateway.url_for(route, "127.0.0.1")
    body = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]}
    assert httpx.post(f"{url}/v1/messages", json=body).status_code == 200
    assert mock.requests[0]["auth"] == "k"
    time.sleep(0.3)
    assert httpx.post(f"{url}/v1/messages", json=body).status_code == 429   # 18 tokens used > 10
    gateway.stop()
    mock.stop()
