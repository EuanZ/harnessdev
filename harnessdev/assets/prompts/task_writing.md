# Task: Build A Writing-Agent Harness

Build a writing agent that can respond to demanding creative, role-play, and analytical writing scenarios. This agent must be delivered as a runnable harness package. It will receive a scenario prompt, possibly across multiple turns, inhabit a character or an analytic stance, decide what to write, draft a response, critique and revise it against the qualities the scenario rewards, and produce a final response that a downstream judge can read and score. The final and most important objective is to score as high as possible on the benchmark, so you should design a more complete harness that can achieve strong downstream writing quality.

In this task, "agent" means the complete execution entity formed by combining the generated harness with the runtime LLM. "Harness" means the software system you implement, responsible for making the runtime LLM's semantic writing decisions executable, observable, recoverable, checkable, and scorable.

It should generalize to a family of unseen writing tasks, including:

- multi-turn role-play under emotional pressure, where the agent stays in character and reacts believably;
- third-person analysis of an interaction, reasoning about human dynamics, motives, and psychology;
- interpersonal mediation and de-escalation;
- drafting a difficult message (an apology, a boundary, a refusal, a confession);
- creative prose with a controlled tone, voice, and arc;
- out-of-character reflection / debrief on one's own responses;
- scenarios with explicit length, format, or perspective constraints.

The final benchmark evaluates the real quality of the written response, not the polish of an explanation about it. An outline, a plan, a description of how you would answer, or an empty response is not enough to complete a writing task. The scored artifact is the prose itself.

## Starting Point

The workspace contains only a very weak `harness/` seed. It makes `python -m harness ...` callable and demonstrates which files should appear in the output directory. It does not truly solve tasks and is not a recommended architecture.

You may keep, modify, replace, or delete this seed. The only final requirement is that the delivered harness is runnable and its final response is readable by the benchmark.

The workspace also provides `ENVIRONMENT.md`: the sandbox, the `hd` tool, the downstream benchmarks, and the public dev tasks.

## Role Of The Runtime LLM

The runtime LLM should drive task-specific semantic decisions: understanding the scenario, inhabiting the assigned character or analytic stance, forming the emotional and rhetorical arc, choosing register and subtext, deciding what to state and what to leave implicit, critiquing its own draft against the qualities the scenario rewards, revising, and deciding when the response is strong enough. Deterministic tools and heuristics may assist with drafting scaffolds, structure checks, length and format checks, self-evaluation prompts, and artifact validation, but they must not hard-code scenario-specific answers.

The runtime LLM call path is already provided by the model-config file. Do not invoke Claude/Anthropic API skills or provider-specific SDK documentation to build LLM access, and do not switch to an Anthropic SDK or any hard-coded provider SDK. Use the visible provider-neutral runtime LLM interface.

The harness is not "just a wrapper" and is not a one-shot prompt forwarder. A harness is the execution system that organizes the runtime LLM into a writing agent. The runtime LLM handles semantic reasoning and craft decisions; the harness handles tools, context, state, self-evaluation, revision, logs, and benchmark-compatible artifacts.

The harness should make this process more reliable:

- give the LLM the scenario, prior turns, and its own working notes as clean context, instead of losing track of the character, the constraints, or what has already been said;
- provide clear, safe, and logged tools, such as drafting, critique, revision, and final-response checks;
- track state: the current turn, the character or stance, the intended arc, drafts produced, critiques raised, and constraints still unmet;
- choose and run appropriate self-evaluation, such as checking whether the draft is on-task, in character, within length/format limits, and strong on the qualities the scenario rewards;
- change approach after a weak draft instead of re-emitting the same flat response;
- write a complete, auditable, benchmark-readable final response.

Do not hard-code writing ability into scenario-specific rules. Do not emit fixed responses based on scenario IDs, keyword allowlists, or memorized answers.

## Required Invocation Contract

The final harness must support:

```bash
python -m harness run --task-json <task.json> --model-config <model.json> --output-dir <out>
python -m harness --task-json <task.json> --workdir <dir> --model-config <model.json> --output-dir <out>
python -m harness -p "<task>" --workdir <dir> --output-dir <out> --max-steps <n>
```

Where:

- `--workdir`, `--work-dir`, and `--workspace` all mean the working root directory;
- all drafting, self-evaluation, and artifact generation should be centered on the output directory;
- if the task text gives an absolute output path, handle it exactly as required by the task;
- `--model-config` must be accepted and may contain the runtime LLM endpoint, key, and model;
- `--max-steps` / `--max-turns` must be accepted and used to limit execution.

Full-benchmark orchestration is external to the harness: the evaluation framework loads the EQ-Bench 3 scenarios, calls your harness once per turn with the conversation so far, and passes the responses to the fixed judge.

## Required Output Files

Each run must write at least these files under `--output-dir`:

- `result.json`: status, summary, key artifact paths, a short self-evaluation, errors, and metrics;
- `trajectory.jsonl`: structured records of each key action, observation, tool call, LLM call, draft, critique, revision, self-evaluation, and finalization step;
- `response.md`: the final prose response — the authoritative deliverable the judge reads and scores. It must be the actual answer to the scenario (the in-character reply, the analysis, the debrief), not a description of what you did;
- `stdout.log` and `stderr.log`, or equivalent command/run logs;
- any other output files required by the benchmark task, such as a per-turn response file or a structured multi-part answer.

For writing-response tasks, `result.json` should include top-level `response_path`, `response_chars`, `response_is_empty`, and a short self-evaluation of the response against the qualities the scenario rewards. The authoritative artifact is the final `response.md` content the judge reads; writing only `result.json` or a plan without a real `response.md` is not a valid delivery.

For writing tasks, an empty or placeholder response is not a valid delivery. If the scenario requires prose and the response is empty or off-task, continue drafting and revising, or return `partial/failed` with a clear blocking reason.

## Optional Human-Interactive Mode

The official benchmark uses unattended headless mode. After startup, it will not provide human clarification, approval, or mid-run feedback. Therefore, benchmark mode must not wait for user input, human approval, or mid-run editing.

If implementation cost is reasonable, you may additionally expose a local human-interactive mode, such as:

```bash
python -m harness chat --workdir <dir>
python -m harness interactive --workdir <dir>
```

In that mode, a human user may continue giving instructions, inspect drafts, request revisions, review the response, and reuse the same agent core, tools, state, context, self-evaluation, and artifact logic.

This optional mode must not replace or weaken the required headless benchmark contract. The official score only considers headless downstream benchmark performance.

## Public Dev Self-Tests

This is an unattended creation session. You do not need to wait for human approval.

After creating the harness, use `hd selftest` to run it on the public dev tasks. A self-test runs your current workspace harness with the real runtime LLM, in the same task containers and with the same scorer as the final evaluation. Each public dev task may be self-tested at most {max_selftests} times.

```bash
hd selftest                                   # every public dev task of this domain
hd selftest --benchmark <name> --task <id>    # one task
```

The command blocks until scoring finishes, then prints the directory that holds the results. After each run, inspect:

- `result.json` in the run directory: per-task score and error;
- the scorer's output for each task;
- your harness's own `result.json`, `trajectory.jsonl`, stdout and final output files;
- signals such as empty outputs, missing artifacts, output-contract failures, model-parameter errors, and repeated command failures.

Modify the harness based on this feedback, then run the necessary self-tests again. `ENVIRONMENT.md` lists the dev tasks and everything else available in this sandbox.

## Delivery

When delivering, leave the final runnable harness files in the workspace and write `DESIGN_NOTES.md` explaining:

- how the execution loop works;
- how tools are organized;
- how state and context are managed;
- how self-evaluation and revision are selected;
- how the final response is produced and validated;
- which public dev self-tests were run, what was observed, and how the harness was modified.

The final score is determined only by downstream benchmark performance.
