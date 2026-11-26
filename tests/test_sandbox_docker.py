"""Creator-sandbox integration test against a real container.

Needs Docker and an image with bash, git and python3:
    HDEV_TEST_IMAGE=<image> pytest tests/test_sandbox_docker.py
"""

import json
import os
from pathlib import Path

import pytest

import harnessdev.controller.core as core
from harnessdev.config import Experiment
from tests.test_protocol import FakeBench

IMAGE = os.environ.get("HDEV_TEST_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set HDEV_TEST_IMAGE to run")

EXP = """
rq: rq2
domain: code
benchmarks: [fake-a]
models:
  creator: {{model: m, api_base_url: http://127.0.0.1:9, api_key: sk-host-only}}
  executor: {{model: m, api_base_url: http://127.0.0.1:9, api_key: sk-host-only}}
creator: {{image: {image}, nested_docker: false}}
evolve: {{max_pairs: 2}}
"""


def test_submit_feedback_and_final_through_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "get_benchmark", lambda name: FakeBench(name))
    monkeypatch.setattr("harnessdev.controller.evaluate.get_benchmark", lambda name: FakeBench(name))
    seed = tmp_path / "seed" / "harness"
    seed.mkdir(parents=True)
    (seed / "__main__.py").write_text("# h0\n")
    cfg = tmp_path / "exp.yaml"
    cfg.write_text(EXP.format(image=IMAGE))
    ctl = core.Controller(Experiment.load(cfg), tmp_path / "run")
    try:
        box = ctl.start_sandbox(tmp_path / "seed", {"ENVIRONMENT.md": "env\n"})
        tool = Path(core.__file__).resolve().parents[1] / "sandbox_tool.py"
        box.copy_in(tool, "/usr/local/bin/hd.py")
        hd = lambda args: json.loads(box.exec(f"python3 /usr/local/bin/hd.py {args}").stdout.decode())

        env = box.exec("env").stdout.decode()
        assert "sk-host-only" not in env and "HDEV_TOKEN=" in env

        assert hd("budget")["pairs_remaining"] == 2
        assert hd("declare-final")["reason"] == "h0_baseline"
        box.exec("echo '# good' > harness/__main__.py && git commit -qam v1")
        pair = hd("submit-pair")
        assert pair["ok"], pair
        events = hd("wait-feedback --since 0 --timeout 60")
        ctl.wait_idle()
        assert any(e["type"] == "eval_submitted" for e in events)

        listing = box.exec("ls feedback/evals && cat feedback/events.jsonl | wc -l && git status --short")
        out = listing.stdout.decode()
        assert pair["eval_ids"][0] in out and "feedback" not in out.splitlines()[-1]
        result = json.loads(box.exec(f"cat feedback/evals/{pair['eval_ids'][0]}/result.json").stdout)
        assert result["score"] == 1.0 and len(result["tasks"]) == 4

        final = hd("declare-final")
        assert final["ok"] and final["final"]["commit"] == pair["commit"]
        snap = tmp_path / "run" / "submissions" / pair["submission"] / "harness" / "harness" / "__main__.py"
        assert snap.read_text() == "# good\n"
    finally:
        ctl.close()
