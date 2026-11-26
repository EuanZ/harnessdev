"""Evaluate one frozen harness on one or more benchmarks.

The same function serves formal evaluation on the host and ``hd selftest``
inside the creator sandbox; only the Docker daemon and the gateway route differ.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..benchmarks import Endpoint, RunOptions, get_benchmark, write_results


class HarnessInvalid(ValueError):
    pass


def validate_harness(harness_dir: Path) -> None:
    """The one structural requirement: ``python -m harness`` must be importable."""
    if not (Path(harness_dir) / "harness" / "__main__.py").exists():
        raise HarnessInvalid("the snapshot has no harness/__main__.py, so `python -m harness` cannot run")


def evaluate(*, harness_dir: Path, benchmark: str, task_ids: list[str], out_dir: Path,
             executor: Endpoint, judge: Endpoint | None, options: RunOptions) -> dict[str, Any]:
    """Run one benchmark and write ``out_dir/result.json``."""
    validate_harness(harness_dir)
    bench = get_benchmark(benchmark)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    results = bench.run(Path(harness_dir), task_ids, executor, out_dir, options, judge=judge)
    payload = write_results(out_dir, bench, results)
    payload["duration_s"] = round(time.time() - started, 1)
    (out_dir / "result.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def combined_score(per_benchmark: dict[str, dict[str, Any]]) -> float | None:
    scores = [r["score"] for r in per_benchmark.values() if r.get("score") is not None]
    return round(sum(scores) / len(scores), 6) if scores and len(scores) == len(per_benchmark) else None
