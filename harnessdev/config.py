"""Experiment configuration.

An experiment YAML names the research question, the domain, the three model
roles and the budgets. ``${VAR}`` in a model block is expanded from the
environment when the file is loaded, so keys stay out of the file.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    pass


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ConfigError(f"environment variable {name} is not set")
            return os.environ[name]
        return _VAR.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


@dataclass
class ModelConfig:
    """One model role. ``protocol`` is the wire API the upstream speaks."""

    model: str
    api_base_url: str
    api_key: str
    protocol: str = "openai"          # "openai" | "anthropic"
    token_budget: int | None = None
    auth: str | None = None           # "bearer" | "x-api-key"; default follows the protocol

    @classmethod
    def load(cls, raw: dict[str, Any], role: str) -> "ModelConfig":
        raw = _expand(raw or {})
        missing = [k for k in ("model", "api_base_url", "api_key") if not raw.get(k)]
        if missing:
            raise ConfigError(f"models.{role} is missing {', '.join(missing)}")
        if raw.get("protocol", "openai") not in ("openai", "anthropic"):
            raise ConfigError(f"models.{role}.protocol must be 'openai' or 'anthropic'")
        return cls(**{k: raw[k] for k in ("model", "api_base_url", "api_key", "protocol", "token_budget", "auth")
                      if k in raw})


@dataclass
class CreatorConfig:
    agent: str = "claude-code"        # "claude-code" | "codex"
    agent_version: str | None = None
    timeout: int = 6 * 3600           # wall-clock seconds for the creator session
    effort: str | None = None
    image: str = "harnessdev/creator:latest"
    cpus: float | None = None
    memory: str | None = None
    nested_docker: bool = True        # give the sandbox its own Docker daemon for self-tests
    max_selftests_per_task: int | None = 6


@dataclass
class EvolveConfig:
    """RQ2 protocol. Defaults are the paper's setting."""

    max_pairs: int = 10               # billed full evaluations
    probes_per_round: int = 2
    probe_size: int = 5               # tasks per benchmark in one probe
    serial: bool = False              # True: block the creator until feedback is delivered
    feedback_split: str = "feedback"
    heldout_split: str = "heldout"
    heldout: str = "all"              # held-out evaluation: "all" official versions | "final" | "none"
    parallel_evals: int = 2           # benchmark legs evaluated at a time


@dataclass
class ExecutorConfig:
    n_concurrent: int = 4
    max_steps: int = 500
    timeout_multiplier: float = 1.0
    environment: str = "docker"       # Harbor environment type: docker | daytona | modal | e2b
    attempts: int = 1
    task_env: dict[str, str] = field(default_factory=dict)   # e.g. search API keys for task containers


@dataclass
class Experiment:
    name: str
    rq: str                           # "rq1" | "rq2"
    domain: str                       # "code" | "data" | "writing" | "search"
    benchmarks: list[str]
    creator_model: ModelConfig
    executor_model: ModelConfig
    judge_model: ModelConfig | None = None
    creator: CreatorConfig = field(default_factory=CreatorConfig)
    executor: ExecutorConfig = field(default_factory=ExecutorConfig)
    evolve: EvolveConfig = field(default_factory=EvolveConfig)
    seed_harness: str | None = None   # path to H0 (rq2) or a custom weak seed (rq1)
    limit: int | None = None          # cap tasks per split; for smoke runs
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Experiment":
        path = Path(path)
        raw = yaml.safe_load(path.read_text()) or {}
        for key in ("rq", "domain", "models"):
            if key not in raw:
                raise ConfigError(f"{path}: missing top-level '{key}'")
        rq = str(raw["rq"]).lower()
        if rq not in ("rq1", "rq2"):
            raise ConfigError("rq must be 'rq1' or 'rq2'")
        from .benchmarks import DOMAINS
        domain = raw["domain"]
        if domain not in DOMAINS:
            raise ConfigError(f"domain must be one of {sorted(DOMAINS)}")
        models = raw["models"]
        judge = ModelConfig.load(models["judge"], "judge") if models.get("judge") else None
        return cls(
            name=raw.get("name", path.stem),
            rq=rq,
            domain=domain,
            benchmarks=list(raw.get("benchmarks") or DOMAINS[domain]),
            creator_model=ModelConfig.load(models.get("creator"), "creator"),
            executor_model=ModelConfig.load(models.get("executor"), "executor"),
            judge_model=judge,
            creator=CreatorConfig(**(raw.get("creator") or {})),
            executor=ExecutorConfig(**_expand(raw.get("executor") or {})),
            evolve=EvolveConfig(**(raw.get("evolve") or {})),
            seed_harness=raw.get("seed_harness"),
            limit=raw.get("limit"),
            path=path,
        )

    def redacted(self) -> dict[str, Any]:
        """Resolved configuration with credentials removed, for the run ledger."""
        def model(m: ModelConfig | None) -> dict[str, Any] | None:
            if m is None:
                return None
            return {"model": m.model, "api_base_url": m.api_base_url, "protocol": m.protocol,
                    "token_budget": m.token_budget}
        return {
            "name": self.name, "rq": self.rq, "domain": self.domain, "benchmarks": self.benchmarks,
            "models": {"creator": model(self.creator_model), "executor": model(self.executor_model),
                       "judge": model(self.judge_model)},
            "creator": vars(self.creator), "executor": vars(self.executor), "evolve": vars(self.evolve),
            "seed_harness": self.seed_harness, "limit": self.limit,
        }
