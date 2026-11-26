"""BrowseComp, using the dataset, prompts and grading rule of openai/simple-evals.

The question set and the two prompt templates are read from the upstream
``browsecomp_eval.py`` at prepare time, so the grader text is upstream's own.
Task ids are row indices of the released CSV.
"""

from __future__ import annotations

import ast
import base64
import csv
import hashlib
import io
import json
import re
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .. import llm
from . import Benchmark, TaskResult
from .container_runner import RUNNER_IMAGE, run_harness

SOURCE_URL = "https://raw.githubusercontent.com/openai/simple-evals/main/browsecomp_eval.py"
DATA_URL = "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv"
CACHE = Path.home() / ".cache" / "harnessdev" / "browsecomp"


def _derive_key(password: str, length: int) -> bytes:
    key = hashlib.sha256(password.encode()).digest()
    return key * (length // len(key)) + key[: length % len(key)]


def decrypt(ciphertext_b64: str, password: str) -> str:
    encrypted = base64.b64decode(ciphertext_b64)
    key = _derive_key(password, len(encrypted))
    return bytes(a ^ b for a, b in zip(encrypted, key)).decode()


def _templates(source: str) -> dict[str, str]:
    found = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)     # upstream prompt text
        tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("QUERY_TEMPLATE", "GRADER_TEMPLATE"):
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
                    value = value.func.value        # """...""".strip()
                found[name] = ast.literal_eval(value).strip()
    if len(found) != 2:
        raise RuntimeError("could not read the BrowseComp templates from simple-evals")
    return found


class BrowseComp(Benchmark):
    name = "browsecomp"
    domain = "search"
    metric = "accuracy"
    sandbox_images = (RUNNER_IMAGE,)
    task_timeout = 3600

    def prepare(self) -> None:
        CACHE.mkdir(parents=True, exist_ok=True)
        for url, name in ((SOURCE_URL, "browsecomp_eval.py"), (DATA_URL, "browse_comp_test_set.csv")):
            path = CACHE / name
            if not path.exists():
                resp = httpx.get(url, timeout=120, follow_redirects=True)
                resp.raise_for_status()
                path.write_bytes(resp.content)

    def _rows(self) -> list[dict[str, str]]:
        self.prepare()
        text = (CACHE / "browse_comp_test_set.csv").read_text()
        return list(csv.DictReader(io.StringIO(text)))

    def tasks(self, split: str) -> list[str]:
        try:
            return super().tasks(split)
        except FileNotFoundError:
            if split != "test":
                raise
            return [str(i) for i in range(len(self._rows()))]

    def run(self, harness_dir, task_ids, executor, out_dir, options, judge=None):
        if judge is None:
            raise ValueError("browsecomp needs models.judge in the experiment file")
        rows = self._rows()
        templates = _templates((CACHE / "browsecomp_eval.py").read_text())
        out_dir = Path(out_dir)

        def one(task_id: str) -> TaskResult:
            row = rows[int(task_id)]
            question = decrypt(row["problem"], row["canary"])
            answer = decrypt(row["answer"], row["canary"])
            task_dir = out_dir / f"task-{task_id}"
            run = run_harness(
                harness_dir=harness_dir, task_id=f"browsecomp-{task_id}", domain="search",
                prompt=templates["QUERY_TEMPLATE"].format(Question=question), executor=executor,
                out_dir=task_dir, max_steps=options.max_steps,
                timeout=int(self.task_timeout * options.timeout_multiplier),
                env=options.task_env)
            if not run.response.strip():
                return TaskResult(task_id=task_id, score=0.0, passed=False,
                                  error=run.error or "empty_response", artifacts=str(task_dir),
                                  usage=run.usage)
            grading = llm.chat(judge.local_url, judge.model, [{
                "role": "user",
                "content": templates["GRADER_TEMPLATE"].format(
                    question=question, correct_answer=answer, response=run.response)}],
                protocol=judge.protocol, api_key=judge.api_key)
            match = re.search(r"correct: (yes|no)", grading)
            correct = bool(match and match.group(1) == "yes")
            (task_dir / "grading.json").write_text(json.dumps(
                {"grader_response": grading, "correct": correct}, indent=2, ensure_ascii=False))
            return TaskResult(task_id=task_id, score=1.0 if correct else 0.0, passed=correct,
                              error=run.error, artifacts=str(task_dir), usage=run.usage)

        with ThreadPoolExecutor(max_workers=options.n_concurrent) as pool:
            return list(pool.map(one, task_ids))
