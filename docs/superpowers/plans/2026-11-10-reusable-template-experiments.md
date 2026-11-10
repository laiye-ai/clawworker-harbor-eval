# Reusable template experiments

**Goal:** Select an installed harness, a template ZIP, a model connection and a question without editing runner code. Build a reproducible template image and let Harbor own execution, grading and results.

**Scope:** Extend only this checkout's Clawworker adapter, examples and tests. Keep existing uncommitted work. No UI, deployment, business-template edits, commits or pushes.

- [x] Bundle `unpackTemplate` from the pinned Host source into the existing base image. Keep the canonical runtime manifest unchanged. Local packages use Host content validation; Marketplace signature/download authorization is not simulated.
- [x] Add `template_image.py`: accept a ZIP, base image and new build-context directory; verify the runtime and package, copy its exact payload, derive identity from its manifest, build a clean template layer and emit a Harbor Compose image override. No template-specific name, version or scripts in the builder.
- [x] Extend `runtime.py` to verify installed template hashes and include its actual identity in runtime metrics. Leave base-only smoke behavior intact.
- [x] Add `experiment.py`: a YAML/JSON file uses Harbor's `AgentConfig` plus template, question, attachments and optional verifier. Create a task image from the selected template image and run Harbor's `Job`. Preview uses Host template loading. Reviewed native installed agents (`hermes`, `codex`, `claude-code`) use Harbor skills and prompt-template APIs; report this distinction and reject unsupported combinations. Missing verification yields no score, and missing HTTP counts remain null.
- [x] Test real ZIPs, package rejection, replacement without stale files, non-overwrite behavior, model/harness configuration and task inputs before implementation. Run Docker/Host validation against two independent template versions and the actual AP package. Use a deterministic model for the end-to-end experiment; do not claim AP matching accuracy.
- [x] Update build/run examples. Run adapter tests, relevant existing Harbor tests, real Docker smoke, Ruff, pinned type checking, py_compile and whitespace checks. Baseline diagnostics were local-only artifacts and are not part of the reusable workflow.

**Acceptance:** Changing only the config's template/model/harness/question selects the next experiment. Build output and trial summaries retain template and runtime provenance. Inputs and model keys never enter the base image or template image. Existing task fixtures are not silently replaced by a template-image override; generated question tasks add their attachments in their own Docker layer.
