"""Code benchmarks, run through Harbor.

Harbor is the official runner of Terminal-Bench 2.x and ships the SWE-bench Pro
adapter. It owns task containers, timeouts and verification; this module only
selects tasks, points Harbor at the harness agent and reads the trial results.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from . import Benchmark, TaskResult

AGENT = "harnessdev.executor.harbor_agent:HarnessAgent"


def _harbor_bin() -> str:
    local = Path(sys.executable).parent / "harbor"
    if local.exists():
        return str(local)
    found = shutil.which("harbor")
    if not found:
        raise RuntimeError("harbor is not installed; run `pip install 'harnessdev[code]'`")
    return found


class HarborBenchmark(Benchmark):
    domain = "code"
    dataset = ""                 # Harbor registry identifier
    agent_timeout_sec = 7200

    def _source(self) -> list[str]:
        """Registry dataset by default; a local directory of Harbor tasks when
        ``HDEV_DATASET_<NAME>`` points at one (offline mirrors, private tasks)."""
        override = os.environ.get("HDEV_DATASET_" + self.name.upper().replace("-", "_"))
        return ["-p", override] if override else ["-d", self.dataset]

    def _filter(self, task_id: str) -> str:
        """Harbor's ``-i`` pattern for one manifest id."""
        return task_id if "/" in task_id else f"*{task_id}"

    def run(self, harness_dir, task_ids, executor, out_dir, options, judge=None):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        job = f"job-{uuid.uuid4().hex[:8]}"
        cmd = [
            _harbor_bin(), "run", *self._source(), "-a", AGENT,
            "--ak", f"harness_path={Path(harness_dir).resolve()}",
            "--ak", f"model={executor.model}",
            "--ak", f"model_base_url={executor.base_url}",
            "--ak", f"model_api_key={executor.api_key}",
            "--ak", f"protocol={executor.protocol}",
            "--ak", f"max_steps={options.max_steps}",
            "--ak", f"timeout_sec={int(self.agent_timeout_sec * options.timeout_multiplier)}",
            "-e", options.environment, "-n", str(options.n_concurrent),
            "-k", str(options.attempts), "-o", str(out_dir), "--job-name", job,
            "--timeout-multiplier", str(options.timeout_multiplier), "-q",
        ]
        for task_id in task_ids:
            cmd += ["-i", self._filter(task_id)]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(Path(__file__).resolve().parents[2]), env.get("PYTHONPATH", "")])
        with (out_dir / "harbor.log").open("w") as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
        return self._collect(out_dir / job, task_ids)

    def _collect(self, job_dir: Path, task_ids: list[str]) -> list[TaskResult]:
        by_task: dict[str, list[TaskResult]] = {}
        for path in sorted(job_dir.glob("*/result.json")):
            try:
                trial = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            name = str(trial.get("task_name") or "")
            task_id = _match(name, task_ids)
            rewards = ((trial.get("verifier_result") or {}).get("rewards") or {})
            reward = rewards.get("reward", next(iter(rewards.values()), None)) if rewards else None
            exception = trial.get("exception_info")
            agent = trial.get("agent_result") or {}
            by_task.setdefault(task_id, []).append(TaskResult(
                task_id=task_id,
                score=float(reward) if reward is not None else None,
                passed=(float(reward) >= 1.0) if reward is not None else None,
                error=(exception or {}).get("exception_type") if exception else None,
                artifacts=str(path.parent),
                usage={"input_tokens": int(agent.get("n_input_tokens") or 0),
                       "output_tokens": int(agent.get("n_output_tokens") or 0)},
                details={"trial_name": trial.get("trial_name"), "rewards": rewards},
            ))
        results = []
        for task_id in task_ids:
            trials = by_task.get(task_id)
            if not trials:
                results.append(TaskResult(task_id=task_id, score=None, error="no_trial_result",
                                          artifacts=str(job_dir)))
                continue
            scores = [t.score or 0.0 for t in trials]
            first = trials[0]
            first.score = sum(scores) / len(scores)
            first.details["n_trials"] = len(trials)
            results.append(first)
        return results


def _match(task_name: str, task_ids: list[str]) -> str:
    """Harbor reports ``org/name``; manifests may hold either form."""
    if task_name in task_ids:
        return task_name
    short = task_name.rsplit("/", 1)[-1].lower()
    for task_id in task_ids:
        if task_id.rsplit("/", 1)[-1].lower() == short:
            return task_id
    return task_name


class TerminalBench(HarborBenchmark):
    """Terminal-Bench 2.1: 89 terminal tasks, verified by each task's own tests."""

    name = "terminal-bench"
    dataset = "terminal-bench/terminal-bench-2-1"
    metric = "resolved_rate"


class SWEBenchPro(HarborBenchmark):
    """SWE-bench Pro public set (730 instances), through Harbor's adapter.

    Task images are linux/amd64.
    """

    name = "swebench-pro"
    dataset = "scale-ai/swe-bench-pro"
    metric = "resolved_rate"

    def _filter(self, task_id: str) -> str:
        # manifests keep the upstream instance ids; Harbor's task names are their lowercase
        return super()._filter(task_id.lower())
