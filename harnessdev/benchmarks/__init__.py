"""Benchmark adapters.

Each adapter wires one public benchmark's own runner and scorer to the harness
contract. An adapter does three things: list the task ids of a split, run a
frozen harness on a set of tasks, and return one ``TaskResult`` per task.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFESTS = Path(__file__).resolve().parent.parent / "manifests"

DOMAINS: dict[str, list[str]] = {
    "code": ["swebench-pro", "terminal-bench"],
    "data": ["mle-bench"],
    "writing": ["eqbench3"],
    "search": ["browsecomp"],
}


@dataclass
class Endpoint:
    """A model as a sandbox reaches it: a gateway route."""

    model: str
    base_url: str
    protocol: str = "openai"
    api_key: str = "gateway"
    host_url: str | None = None           # the same route as the host process reaches it

    @property
    def local_url(self) -> str:
        return self.host_url or self.base_url


@dataclass
class RunOptions:
    n_concurrent: int = 4
    max_steps: int = 500
    timeout_multiplier: float = 1.0
    environment: str = "docker"
    attempts: int = 1
    task_env: dict[str, str] = field(default_factory=dict)


@dataclass
class TaskResult:
    task_id: str
    score: float | None                   # None: the task did not produce a valid score
    passed: bool | None = None
    error: str | None = None
    artifacts: str | None = None          # directory with raw executor and scorer output
    usage: dict[str, int] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


class Benchmark:
    name: str = ""
    domain: str = ""
    metric: str = "resolved_rate"
    sandbox_images: tuple[str, ...] = ()   # task images the creator's self-tests start

    def sandbox_mounts(self) -> dict[str, str]:
        """Host paths the creator sandbox needs for self-tests, as host -> container."""
        return {}

    def sandbox_env(self) -> dict[str, str]:
        return {}

    def splits(self) -> list[str]:
        base = MANIFESTS / self.name
        return sorted(p.stem for p in base.glob("*.txt")) if base.exists() else []

    def tasks(self, split: str) -> list[str]:
        path = MANIFESTS / self.name / f"{split}.txt"
        if not path.exists():
            raise FileNotFoundError(f"{self.name} has no split '{split}' ({path})")
        return [line.strip() for line in path.read_text().splitlines()
                if line.strip() and not line.startswith("#")]

    def prepare(self) -> None:
        """Fetch datasets and images. Safe to call repeatedly."""

    def run(self, harness_dir: Path, task_ids: list[str], executor: Endpoint, out_dir: Path,
            options: RunOptions, judge: Endpoint | None = None) -> list[TaskResult]:
        raise NotImplementedError

    def aggregate(self, results: list[TaskResult]) -> dict[str, Any]:
        scored = [r.score if r.score is not None else 0.0 for r in results]
        return {
            "benchmark": self.name, "metric": self.metric, "n_tasks": len(results),
            "n_errors": sum(1 for r in results if r.error),
            "score": round(sum(scored) / len(scored), 6) if scored else None,
        }


def write_results(out_dir: Path, benchmark: Benchmark, results: list[TaskResult]) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = benchmark.aggregate(results)
    payload = {**summary, "tasks": [asdict(r) for r in results]}
    (out_dir / "result.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def get_benchmark(name: str) -> Benchmark:
    from .browsecomp import BrowseComp
    from .eqbench3 import EQBench3
    from .harbor_bench import SWEBenchPro, TerminalBench
    from .mlebench import MLEBench
    registry = {b.name: b for b in (SWEBenchPro, TerminalBench, MLEBench, EQBench3, BrowseComp)}
    if name not in registry:
        raise KeyError(f"unknown benchmark '{name}'; choose from {sorted(registry)}")
    return registry[name]()


def all_benchmarks() -> list[Benchmark]:
    return [get_benchmark(n) for names in DOMAINS.values() for n in names]
