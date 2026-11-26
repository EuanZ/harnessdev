"""RQ2 protocol rules, exercised against a fake sandbox and a fake benchmark."""

import subprocess
import time
from pathlib import Path

import pytest

import harnessdev.controller.core as core
from harnessdev.benchmarks import Benchmark, TaskResult
from harnessdev.config import Experiment

EXP = """
name: t
rq: rq2
domain: code
benchmarks: [fake-a, fake-b]
seed_harness: {seed}
models:
  creator: {{model: m, api_base_url: http://127.0.0.1:9, api_key: k}}
  executor: {{model: m, api_base_url: http://127.0.0.1:9, api_key: k}}
evolve: {{max_pairs: 2, probes_per_round: 1, probe_size: 2}}
"""


class FakeBench(Benchmark):
    domain = "code"

    def __init__(self, name):
        self.name = name

    def splits(self):
        return ["feedback", "heldout"]

    def tasks(self, split):
        return [f"{split}-{i}" for i in range(4)]

    def run(self, harness_dir, task_ids, executor, out_dir, options, judge=None):
        marker = (Path(harness_dir) / "harness" / "__main__.py").read_text()
        return [TaskResult(task_id=t, score=1.0 if "good" in marker else 0.0, artifacts=str(out_dir))
                for t in task_ids]


class LocalSandbox:
    """Stands in for a container: the workspace is a host directory."""

    def __init__(self, path: Path):
        self.path, self.name = path, "local"

    def exec(self, command, **kwargs):
        return subprocess.run(["bash", "-lc", command.replace(core.WORKSPACE, str(self.path))],
                              capture_output=True)

    def archive(self, path, treeish=None):
        return subprocess.run(["git", "-C", str(self.path), "archive", "--format=tar.gz", treeish],
                              capture_output=True, check=True).stdout

    def copy_in(self, src, dest):
        pass

    def pause(self):
        pass

    def unpause(self):
        pass


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "get_benchmark", lambda name: FakeBench(name))
    monkeypatch.setattr("harnessdev.controller.evaluate.get_benchmark", lambda name: FakeBench(name))
    monkeypatch.setitem(__import__("harnessdev.benchmarks", fromlist=["DOMAINS"]).DOMAINS, "code",
                        ["fake-a", "fake-b"])
    ws = tmp_path / "ws"
    (ws / "harness").mkdir(parents=True)
    (ws / "harness" / "__main__.py").write_text("# h0\n")
    git = lambda *a: subprocess.run(["git", "-C", str(ws), *a], check=True, capture_output=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(ws)], check=True)
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("add", "-A"); git("commit", "-q", "-m", "h0")
    cfg = tmp_path / "exp.yaml"
    cfg.write_text(EXP.format(seed=ws))
    controller = core.Controller(Experiment.load(cfg), tmp_path / "run", advertise_host="127.0.0.1")
    controller.sandbox = LocalSandbox(ws)
    controller.state["h0_commit"] = subprocess.run(
        ["git", "-C", str(ws), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    controller.ws, controller.git = ws, git
    yield controller
    controller.close()


def commit(ctl, text):
    (ctl.ws / "harness" / "__main__.py").write_text(text)
    ctl.git("commit", "-qam", text)


def test_pair_budget_probe_quota_and_final(ctl):
    assert ctl.declare_final("HEAD")["reason"] == "h0_baseline"

    commit(ctl, "# good v1\n")
    probe = ctl.submit("probe")
    assert probe["ok"] and len(probe["eval_ids"]) == 2
    assert ctl.submit("probe")["reason"] == "probe_quota_exhausted"   # 1 probe per round
    ctl.wait_idle()
    assert all(e["n_tasks"] == 2 for e in ctl.list_evals())           # fixed subset

    assert ctl.declare_final("HEAD")["reason"] == "not_an_official_version"

    pair = ctl.submit("pair")
    assert pair["ok"] and pair["budget"]["pairs_remaining"] == 1
    assert ctl.submit("pair")["reason"] == "duplicate"                # same commit, nothing launched
    assert ctl.submit("probe")["ok"]                                  # allowance reset by the pair
    ctl.wait_idle()

    commit(ctl, "# worse v2\n")
    assert ctl.submit("pair")["ok"]
    commit(ctl, "# v3\n")
    assert ctl.submit("pair")["reason"] == "budget_exhausted"
    ctl.wait_idle()

    official = ctl.official_versions()
    assert len(official) == 2
    good = next(c for c, legs in official.items() if legs["fake-a"]["score"] == 1.0)
    final = ctl.declare_final(good)
    assert final["ok"] and final["final"]["scores"] == {"fake-a": 1.0, "fake-b": 1.0}
    assert ctl.submit("pair")["reason"] == "declare_only"

    kinds = [e["type"] for e in ctl.events_since(0)]
    assert kinds.count("trial_completed") == 2 * 2 * 2 + 2 * 2 * 4    # 2 probes x2 legs x2, 2 pairs x2 legs x4
    assert kinds[-1] == "final_declared"
    seqs = [e["seq"] for e in ctl.events_since(0)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_invalid_snapshot_is_not_charged(ctl):
    (ctl.ws / "harness" / "__main__.py").unlink()
    ctl.git("commit", "-qam", "broken")
    out = ctl.submit("pair")
    assert out == {"ok": False, "reason": "invalid_harness", "detail": out["detail"]}
    assert ctl.budget()["pairs_used"] == 0


def test_selftest_allowance(ctl):
    ctl.exp.creator.max_selftests_per_task = 2
    assert ctl.selftest_ticket("fake-a", ["t1"])["ok"]
    assert ctl.selftest_ticket("fake-a", ["t1", "t2"])["ok"]
    denied = ctl.selftest_ticket("fake-a", ["t1"])
    assert denied["reason"] == "selftest_limit_reached" and denied["tasks"] == ["t1"]


def test_wait_events_returns_on_new_event(ctl):
    start = time.time()
    assert ctl.wait_events(ctl.state["seq"], timeout=0.3) == []
    assert time.time() - start < 2
