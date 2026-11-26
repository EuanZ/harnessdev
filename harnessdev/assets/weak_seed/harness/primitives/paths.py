from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_path(workdir: str | Path, path: str | Path) -> Path:
    """Resolve relative paths against workdir while preserving absolute paths."""

    root = Path(workdir).expanduser().resolve()
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = root / target
    return target.resolve()


def is_within(path: str | Path, root: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def ensure_parent(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def display_path(path: str | Path, root: str | Path) -> str:
    resolved = Path(path).resolve()
    base = Path(root).resolve()
    try:
        return str(resolved.relative_to(base))
    except ValueError:
        return str(resolved)


def path_info(path: str | Path, root: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    base = Path(root).expanduser().resolve()
    within = is_within(resolved, base)
    warning = ""
    if resolved.is_absolute() and not within:
        warning = "absolute path is outside workdir; keep only if the task explicitly requires it"
    return {
        "path": str(resolved),
        "root": str(base),
        "display_path": display_path(resolved, base),
        "within_root": within,
        "warning": warning,
    }
