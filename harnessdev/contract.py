"""The harness contract.

A harness is a directory with a ``harness/`` Python package and, optionally, a
``requirements.txt``. Everything inside the package is the creator's design.
The entry point is fixed:

    python3 -m harness run --task-json T --model-config C --output-dir O --max-steps N

``T`` (task.json)::

    {"task_id": "...", "domain": "code|data|writing|search",
     "prompt": "<task instruction>", "workdir": "<directory to work in>",
     "output_dir": "O", "max_steps": N, "budget": {"max_steps": N},
     "messages": [...]            # writing only: the conversation so far
    }

``C`` (model_config.json)::

    {"policy": {"max_steps": N},
     "llm": {"provider": "openai_like" | "anthropic_messages",
             "model": "...", "base_url": "...", "api_key": "..."}}

The harness writes into ``O``:

    result.json       {"status": ..., "usage": {"input_tokens": .., "output_tokens": ..}}
    response.md       final answer text (writing, search)
    trajectory.jsonl  one JSON object per step; free-form

Code tasks are scored on the state of ``workdir``; data tasks on the submission
file the instruction names; writing and search tasks on ``response.md``.
"""

from __future__ import annotations

import shlex
from typing import Any
from urllib.parse import urlsplit

HARNESS_DIR = "/opt/harness"


def task_payload(*, task_id: str, domain: str, prompt: str, workdir: str, output_dir: str,
                 max_steps: int, **extra: Any) -> dict[str, Any]:
    return {"task_id": task_id, "domain": domain, "prompt": prompt, "workdir": workdir,
            "output_dir": output_dir, "max_steps": max_steps, "budget": {"max_steps": max_steps},
            **extra}


def model_config(*, model: str, base_url: str, api_key: str, protocol: str, max_steps: int) -> dict[str, Any]:
    provider = "anthropic_messages" if protocol == "anthropic" else "openai_like"
    return {"policy": {"max_steps": max_steps},
            "llm": {"provider": provider, "model": model, "base_url": base_url, "api_key": api_key}}


def no_proxy_prefix(base_url: str) -> str:
    """Shell prefix that keeps the model endpoint off any HTTP proxy the
    container inherits."""
    host = urlsplit(base_url).hostname or ""
    if not host:
        return ""
    return (f'export NO_PROXY="{host}${{NO_PROXY:+,$NO_PROXY}}" '
            f'no_proxy="{host}${{no_proxy:+,$no_proxy}}"; ')


def run_command(*, task_json: str, model_config: str, output_dir: str, max_steps: int,
                model_base_url: str = "") -> str:
    return (f"{no_proxy_prefix(model_base_url)}"
            f"PYTHONPATH={HARNESS_DIR}:$PYTHONPATH python3 -m harness run "
            f"--task-json {shlex.quote(task_json)} --model-config {shlex.quote(model_config)} "
            f"--output-dir {shlex.quote(output_dir)} --max-steps {int(max_steps)}")


def bootstrap_python_command() -> str:
    """Install python3 in task images that ship without it."""
    return (
        "command -v python3 >/dev/null 2>&1 || { "
        "if command -v apt-get >/dev/null 2>&1; then "
        "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && "
        "apt-get install -y -qq --no-install-recommends python3 python3-pip; "
        "elif command -v apk >/dev/null 2>&1; then apk add --no-cache python3 py3-pip; "
        "elif command -v dnf >/dev/null 2>&1; then dnf install -y python3 python3-pip; "
        "elif command -v yum >/dev/null 2>&1; then yum install -y python3 python3-pip; "
        "else echo 'no supported package manager' >&2; exit 127; fi; }"
    )


def install_command() -> str:
    """Install the harness's own dependencies, when it declares any."""
    req = f"{HARNESS_DIR}/requirements.txt"
    return (f"if [ -f {req} ]; then PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install -q -r {req} "
            f"|| python3 -m pip install --user -q -r {req}; fi")
