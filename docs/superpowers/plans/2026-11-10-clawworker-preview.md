# Clawworker Preview adapter

**Goal:** Run Preview's pinned, patched Hermes 0.22.0 through Harbor, with tools and workspace in the same Docker container.

**Scope:** Changes belong to this Harbor checkout only. Desktop and Service are read-only inputs. Do not change or deploy cloud resources, modify existing containers, commit, or push.

## Implementation

1. Export Host commit `f33c9a9bc818d7f96e6c9a224c4684444adce724` into an isolated build directory. Run its existing `server-runtime-v22/build.py` and canonical patch engine. Preserve the original context and manifest. For local AMD64 use the immutable AMD64 counterpart of the same upstream v0.22.0 image; ARM64 retains Preview's exact base digest.
   - Files: `src/harbor/agents/clawworker_preview/build.py`, `preview.json`.
   - Verify: real Git archive tests, pinned upstream stamp, original runtime manifest, all patched file hashes.
2. Add an in-container runner that runs the Host settings renderer, explicitly changes terminal execution to local, then launches the native `gateway.run` and drives `/v1/runs`. A private loopback model relay preserves streaming and records request count/status/duration without prompts or keys. Supply ADP_API_KEY to local commands from the test environment.
   - File: `src/harbor/agents/clawworker_preview/runtime.py`.
   - Verify: corrupted/stale runtime rejection, configuration preservation, actual HTTP forwarding/counting and error behavior.
3. Add a custom Harbor agent through its import-path extension. Reuse Harbor model auth resolution and Hermes ATIF conversion, without the upstream Hermes installer or its config rewriting.
   - File: `src/harbor/agents/clawworker_preview/agent.py`.
   - Verify: setup rejection of another runtime, model/ADP environment forwarding, native run failure propagation, metrics and trajectory collection.
4. Document build/run instructions and add one self-contained smoke task. Build locally and run actual Harbor + patched Hermes against a deterministic HTTP model fixture. Verify a real terminal action writes into workspace and that a final response, trajectory and metrics are collected.
   - Files: `examples/clawworker-preview/README.md`, `examples/tasks/clawworker-preview-smoke/` and tests.
   - Verify: targeted tests, Harbor unit suite, Ruff, type checking, py_compile, diff checks, real Docker smoke. Record any upstream failures separately.

## Acceptance boundaries

- This is a single-container local-execution benchmark, not a replica of AWS MicroVM/EFS latency or Host lifecycle.
- No real-model or live ADP claim follows from a fixture-backed smoke.
- Models initially use an OpenAI Chat Completions compatible endpoint, matching the Host managed model route. Unsupported protocols fail explicitly.
- Each trial gets fresh runtime state and workspace through Harbor; no global state cache.
- Keep the Preview source/patch provenance in the image and trial artifacts. Never infer identity from a mutable tag or version label alone.
