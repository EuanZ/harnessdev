"""Weak seed: a runnable compatibility layer, not a task-solving agent.

It parses the task and model configuration, issues one connectivity probe to
the runtime LLM, and writes the result, trajectory, log and patch artifacts.
``primitives/`` holds passive file, search, process and git helpers; nothing
here calls them in a loop. ``response.md`` is left empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import llm_client
from .io_contract import append_jsonl, ensure_output_dir, now_ms, relpath, write_json, write_text
from .primitives.git_ops import write_patch_artifacts


def _text_field(task: dict[str, Any]) -> str:
    for key in ("prompt", "instruction", "task", "description", "issue", "question"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _workdir(task: dict[str, Any]) -> Path:
    for key in ("workdir", "work_dir", "workspace", "repo_path", "repository"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).resolve()
    return Path.cwd().resolve()


def run(task: dict[str, Any], model_config: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    out = ensure_output_dir(output_dir)
    trajectory = out / "trajectory.jsonl"
    response_path = out / "response.md"
    prompt = _text_field(task)
    workdir = _workdir(task)
    append_jsonl(trajectory, {"ts_ms": now_ms(), "phase": "start", "status": "partial", "workdir": str(workdir)})

    configured = llm_client.is_configured(model_config)
    probe: dict[str, Any] = {"ok": False, "error": "not configured"}
    if configured:
        messages = task.get("messages") or [{"role": "user", "content": prompt or "The task text is empty."}]
        probe = llm_client.chat_once(messages, model_config)
        append_jsonl(trajectory, {
            "ts_ms": now_ms(), "phase": "single_llm_probe", "ok": bool(probe.get("ok")),
            "usage": probe.get("usage") or {}, "error": probe.get("error") or "",
            "content": probe.get("content") or "",
        })

    patch = write_patch_artifacts(workdir, out)
    write_text(out / "stdout.log", "")
    write_text(out / "stderr.log", "")
    write_text(response_path, "")

    result = {
        "status": "partial",
        "summary": "weak seed ran; no task-solving policy is implemented",
        "trajectory": relpath(trajectory, out),
        "response": relpath(response_path, out),
        "patch_path": relpath(out / "patch.diff", out),
        "patch_chars": patch["patch_chars"],
        "patch_is_empty": patch["patch_is_empty"],
        "changed_files": patch["changed_files"],
        "usage": probe.get("usage") or {},
        "metrics": {"llm_calls": 1 if probe.get("ok") else 0},
        "errors": [probe.get("error", "llm error")] if configured and not probe.get("ok") else [],
    }
    write_json(out / "result.json", result)
    append_jsonl(trajectory, {"ts_ms": now_ms(), "phase": "finish", "status": result["status"]})
    return result
