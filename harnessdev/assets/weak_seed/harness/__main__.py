from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .llm_client import load_model_config
from .seed_runner import run


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "run":
        args = args[1:]
    parser = argparse.ArgumentParser(prog="python -m harness")
    parser.add_argument("positional_prompt", nargs="*")
    parser.add_argument("-p", "--prompt", default=None)
    parser.add_argument("--task-json", default=None)
    parser.add_argument("--model-config", default=None)
    parser.add_argument("--output-dir", "--output", dest="output_dir", required=True)
    parser.add_argument("--workdir", "--work-dir", "--workspace", dest="workdir", default=None)
    parser.add_argument("--max-steps", "--max-turns", dest="max_steps", type=int, default=None)
    ns = parser.parse_args(args)

    if ns.task_json:
        task = json.loads(Path(ns.task_json).read_text(encoding="utf-8"))
    else:
        task = {"prompt": ns.prompt or " ".join(ns.positional_prompt)}
    if ns.workdir:
        task["workdir"] = ns.workdir
    task.setdefault("workdir", str(Path.cwd()))
    if ns.max_steps is not None:
        task["max_steps"] = ns.max_steps
    run(task, load_model_config(ns.model_config), ns.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
