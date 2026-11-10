# Fixed dialogue experiments implementation plan

**Goal:** Run several independent scripted conversations, retain native session and workspace state within each, and report actual per-turn replies, timings, HTTP attempts and verification.

**Architecture:** Reuse Harbor `MultiStepTrial`, step workdir uploads, `resume_trajectory` and `min_reward`. Keep the existing single-question configuration. Add `cases: [{name, turns: [{question, attachments, verifier}]}]` as its mutually exclusive alternative. No simulator, UI, NFS or changes outside Harbor.

## Constraints

- Preserve the pinned Host/Hermes source and all patches; only the Harbor helper gains native session continuation.
- Upload each turn's attachments immediately before that step. Never put future inputs in the initial container image.
- A required verifier writes `reward=1` to continue; failure stops that conversation. Other conversations continue. Unchecked turns remain unverified.
- Each case and attempt uses fresh container state. Within a case, use the actual preceding native session ID, not a reconstructed prompt transcript.
- Keep model keys out of build contexts and reports. No commit or push in this task.

## Task 1: Generate native multi-step tasks

Files: `experiment.py`, `tests/unit/agents/test_clawworker_experiment.py`.

- [x] Add failing tests for cases, path resolution, mutually exclusive inputs, duplicate case names and just-in-time attachment staging.
- [x] Add strict turn/case models and generate one task per case, with ordered steps and `resume_trajectory=True`.
- [x] Use checked-step `min_reward=1`; unchecked steps emit empty rewards when a job has verification enabled, never a fabricated pass.
- [x] Preserve single-question task generation and tests.

## Task 2: Resume the native Preview session

Files: `agent.py`, `runtime.py`, agent unit tests and Docker runtime tests.

- [x] Add failing tests that a resumed agent passes the native continuation flag.
- [x] Read the preceding native `result.json` session ID on resume; refuse missing/failed state. Send it in the native Runs request.
- [x] Keep template memory on resume (`seedTemplateMemory=False`), while regenerating the ephemeral loopback model/API configuration.
- [x] Keep per-turn result, session export, metrics and actual HTTP attempt counts. Gateway process startup remains per turn and is included in timings.

## Task 3: Collect per-turn results and verify in Docker

Files: `experiment.py`, runtime model fixture, unit/runtime tests, README and example YAML.

- [x] Add reports for each configured turn, including unexecuted turns, question/input hashes, actual output/session/run IDs, runtime status, check status, timings and HTTP attempts.
- [x] Aggregate scenario completion only when every configured turn ran and all supplied checks passed. Report unverified scenarios separately from verified success.
- [x] Test two turns in one real container: native history retained, SQLite state retained, next-turn file absent early and present later.
- [x] Test a failed check stops subsequent turns while another case runs, and fresh attempts do not inherit state.
- [x] Run adapter and existing related unit tests, Docker tests, Ruff, pinned ty and py_compile; inspect diffs for scope and secrets.
- [x] Document one reusable multi-case config and the unchanged command. Distinguish execution completion from business correctness and single-container timing from Preview timing.
