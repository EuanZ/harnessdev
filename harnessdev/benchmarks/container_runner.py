"""Run a frozen harness on one task in a fresh container.

Harbor does this for the code benchmarks. The data, writing and search
benchmarks have no task containers of their own, so this module provides the
same isolation for them: one container per task, harness uploaded read-only,
the model reached through the gateway.
"""

from __future__ import annotations

import json
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..backends.docker import DockerBackend
from ..contract import HARNESS_DIR, install_command, model_config, run_command, task_payload
from . import Endpoint

RUNNER_IMAGE = "harnessdev/runner:latest"
OUT = "/hdev/out"


@dataclass
class HarnessRun:
    return_code: int
    out_dir: Path
    response: str
    usage: dict[str, int]
    error: str | None = None


def run_harness(*, harness_dir: Path, task_id: str, domain: str, prompt: str, executor: Endpoint,
                out_dir: Path, max_steps: int, timeout: int, image: str = RUNNER_IMAGE,
                workdir: str = "/workspace", mounts: dict[str, str] | None = None,
                extra_task: dict[str, Any] | None = None, env: dict[str, str] | None = None,
                collect: dict[str, str] | None = None, gpus: bool = False,
                backend: DockerBackend | None = None) -> HarnessRun:
    """Start a container, run the harness once, copy its output directory back.

    ``collect`` maps extra container paths to file names under ``out_dir``.
    """
    backend = backend or DockerBackend()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    label = "".join(c if c.isalnum() else "-" for c in task_id)[:40]
    sandbox = backend.start(image, label=f"task-{label}", mounts=mounts, workdir=workdir, env=env,
                            gpus=gpus)
    error = None
    code = -1
    try:
        sandbox.exec(f"mkdir -p {HARNESS_DIR} {OUT} {workdir}", workdir="/")
        sandbox.copy_contents(harness_dir, HARNESS_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            task = task_payload(task_id=task_id, domain=domain, prompt=prompt, workdir=workdir,
                                output_dir=OUT, max_steps=max_steps, **(extra_task or {}))
            config = model_config(model=executor.model, base_url=executor.base_url,
                                  api_key=executor.api_key, protocol=executor.protocol,
                                  max_steps=max_steps)
            (Path(tmp) / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2))
            (Path(tmp) / "model_config.json").write_text(json.dumps(config, indent=2))
            sandbox.copy_in(Path(tmp) / "task.json", f"{OUT}/task.json")
            sandbox.copy_in(Path(tmp) / "model_config.json", f"{OUT}/model_config.json")
        sandbox.exec(install_command(), timeout=600)
        command = run_command(task_json=f"{OUT}/task.json", model_config=f"{OUT}/model_config.json",
                              output_dir=OUT, max_steps=max_steps,
                              model_base_url=executor.base_url)
        try:
            proc = sandbox.exec(f"timeout {int(timeout)} bash -c {shlex.quote(command)} > {OUT}/stdout.log 2>&1",
                                timeout=timeout + 120)
            code = proc.returncode
            if code == 124:
                error = "timeout"
        except Exception as exc:  # subprocess timeout or docker failure
            error = type(exc).__name__
        sandbox.exec(f"rm -f {OUT}/model_config.json")
        staged = out_dir / "_harness"
        shutil.rmtree(staged, ignore_errors=True)
        try:
            sandbox.copy_out(f"{OUT}/.", staged)  # str path keeps the trailing "/."
        except Exception as exc:
            error = error or f"copy_out:{type(exc).__name__}"
        for src, name in (collect or {}).items():
            try:
                sandbox.copy_out(src, out_dir / name)
            except Exception:
                pass
    finally:
        sandbox.remove()
    harness_out = out_dir / "_harness"
    response = ""
    if (harness_out / "response.md").exists():
        response = (harness_out / "response.md").read_text(errors="replace")
    usage: dict[str, int] = {}
    if (harness_out / "result.json").exists():
        try:
            raw = json.loads((harness_out / "result.json").read_text()).get("usage") or {}
            usage = {"input_tokens": int(raw.get("input_tokens") or raw.get("prompt_tokens") or 0),
                     "output_tokens": int(raw.get("output_tokens") or raw.get("completion_tokens") or 0)}
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            pass
    if code != 0 and error is None:
        error = f"harness_exit_{code}"
    return HarnessRun(return_code=code, out_dir=out_dir, response=response, usage=usage, error=error)
