import base64
import hashlib
import json

from harnessdev.benchmarks import all_benchmarks, get_benchmark
from harnessdev.benchmarks.browsecomp import _templates, decrypt
from harnessdev.benchmarks.eqbench3 import _split_scenarios
from harnessdev.benchmarks.mlebench import _last_json
from harnessdev.config import Experiment


def _encrypt(text: str, password: str) -> str:
    raw = text.encode()
    key = hashlib.sha256(password.encode()).digest()
    key = key * (len(raw) // len(key)) + key[: len(raw) % len(key)]
    return base64.b64encode(bytes(a ^ b for a, b in zip(raw, key))).decode()


def test_browsecomp_decrypt_roundtrip():
    assert decrypt(_encrypt("who wrote it?", "canary-1"), "canary-1") == "who wrote it?"


def test_browsecomp_templates_are_read_from_upstream_source():
    source = 'X = 1\nQUERY_TEMPLATE = """\n{Question}\nExact Answer:\n"""\nGRADER_TEMPLATE = """\n{question} {correct_answer} {response}\n"""\n'
    templates = _templates(source)
    assert templates["QUERY_TEMPLATE"].format(Question="q").startswith("q")
    assert "{response}" in templates["GRADER_TEMPLATE"]


def test_eqbench3_scenario_split():
    text = "######## 1   | Work | A\n####### Prompt1\nx\n######## 2   | Love | B\n####### Prompt1\ny\n"
    blocks = _split_scenarios(text)
    assert list(blocks) == ["1", "2"] and blocks["2"].endswith("y\n")


def test_mlebench_report_parsing():
    out = 'INFO log line\n{"competition_id": "x", "score": 0.8, "any_medal": true,\n "nested": {"a": 1}}\n'
    assert _last_json(out)["any_medal"] is True


def test_manifests():
    swe, tb = get_benchmark("swebench-pro"), get_benchmark("terminal-bench")
    feedback, heldout, test = set(swe.tasks("feedback")), set(swe.tasks("heldout")), set(swe.tasks("test"))
    assert (len(feedback), len(heldout), len(test)) == (100, 630, 730)
    assert not feedback & heldout and feedback | heldout == test
    assert len(tb.tasks("test")) == 89 and tb.tasks("feedback") == tb.tasks("test")
    assert {b.name for b in all_benchmarks()} == {
        "swebench-pro", "terminal-bench", "mle-bench", "eqbench3", "browsecomp"}


def test_experiment_config_expands_env_and_redacts(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-secret")
    path = tmp_path / "e.yaml"
    path.write_text("rq: rq1\ndomain: code\nmodels:\n"
                    "  creator: {model: a, api_base_url: http://x, api_key: '${MY_KEY}', protocol: anthropic}\n"
                    "  executor: {model: b, api_base_url: http://y, api_key: '${MY_KEY}'}\n")
    exp = Experiment.load(path)
    assert exp.creator_model.api_key == "sk-secret" and exp.benchmarks == ["swebench-pro", "terminal-bench"]
    assert "sk-secret" not in json.dumps(exp.redacted())
