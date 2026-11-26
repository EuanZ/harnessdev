from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any


DEFAULT_IGNORE = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    "target",
}


def _ignore_set(ignore: set[str] | None) -> set[str]:
    return set(DEFAULT_IGNORE if ignore is None else ignore)


def list_tree(
    root: str | Path,
    *,
    max_depth: int = 3,
    max_entries: int = 300,
    ignore: set[str] | None = None,
) -> dict[str, Any]:
    base = Path(root).resolve()
    ignored = _ignore_set(ignore)
    entries: list[str] = []
    truncated = False
    for current, dirs, files in os.walk(base):
        rel = Path(current).relative_to(base)
        depth = 0 if str(rel) == "." else len(rel.parts)
        dirs[:] = [d for d in sorted(dirs) if d not in ignored and depth < max_depth]
        names = [(d + "/") for d in dirs] + sorted(files)
        for name in names:
            if name.rstrip("/") in ignored:
                continue
            bare_name = name.rstrip("/")
            shown = Path(rel) / bare_name if str(rel) != "." else Path(bare_name)
            suffix = "/" if name.endswith("/") else ""
            entries.append(str(shown) + suffix)
            if len(entries) >= max_entries:
                truncated = True
                return {"ok": True, "root": str(base), "entries": entries, "truncated": truncated}
    return {"ok": True, "root": str(base), "entries": entries, "truncated": truncated}


def glob_paths(root: str | Path, pattern: str, *, max_matches: int = 200) -> dict[str, Any]:
    base = Path(root).resolve()
    matches: list[str] = []
    for path in base.rglob(pattern):
        if any(part in DEFAULT_IGNORE for part in path.parts):
            continue
        matches.append(str(path.relative_to(base)))
        if len(matches) >= max_matches:
            return {"ok": True, "root": str(base), "pattern": pattern, "matches": matches, "truncated": True}
    return {"ok": True, "root": str(base), "pattern": pattern, "matches": matches, "truncated": False}


def grep(
    root: str | Path,
    pattern: str,
    *,
    include_glob: str | None = None,
    max_matches: int = 200,
    max_file_bytes: int = 1_000_000,
) -> dict[str, Any]:
    base = Path(root).resolve()
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return {"ok": False, "root": str(base), "pattern": pattern, "matches": [], "error": str(exc)}
    matches: list[dict[str, Any]] = []
    for file_path in base.rglob("*"):
        if any(part in DEFAULT_IGNORE for part in file_path.parts):
            continue
        if not file_path.is_file():
            continue
        rel = str(file_path.relative_to(base))
        if include_glob and not fnmatch.fnmatch(rel, include_glob):
            continue
        try:
            if file_path.stat().st_size > max_file_bytes:
                continue
            data = file_path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue
        for line_no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            if regex.search(line):
                matches.append({"path": rel, "line": line_no, "text": line[:1000]})
                if len(matches) >= max_matches:
                    return {"ok": True, "root": str(base), "pattern": pattern, "matches": matches, "truncated": True}
    return {"ok": True, "root": str(base), "pattern": pattern, "matches": matches, "truncated": False}
