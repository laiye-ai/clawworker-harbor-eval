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

## Results

Agent artifacts include:

- `preview-metrics.json`: verified runtime identity, canonical configuration receipt plus the local-backend adaptation, actual model HTTP attempt count, request statuses/durations, native usage and elapsed times.
- `result.json`: actual native run status/result.
- `hermes-session.jsonl` and `trajectory.json`: complete paginated session history and Harbor ATIF trajectory.
- `gateway.log`: native startup/diagnostic output for this disposable trial.

The same metrics are copied into `AgentContext.metadata`. `llm_http_requests` counts actual POST attempts seen by the model relay, including SDK retries and auxiliary requests; it is not a count of chat messages or a guarantee of distinct logical model turns. Unsupported routes and infrastructure failures must not be reported as zero-call successes.

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
