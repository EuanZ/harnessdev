"""EQ-Bench 3, run through the upstream repository.

EQ-Bench 3 talks to its test model over an OpenAI-compatible chat endpoint.
This adapter serves the frozen harness behind such an endpoint: every chat
request becomes one harness run in a fresh container, and the harness's
``response.md`` is returned as the assistant turn. Scenario simulation,
debriefing and rubric judging are all upstream code.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import Benchmark, Endpoint, RunOptions, TaskResult
from .container_runner import RUNNER_IMAGE, run_harness

REPO = "https://github.com/EQ-bench/eqbench3"
CACHE = Path.home() / ".cache" / "harnessdev" / "eqbench3"
HEADER = re.compile(r"^######## +(\S+)")


def _split_scenarios(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    current, lines = None, []
    for line in text.splitlines(keepends=True):
        match = HEADER.match(line)
        if match:
            if current is not None:
                blocks[current] = "".join(lines)
            current, lines = match.group(1), []
        lines.append(line)
    if current is not None:
        blocks[current] = "".join(lines)
    return blocks


class _HarnessServer:
    """OpenAI-compatible chat endpoint backed by one harness run per request."""

    def __init__(self, harness_dir: Path, executor: Endpoint, out_dir: Path, options: RunOptions,
                 timeout: int):
        counter = itertools.count(1)
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.errors: list[str] = []
        lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                messages = body.get("messages") or []
                prompt = next((m.get("content", "") for m in reversed(messages)
                               if m.get("role") == "user"), "")
                with lock:
                    call = next(counter)
                run = run_harness(
                    harness_dir=harness_dir, task_id=f"eqbench3-call-{call}", domain="writing",
                    prompt=prompt, executor=executor, out_dir=out_dir / "calls" / f"{call:04d}",
                    max_steps=options.max_steps, timeout=timeout, extra_task={"messages": messages})
                with lock:
                    for key in server.usage:
                        server.usage[key] += run.usage.get(key, 0)
                    if run.error:
                        server.errors.append(f"call {call}: {run.error}")
                payload = json.dumps({
                    "id": f"hdev-{call}", "object": "chat.completion", "model": body.get("model"),
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": run.response}}],
                    "usage": {"prompt_tokens": run.usage.get("input_tokens", 0),
                              "completion_tokens": run.usage.get("output_tokens", 0)},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/v1/chat/completions"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class EQBench3(Benchmark):
    name = "eqbench3"
    domain = "writing"
    metric = "rubric_score_0_100"
    sandbox_images = (RUNNER_IMAGE,)
    turn_timeout = 1800

    def prepare(self) -> None:
        if not (CACHE / "repo" / "eqbench3.py").exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--depth", "1", REPO, str(CACHE / "repo")], check=True)
        venv = CACHE / "venv"
        if not (venv / "bin" / "python").exists():
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
            subprocess.run([str(venv / "bin" / "pip"), "install", "-q", "-r",
                            str(CACHE / "repo" / "requirements.txt")], check=True)

    def _scenarios(self) -> dict[str, str]:
        self.prepare()
        return _split_scenarios((CACHE / "repo" / "data" / "scenario_prompts.txt").read_text())

    def tasks(self, split: str) -> list[str]:
        try:
            return super().tasks(split)
        except FileNotFoundError:
            if split != "test":
                raise
            return list(self._scenarios())

    def run(self, harness_dir, task_ids, executor, out_dir, options, judge=None):
        if judge is None:
            raise ValueError("eqbench3 needs models.judge in the experiment file")
        if judge.protocol != "openai":
            raise ValueError("eqbench3 calls its judge over the OpenAI chat API; "
                             "set models.judge.protocol to 'openai'")
        scenarios = self._scenarios()
        out_dir = Path(out_dir).resolve()
        work = out_dir / "eqbench3"
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(CACHE / "repo", work, ignore=shutil.ignore_patterns(".git", "results"))
        # Upstream has no scenario filter flag; a split is a filtered prompts file.
        (work / "data" / "scenario_prompts.txt").write_text(
            "".join(scenarios[t] for t in task_ids))

        server = _HarnessServer(Path(harness_dir), executor, out_dir, options,
                                int(self.turn_timeout * options.timeout_multiplier))
        env = dict(os.environ, TEST_API_URL=server.url, TEST_API_KEY="harness",
                   JUDGE_API_URL=f"{judge.local_url.rstrip('/')}/v1/chat/completions",
                   JUDGE_API_KEY=judge.api_key,
                   REQUEST_TIMEOUT=str(int(self.turn_timeout * options.timeout_multiplier) + 300))
        try:
            with (out_dir / "eqbench3.log").open("w") as log:
                subprocess.run(
                    [str(CACHE / "venv" / "bin" / "python"), "eqbench3.py",
                     "--test-model", "harness", "--model-name", "harness",
                     "--judge-model", judge.model, "--no-elo", "--ignore-canonical",
                     "--iterations", "1", "--threads", str(options.n_concurrent),
                     "--runs-file", str(out_dir / "eqbench3_runs.json")],
                    cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        finally:
            server.stop()
        return self._collect(out_dir, task_ids, server)

    def _collect(self, out_dir: Path, task_ids: list[str], server: _HarnessServer) -> list[TaskResult]:
        runs_path = out_dir / "eqbench3_runs.json"
        runs = json.loads(runs_path.read_text()) if runs_path.exists() else {}
        run = next(iter(runs.values()), {}) if isinstance(runs, dict) else {}
        upstream = (run.get("results") or {}).get("average_rubric_score")
        self._upstream_score = upstream * 5.0 if isinstance(upstream, (int, float)) else None
        tasks = (run.get("scenario_tasks") or {}).get("1") or next(
            iter((run.get("scenario_tasks") or {}).values()), {})
        results = []
        for task_id in task_ids:
            info = tasks.get(task_id) or {}
            scores = [float(v) for v in (info.get("rubric_scores") or {}).values()
                      if isinstance(v, (int, float))]
            results.append(TaskResult(
                task_id=task_id,
                score=round(sum(scores) / len(scores) * 5.0, 4) if scores else None,
                error=None if scores else "not_scored",
                artifacts=str(out_dir),
                details={"rubric_scores": info.get("rubric_scores"), "upstream_status": info.get("status")}))
        if results:
            results[0].usage = dict(server.usage)
        return results

    def aggregate(self, results):
        summary = super().aggregate(results)
        # Report upstream's own aggregate when it produced one.
        if getattr(self, "_upstream_score", None) is not None:
            summary["score"] = round(self._upstream_score, 4)
        return summary
