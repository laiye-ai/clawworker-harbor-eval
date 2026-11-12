# Simulated-user evaluation

Use `simulated_user` to have a model play the user in a Clawworker Preview
experiment. Each case runs once per selected SOUL and `n_attempts`. Harbor
creates a fresh container for each run and uses `n_concurrent_trials` for the
shared concurrency limit. Tasks in one case use the same Hermes session and
workspace in the order listed. The existing fixed `cases[].turns` profiles use
the same experiment command.

## Choose a profile

- [AP onboarding](ap-onboarding.yaml) configures a fresh Worker for chat uploads,
  drafts only and zero tolerance, then verifies the stored SQLite configuration
  and the three verified ADP app bindings.
- [AP matching](ap-matching.yaml) runs onboarding before each independent case.
  Keep just the `cases` you want to run, including the `&onboarding` definition
  when retaining aliases. The examples check actual case state and source
  allocations. A correct request for human review is an expected result for
  discrepancy cases.

| Scenario | Source dataset | Expected outcome |
| --- | --- | --- |
| `normal-three-way` | Three-way case01 | Invoice is matched with the correct PO and delivery note. |
| `missing-delivery-recovery` | Three-way case18 | Invoice and PO first, then delivery note. The original invoice moves from waiting for the delivery note to matched. |
| `supplier-conflict` | Three-way case07 | The Worker records the supplier conflict and requests review. |
| `quantity-shortage` | Three-way case12 | The Worker records the delivery quantity shortfall and requests review. |
| `product-conflict` | Three-way case20 | The Worker records the product mismatch and requests review. |
| `weekly-consolidation` | Weekly set 2, invoices without delivery references | The same PO is reused across three periods. Each invoice uses its seven deliveries. The two fourth-period notes sent with the final batch are excluded from all three allocations. |

The tests use the SQLite schema in AP 1.5.37 and 1.6.0. The expected outcomes
are from the supplied datasets and are scored by [verify-ap.sh](checks/verify-ap.sh).
Adjust the verifier if the selected template uses a different schema or workflow.

## Run an AP example

From the Harbor checkout root, copy this profile directory into your local
experiment folder and set `template` to your AP ZIP. To run the matching profile,
put the contents of the `三单匹配20case` dataset under `inputs/three-way/` and
those of `第二组_周期汇总_不列供货单号` under `inputs/weekly/`. Keep their original
subdirectories and PDF names. The profiles list the exact files they use. All
paths are relative to the profile. Source documents and template packages are
supplied locally.

Build `harbor-ap-onboarding:fresh` for the selected ZIP using the
[onboarding fixture steps](../ap-onboarding.md). Both profiles use that same
fresh image and verify that initialization succeeded before any matching task.
Set the worker and simulated user model names and API base URLs in YAML.
The simulated user uses an OpenAI Chat Completions endpoint that supports JSON
output. Set `MODEL_API_KEY`, `SIMULATED_USER_API_KEY`, and `ADP_API_KEY` in the
launch environment. ADP onboarding can create apps in the selected ADP account.
Use a test account when running role comparisons.

```sh
python -m examples.agents.clawworker_preview.experiment \
  --config ./tmp/my-simulation/ap-onboarding.yaml \
  --output ./tmp/my-simulation/run-01
```

## Define another scenario

Choose one or more Markdown files in `simulated_user.souls`. Keep the template,
models, facts and attachments identical to compare roles. The SOUL describes
how a person talks and attempts recovery. The case describes what they know and
want. A task has a `goal`, optional `facts`, an `attachments` map from alias to
file, optional `authorization`, and an optional `verifier` shell script. The model
can only select an attachment alias from the current task. Files are copied to
the Worker just before the selected turn. Invalid selections are held and the
simulated user gets up to two requests to choose again. These requests appear in
the user model's statistics. A selection still invalid after those attempts is
reported as a simulated-user failure. Use `facts` to describe when the user
should send them, or split the scenario into tasks to make files available only
after earlier tasks complete. For example, the missing delivery case keeps the
delivery PDF in its second task.

The model sees earlier user messages, Worker replies, and visible choice
questions with their option labels. It can send a message, start a repair,
request completion verification, or stop. A task verifier uses the same reward
file protocol as fixed dialogues. Use `verifier_env` for expected values needed
only by the verifier. A failed check returns a short notice to the simulated user.
Detailed diagnostics are stored in the verifier logs. The scenario authorization
guides what the model may confirm. Configure a drafts-only Worker or sandbox
accounts when testing actions with external effects.

A case defaults to 60 turns and 3600 seconds of dialogue time. Set `max_turns`
and `max_elapsed_sec` per case, and `max_repairs` per task (default 2). Image
build and container startup are outside the dialogue clock. A turn is one sent
user message and one completed Worker reply. Repair messages count toward that
limit. The runner stops the case after repairs are exhausted and retains the
results of earlier tasks. `timeout_sec` still limits each Worker turn.

Use `summary.json` for case/persona and task results, first and final rewards,
repair counts and duration. Each Worker turn contains `worker_model_requests`
and refers to its user request with `simulated_user_request_index`. The full
`simulated_user_requests` list includes decisions to verify or stop. Provider
usage is kept separately for both sides, with unknown counters as `null`.
The runner also saves a report during execution under each trial's `simulation/`
directory. Cases with no verifiers are reported as `unverified`.

The same scenario format works with other templates running in the Clawworker
Preview harness. The local fixture exposes Gateway replies and choice cards.
For scenarios that rely on cloud connectors or Host background notifications,
include the corresponding test environment and verify its behavior separately.
