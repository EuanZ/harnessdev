from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import ensure_parent


def _looks_binary(path: Path, sample_size: int = 4096) -> bool:
    try:
        sample = path.read_bytes()[:sample_size]
    except OSError:
        return False
    return b"\x00" in sample


def read_text(
    path: str | Path,
    *,
    offset: int = 1,
    limit: int | None = None,
    max_bytes: int = 60000,
    line_numbers: bool = False,
) -> dict[str, Any]:
    target = Path(path)
    result: dict[str, Any] = {"ok": False, "path": str(target), "text": "", "error": ""}
    if not target.exists():
        result["error"] = "file does not exist"
        return result
    if not target.is_file():
        result["error"] = "path is not a file"
        return result
    if _looks_binary(target):
        result["error"] = "binary file refused"
        return result
    try:
        data = target.read_bytes()
    except OSError as exc:
        result["error"] = str(exc)
        return result
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(offset or 1))
    selected = lines[start - 1 :]
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    if line_numbers:
        selected = [f"{i}: {line}" for i, line in enumerate(selected, start=start)]
    result.update(
        {
            "ok": True,
            "text": "\n".join(selected),
            "bytes": min(len(data), max_bytes),
            "truncated": truncated,
            "line_start": start,
            "line_count": len(selected),
        }
    )
    return result


def write_text(path: str | Path, content: str, *, append: bool = False) -> dict[str, Any]:
    target = Path(path)
    ensure_parent(target)
    before = target.read_text(encoding="utf-8", errors="replace") if target.exists() and not append else None
    try:
        if append:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "path": str(target), "changed": False, "error": str(exc)}
    changed = append or before != content
    return {"ok": True, "path": str(target), "bytes": len(content.encode("utf-8")), "changed": changed, "error": ""}


def replace_text(path: str | Path, old: str, new: str, *, count: int = 1) -> dict[str, Any]:
    target = Path(path)
    if not old:
        return {"ok": False, "path": str(target), "changed": False, "error": "old text is empty"}
    if not target.exists() or not target.is_file():
        return {"ok": False, "path": str(target), "changed": False, "error": "file does not exist"}
    if _looks_binary(target):
        return {"ok": False, "path": str(target), "changed": False, "error": "binary file refused"}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "path": str(target), "changed": False, "error": str(exc)}
    occurrences = text.count(old)
    if occurrences == 0:
        return {"ok": False, "path": str(target), "changed": False, "matches": 0, "error": "old text not found"}
    new_text = text.replace(old, new, count)
    written = write_text(target, new_text)
    written.update({"matches": occurrences, "replacements": min(occurrences, count)})
    return written


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, obj: Any) -> dict[str, Any]:
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
