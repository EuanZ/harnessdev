# Task: Build a Search Agent

Build an agent that answers hard-to-find factual questions by investigating the
open web — decomposing the question's constraints, searching, reading and
cross-checking sources, verifying a candidate against every constraint, and
committing to a correct short answer. At evaluation it is combined with a runtime
LLM (reached through the model-config file) and is run on unseen
BrowseComp questions. Its only goal is to score as high as possible with the
official judge (a correct short answer matched against a hidden reference).

## The LLM is the agent; the harness only amplifies it

The agent's body IS the LLM. The intelligence — decomposing the question,
deciding what to search, reading and judging sources, verifying the candidate,
committing to a short answer — must come from the runtime LLM. Your harness
exists only to let the LLM do that better:

- **memory** of the extracted constraints, the queries tried, and the evidence
  collected (which source supports or contradicts which claim),
- **context / state management** so the LLM tracks candidates over a long,
  multi-search investigation without losing the thread,
- **tool use** — search, fetch, read — as the LLM's hands.

Do not put the problem-solving into hardcoded logic. Put it in the LLM, and build
the scaffolding that makes the LLM effective. A program that answers with fixed
query templates and barely uses the LLM's judgment is not the agent we want.

## The runtime LLM must drive the investigation

For each question the runtime LLM itself plans the queries, reads the results,
decides the next search, verifies the candidate against every constraint, and
commits to a concise answer — driven by the LLM's judgment, not a fixed pipeline.
It must adapt to question types, constraint structures, and source landscapes you
did not anticipate. **Ground every answer in retrieved evidence — never answer
from model memory, and never fabricate URLs, citations, or excerpts.** Litmus
test: if two different questions would run byte-identical search/answer code from
your harness (differing only in the query string the LLM filled in), you built a
script, not an agent.

## What you build on

The workspace contains only a very weak `harness/` seed. It makes `python -m harness ...` callable and shows which files belong in the output directory. You may keep, modify, replace, or delete it. Leave no `NotImplementedError`, TODOs, or placeholder stubs.

Read `ENVIRONMENT.md` for the environment facts: the runtime LLM, the step limit, the search keys available to the produced agent, and the public dev questions.

## Contract (the environment, not advice)

- Invoked as `python -m harness run --task-json ... --model-config ...
  --output-dir ...`, and also `python -m harness -p "<question>" --output-dir
  ...`. Accept `--workdir/--work-dir/--workspace` and `--max-steps/--max-turns`.
- Detect available search backends (Tavily / Serper / SerpApi keys from the
  environment); when no usable search/fetch path exists, report the run as
  **`partial`** and name the missing dependency — never fabricate sources.
- Write the final short answer to `response.md` and put its path in
  `result.json`; write `evidence.json`/`sources.json` (query, URL, excerpt,
  support/contradiction) and `trajectory.jsonl` (search/read/verify/final) under
  `--output-dir`. Keep the final answer concise and clearly marked — not buried
  in a long report, not hedged across multiple candidates.
- A **correct answer is the goal**, not a long report — the judge scores a single
  short answer against a hidden reference. Push accuracy as high as the budget
  allows; verify the candidate against every extracted constraint before
  committing, and keep searching when the evidence is weak or indirect rather than
  settling for the first plausible guess.
- Per question the produced agent runs under a **step limit** (stated in
  `ENVIRONMENT.md`) and a wall-clock limit; there is **no fixed query/attempt cap**.
  Search and verify as much as the budget allows, then commit the best-grounded answer.

## Headless self-test and delivery

This is a headless run with no human reviewer. Decide everything yourself and
deliver in this single session; never wait for approval.

Self-test with `hd selftest`. Each public dev question may be self-tested at most
{max_selftests} times. **Run it in the foreground and wait for it to finish.**
Read the produced agent's `trajectory.jsonl`, the judge verdicts, the
search queries, the opened sources, and the evidence/answer files; find where it
fell short (memory answers, dropped constraints, hedged or buried answers,
fabricated sources, ungrounded claims) and improve the agent, then run again. A
low judge score means the agent is not done.

When you deliver, write a short `DESIGN_NOTES.md` describing the agent's
architecture and the key design choices.

The judge's score is the only measure of success.
