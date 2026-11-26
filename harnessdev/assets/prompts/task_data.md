# Task: Build A Data-Agent Harness

Build a data-science agent that can solve Kaggle-style machine-learning
competitions. This agent must be delivered as a runnable harness package. It
will receive a competition task directory, inspect data and instructions, choose
modeling approaches, write and run experiment code, validate submissions, recover
from failures, and produce the final submission file that downstream MLE-bench
can score. The final and most important objective is to score as high as
possible on the benchmark, so you should design a complete harness that can
achieve strong downstream performance.

In this task, "agent" means the complete execution entity formed by combining
the generated harness with the runtime LLM. "Harness" means the software system
you implement, responsible for making the runtime LLM's semantic decisions
executable, observable, recoverable, verifiable, and scorable.

It should generalize to a family of unseen data-science competitions, including:

- tabular classification and regression;
- time series forecasting;
- NLP, text classification, and sequence labeling;
- computer vision and image classification or segmentation;
- audio, signal, and array-based tasks;
- ranking, recommendation, and metric-specific optimization;
- competitions with unusual file formats, custom metrics, or strict submission
  schemas;
- MLE-bench-style Kaggle competitions.

The final benchmark evaluates the submitted file with the fixed scorer. A plan,
explanation, notebook-style report, or invalid submission is not enough to
complete a data task.

## Starting Point

The workspace contains only a very weak `harness/` seed. It makes
`python -m harness ...` callable and demonstrates which files should appear in
the output directory. It does not truly solve competitions and is not a
recommended architecture.

You may keep, modify, replace, or delete this seed. The only final requirement
is that the delivered harness is runnable and readable by the benchmark.

The workspace also provides `ENVIRONMENT.md`: the sandbox, the `hd` tool, the downstream benchmarks, and the public dev tasks.

## Role Of The Runtime LLM

The runtime LLM should drive task-specific semantic decisions: understanding the
competition objective, reading data descriptions, selecting features and models,
writing experiment code, interpreting validation failures, improving submissions,
and deciding when to finalize. Deterministic tools and heuristics may assist
with file inspection, execution, validation, logging, and artifact checks, but
they must not hard-code instance-specific solutions.

The runtime LLM call path is already provided by the model-config file. Do not invoke Claude/Anthropic API skills or
provider-specific SDK documentation to build LLM access, and do not switch to an
Anthropic SDK or any hard-coded provider SDK. Use the visible provider-neutral
runtime LLM interface.

The harness is not "just a wrapper" and is not a one-shot prompt forwarder. A
harness is the execution system that organizes the runtime LLM into a
data-science agent. The runtime LLM handles semantic reasoning and task
decisions; the harness handles tools, context, state, validation, recovery,
logs, and benchmark-compatible artifacts.

The harness should make this process more reliable:

- give the LLM appropriate context instead of indiscriminately stuffing every
  row, file, or log into context;
- provide clear, safe, and logged tools for data inspection and code execution;
- track task state, data facts, attempted experiments, validation results, and
  unresolved risks;
- create a valid baseline submission early, then improve it;
- validate submission format before finalization;
- change hypotheses or strategies after failure instead of repeating the same
  failed experiment;
- write complete, auditable, benchmark-readable final artifacts.

Do not hard-code solving ability into instance-specific rules. Do not generate
fixed submissions based on competition IDs, public dev answers, hidden labels,
or known leaderboard solutions.

## Required Invocation Contract

The final harness must support:

```bash
python -m harness run --task-json <task.json> --model-config <model.json> --output-dir <out>
python -m harness --task-json <task.json> --workdir <dir> --model-config <model.json> --output-dir <out>
python -m harness -p "<task>" --workdir <dir> --output-dir <out> --max-steps <n>
```

Where:

- `--workdir`, `--work-dir`, and `--workspace` all mean the competition task root
  directory;
- all reading, command execution, experiment writing, validation, and artifact
  generation should be centered on the task root and output directory;
- if the task text gives an absolute path, handle it exactly as required;
- `--model-config` must be accepted and may contain the runtime LLM endpoint,
  key, and model;
- `--max-steps` / `--max-turns` must be accepted and used to limit execution.

The evaluation framework creates task containers, runs the fixed scorer and aggregates results. Your artifact only needs to be a runnable directory whose root directly contains `harness/`.

## Required Output Files

Each run must write at least these files under `--output-dir`:

- `result.json`: status, summary, key artifact paths, submission path, command
  and validation summaries, errors, and metrics;
- `trajectory.jsonl`: structured records of each key action, observation, tool
  call, LLM call, data inspection, experiment, validation, failure, and
  finalization step;
- `response.md`: a short description of what was done, what was validated, and
  what remains uncertain;
- `submission.csv` or the exact submission file required by the task, with its
  path recorded as top-level `submission_path` in `result.json`;
- `stdout.log` and `stderr.log`, or equivalent command/run logs;
- any experiment scripts, validation reports, model notes, or artifacts needed
  to audit the result.

For MLE-bench tasks, the submitted file is the authoritative final artifact.
`result.json` should include top-level `submission_path`, `submission_exists`,
`submission_rows` when knowable, `validation_status`, `errors`, and `metrics`.
An invalid or missing submission is usually a zero-score delivery. If no valid
submission can be produced, return `partial/failed` with a clear blocking reason.

## Optional Human-Interactive Mode

The official benchmark uses unattended headless mode. After startup, it will not
provide human clarification, approval, or mid-run feedback. Therefore, benchmark
mode must not wait for user input, human approval, or clarification.

If implementation cost is reasonable, you may additionally expose a local
human-interactive mode, such as:

```bash
python -m harness chat --workdir <dir>
python -m harness interactive --workdir <dir>
```

In that mode, a human user may continue giving instructions, inspect proposed
experiments, approve or reject commands, review submissions, and reuse the same
agent core, tools, state, context, validation, and artifact logic.

This optional mode must not replace or weaken the required headless benchmark
contract. The official score only considers headless downstream benchmark
performance.

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

When delivering, leave the final runnable harness files in the workspace and
write `DESIGN_NOTES.md` explaining:

- how the execution loop works;
- how tools are organized;
- how state and context are managed;
- how validation and recovery are selected;
- how final submissions and artifacts are produced;
- which public dev self-tests were run, what was observed, and how the harness
  was modified.

The final score is determined only by downstream benchmark performance.
