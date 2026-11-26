"""The two experiment flows.

RQ1, creation: the creator starts from the weak seed, builds a harness with
self-tests on public dev tasks, and the final workspace is evaluated once on
the test split.

RQ2, evolution: the creator starts from H0 with its full feedback, spends a
fixed budget of evaluation pairs and probes, declares a final version, and the
official versions are then evaluated on the held-out split.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..benchmarks import get_benchmark
from ..config import Experiment
from .core import ASSETS, WORKSPACE, Controller

PROMPTS = ASSETS / "prompts"


def _environment_doc(exp: Experiment, rq2: bool) -> str:
    lines = [
        "# Environment",
        "",
        "You are root in a Linux container with internet access. Install anything you need.",
        f"The workspace is `{WORKSPACE}`, a git repository whose root holds the `harness/` package.",
    ]
    if exp.creator.nested_docker:
        lines.append("`docker` is available and talks to a private daemon; `hd selftest` uses it to start "
                     "task containers.")
    lines += [
        "",
        "## Runtime LLM",
        "",
        f"The harness is evaluated with the runtime LLM `{exp.executor_model.model}` "
        f"(`{exp.executor_model.protocol}` protocol), reached through the model-config file passed to "
        "`python -m harness run`. Self-tests use the same model.",
        f"Each task allows at most {exp.executor.max_steps} steps.",
        "",
        "## Benchmarks",
        "",
    ]
    for name in exp.benchmarks:
        bench = get_benchmark(name)
        doc = (bench.__doc__ or "").strip().splitlines()[0] if bench.__doc__ else ""
        lines.append(f"- `{name}` ({bench.metric}). {doc}")
        try:
            dev = bench.tasks("dev")
            lines.append(f"  Public dev tasks: {', '.join(f'`{t}`' for t in dev)}")
        except FileNotFoundError:
            pass
    if exp.executor.task_env:
        lines += ["", "## Extra environment in task containers", "",
                  ", ".join(f"`{k}`" for k in exp.executor.task_env)]
    lines += ["", "## Tools", "", "```", (ASSETS / "hd_help.txt").read_text().strip(), "```"]
    if not rq2:
        limit = exp.creator.max_selftests_per_task
        lines += ["", f"Self-test allowance: {limit if limit is not None else 'unlimited'} runs per dev task."]
    return "\n".join(lines) + "\n"


def run_create(exp: Experiment, run_dir: Path, *, keep_sandbox: bool = False,
               skip_eval: bool = False) -> dict[str, Any]:
    """RQ1."""
    ctl = Controller(exp, run_dir, keep_sandbox=keep_sandbox)
    try:
        seed = Path(exp.seed_harness) if exp.seed_harness else ASSETS / "weak_seed"
        limit = exp.creator.max_selftests_per_task
        task = (PROMPTS / f"task_{exp.domain}.md").read_text().replace(
            "{max_selftests}", str(limit) if limit is not None else "unlimited")
        system = (PROMPTS / "system.md").read_text()
        ctl.start_sandbox(seed, {"TASK.md": task, "ENVIRONMENT.md": _environment_doc(exp, rq2=False)})
        attempts = {"n": 0}

        def on_exit(code: int) -> str | None:
            # restart a crashed agent process, up to three times
            attempts["n"] += 1
            if code == 0 or attempts["n"] > 3:
                return None
            return ("The previous agent process exited abnormally. Your workspace is intact. "
                    "Continue the task in TASK.md.")

        ctl.run_creator(system + "\n\n---\n\n" + task, on_exit=on_exit)
        ctl.sandbox.exec(f"cd {WORKSPACE} && git add -A && "
                         "(git diff --cached --quiet || git commit -q -m 'final workspace state')")
        commit, harness_dir = ctl.freeze("HEAD", "final")
        ctl.ledger.save_submission({"id": "final", "kind": "final", "commit": commit,
                                    "ts": time.time(), "seq": 1})
        summary: dict[str, Any] = {"rq": "rq1", "final_commit": commit}
        if not skip_eval:
            summary["test"] = ctl.evaluate_dir(harness_dir, "test", "test")
        ctl.ledger.write_json("summary.json", summary)
        return summary
    finally:
        ctl.close()


def run_evolve(exp: Experiment, run_dir: Path, *, keep_sandbox: bool = False) -> dict[str, Any]:
    """RQ2."""
    if not exp.seed_harness:
        raise ValueError("rq2 needs seed_harness: the H0 harness directory to evolve")
    ctl = Controller(exp, run_dir, keep_sandbox=keep_sandbox)
    ev = exp.evolve
    try:
        ctl.start_sandbox(Path(exp.seed_harness), {"ENVIRONMENT.md": _environment_doc(exp, rq2=True)})
        # H0 baseline: a full evaluation on every benchmark, exempt from the budget.
        commit, harness_dir = ctl.freeze("HEAD", "h0")
        with ctl.lock:
            h0_evals = ctl._launch("h0", commit, harness_dir, "h0")
            ctl.ledger.save_submission({"id": "h0", "kind": "h0", "commit": commit, "ts": time.time(),
                                        "eval_ids": h0_evals, "seq": 0})
        ctl.wait_idle()

        facts = "\n".join(
            f"- `{b}`: {len(ctl._tasks(b, ev.feedback_split))} tasks, 1 trial, "
            f"per-task limit {exp.executor.max_steps} steps" for b in exp.benchmarks)
        fill = {
            "{workspace}": WORKSPACE, "{benchmark_list}": ", ".join(f"`{b}`" for b in exp.benchmarks),
            "{max_pairs}": str(ev.max_pairs), "{probes_per_round}": str(ev.probes_per_round),
            "{probe_size}": str(ev.probe_size), "{timeout_hours}": f"{exp.creator.timeout / 3600:g}",
            "{h0_evals}": ", ".join(h0_evals), "{benchmark_facts}": facts,
            "{parallel_evals}": str(ev.parallel_evals), "{domain}": exp.domain,
        }
        prompt = (PROMPTS / "evolve_system.md").read_text() + "\n\n---\n\n# Goal\n\n" + \
            (PROMPTS / "evolve_goal.md").read_text()
        for key, value in fill.items():
            prompt = prompt.replace(key, value)

        idle = {"n": 0, "seq": ctl.state["seq"]}

        def on_exit(code: int) -> str | None:
            if ctl.state["final"]:
                return None
            running = ctl.budget()["evals_running"]
            if running:
                ctl.wait_events(ctl.state["seq"], timeout=24 * 3600)
            if ctl.state["seq"] == idle["seq"] and not running:
                idle["n"] += 1
                if idle["n"] > 2:
                    return None
            else:
                idle["n"] = 0
            idle["seq"] = ctl.state["seq"]
            evals = [{k: e[k] for k in ("id", "kind", "status", "score")} for e in ctl.list_evals()]
            return ("Factual snapshot from the controller.\n"
                    f"budget: {ctl.budget()}\nevals: {evals}\n"
                    "No final version has been declared. The run ends with `hd declare-final`.")

        ctl.run_creator(prompt, on_exit=on_exit)
        ctl.sandbox.unpause()
        ctl.wait_idle()

        official = ctl.official_versions()
        final = ctl.state["final"] or {"commit": None, "declared": False}
        summary: dict[str, Any] = {
            "rq": "rq2", "h0_commit": ctl.state["h0_commit"], "final": final,
            "budget": ctl.budget(),
            "versions": [{"commit": c, "feedback": {b: e["score"] for b, e in legs.items()}}
                         for c, legs in official.items()],
        }
        # Held-out evaluation.
        targets = {"all": list(official), "final": [final["commit"]] if final["commit"] else [],
                   "none": []}[ev.heldout]
        for commit in targets:
            sub = next(p for p in ctl.state["pairs"] if p["commit"] == commit)
            result = ctl.evaluate_dir(ctl.ledger.submission_dir(sub["id"]) / "harness",
                                      ev.heldout_split, f"heldout/{sub['id']}")
            for version in summary["versions"]:
                if version["commit"] == commit:
                    version["heldout"] = result
        ctl.ledger.write_json("summary.json", summary)
        return summary
    finally:
        ctl.close()


def run_evaluate(exp: Experiment, run_dir: Path, harness_dir: Path, split: str) -> dict[str, Any]:
    """Evaluate an existing harness directory, with no creator involved."""
    ctl = Controller(exp, run_dir)
    try:
        summary = ctl.evaluate_dir(Path(harness_dir).resolve(), split, split)
        ctl.ledger.write_json("summary.json", summary)
        return summary
    finally:
        ctl.close()
