"""``hd``: the creator's tool inside the sandbox. Prints JSON.

    hd selftest [--benchmark B] [--task ID ...]   run the workspace harness on dev tasks
    hd submit-pair [--commit SHA] [--dry-run]     billed full evaluation of one commit
    hd submit-probe [--commit SHA] [--dry-run]    fixed-subset probe of one commit
    hd list-evals | hd eval-status --eval-id ID
    hd read-feedback --since SEQ | hd wait-feedback --since SEQ [--timeout S]
    hd budget
    hd declare-final [--commit SHA]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlparse


def _call(method: str, path: str, payload: dict | None = None, timeout: float = 120.0, **query) -> object:
    url = os.environ["HDEV_CONTROLLER_URL"] + path
    if query:
        url += "?" + urlencode(query)
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {os.environ['HDEV_TOKEN']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode() or "{}")


def _numeric(url: str) -> str:
    """Rewrite the gateway host to its IP so nested task containers can reach it."""
    parsed = urlparse(url)
    try:
        ip = socket.gethostbyname(parsed.hostname)
    except OSError:
        return url
    return url.replace(parsed.hostname, ip, 1)


def _selftest(args: argparse.Namespace) -> object:
    from .benchmarks import Endpoint, RunOptions, get_benchmark
    from .controller.evaluate import evaluate

    benchmarks = [args.benchmark] if args.benchmark else os.environ["HDEV_BENCHMARKS"].split(",")
    executor = Endpoint(model=os.environ["HDEV_EXECUTOR_MODEL"],
                        base_url=_numeric(os.environ["HDEV_EXECUTOR_URL"]),
                        protocol=os.environ.get("HDEV_EXECUTOR_PROTOCOL", "openai"))
    judge = None
    if os.environ.get("HDEV_JUDGE_URL"):
        judge = Endpoint(model=os.environ["HDEV_JUDGE_MODEL"], base_url=os.environ["HDEV_JUDGE_URL"],
                         protocol=os.environ.get("HDEV_JUDGE_PROTOCOL", "openai"))
    task_env = {k: v for k, v in os.environ.items()
                if k.endswith("_API_KEY") and not k.startswith(("ANALYSIS_", "ANTHROPIC_", "OPENAI_"))}
    options = RunOptions(n_concurrent=args.concurrency, max_steps=int(os.environ.get("HDEV_MAX_STEPS", 500)),
                         task_env=task_env)
    root = Path(os.environ.get("HDEV_SHARED", "/hdev-shared")) / "selftests" / time.strftime("%Y%m%d-%H%M%S")
    # Evaluate a copy, so edits made while the self-test runs do not leak into it.
    import shutil
    snapshot = root / "harness"
    shutil.copytree(args.workspace, snapshot, ignore=shutil.ignore_patterns(
        ".git", "feedback", "selftests", "__pycache__"))
    report = {"ok": True, "run_dir": str(root), "benchmarks": {}}
    for name in benchmarks:
        tasks = args.task or get_benchmark(name).tasks("dev")
        ticket = _call("POST", "/selftest", {"benchmark": name, "tasks": tasks})
        if not ticket.get("ok"):
            report["benchmarks"][name] = ticket
            continue
        result = evaluate(harness_dir=snapshot, benchmark=name, task_ids=tasks, out_dir=root / name,
                          executor=executor, judge=judge, options=options)
        report["benchmarks"][name] = {
            "score": result.get("score"), "result": str(root / name / "result.json"),
            "tasks": [{"task_id": t["task_id"], "score": t["score"], "error": t["error"],
                       "artifacts": t["artifacts"]} for t in result["tasks"]],
            "selftests_used": ticket.get("counts"), "limit": ticket.get("limit")}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hd", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("selftest")
    st.add_argument("--benchmark")
    st.add_argument("--task", action="append")
    st.add_argument("--workspace", default="/workspace")
    st.add_argument("--concurrency", type=int, default=2)
    for name in ("submit-pair", "submit-probe"):
        sp = sub.add_parser(name)
        sp.add_argument("--commit", default="HEAD")
        sp.add_argument("--dry-run", action="store_true")
    sub.add_parser("list-evals")
    es = sub.add_parser("eval-status")
    es.add_argument("--eval-id", required=True)
    for name in ("read-feedback", "wait-feedback"):
        rf = sub.add_parser(name)
        rf.add_argument("--since", type=int, default=0)
        rf.add_argument("--timeout", type=float, default=3600)
    sub.add_parser("budget")
    df = sub.add_parser("declare-final")
    df.add_argument("--commit", default="HEAD")
    args = parser.parse_args(argv)

    if args.cmd == "selftest":
        out = _selftest(args)
    elif args.cmd in ("submit-pair", "submit-probe"):
        out = _call("POST", "/submit", {"kind": args.cmd.split("-")[1], "commit": args.commit,
                                        "dry_run": args.dry_run})
    elif args.cmd == "list-evals":
        out = _call("GET", "/evals")
    elif args.cmd == "eval-status":
        out = _call("GET", "/eval", id=args.eval_id)
    elif args.cmd == "read-feedback":
        out = _call("GET", "/events", since=args.since)
    elif args.cmd == "wait-feedback":
        out = _call("GET", "/wait", timeout=args.timeout + 30, since=args.since)
    elif args.cmd == "budget":
        out = _call("GET", "/budget")
    else:
        out = _call("POST", "/final", {"commit": args.commit})
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if not isinstance(out, dict) or out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
