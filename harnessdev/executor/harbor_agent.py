"""Harbor agent that runs a frozen harness inside a benchmark task container.

Used for every Harbor-format benchmark (Terminal-Bench 2.1, SWE-bench Pro):

    harbor run -d terminal-bench/terminal-bench-2-1 \
        -a harnessdev.executor.harbor_agent:HarnessAgent \
        --ak harness_path=/path/to/harness --ak model=<executor model> \
        --ak model_base_url=<gateway route url> --ak protocol=openai

The harness is uploaded and invoked through its entry point,
``python3 -m harness run``. Task verification is Harbor's own.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from ..contract import HARNESS_DIR, bootstrap_python_command, install_command, model_config, run_command, task_payload

OUT_DIR = "/logs/agent/harness"


class HarnessAgent(BaseAgent):
    SUPPORTS_ATIF: bool = False

    def __init__(self, logs_dir: Path, model_name: str | None = None, *,
                 harness_path: str, model: str = "", model_base_url: str = "",
                 model_api_key: str = "gateway", protocol: str = "openai",
                 domain: str = "code", max_steps: int | str = 500,
                 timeout_sec: int | str = 7200, **kwargs: Any) -> None:
        kwargs.setdefault("skip_model_parsing", True)
        super().__init__(logs_dir=logs_dir, model_name=model_name or model or None, **kwargs)
        self.harness_path = Path(harness_path)
        self.model = model or model_name or ""
        self.model_base_url = model_base_url
        self.model_api_key = model_api_key
        self.protocol = protocol
        self.domain = domain
        self.max_steps = int(max_steps)
        self.timeout_sec = int(timeout_sec)

    @staticmethod
    def name() -> str:
        return "harnessdev"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        if not (self.harness_path / "harness").is_dir():
            raise FileNotFoundError(f"{self.harness_path} has no harness/ package")
        await environment.exec(command=f"mkdir -p {HARNESS_DIR} {OUT_DIR}", user="root")
        await environment.upload_dir(source_dir=self.harness_path, target_dir=HARNESS_DIR)
        boot = await environment.exec(command=bootstrap_python_command(), user="root", timeout_sec=900)
        if boot.return_code != 0:
            raise RuntimeError(f"python3 bootstrap failed: {(boot.stderr or boot.stdout or '')[-800:]}")
        await environment.exec(command=install_command(), timeout_sec=600)

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        pwd = await environment.exec(command="pwd")
        workdir = (pwd.stdout or "").strip().splitlines()[-1] if (pwd.stdout or "").strip() else "/app"
        task = task_payload(task_id="task", domain=self.domain, prompt=instruction, workdir=workdir,
                            output_dir=OUT_DIR, max_steps=self.max_steps,
                              model_base_url=self.model_base_url)
        config = model_config(model=self.model, base_url=self.model_base_url,
                              api_key=self.model_api_key, protocol=self.protocol,
                              max_steps=self.max_steps)
        local = self.logs_dir / "harness_inputs"
        local.mkdir(parents=True, exist_ok=True)
        (local / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2))
        (local / "model_config.json").write_text(json.dumps(config, indent=2))
        await environment.upload_file(source_path=local / "task.json", target_path=f"{OUT_DIR}/task.json")
        await environment.upload_file(source_path=local / "model_config.json",
                                      target_path=f"{OUT_DIR}/model_config.json")

        command = run_command(task_json=f"{OUT_DIR}/task.json", model_config=f"{OUT_DIR}/model_config.json",
                              output_dir=OUT_DIR, max_steps=self.max_steps,
                              model_base_url=self.model_base_url)
        result = await environment.exec(command=f"{command} > {shlex.quote(OUT_DIR)}/stdout.log 2>&1",
                                        cwd=workdir, timeout_sec=self.timeout_sec)
        context.metadata = {"harness_return_code": result.return_code, "workdir": workdir}

        try:
            await environment.download_dir(source_dir=OUT_DIR, target_dir=self.logs_dir / "harness")
        except Exception as exc:  # the trial result still carries the verifier reward
            (self.logs_dir / "harness_download_error.txt").write_text(repr(exc))
        (local / "model_config.json").unlink(missing_ok=True)

        usage = _read_usage(self.logs_dir / "harness")
        if usage:
            context.n_input_tokens = usage.get("input_tokens")
            context.n_output_tokens = usage.get("output_tokens")


def _read_usage(out: Path) -> dict[str, int]:
    """Token usage as the harness reported it in result.json, when present."""
    path = out / "result.json"
    if not path.exists():
        return {}
    try:
        usage = (json.loads(path.read_text()).get("usage") or {})
    except (json.JSONDecodeError, AttributeError):
        return {}
    return {
        "input_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
    }
