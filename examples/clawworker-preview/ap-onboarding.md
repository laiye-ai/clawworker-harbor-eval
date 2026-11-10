# Fresh AP onboarding

`ap-onboarding.yaml` sends a greeting and four fixed preference answers in one
native session. No documents, AP database, Worker configuration or saved answers
are seeded. It selects chat uploads, a chat ledger, drafts only and zero tolerance.
These are test inputs, not new product defaults.

Use the current Preview base built with this adapter. The local fixture supplies
matching owner/Worker identities and the workspace namespace expected by the AP
bootstrap. It also installs the selected package's skills where that bootstrap
discovers them. It uses local Docker storage, not NFS or a cloud account.

From the Harbor repository, choose the template and build its fixture:

```sh
python -m harbor.agents.clawworker_preview.template_image \
  --template /absolute/path/to/selected-template.zip \
  --context /tmp/ap-onboarding-template --tag harbor-onboarding-template:selected
docker build -f examples/clawworker-preview/ap-onboarding.Dockerfile \
  --build-arg TEMPLATE_IMAGE=harbor-onboarding-template:selected \
  -t harbor-ap-onboarding:fresh examples/clawworker-preview
```

The build context path must be new. When changing templates, rebuild this fixture
too: its installed skill copies must match the selected package. Do not base this
fixture on a matching task image that already contains an initialized AP ledger.

Copy `ap-onboarding.yaml`, set `template` to the same ZIP, and choose the model/API.
Keep `base_image: harbor-ap-onboarding:fresh`. Supply `MODEL_API_KEY` and
`ADP_API_KEY` through the environment, then run the existing experiment command:

```sh
python -m harbor.agents.clawworker_preview.experiment \
  --config /absolute/path/to/ap-onboarding.yaml --output /tmp/ap-onboarding-run
```

Output must be a new directory. Each attempt gets fresh writable state. Reusing
the immutable image does not reuse a previous trial's SQLite database or session.

For example, set `n_attempts: 6` and `n_concurrent_trials: 2` to run six complete
onboardings with at most two active at a time. Leave `n_concurrent_trials` unset
or set it to 1 for serial measurement. The five user turns within each trial stay
sequential. Concurrency can affect latency and API throttling, so record the limit
when comparing results.

Rebuild the fixture with the current Dockerfile before using parallel onboarding.
Its entrypoint prepares a separate Worker namespace for each container. The Worker
ID derives from the container hostname and stays unchanged for all turns in that
container; it is not baked into the image. `BASH_ENV` supplies that same identity
to Harbor's bash execs, including the agent and verifier. No preference or AP state
is initialized by this hook. The old fixture's fixed Worker ID must not be used
to claim independent concurrent Workers against the same ADP account.

Inspect every turn's `model_requests` in `summary.json`: request duration and
provider token usage are distinct from the native aggregate in `usage`. Missing
usage remains null. Use the native session/tool logs for questions shown through
`ask_choice`; the final output alone may be an empty control message.

An agent saying setup is complete is not a correctness score. Check the actual
SQLite binding, tolerance, Worker configuration and ADP setup, then read back the
three live ADP app configurations. A verifier or an `on_agent_ended` observer must
capture SQLite before Harbor removes the trial container. The supplied dialogue
is unscored by default, as indicated by `verification_status: unverified`.

The ordinary initialization ends after the fourth answer and successful readback.
Any extra recheck, deferral or recovery messages are separate scenarios and must
not be included in its timing. This local test does not emulate Host connector
cards, frontend rendering, cloud background notifications or NFS latency.
