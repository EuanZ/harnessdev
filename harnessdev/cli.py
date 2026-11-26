"""harnessdev command line.

    harnessdev list                         benchmarks, splits and task counts
    harnessdev build                        build the creator and runner images
    harnessdev prepare -e EXP               fetch datasets and images for an experiment
    harnessdev run -e EXP [--run-id ID] [--seed-harness DIR]
                                            run an experiment (rq1: create, rq2: evolve)
    harnessdev evaluate -e EXP --harness DIR [--split test]
    harnessdev report [--runs-dir runs]     summarise finished runs
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .backends.docker import DockerBackend
from .benchmarks import all_benchmarks, get_benchmark
from .benchmarks.container_runner import RUNNER_IMAGE
from .config import ConfigError, Experiment

ROOT = Path(__file__).resolve().parent.parent
DOCKER = Path(__file__).resolve().parent / "assets" / "docker"


def cmd_list(args: argparse.Namespace) -> int:
    print(f"{'benchmark':<16}{'domain':<10}{'metric':<22}splits")
    for bench in all_benchmarks():
        splits = []
        for split in bench.splits():
            splits.append(f"{split}={len(bench.tasks(split))}")
        if "test" not in bench.splits():
            splits.append("test=all upstream tasks")
        print(f"{bench.name:<16}{bench.domain:<10}{bench.metric:<22}{', '.join(splits)}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    backend = DockerBackend()
    backend.build(ROOT, args.creator_image, dockerfile=DOCKER / "creator.Dockerfile")
    backend.build(DOCKER, RUNNER_IMAGE, dockerfile=DOCKER / "runner.Dockerfile")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    exp = Experiment.load(args.experiment)
    for name in exp.benchmarks:
        print(f"preparing {name} ...", flush=True)
        get_benchmark(name).prepare()
    return 0


def _run_dir(args: argparse.Namespace, exp: Experiment) -> Path:
    run_id = args.run_id or f"{exp.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    return Path(args.runs_dir) / run_id


def cmd_run(args: argparse.Namespace) -> int:
    from .controller.flows import run_create, run_evolve
    exp = Experiment.load(args.experiment)
    if args.seed_harness:
        exp.seed_harness = args.seed_harness
    run_dir = _run_dir(args, exp)
    print(f"run directory: {run_dir}", flush=True)
    if exp.rq == "rq1":
        summary = run_create(exp, run_dir, keep_sandbox=args.keep_sandbox, skip_eval=args.skip_eval)
    else:
        summary = run_evolve(exp, run_dir, keep_sandbox=args.keep_sandbox)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .controller.flows import run_evaluate
    exp = Experiment.load(args.experiment)
    run_dir = _run_dir(args, exp)
    print(f"run directory: {run_dir}", flush=True)
    summary = run_evaluate(exp, run_dir, Path(args.harness), args.split)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .report import report
    print(report(Path(args.runs_dir)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harnessdev", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs-dir", default="runs")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    build = sub.add_parser("build")
    build.add_argument("--creator-image", default="harnessdev/creator:latest")
    build.set_defaults(fn=cmd_build)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("-e", "--experiment", required=True)
    prepare.set_defaults(fn=cmd_prepare)
    run = sub.add_parser("run")
    run.add_argument("-e", "--experiment", required=True)
    run.add_argument("--run-id")
    run.add_argument("--seed-harness", help="starting harness directory; overrides seed_harness in the file")
    run.add_argument("--keep-sandbox", action="store_true", help="leave containers running afterwards")
    run.add_argument("--skip-eval", action="store_true", help="rq1: stop after creation")
    run.set_defaults(fn=cmd_run)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("-e", "--experiment", required=True)
    evaluate.add_argument("--harness", required=True)
    evaluate.add_argument("--split", default="test")
    evaluate.add_argument("--run-id")
    evaluate.set_defaults(fn=cmd_evaluate)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
