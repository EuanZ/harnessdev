from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .files import write_text
from .process import run_command


def _is_git_repo(workdir: Path) -> bool:
    return (workdir / ".git").exists()


def git_status(workdir: str | Path) -> dict[str, Any]:
    root = Path(workdir).resolve()
    if not _is_git_repo(root):
        return {"ok": False, "git_available": True, "is_git_repo": False, "status": "", "error": "not a git repo"}
    result = run_command("git status --short", cwd=root, timeout_seconds=30)
    result.update({"git_available": True, "is_git_repo": True, "status": result.get("stdout", "")})
    return result


def git_diff(workdir: str | Path, *, binary: bool = True) -> dict[str, Any]:
    root = Path(workdir).resolve()
    if not _is_git_repo(root):
        return {"ok": False, "git_available": True, "is_git_repo": False, "diff": "", "error": "not a git repo"}
    cmd = "git diff --binary" if binary else "git diff"
    result = run_command(cmd, cwd=root, timeout_seconds=120, max_output_bytes=20_000_000)
    result.update({"git_available": True, "is_git_repo": True, "diff": result.get("stdout", "")})
    return result


def changed_files(workdir: str | Path) -> list[str]:
    root = Path(workdir).resolve()
    if not _is_git_repo(root):
        return []
    result = run_command("git diff --name-only", cwd=root, timeout_seconds=30)
    if not result.get("ok"):
        return []
    return [line for line in str(result.get("stdout") or "").splitlines() if line.strip()]


def write_patch_artifacts(workdir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    root = Path(workdir).resolve()
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    patch_path = out / "patch.diff"
    changed_path = out / "changed_files.json"
    diff_result = git_diff(root, binary=True)
    patch = str(diff_result.get("diff") or "")
    files = changed_files(root)
    write_text(patch_path, patch)
    changed_path.write_text(json.dumps({"changed_files": files}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "patch_path": str(patch_path),
        "patch_chars": len(patch),
        "patch_is_empty": len(patch) == 0,
        "changed_files_path": str(changed_path),
        "changed_files": files,
        "git_available": True,
        "is_git_repo": bool(diff_result.get("is_git_repo")),
        "error": "" if diff_result.get("is_git_repo") else str(diff_result.get("error") or ""),
    }
