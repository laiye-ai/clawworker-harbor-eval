# Clawworker Preview Hermes in Harbor

This adapter runs the actual pinned, patched Hermes 0.22.0 and native Gateway Runs API. It does not invoke Harbor's upstream Hermes installer or replace Host policies with the built-in benchmark configuration.

The integration is deliberately one Docker container: Hermes, terminal tools and `/workspace/work` all run locally. It does not reproduce AWS MicroVM, SSH, EFS latency, Host notification delivery or connector authorization. The Preview SSH patches remain installed and verified but are unused by the local terminal backend.

Host-managed web/image services and connector brokers are not supplied by this adapter. Direct HTTP tools such as the AP template's ADP calls can use credentials passed into the trial.

## Build

The lock is `src/harbor/agents/clawworker_preview/preview.json`. It pins the Host deployment source, upstream commit, official base image digests and the runtime manifest observed in Global Preview. Both base images are official **v0.22.0** images with upstream commit `e06df9c2eae6901f4c88f464e0ae6e753c0428b8`; only ARM64 is the exact Preview base image.

From the Harbor checkout, prepare a private upstream Git cache if the pinned commit is not already available:

```sh
git init --bare /tmp/clawworker-preview-hermes.git
git -C /tmp/clawworker-preview-hermes.git fetch --depth=1 \
  https://github.com/NousResearch/hermes-agent.git \
  e06df9c2eae6901f4c88f464e0ae6e753c0428b8

python3 src/harbor/agents/clawworker_preview/build.py \
  --host-repo /home/admin/workspaces/clawworker \
  --hermes-repo /tmp/clawworker-preview-hermes.git \
  --context /tmp/clawworker-preview-build \
  --tag harbor-clawworker-preview:0.22.0
```

The context directory must not exist. No checkout or file in the Host repository is changed. The builder exports the pinned commit, installs its two pinned Node build dependencies in a disposable directory, runs the **original Host build.py**, then extends its Dockerfile with the Harbor entry. The original Dockerfile and runtime manifest remain in the context. No cloud access, registry push or deployment is performed.

`--architecture amd64|arm64` overrides native architecture selection. The build refuses a manifest mismatch; container startup also verifies the upstream stamp, all patch after-hashes and every packaged runtime/asset hash. A matching version string alone is insufficient.

## Run

Install Harbor using its locked dependencies, then supply an OpenAI Chat Completions compatible model endpoint. For example, with a model exposed as `test-model`:

```sh
export TEST_MODEL_API_KEY='your-model-key'
export ADP_API_KEY='your-test-adp-key'

harbor run \
  --path examples/tasks/clawworker-preview-smoke \
  --agent harbor.agents.clawworker_preview.agent:ClawworkerPreview \
  --model openai/test-model \
  --base-url https://your-model-endpoint.example/v1 \
  --api-key-name TEST_MODEL_API_KEY \
  --api-format openai_chat_completions \
  --n-concurrent 1
```

Use real keys only in the environment or a private secret manager; never put them in task instructions, Docker build arguments or files. `ADP_API_KEY` is forwarded into local tool execution and is not required when the task does not use ADP. The smoke task checks its presence without invoking ADP. A fixture-backed smoke proves execution wiring, not real-model matching quality or live ADP behavior.

The agent uses Harbor's normal model/auth resolution. Native Anthropic/Responses protocol endpoints are not accepted by this initial Host-compatible Chat Completions adapter. Such models can be evaluated through a compatible gateway; do not silently relabel their native protocol.

## Templates and state

Use the reusable template builder below. It validates the selected ZIP using `unpackTemplate` bundled from the pinned Host source. This is local package/content validation; it does not simulate Marketplace signatures, Service download authorization or cloud template installation. Template ID, version, file hashes and package SHA-256 come from the selected package, never a hardcoded AP version.

```sh
python -m harbor.agents.clawworker_preview.template_image \
  --template /absolute/path/to/selected-template.zip \
  --context /tmp/selected-template-build
```

The context must not exist. The default base is `harbor-clawworker-preview:0.22.0`; `--base-image` selects another locally available compatible image and `--tag` optionally overrides the generated `harbor-template:<id>-<version>-<hash>` tag. The builder replaces the inherited definition in the **new image layer**, preserving the base image and host files. It emits `template.json`, Docker labels and `harbor-image.yaml`. Startup rejects missing/changed template files or inherited leftovers.

To use the image with an existing **prebuilt-image** Harbor task, pass `--extra-docker-compose /tmp/selected-template-build/harbor-image.yaml` to `harbor run`. This overrides the agent container's image. Do not use it to replace a task Dockerfile that adds its own fixtures or dependencies: that would lose those additions. The experiment command below instead builds a task layer on top of the template image and preserves its supplied attachments.

For Preview Hermes, the canonical Host renderer registers template skills as external directories, applies SOUL, seeds MEMORY and preserves memory, approval and delegation policies. It then changes only terminal backend/SSH settings to local execution in `/workspace/work`. Template schedules are retained as package content; this adapter does not provision the Host's scheduled jobs.

Each Harbor trial must use fresh state/workspace. A preinitialized AP task can seed its own database and Worker configuration in its task image; an onboarding task starts without that seed. Do not substitute sample mode for normal matching. ADP app configuration is still checked by the AP template; supplying a key alone does not initialize its three routed apps.

## Select a harness, template, API and question

Copy `experiment.yaml` to your experiment folder. Set its `template`, `agent` connection fields, `question` and optional `attachments`. Input paths resolve relative to the config file, independently of the calling directory. Then run:

```sh
export MODEL_API_KEY='your-model-key'
export ADP_API_KEY='your-test-adp-key'  # only for tasks requiring ADP
python -m harbor.agents.clawworker_preview.experiment \
  --config /absolute/path/to/experiment.yaml \
  --output /tmp/my-worker-experiment
```

`--output` must be a new directory. The command validates model credentials, builds the selected template image, adds the supplied attachments in a task image, and uses Harbor's normal `Job` to run and collect results. Set `n_attempts` for repeated fresh trials. The Preview base is reused; replacing a template does not require rebuilding Hermes. No provider key is passed to Docker build.

The initial reviewed loading paths are:

| `agent.name` | Template loading | Model API |
| --- | --- | --- |
| `clawworker-preview` | Canonical Host SOUL, MEMORY and skills | Chat Completions compatible |
| `hermes` | Harbor prompt template and native skills registration | As supported by Harbor's Hermes adapter |
| `codex` | Harbor prompt template and native skills registration | As supported by Harbor's Codex adapter |
| `claude-code` | Harbor prompt template and native skills registration | As supported by Harbor's Claude Code adapter |

Other harnesses fail explicitly until their loading path is reviewed. The three native routes reuse Harbor's existing installers/authentication; their binaries are installed during normal Harbor agent setup. Use their standard `agent.kwargs.version` when a version must be pinned. They do not run Preview's Gateway and do not claim its patched runtime identity. Their SOUL/MEMORY content is supplied through the task prompt, rather than Host-native system/memory settings. MCP, schedules and platform-specific tools are not automatically translated between harnesses. The same package loading successfully does not establish that its business workflow is portable.

The initial end-to-end integration test covers **Preview Hermes**. Native harness config, prompt rendering and skill registration are unit-tested; live native CLI/API execution still needs the selected model/account. A template's platform-specific onboarding also needs matching test identities/workspace configuration. This command does not bypass onboarding or invent AP configuration.

Results are in `summary.json` and `jobs/run/`, including raw agent output, trajectory and Harbor results. The summary records the selected harness/model, template hashes, loading mode, input hashes, build/execution times, rewards and trial errors. Actual HTTP counts are present when the harness supplies them (currently Preview); other harnesses return **null**, not zero or an inferred call count.

Supply `verifier: /absolute/path/to/check-result.sh` to score a question. The script must write Harbor rewards and can inspect the actual files/database. Without it, verification is disabled and rewards remain null: a completed run is not a claim of AP correctness. `agent.env` can explicitly forward additional runtime credentials by environment-variable reference; do not place literal keys in the config.

## Fixed multi-turn conversations and multiple cases

Use `multi-turn.yaml` with the same command. Replace the top-level `question`, `attachments` and `verifier` with `cases`. Each named case contains ordered `turns`; each turn has `question` and optional `attachments` and `verifier`. Do not combine the two config forms. This sends fixed user messages, without another model acting as the user.

Rebuild the Preview base once with the current `build.py` when upgrading this adapter: its container helper now accepts native session continuation. Afterwards, changing only a template still rebuilds only the template/task layers.

```yaml
cases:
  - name: follow-up
    turns:
      - question: Match this invoice and PO; report missing evidence.
        attachments: [./invoice.pdf, ./po.pdf]
        verifier: ./expect-missing.sh
      - question: Here is the missing delivery. Continue.
        attachments: [./delivery.pdf]
        verifier: ./expect-matched.sh
  - name: separate-question
    turns:
      - question: Explain which documents you need for matching.
```

Within a case, Harbor reuses the container, native conversation, workspace, template memory and business database. Preview resumes using the preceding Runs API `session_id`, including any native session rotation. Attachments are uploaded only before their turn under `/workspace/work/attachments/turn-XX/`; future files are not baked into the image. Earlier files remain available. `timeout_sec` applies separately to each turn.

Each case and each `n_attempts` repetition starts fresh. Cases do not inherit another case's onboarding, files or SQLite state. To test onboarding followed by processing, put the initialization answers and subsequent requests in the same case. This runner does not bypass the template's configuration requirements or supply Host connectors/interactive UI. Native `hermes`, `codex` and `claude-code` use Harbor's existing session-resume support; their live multi-turn API execution still needs separate validation with the selected account/model.

For fresh AP initialization, use [ap-onboarding.md](ap-onboarding.md) and its fixed five-turn profile. The Docker fixture supplies only runtime identity, workspace and installed skills; AP state and answers are created by the conversation.

Set `n_concurrent_trials` to control Harbor's maximum active trials. It defaults to
1 and accepts only positive integers. For example, `n_attempts: 6` with
`n_concurrent_trials: 2` repeats each case six times, scheduling at most two trials
at once. With three cases this creates 18 trials, not six. All turns within a
trial stay sequential and share that trial's native session and workspace.
Separate trials use separate containers and logs. The selected limit is recorded
in `job.json` and `summary.json`; it is a scheduling ceiling, not a guarantee of
that many simultaneously active model requests.

Harbor supplies the queue and concurrency limit; this adapter does not add a
second scheduler. Model/ADP rate limits are still shared by account. Each task is
currently limited to 2 CPUs and 4096 MiB; the memory limit is not a reservation.
Choose concurrency for the available resources and API quota. The fresh onboarding
fixture assigns a different Worker identity per container so attempts do not
share a Worker-scoped ADP mapping. External data seeded by other custom fixtures
is their responsibility and is not automatically cloned by Harbor.

A supplied verifier must write `1` to `/logs/verifier/reward.txt` to pass (or `{"reward":1}` to `reward.json`). A lower/missing reward or verification error stops that case before the next fixed answer. Other independent cases continue. Use a verifier to guard answers that depend on a particular question; an unchecked turn advances when its agent execution finishes. Verifiers run after the turn and can inspect the actual workspace/database and `/logs/agent/result.json` for Preview's actual reply. Do not infer a successful match from the model's wording. Unchecked turns emit no score, even in a job containing other checked turns.

`summary.json` includes `trials[].turns[]` with the exact user question, actual reply, native run/session IDs, runtime status, verification status/rewards, timestamps, elapsed execution, token usage, HTTP attempts and raw-log directory. Unsent turns appear as `not_run` with no invented zero-call success. Per-turn raw files live in `jobs/run/<trial>/steps/turn-XX/agent/`. The experiment records attachment hashes with case/turn labels.

`execution_complete` means all scripted turns executed. `verification_status` is `passed`, `failed`, `unverified` or `incomplete`; only supplied checks establish a verified result. `evaluation.pass_rate` is passed cases divided by scored cases/attempts, counting incomplete scored attempts as not passed and excluding unscored exploration. Its meaning depends on the quality/coverage of your checks, not the agent's self-assessment.

For minimal changes, each Preview turn starts and stops its Gateway process while retaining the native session database. `agent_execution_seconds` includes that startup/shutdown and Harbor invocation; `native_execution_seconds` separately measures the admitted native run. HTTP counts include real retry/auxiliary attempts within that turn. These local timings do not reproduce cloud notification, MicroVM or NFS overhead.

## Results

Agent artifacts include:

- `preview-metrics.json`: verified runtime identity, canonical configuration receipt plus the local-backend adaptation, actual model HTTP attempt count, request statuses/durations, native usage and elapsed times.
- `result.json`: actual native run status/result.
- `hermes-session.jsonl` and `trajectory.json`: complete paginated session history and Harbor ATIF trajectory.
- `gateway.log`: native startup/diagnostic output for this disposable trial.

The same metrics are copied into `AgentContext.metadata`. `llm_http_requests` counts actual POST attempts seen by the model relay, including SDK retries and auxiliary requests; it is not a count of chat messages or a guarantee of distinct logical model turns. Unsupported routes and infrastructure failures must not be reported as zero-call successes.

Each `model_requests` entry now records `request_index` (starting at 1 within each user turn), HTTP `status`, `duration_seconds`, and that response's `usage`:

```json
{"request_index":1,"status":200,"duration_seconds":3.2,"usage":{"input_tokens":120,"output_tokens":7,"total_tokens":127,"cache_read_tokens":100,"cache_write_tokens":null}}
```

Counters come from the provider response, including the usage event in a streaming SSE response. The relay observes bytes without changing the request or forwarded response. Streaming usage snapshots replace earlier snapshots; they are never added together. Missing usage, truncated responses and unavailable counters remain `null`, while an explicitly reported zero stays zero. No token estimation is used, and no prompts, credentials or arbitrary response fields are added to metrics.

Find individual calls in `summary.json` at `trials[].turns[].model_requests[]`, or the turn's `preview-metrics.json`. This differs from `turns[].usage`, which retains the existing native Runs aggregate. Request records can also include auxiliary calls, so their known usage need not equal the native main-agent total. Do not add these two accounting levels together. Historical runs without per-request usage cannot be backfilled exactly; rebuild the base and run a new test to collect it.

For example, a two-turn terminal smoke can make three HTTP requests because the native Gateway also generates a session title. Native `usage` describes the run's conversation usage; it need not include auxiliary requests counted by the relay.

Build inputs/provenance are in the context's `harbor-build.json`. Docker image IDs can differ across architectures/build metadata while the pinned Hermes source, patched files and original Host runtime manifest remain identical. Single-container timings are comparisons within this benchmark, not Preview cloud latency claims.

## Local validation

After building the image, run the adapter unit tests and the actual Docker smoke from this checkout:

```sh
python -m pytest tests/unit/agents/test_clawworker_preview_*.py
python -m pytest tests/unit/agents/test_clawworker_template_image.py tests/unit/agents/test_clawworker_experiment.py
python -m pytest tests/runtime/test_clawworker_preview.py tests/runtime/test_clawworker_template_experiment.py
```

The runtime test starts a temporary model protocol fixture on the host and supplies synthetic model/ADP keys. Harbor creates and removes its own trial container; the verifier checks the file written by the real Hermes terminal tool. No external model, live ADP request or cloud deployment is used.
