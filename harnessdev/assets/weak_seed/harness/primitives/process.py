from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any


def _cap(text: str, max_bytes: int) -> tuple[str, bool]:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= max_bytes:
        return text, False
    return data[:max_bytes].decode("utf-8", errors="replace"), True


def run_command(
    command: str | list[str],
    *,
    cwd: str | Path,
    timeout_seconds: int = 120,
    max_output_bytes: int = 60000,
    env: dict[str, str] | None = None,
    shell: bool = True,
) -> dict[str, Any]:
    start = time.monotonic()
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})
    timed_out = False
    run_command_value: str | list[str] = command
    if shell and isinstance(command, list):
        run_command_value = " ".join(shlex.quote(str(part)) for part in command)
    try:
        completed = subprocess.run(
            run_command_value,
            cwd=Path(cwd),
            env=merged_env,
            shell=shell,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
    except Exception as exc:
        elapsed = time.monotonic() - start
        return {
            "ok": False,
            "exit_code": -1,
            "timed_out": False,
            "elapsed_seconds": round(elapsed, 3),
            "stdout": "",
            "stderr": "",
            "command": command if isinstance(command, str) else " ".join(str(part) for part in command),
            "cwd": str(Path(cwd)),
            "error": str(exc),
        }
    stdout, stdout_truncated = _cap(stdout, max_output_bytes)
    stderr, stderr_truncated = _cap(stderr, max_output_bytes)
    elapsed = time.monotonic() - start
    return {
        "ok": exit_code == 0 and not timed_out,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "elapsed_seconds": round(elapsed, 3),
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "command": command if isinstance(command, str) else " ".join(str(part) for part in command),
        "cwd": str(Path(cwd)),
        "error": "",
    }
