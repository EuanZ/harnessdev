"""Summarise finished runs from the ledger."""

from __future__ import annotations

import json
from pathlib import Path


def _fmt(value) -> str:
    return "-" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)


def report(runs_dir: Path) -> str:
    lines = []
    for summary_path in sorted(runs_dir.glob("*/summary.json")):
        run = summary_path.parent
        summary = json.loads(summary_path.read_text())
        config = json.loads((run / "run_config.json").read_text())
        usage = json.loads((run / "usage.json").read_text()) if (run / "usage.json").exists() else {}
        models = config["models"]
        lines.append(f"## {run.name}")
        lines.append(f"{config['rq']} / {config['domain']} / creator {models['creator']['model']} "
                     f"/ executor {models['executor']['model']}")
        if summary.get("rq") == "rq1" and summary.get("test"):
            for name, bench in summary["test"]["benchmarks"].items():
                lines.append(f"  test  {name:<16}{_fmt(bench['score'])}  ({bench['n_tasks']} tasks, "
                             f"{bench['n_errors']} errors)")
        elif summary.get("rq") == "rq2":
            final = (summary.get("final") or {}).get("commit")
            lines.append(f"  pairs used {summary['budget']['pairs_used']}/{summary['budget']['pairs_total']}"
                         f", final {final[:10] if final else 'not declared'}")
            for version in summary.get("versions", []):
                mark = "*" if version["commit"] == final else " "
                feedback = "  ".join(f"{b}={_fmt(s)}" for b, s in version["feedback"].items())
                heldout = version.get("heldout")
                held = "  ".join(f"{b}={_fmt(v['score'])}" for b, v in heldout["benchmarks"].items()) \
                    if heldout else "-"
                lines.append(f" {mark} {version['commit'][:10]}  feedback: {feedback}  heldout: {held}")
        elif "benchmarks" in summary:
            for name, bench in summary["benchmarks"].items():
                lines.append(f"  {summary['split']}  {name:<16}{_fmt(bench['score'])}  "
                             f"({bench['n_tasks']} tasks, {bench['n_errors']} errors)")
        for role, u in usage.items():
            if u.get("requests"):
                lines.append(f"  tokens {role:<9} in {u['input_tokens']:>12,}  out {u['output_tokens']:>10,}"
                             f"  requests {u['requests']:,}")
        lines.append("")
    return "\n".join(lines) if lines else f"no finished runs under {runs_dir}"
