"""MLE-bench, using openai/mle-bench for data preparation, the agent image and grading.

One competition is one task. The harness runs in the upstream ``mlebench-env``
image with the prepared public data mounted read-only at ``/home/data`` and
writes ``/home/submission/submission.csv``; ``mlebench grade-sample`` scores it
against the private split.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..backends.docker import DockerBackend
from . import Benchmark, TaskResult
from .container_runner import run_harness

IMAGE = "mlebench-env"
REPO = "https://github.com/openai/mle-bench"
CACHE = Path.home() / ".cache" / "harnessdev" / "mle-bench"


LEADERBOARDS = "mlebench/competitions/*/leaderboard.csv"   # kept in Git LFS upstream


def _checkout() -> Path:
    """A clone of openai/mle-bench with its leaderboards pulled from Git LFS."""
    repo = CACHE / "repo"
    if not repo.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", REPO, str(repo)], check=True,
                       env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"})
    board = next(repo.glob(LEADERBOARDS), None)
    if board is None or board.read_bytes().startswith(b"version https://git-lfs"):
        if not shutil.which("git-lfs"):
            raise RuntimeError("mle-bench keeps its leaderboards in Git LFS; install git-lfs")
        subprocess.run(["git", "-C", str(repo), "lfs", "install", "--local"], check=True)
        subprocess.run(["git", "-C", str(repo), "lfs", "pull", "--include", LEADERBOARDS], check=True)
    return repo


def _mlebench() -> str:
    """The upstream CLI, installed from the checkout on first use."""
    local = Path(sys.executable).parent / "mlebench"
    if not local.exists():
        repo = str(_checkout())
        pip = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", repo])
        if pip.returncode != 0:     # environments created by uv ship without pip
            subprocess.run(["uv", "pip", "install", "-q", "--python", sys.executable, "-e", repo],
                           check=True)
    if not local.exists():
        raise RuntimeError("could not install mlebench from " + REPO)
    return str(local)


def data_dir() -> Path:
    return Path(os.environ.get("MLEBENCH_DATA_DIR") or Path.home() / ".cache" / "mle-bench" / "data")


class MLEBench(Benchmark):
    name = "mle-bench"
    domain = "data"
    metric = "any_medal_rate"
    task_timeout = 24 * 3600
    sandbox_images = (IMAGE,)

    def sandbox_mounts(self) -> dict[str, str]:
        # the dev competitions, at the same path as on the host
        return {str(data_dir() / t): f"{data_dir() / t}:ro" for t in self.tasks("dev")
                if (data_dir() / t).exists()}

    def sandbox_env(self) -> dict[str, str]:
        return {"MLEBENCH_DATA_DIR": str(data_dir())}

    def prepare(self, task_ids: list[str] | None = None) -> None:
        """Build the agent image and download competitions. Needs Kaggle credentials."""
        backend = DockerBackend()
        if not backend.image_exists(IMAGE):
            repo = _checkout()
            backend.build(repo, IMAGE, dockerfile=repo / "environment" / "Dockerfile",
                          platform="linux/amd64")
        for task_id in task_ids or []:
            if not (data_dir() / task_id / "prepared" / "public").exists():
                subprocess.run([_mlebench(), "prepare", "-c", task_id, "--data-dir", str(data_dir())],
                               check=True)

    def _instructions(self) -> str:
        path = CACHE / "instructions.txt"
        if not path.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(["docker", "run", "--rm", "--entrypoint", "cat", IMAGE,
                                   "/home/instructions.txt"], capture_output=True, text=True, check=True)
            path.write_text(proc.stdout)
        return path.read_text()

    def run(self, harness_dir, task_ids, executor, out_dir, options, judge=None):
        self.prepare(task_ids)
        out_dir = Path(out_dir)
        instructions = self._instructions()
        gpus = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"],
                              capture_output=True, text=True).stdout.find("nvidia") >= 0

        def one(task_id: str) -> TaskResult:
            task_dir = out_dir / task_id
            public = data_dir() / task_id / "prepared" / "public"
            run = run_harness(
                harness_dir=harness_dir, task_id=task_id, domain="data", prompt=instructions,
                executor=executor, out_dir=task_dir, max_steps=options.max_steps,
                timeout=int(self.task_timeout * options.timeout_multiplier), image=IMAGE,
                workdir="/home/agent", mounts={str(public): "/home/data:ro"},
                env={"COMPETITION_ID": task_id}, gpus=gpus,
                collect={"/home/submission/submission.csv": "submission.csv"})
            submission = task_dir / "submission.csv"
            if not submission.exists():
                return TaskResult(task_id=task_id, score=0.0, passed=False,
                                  error=run.error or "no_submission", artifacts=str(task_dir),
                                  usage=run.usage)
            proc = subprocess.run([_mlebench(), "grade-sample", str(submission), task_id,
                                   "--data-dir", str(data_dir())], capture_output=True, text=True)
            report = _last_json(proc.stdout + "\n" + proc.stderr)
            (task_dir / "grading_report.json").write_text(json.dumps(report, indent=2))
            medal = bool(report.get("any_medal"))
            return TaskResult(task_id=task_id, score=1.0 if medal else 0.0, passed=medal,
                              error=run.error, artifacts=str(task_dir), usage=run.usage,
                              details={k: report.get(k) for k in (
                                  "score", "gold_medal", "silver_medal", "bronze_medal",
                                  "above_median", "valid_submission")})

        with ThreadPoolExecutor(max_workers=options.n_concurrent) as pool:
            return list(pool.map(one, task_ids))


def _last_json(text: str) -> dict:
    """The grading report is the last JSON object in mlebench's output."""
    end = text.rfind("}")
    while end != -1:
        depth = 0
        for start in range(end, -1, -1):
            if text[start] == "}":
                depth += 1
            elif text[start] == "{":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break
        end = text.rfind("}", 0, end)
    return {}
