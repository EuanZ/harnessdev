"""The controller.

Runs on the host: it starts the creator sandbox, freezes submitted
commits, runs evaluations, keeps the budget ledger and copies raw feedback
back. Which version to keep, when to submit and which one is final are the
creator's decisions.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..backends.docker import DockerBackend, Sandbox, client_proxy_settings, no_proxy_with
from ..benchmarks import Endpoint, RunOptions, get_benchmark
from ..config import Experiment, ModelConfig
from ..creators.scaffolds import get_scaffold
from ..gateway.proxy import Gateway, Route
from .evaluate import HarnessInvalid, combined_score, evaluate, validate_harness
from .ledger import Ledger
from .server import ControlServer

ASSETS = Path(__file__).resolve().parent.parent / "assets"
WORKSPACE = "/workspace"
SHARED = "/hdev-shared"
DIND_IMAGE = "docker:27-dind"
FINAL_GRACE_SEC = 60          # agent time left after a successful declare-final
DIND_ARGS = ["--host=tcp://0.0.0.0:2375", "--tls=false", "--bip=10.201.0.1/24",
             "--default-address-pool=base=10.202.0.0/16,size=24"]


def default_advertise_host() -> str:
    """Address at which containers reach services on the host.

    Docker Desktop resolves ``host.docker.internal`` in every container. On
    Linux the default bridge gateway is used, which task containers started by
    any tool can reach without extra host entries.
    """
    import os
    import platform
    import subprocess
    if os.environ.get("HDEV_ADVERTISE_HOST"):
        return os.environ["HDEV_ADVERTISE_HOST"]
    if platform.system() != "Linux":
        return "host.docker.internal"
    proc = subprocess.run(["docker", "network", "inspect", "bridge", "--format",
                           "{{(index .IPAM.Config 0).Gateway}}"], capture_output=True, text=True)
    return proc.stdout.strip() or "172.17.0.1"


class Controller:
    def __init__(self, exp: Experiment, run_dir: Path, *, advertise_host: str | None = None,
                 keep_sandbox: bool = False):
        self.exp = exp
        self.ledger = Ledger(run_dir)
        self.backend = DockerBackend()
        self.advertise_host = advertise_host or default_advertise_host()
        self.keep_sandbox = keep_sandbox
        self.gateway = Gateway(self.ledger.path("llm_requests.jsonl")).start()
        self.server = ControlServer(self).start()
        self.sandbox: Sandbox | None = None
        self.pool = ThreadPoolExecutor(max_workers=max(1, exp.evolve.parallel_evals))
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.started = time.time()
        self.state: dict[str, Any] = self.ledger.read_json("state.json") or {
            "seq": 0, "evals": {}, "pairs": [], "probes": [], "final": None,
            "h0_commit": None, "selftests": {}, "declare_only": False,
        }
        self.ledger.write_json("run_config.json", exp.redacted())

    # ------------------------------------------------------------- endpoints
    def _route(self, model: ModelConfig, role: str, tag: str = "") -> Route:
        return self.gateway.add_route(Route(
            role=role, base_url=model.api_base_url, api_key=model.api_key, model=model.model,
            auth=model.auth or ("x-api-key" if model.protocol == "anthropic" else "bearer"),
            token_budget=model.token_budget if role == "creator" else None, tag=tag))

    def _endpoint(self, route: Route, model: ModelConfig) -> Endpoint:
        return Endpoint(model=model.model, base_url=self.gateway.url_for(route, self.advertise_host),
                        protocol=model.protocol, host_url=self.gateway.url_for(route, "127.0.0.1"))

    def _options(self) -> RunOptions:
        e = self.exp.executor
        return RunOptions(n_concurrent=e.n_concurrent, max_steps=e.max_steps,
                          timeout_multiplier=e.timeout_multiplier, environment=e.environment,
                          attempts=e.attempts, task_env=dict(e.task_env))

    def _tasks(self, benchmark: str, split: str) -> list[str]:
        tasks = get_benchmark(benchmark).tasks(split)
        return tasks[: self.exp.limit] if self.exp.limit else tasks

    def _save(self) -> None:
        self.ledger.write_json("state.json", self.state)

    # ------------------------------------------------------------- evaluation
    def run_eval(self, *, harness_dir: Path, benchmark: str, task_ids: list[str], out_dir: Path,
                 tag: str) -> dict[str, Any]:
        """One benchmark leg, with its own gateway routes for exact usage attribution."""
        exec_route = self._route(self.exp.executor_model, "executor", tag)
        judge_route = self._route(self.exp.judge_model, "judge", tag) if self.exp.judge_model else None
        try:
            result = evaluate(
                harness_dir=harness_dir, benchmark=benchmark, task_ids=task_ids, out_dir=out_dir,
                executor=self._endpoint(exec_route, self.exp.executor_model),
                judge=self._endpoint(judge_route, self.exp.judge_model) if judge_route else None,
                options=self._options())
        finally:
            self.gateway.remove_route(exec_route)
            if judge_route:
                self.gateway.remove_route(judge_route)
        result["usage"] = self.gateway.usage(tag=tag)
        (Path(out_dir) / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        return result

    def evaluate_dir(self, harness_dir: Path, split: str, label: str) -> dict[str, Any]:
        """Evaluate a harness directory on every benchmark of the experiment."""
        validate_harness(harness_dir)
        per_bench = {}
        for benchmark in self.exp.benchmarks:
            if split not in get_benchmark(benchmark).splits() and split != "test":
                continue  # e.g. Terminal-Bench has no held-out split
            out = self.ledger.root / label / benchmark
            self.ledger.event("eval_started", label=label, benchmark=benchmark, split=split)
            result = self.run_eval(harness_dir=harness_dir, benchmark=benchmark,
                                   task_ids=self._tasks(benchmark, split), out_dir=out,
                                   tag=f"{label}:{benchmark}")
            per_bench[benchmark] = {k: result.get(k) for k in
                                    ("score", "metric", "n_tasks", "n_errors", "duration_s", "usage")}
            self.ledger.event("eval_finished", label=label, benchmark=benchmark, score=result.get("score"))
        summary = {"label": label, "split": split, "benchmarks": per_bench,
                   "score": combined_score(per_bench)}
        self.ledger.write_json(f"{label}/summary.json", summary)
        return summary

    # ---------------------------------------------------------------- sandbox
    def start_sandbox(self, seed_dir: Path, docs: dict[str, str]) -> Sandbox:
        c = self.exp.creator
        network = self.backend.create_network("net")
        env = self._sandbox_env()
        mounts = {}
        benches = [get_benchmark(b) for b in self.exp.benchmarks]
        for bench in benches:           # data the self-tests read, at host paths
            mounts.update(bench.sandbox_mounts())
            env.update(bench.sandbox_env())
        if c.nested_docker:
            volume = f"{network}-shared"
            dind = self.backend.start(DIND_IMAGE, label="dind", network=network, network_alias="dind",
                                      privileged=True, mounts={volume: SHARED, **mounts},
                                      command=DIND_ARGS, workdir="/", env={"DOCKER_TLS_CERTDIR": ""})
            env["DOCKER_HOST"] = "tcp://dind:2375"
            mounts[volume] = SHARED
            self._load_images(dind, sorted({i for b in benches for i in b.sandbox_images}))
        if client_proxy_settings():
            # the Docker client hands its proxy to every container; keep the
            # sandbox's own services off it
            env["NO_PROXY"] = env["no_proxy"] = no_proxy_with("dind", self.advertise_host)
        sandbox = self.backend.start(c.image, label="creator", env=env, network=network,
                                     mounts=mounts, cpus=c.cpus, memory=c.memory, workdir=WORKSPACE)
        sandbox.exec(f"mkdir -p {WORKSPACE} {SHARED} /hdev", workdir="/")
        proxy = client_proxy_settings()
        if proxy:   # task containers the creator starts get the same proxy as the host's
            proxy["noProxy"] = no_proxy_with(self.advertise_host)
            config = self.ledger.path("creator", "docker_config.json")
            config.write_text(json.dumps({"proxies": {"default": proxy}}))
            sandbox.exec("mkdir -p /root/.docker", workdir="/")
            sandbox.copy_in(config, "/root/.docker/config.json")
        sandbox.copy_contents(seed_dir, WORKSPACE)
        staging = self.ledger.root / "creator" / "docs"
        staging.mkdir(parents=True, exist_ok=True)
        for name, text in docs.items():
            (staging / name).write_text(text)
            sandbox.copy_in(staging / name, f"{WORKSPACE}/{name}")
        ignore = "\\n".join(["feedback/", "ENVIRONMENT.md", "TASK.md", "AGENTS.md", "CLAUDE.md",
                             "__pycache__/", "selftests/"])
        init = sandbox.exec(
            f"cd {WORKSPACE} && find . -name __pycache__ -prune -exec rm -rf {{}} + ; "
            f"printf '{ignore}\\n' >> .gitignore && "
            "git init -q -b main 2>/dev/null; git config user.email creator@harnessdev && "
            "git config user.name creator && git add -A && "
            "(git diff --cached --quiet || git commit -q -m 'initial harness') && git rev-parse HEAD")
        if init.returncode != 0:
            raise RuntimeError(f"workspace init failed: {init.stderr.decode(errors='replace')[-500:]}")
        self.state["h0_commit"] = init.stdout.decode().strip().splitlines()[-1]
        self._save()
        self.sandbox = sandbox
        self.ledger.event("sandbox_started", name=sandbox.name, h0_commit=self.state["h0_commit"])
        return sandbox

    def _load_images(self, dind: Sandbox, images: list[str]) -> None:
        """Copy locally built task images into the sandbox's Docker daemon."""
        if not images:
            return
        for _ in range(60):
            if dind.exec("docker info", workdir="/").returncode == 0:
                break
            time.sleep(1)
        for image in images:
            if not self.backend.image_exists(image):
                raise RuntimeError(f"image {image} is missing; run `harnessdev build` / `harnessdev prepare`")
            save = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
            load = subprocess.run(["docker", "exec", "-i", dind.name, "docker", "load"],
                                  stdin=save.stdout, capture_output=True)
            save.wait()
            if save.returncode or load.returncode:
                raise RuntimeError(f"could not load {image} into the sandbox: "
                                   f"{load.stderr.decode(errors='replace')[-400:]}")
            self.ledger.event("sandbox_image_loaded", image=image)

    def _sandbox_env(self) -> dict[str, str]:
        exp = self.exp
        selftest = self._route(exp.executor_model, "selftest", "selftest")
        analysis = self._route(exp.creator_model, "creator", "analysis")
        env = {
            "HDEV_CONTROLLER_URL": f"http://{self.advertise_host}:{self.server.port}",
            "HDEV_TOKEN": self.server.token,
            "HDEV_DOMAIN": exp.domain,
            "HDEV_BENCHMARKS": ",".join(exp.benchmarks),
            "HDEV_EXECUTOR_URL": self.gateway.url_for(selftest, self.advertise_host),
            "HDEV_EXECUTOR_MODEL": exp.executor_model.model,
            "HDEV_EXECUTOR_PROTOCOL": exp.executor_model.protocol,
            "HDEV_MAX_STEPS": str(exp.executor.max_steps),
            "HDEV_SHARED": SHARED,
            "ANALYSIS_LLM_BASE_URL": self.gateway.url_for(analysis, self.advertise_host),
            "ANALYSIS_LLM_MODEL": exp.creator_model.model,
            "ANALYSIS_LLM_API_KEY": "gateway",
            "ANALYSIS_LLM_PROTOCOL": exp.creator_model.protocol,
        }
        if exp.judge_model:
            judge = self._route(exp.judge_model, "judge", "selftest")
            env.update({"HDEV_JUDGE_URL": self.gateway.url_for(judge, self.advertise_host),
                        "HDEV_JUDGE_MODEL": exp.judge_model.model,
                        "HDEV_JUDGE_PROTOCOL": exp.judge_model.protocol})
        env.update(exp.executor.task_env)
        return env

    def run_creator(self, prompt: str, *, on_exit) -> None:
        """Drive the creator session until ``on_exit`` returns None or time runs out.

        ``on_exit(return_code)`` is called each time the agent process exits. It
        returns the message to resume the session with, or None to end it.
        """
        assert self.sandbox is not None
        exp, sandbox = self.exp, self.sandbox
        route = self._route(exp.creator_model, "creator", "creator")
        scaffold = get_scaffold(exp.creator.agent)
        launch = scaffold.launch(
            prompt_file="/hdev/prompt.md", model=exp.creator_model.model,
            gateway_url=self.gateway.url_for(route, self.advertise_host),
            protocol=exp.creator_model.protocol, effort=exp.creator.effort,
            version=exp.creator.agent_version)
        creator_dir = self.ledger.root / "creator"
        creator_dir.mkdir(parents=True, exist_ok=True)
        (creator_dir / "prompt.md").write_text(prompt)
        sandbox.copy_in(creator_dir / "prompt.md", "/hdev/prompt.md")
        install = sandbox.exec(launch.install, timeout=1800, workdir="/")
        if install.returncode != 0:
            raise RuntimeError(f"creator agent install failed: {install.stderr.decode(errors='replace')[-800:]}")
        deadline = self.started + exp.creator.timeout
        command, turn = launch.command, 0
        while True:
            turn += 1
            self.ledger.event("creator_turn_started", turn=turn)
            with (creator_dir / "agent_output.jsonl").open("ab") as out:
                proc = sandbox.popen(command, env=launch.env, stdout=out, stderr=out)
                while proc.poll() is None:
                    if time.time() > deadline:
                        sandbox.unpause()
                        sandbox.exec("pkill -f 'claude|codex' || true", workdir="/")
                        proc.wait(timeout=60)
                        self.ledger.event("creator_timeout", turn=turn)
                        break
                    final = self.state.get("final")
                    if final and time.time() > final["ts"] + FINAL_GRACE_SEC:
                        # the run ends at the final declaration; give the agent a short grace period
                        sandbox.exec("pkill -f 'claude|codex' || true", workdir="/")
                        proc.wait(timeout=60)
                        break
                    time.sleep(5)
            self._sync_sessions(launch.session_dir)
            self.ledger.event("creator_turn_finished", turn=turn, return_code=proc.returncode)
            if time.time() > deadline:
                break
            message = on_exit(proc.returncode)
            if message is None:
                break
            (creator_dir / "wake.md").write_text(message)
            sandbox.copy_in(creator_dir / "wake.md", "/hdev/wake.md")
            command = launch.resume_command
        self.gateway.remove_route(route)

    def _sync_sessions(self, session_dir: str) -> None:
        try:
            dest = self.ledger.root / "creator" / "sessions"
            shutil.rmtree(dest, ignore_errors=True)
            self.sandbox.copy_out(session_dir, dest)
        except Exception:
            pass

    # --------------------------------------------------------------- snapshots
    def freeze(self, ref: str, sid: str) -> tuple[str, Path]:
        """Resolve ``ref`` in the workspace and store an immutable copy on the host."""
        assert self.sandbox is not None
        rev = self.sandbox.exec(f"git -C {WORKSPACE} rev-parse --verify {ref}^{{commit}}")
        if rev.returncode != 0:
            raise HarnessInvalid(f"'{ref}' is not a commit in the workspace")
        commit = rev.stdout.decode().strip()
        sub = self.ledger.submission_dir(sid)
        data = self.sandbox.archive(WORKSPACE, commit)
        (sub / "harness.tar.gz").write_bytes(data)
        harness_dir = sub / "harness"
        shutil.rmtree(harness_dir, ignore_errors=True)
        harness_dir.mkdir()
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                tar.extractall(harness_dir, filter="data")
        except tarfile.ReadError:
            raise HarnessInvalid("the commit has no files") from None
        validate_harness(harness_dir)
        return commit, harness_dir

    # ----------------------------------------------------------- RQ2 protocol
    def _emit(self, event_type: str, /, **fields: Any) -> dict[str, Any]:
        """Append to the creator-visible event stream."""
        with self.cond:
            self.state["seq"] += 1
            row = {"seq": self.state["seq"], "ts": time.time(), "type": event_type, **fields}
            with self.ledger.path("feedback/events.jsonl").open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._save()
            self.cond.notify_all()
        self.ledger.event(event_type, **fields)
        return row

    def events_since(self, since: int) -> list[dict[str, Any]]:
        path = self.ledger.root / "feedback" / "events.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return [r for r in rows if r["seq"] > since]

    def wait_events(self, since: int, timeout: float) -> list[dict[str, Any]]:
        end = time.time() + timeout
        with self.cond:
            while self.state["seq"] <= since and time.time() < end:
                self.cond.wait(timeout=min(5.0, max(0.1, end - time.time())))
        return self.events_since(since)

    def _sync_feedback(self, eval_id: str | None = None) -> None:
        if self.sandbox is None:
            return
        fb = self.ledger.root / "feedback"
        self.sandbox.exec(f"mkdir -p {WORKSPACE}/feedback/evals")
        if eval_id:
            self.sandbox.copy_in(fb / "evals" / eval_id, f"{WORKSPACE}/feedback/evals/")
        self.sandbox.copy_in(fb / "events.jsonl", f"{WORKSPACE}/feedback/events.jsonl")

    def budget(self) -> dict[str, Any]:
        ev = self.exp.evolve
        with self.lock:
            pairs = self.state["pairs"]
            round_probes = [p for p in self.state["probes"] if p["round"] == len(pairs)]
            running = [e["id"] for e in self.state["evals"].values() if e["status"] == "running"]
            return {
                "pairs_total": ev.max_pairs, "pairs_used": len(pairs),
                "pairs_remaining": ev.max_pairs - len(pairs),
                "charged_commits": [p["commit"] for p in pairs],
                "probes_per_round": ev.probes_per_round,
                "probes_remaining_this_round": ev.probes_per_round - len(round_probes),
                "evals_running": running,
                "seconds_remaining": max(0, int(self.started + self.exp.creator.timeout - time.time())),
                "declare_only": self.state["declare_only"], "final": self.state["final"],
            }

    def list_evals(self) -> list[dict[str, Any]]:
        with self.lock:
            return sorted(self.state["evals"].values(), key=lambda e: e["id"])

    def submit(self, kind: str, ref: str = "HEAD", dry_run: bool = False) -> dict[str, Any]:
        """``kind`` is "pair" (billed full evaluation) or "probe" (fixed subset)."""
        ev = self.exp.evolve
        with self.lock:
            if self.state["declare_only"] or self.state["final"]:
                return {"ok": False, "reason": "declare_only"}
            budget = self.budget()
            if kind == "pair" and budget["pairs_remaining"] <= 0:
                return {"ok": False, "reason": "budget_exhausted", "budget": budget}
            if kind == "probe" and budget["probes_remaining_this_round"] <= 0:
                return {"ok": False, "reason": "probe_quota_exhausted", "budget": budget}
            n = len(self.state["pairs"]) + len(self.state["probes"]) + 1
            sid = f"{'p' if kind == 'pair' else 'q'}{n:03d}"
            try:
                commit, harness_dir = self.freeze(ref, sid)
            except HarnessInvalid as exc:
                shutil.rmtree(self.ledger.submission_dir(sid), ignore_errors=True)
                return {"ok": False, "reason": "invalid_harness", "detail": str(exc)}
            if kind == "pair":
                existing = [e["id"] for e in self.state["evals"].values()
                            if e["commit"] == commit and e["kind"] in ("full", "h0")
                            and e["status"] in ("running", "completed")]
                if existing:
                    shutil.rmtree(self.ledger.submission_dir(sid), ignore_errors=True)
                    return {"ok": False, "reason": "duplicate", "eval_ids": existing}
            if dry_run:
                shutil.rmtree(self.ledger.submission_dir(sid), ignore_errors=True)
                return {"ok": True, "dry_run": True, "commit": commit, "budget": budget}
            record = {"id": sid, "commit": commit, "ts": time.time(), "round": len(self.state["pairs"])}
            self.state["pairs" if kind == "pair" else "probes"].append(record)
            eval_ids = self._launch(sid, commit, harness_dir, "full" if kind == "pair" else "probe")
            self.ledger.save_submission({**record, "kind": kind, "eval_ids": eval_ids,
                                         "seq": n})
            self._save()
        if kind == "pair" and ev.serial and self.sandbox is not None:
            threading.Timer(2.0, self.sandbox.pause).start()
        return {"ok": True, "submission": sid, "commit": commit, "eval_ids": eval_ids,
                "budget": self.budget()}

    def _probe_tasks(self, benchmark: str, feedback: list[str]) -> list[str]:
        """The benchmark's ``probe`` split when it ships one, else the head of
        the feedback split."""
        bench = get_benchmark(benchmark)
        if "probe" in bench.splits():
            listed = [t for t in bench.tasks("probe") if t in feedback]
            if listed:
                return listed[: self.exp.evolve.probe_size]
        return feedback[: self.exp.evolve.probe_size]

    def _launch(self, sid: str, commit: str, harness_dir: Path, kind: str) -> list[str]:
        ev = self.exp.evolve
        eval_ids = []
        for benchmark in self.exp.benchmarks:
            tasks = self._tasks(benchmark, ev.feedback_split)
            if kind == "probe":
                tasks = self._probe_tasks(benchmark, tasks)
            eval_id = f"ev{len(self.state['evals']):03d}_{benchmark}_{kind}"
            self.state["evals"][eval_id] = {
                "id": eval_id, "submission": sid, "commit": commit, "benchmark": benchmark,
                "kind": kind, "n_tasks": len(tasks), "status": "running",
                "submitted": time.time(), "score": None}
            eval_ids.append(eval_id)
            self._emit("eval_submitted", eval_id=eval_id, commit=commit, benchmark=benchmark,
                       kind=kind, n_tasks=len(tasks))
            self.pool.submit(self._run_leg, eval_id, harness_dir, tasks)
        return eval_ids

    def _run_leg(self, eval_id: str, harness_dir: Path, tasks: list[str]) -> None:
        info = self.state["evals"][eval_id]
        out = self.ledger.root / "feedback" / "evals" / eval_id
        try:
            result = self.run_eval(harness_dir=harness_dir, benchmark=info["benchmark"],
                                   task_ids=tasks, out_dir=out, tag=eval_id)
            status, score = "completed", result.get("score")
        except Exception as exc:  # the leg delivers no per-task results
            result, status, score = {"tasks": [], "error": repr(exc)}, "failed", None
            out.mkdir(parents=True, exist_ok=True)
            (out / "result.json").write_text(json.dumps(result, indent=2))
        with self.lock:
            info.update(status=status, score=score, finished=time.time())
            (out / "meta.json").write_text(json.dumps(info, indent=2))
            self._save()
        for task in result.get("tasks", []):
            local = task.get("artifacts") or ""
            rel = Path(local).relative_to(out) if local and Path(local).is_relative_to(out) else Path(".")
            self._emit("trial_completed", eval_id=eval_id, task_id=task["task_id"],
                       score=task.get("score"), error=task.get("error"),
                       local_dir=f"{WORKSPACE}/feedback/evals/{eval_id}/{rel}".rstrip("/."))
        self._emit("eval_finished", eval_id=eval_id, status=status, score=score)
        with self.lock:
            siblings = [e for e in self.state["evals"].values() if e["submission"] == info["submission"]]
            settled = all(e["status"] != "running" for e in siblings)
        if self.sandbox is not None:
            if settled:
                self.sandbox.unpause()
            try:
                self._sync_feedback(eval_id)
            except Exception as exc:
                self.ledger.event("feedback_sync_error", eval_id=eval_id, error=repr(exc))

    def official_versions(self) -> dict[str, dict[str, Any]]:
        """Commits with a completed full evaluation on every benchmark, H0 excluded."""
        versions: dict[str, dict[str, Any]] = {}
        with self.lock:
            for e in self.state["evals"].values():
                if e["kind"] == "full" and e["status"] == "completed":
                    versions.setdefault(e["commit"], {})[e["benchmark"]] = e
        return {c: legs for c, legs in versions.items() if set(legs) == set(self.exp.benchmarks)}

    def declare_final(self, ref: str = "HEAD") -> dict[str, Any]:
        assert self.sandbox is not None
        rev = self.sandbox.exec(f"git -C {WORKSPACE} rev-parse --verify {ref}^{{commit}}")
        commit = rev.stdout.decode().strip()
        official = self.official_versions()
        if rev.returncode != 0 or commit not in official:
            done = {e["benchmark"] for e in self.state["evals"].values()
                    if e["commit"] == commit and e["kind"] == "full" and e["status"] == "completed"}
            missing = sorted(set(self.exp.benchmarks) - done)
            reason = "h0_baseline" if commit == self.state["h0_commit"] else "not_an_official_version"
            self._emit("final_rejected", commit=commit, reason=reason, missing=missing)
            return {"ok": False, "reason": reason, "missing_full_evaluations": missing}
        with self.lock:
            self.state["final"] = {"commit": commit, "declared": True, "ts": time.time(),
                                   "scores": {b: e["score"] for b, e in official[commit].items()}}
            self._save()
        self._emit("final_declared", commit=commit)
        self.ledger.write_json("final.json", self.state["final"])
        return {"ok": True, "final": self.state["final"]}

    def selftest_ticket(self, benchmark: str, tasks: list[str]) -> dict[str, Any]:
        """Count a self-test against the per-task allowance."""
        limit = self.exp.creator.max_selftests_per_task
        with self.lock:
            counts = self.state["selftests"]
            over = [t for t in tasks if limit is not None and counts.get(f"{benchmark}/{t}", 0) >= limit]
            if over:
                return {"ok": False, "reason": "selftest_limit_reached", "tasks": over, "limit": limit}
            for t in tasks:
                counts[f"{benchmark}/{t}"] = counts.get(f"{benchmark}/{t}", 0) + 1
            self._save()
        self.ledger.event("selftest", benchmark=benchmark, tasks=tasks)
        return {"ok": True, "counts": {t: counts[f"{benchmark}/{t}"] for t in tasks}, "limit": limit}

    def wait_idle(self) -> None:
        with self.cond:
            while any(e["status"] == "running" for e in self.state["evals"].values()):
                self.cond.wait(timeout=5.0)

    # ---------------------------------------------------------------- cleanup
    def close(self) -> None:
        self.pool.shutdown(wait=True)
        self.ledger.write_json("usage.json", {
            role: self.gateway.usage(role=role) for role in ("creator", "executor", "judge", "selftest")})
        if not self.keep_sandbox:
            self.backend.cleanup()
        self.server.stop()
        self.gateway.stop()
