# Task: Build A Coding-Agent Harness

Build a coding agent that can solve software engineering tasks. This agent must be delivered as a runnable harness package. It will receive tasks in real repositories or prepared terminal environments, inspect the environment, choose actions, edit files, run commands, verify results, recover from failures, and produce final state that downstream benchmarks can read. The final and most important objective is to score as high as possible on the benchmark, so you should design a more complete harness that can achieve strong downstream performance.

In this task, "agent" means the complete execution entity formed by combining the generated harness with the runtime LLM. "Harness" means the software system you implement, responsible for making the runtime LLM's semantic decisions executable, observable, recoverable, verifiable, and scorable.

It should generalize to a family of unseen coding tasks, including:

- bug fixes;
- feature implementation;
- multi-file repository edits;
- unit-test or integration-test fixes;
- command-line debugging;
- dependency or configuration diagnosis;
- terminal-environment file, service, or program-state repair;
- SWE-bench-style repository tasks;
- Terminal-Bench-style terminal tasks.

The final benchmark evaluates the real repository or environment state, not the polish of the explanation. A plan, explanation, report, or empty patch is not enough to complete a coding task.

## Starting Point

The workspace contains only a very weak `harness/` seed. It makes `python -m harness ...` callable and demonstrates which files should appear in the output directory. It does not truly solve tasks and is not a recommended architecture.

You may keep, modify, replace, or delete this seed. The only final requirement is that the delivered harness is runnable and readable by the benchmark.

The workspace also provides `ENVIRONMENT.md`: the sandbox, the `hd` tool, the downstream benchmarks, and the public dev tasks.

## Role Of The Runtime LLM

The runtime LLM should drive task-specific semantic decisions: understanding the task, choosing hypotheses, locating relevant files, proposing edits, interpreting failures, revising patches, and deciding when to finish. Deterministic tools and heuristics may assist with search, editing, verification, summarization, and artifact checks, but they must not hard-code instance-specific solutions.

The runtime LLM call path is already provided by the model-config file. Do not invoke Claude/Anthropic API skills or provider-specific SDK documentation to build LLM access, and do not switch to an Anthropic SDK or any hard-coded provider SDK. Use the visible provider-neutral runtime LLM interface.

The harness is not "just a wrapper" and is not a one-shot prompt forwarder. A harness is the execution system that organizes the runtime LLM into a coding agent. The runtime LLM handles semantic reasoning and task decisions; the harness handles tools, context, state, verification, recovery, logs, and benchmark-compatible artifacts.

The harness should make this process more reliable:

- give the LLM appropriate context instead of indiscriminately stuffing the entire repository into context;
- provide clear, safe, and logged tools;
- track task state, attempted approaches, command results, and unresolved issues;
- choose and run appropriate verification;
- change hypotheses or strategies after failure instead of repeating the same failed command;
- write complete, auditable, benchmark-readable final artifacts.

Do not hard-code solving ability into instance-specific rules. Do not generate fixed patches based on task IDs, file-name allowlists, or known answers.

## Required Invocation Contract

The final harness must support:

```bash
python -m harness run --task-json <task.json> --model-config <model.json> --output-dir <out>
python -m harness --task-json <task.json> --workdir <dir> --model-config <model.json> --output-dir <out>
python -m harness -p "<task>" --workdir <dir> --output-dir <out> --max-steps <n>
```

Where:

- `--workdir`, `--work-dir`, and `--workspace` all mean the task root directory;
- all reading, editing, command execution, patch generation, and artifact generation should be centered on the task root;
- if the task text gives an absolute path, such as `/app/out.txt`, handle it exactly as required by the task;
- `--model-config` must be accepted and may contain the runtime LLM endpoint, key, and model;
- `--max-steps` / `--max-turns` must be accepted and used to limit execution.

The evaluation framework creates task containers, runs the fixed scorer and aggregates results. Your artifact only needs to be a runnable directory whose root directly contains `harness/`.

## Required Output Files

Each run must write at least these files under `--output-dir`:

- `result.json`: status, summary, key artifact paths, changed files, command/verification summaries, errors, and metrics;
- `trajectory.jsonl`: structured records of each key action, observation, tool call, LLM call, edit, command, verification, failure, and finalization step;
- `response.md`: a short description of what was done, what was verified, and what remains uncertain;
- `patch.diff`: for repository-editing tasks, write the final diff; if the task is not a repository patch task, still write an empty file and explain the authoritative artifact in `result.json`;
- `stdout.log` and `stderr.log`, or equivalent command/run logs;
- any other output files required by the benchmark task.

For repository-editing tasks, `result.json` should include top-level `patch_path`, `patch_chars`, `patch_is_empty`, and `changed_files`. The authoritative patch is the real git diff in the task workdir. Writing `patch.diff` without modifying the workdir is usually not a valid delivery; `patch.diff` is mainly for audit, diagnosis, and compatibility.

For repository-editing tasks, an empty diff is usually not a valid delivery. If the task requires code changes and the diff is empty, continue locating and editing, or return `partial/failed` with a clear blocking reason.

## Optional Human-Interactive Mode

The official benchmark uses unattended headless mode. After startup, it will not provide human clarification, approval, or mid-run feedback. Therefore, benchmark mode must not wait for user input, human approval, or clarification.

If implementation cost is reasonable, you may additionally expose a local human-interactive mode, such as:

```bash
python -m harness chat --workdir <dir>
python -m harness interactive --workdir <dir>
```

In that mode, a human user may continue giving instructions, inspect proposed actions, approve or reject edits/commands, review diffs, and reuse the same agent core, tools, state, context, verification, and artifact logic.

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
- how verification and recovery are selected;
- how final artifacts are produced;
- which public dev self-tests were run, what was observed, and how the harness was modified.

The final score is determined only by downstream benchmark performance.
