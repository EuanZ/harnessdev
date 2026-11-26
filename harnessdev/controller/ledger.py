"""Run ledger.

Everything a run produces lives under ``runs/<run_id>/`` on the host.

    runs/<run_id>/
        run_config.json            resolved configuration, credentials removed
        events.jsonl               append-only controller log
        llm_requests.jsonl         one line per model request (written by the gateway)
        creator/                   prompt, agent stdout, native session files
        submissions/<sid>/
            meta.json              kind, commit, time, budget position, status
            harness.tar.gz         the frozen snapshot that was evaluated
            <benchmark>/           raw executor and scorer output, per task
            result.json            per-benchmark and per-task scores
        final.json                 the version the creator declared final
        heldout/<sid>/             held-out evaluation of formal versions
        summary.json
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- basics
    def path(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, rel: str, payload: Any) -> Path:
        path = self.path(rel)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        tmp.replace(path)
        return path

    def read_json(self, rel: str, default: Any = None) -> Any:
        path = self.root / rel
        if not path.exists():
            return default
        return json.loads(path.read_text())

    def event(self, name: str, /, **fields: Any) -> None:
        row = {"ts": time.time(), "event": name, **fields}
        with self._lock:
            with self.path("events.jsonl").open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def events(self) -> list[dict[str, Any]]:
        path = self.root / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    # ------------------------------------------------------------ submissions
    def submission_dir(self, sid: str) -> Path:
        path = self.root / "submissions" / sid
        path.mkdir(parents=True, exist_ok=True)
        return path

    def submission(self, sid: str) -> dict[str, Any] | None:
        return self.read_json(f"submissions/{sid}/meta.json")

    def save_submission(self, meta: dict[str, Any]) -> None:
        self.write_json(f"submissions/{meta['id']}/meta.json", meta)

    def result(self, sid: str) -> dict[str, Any] | None:
        return self.read_json(f"submissions/{sid}/result.json")
