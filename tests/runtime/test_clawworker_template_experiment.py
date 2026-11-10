"""Actual Docker template replacement and a complete config-driven Harbor job."""

import asyncio
import io
import json
from pathlib import Path
import shlex
import stat
import subprocess
import threading
import zipfile

import pytest

from harbor.agents.clawworker_preview import template_image
from harbor.agents.clawworker_preview.experiment import (
    ExperimentConfig,
    load_config,
    run_experiment,
)
from harbor.models.model_api_format import ModelApiFormat
from harbor.models.trial.config import AgentConfig
from tests.unit.agents.test_clawworker_template_image import package_bytes

pytestmark = [pytest.mark.runtime, pytest.mark.integration]


@pytest.mark.parametrize(
    "previous",
    [None, {"status": "failed", "session_id": "old"}, {"status": "completed"}],
)
def test_invalid_resume_clears_previous_turn_outputs(previous):
    script = """
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
spec = importlib.util.spec_from_file_location('preview_runtime', '/opt/clawworker-harbor/harbor-runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
logs = Path('/logs/agent')
previous = json.loads(PREVIOUS)
if previous is not None:
    (logs / 'result.json').write_text(json.dumps(previous))
for name in ('preview-metrics.json', 'hermes-session.jsonl', 'trajectory.json'):
    (logs / name).write_text('{}')
try:
    runtime.run(SimpleNamespace(resume=True), {})
except ValueError as error:
    assert 'Cannot resume' in str(error)
else:
    raise AssertionError('Invalid continuation must not start a fresh run')
assert not list(logs.iterdir()), 'Previous turn outputs must not leak into failed turn metrics'
""".replace("PREVIOUS", repr(json.dumps(previous)))
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            template_image.PYTHON,
            template_image.BASE_IMAGE,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("invalid", ["changed-content", "illegal-path", "symlink"])
def test_canonical_host_rejects_invalid_packages_before_extraction(tmp_path, invalid):
    raw = package_bytes(
        extra={"../escape.py": b"escape"} if invalid == "illegal-path" else None
    )
    if invalid != "illegal-path":
        buffer = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(raw)) as original,
            zipfile.ZipFile(buffer, "w") as changed,
        ):
            for info in original.infolist():
                data = original.read(info)
                if info.filename == "SOUL.md":
                    if invalid == "symlink":
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    else:
                        data = b"Changed after manifest signing"
                changed.writestr(info, data)
        raw = buffer.getvalue()
    package = tmp_path / "invalid.zip"
    package.write_bytes(raw)
    with pytest.raises(subprocess.CalledProcessError) as error:
        template_image.prepare(package, tmp_path / "context", template_image.BASE_IMAGE)
    assert b"template_package_invalid" in error.value.stderr
    assert not (tmp_path / "context").exists()
    assert not (tmp_path / "escape.py").exists()


def test_replacing_template_drops_inherited_files_and_preserves_base(tmp_path):
    first = tmp_path / "first.zip"
    first.write_bytes(
        package_bytes(
            "worker-alpha", "1.0", {"skills/check/scripts/old.py": b"print('old')"}
        )
    )
    initial = template_image.build(first, tmp_path / "first-image")
    second = tmp_path / "second.zip"
    second.write_bytes(
        package_bytes(
            "other-worker", "2.7", {"skills/check/scripts/new.py": b"print('new')"}
        )
    )
    replacement = template_image.build(
        second, tmp_path / "second-image", initial["image"]
    )
    for image, expected_id, filename in (
        (initial, "worker-alpha", "old.py"),
        (replacement, "other-worker", "new.py"),
    ):
        checked = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                template_image.PYTHON,
                image["image"],
                template_image.RUNNER,
                "verify",
            ],
            capture_output=True,
            check=True,
        )
        identity = json.loads(checked.stdout)
        assert identity["template"]["template_id"] == expected_id
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                template_image.PYTHON,
                image["image"],
                "-c",
                "from pathlib import Path; import json; print(json.dumps(sorted(p.name for p in Path('/opt/clawworker-harbor/definition/skills/check/scripts').iterdir())))",
            ],
            capture_output=True,
            check=True,
        )
        assert json.loads(probe.stdout) == [filename]


@pytest.mark.asyncio
async def test_selected_config_builds_image_loads_template_and_runs_question(
    tmp_path, monkeypatch, preview_model
):
    package = tmp_path / "template.zip"
    package.write_bytes(package_bytes("experiment-worker", "3.2"))
    attachment = tmp_path / "input.txt"
    attachment.write_text("fixture-input")
    verifier = tmp_path / "verify.sh"
    verifier.write_text(
        "#!/bin/sh\nset -eu\n"
        "/opt/hermes/.venv/bin/python - <<'PY'\n"
        "from pathlib import Path\nimport json\n"
        "v=json.loads(Path('/workspace/work/preview-proof.json').read_text())\n"
        "assert v == {'input': 'fixture-input', 'adp': True}\n"
        "Path('/logs/verifier/reward.txt').write_text('1\\n')\nPY\n"
    )
    preview_model.command = (
        "python3 - <<'PY'\nfrom pathlib import Path\nimport json, os\n"
        "Path('preview-proof.json').write_text(json.dumps({'input':Path('attachments/input.txt').read_text(),'adp':bool(os.getenv('ADP_API_KEY'))}))\nPY"
    )
    monkeypatch.setenv("TEST_MODEL_API_KEY", "synthetic-model-key")
    monkeypatch.setenv("ADP_API_KEY", "synthetic-adp-key")
    profile = tmp_path / "experiment.yaml"
    profile.write_text(
        json.dumps(
            {
                "template": "./template.zip",
                "question": "Check the attached input and report the actual result.",
                "attachments": ["./input.txt"],
                "verifier": "./verify.sh",
                "agent": {
                    "name": "clawworker-preview",
                    "model_name": "openai/test-model",
                    "base_url": preview_model.base_url,
                    "model_api_key_name": "TEST_MODEL_API_KEY",
                    "model_api_format": "openai_chat_completions",
                },
            }
        )
    )
    launcher = tmp_path / "unrelated launch directory"
    launcher.mkdir()
    monkeypatch.chdir(launcher)
    config = load_config(profile)
    async with asyncio.timeout(300):
        summary = await run_experiment(config, tmp_path / "experiment")
    assert summary["stats"]["n_completed_trials"] == 1
    assert summary["stats"]["n_errored_trials"] == 0
    assert summary["template"]["template_id"] == "experiment-worker"
    assert summary["template"]["template_version"] == "3.2"
    assert summary["template_loading"] == "host-native"
    assert summary["scored"] is True
    trial = summary["trials"][0]
    assert trial["exception"] is None
    assert trial["rewards"] == {"reward": 1.0}
    assert trial["llm_http_requests"] == len(preview_model.requests) >= 2
    requests = trial["turns"][0]["model_requests"]
    complete = [
        r for r in requests if r["status"] == 200 and r["duration_seconds"] is not None
    ]
    assert len(complete) >= 2
    assert [r["request_index"] for r in requests] == list(range(1, len(requests) + 1))
    assert all(
        r["usage"]["input_tokens"] == 10 and r["usage"]["output_tokens"] == 10
        for r in complete
    )
    sent = json.dumps(preview_model.requests)
    assert "You are experiment-worker, version 3.2." in sent
    assert "Keep the actual task results." in sent
    saved = json.loads((tmp_path / "experiment/summary.json").read_text())
    assert saved == summary
    assert "synthetic-model-key" not in json.dumps(summary)
    assert "synthetic-adp-key" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_fixed_dialogue_preserves_native_history_database_and_delays_inputs(
    tmp_path, monkeypatch, preview_model
):
    package = tmp_path / "template.zip"
    package.write_bytes(package_bytes("dialogue-worker", "1.0"))
    invoice = tmp_path / "invoice.txt"
    invoice.write_text("invoice-input")
    delivery = tmp_path / "delivery.txt"
    delivery.write_text("delivery-input")
    verifier = tmp_path / "check.sh"
    verifier.write_text(
        "#!/bin/sh\nset -eu\npython3 - <<'PY'\n"
        "import sqlite3, json\nfrom pathlib import Path\n"
        "db=sqlite3.connect('/workspace/work/proof.db')\n"
        "assert json.loads(Path('/workspace/work/first-observation.json').read_text()) == {'fresh': True, 'future_file_present': False}\n"
        "assert db.execute('select value from proof order by rowid').fetchall() == [('invoice-input',), ('delivery-input',)]\n"
        "Path('/logs/verifier/reward.txt').write_text('1')\nPY\n"
    )
    # Neither first turn can reach its terminal until both trials are active.
    first_requests = threading.Barrier(2)

    def is_second(body):
        return "Continue with the delivery" in next(
            (
                m.get("content", "")
                for m in reversed(body.get("messages", []))
                if m.get("role") == "user"
            ),
            "",
        )

    def command(body):
        if is_second(body):
            return (
                "python3 - <<'PY'\nimport sqlite3\nfrom pathlib import Path\n"
                "db=sqlite3.connect('proof.db')\n"
                "assert db.execute('select value from proof').fetchall() == [('invoice-input',)]\n"
                "assert 'Retained dialogue memory.' in Path('/opt/data/harbor/memories/MEMORY.md').read_text()\n"
                "db.execute('insert into proof values (?)', (Path('attachments/turn-02/delivery.txt').read_text(),))\n"
                "db.commit()\nPY"
            )
        first_requests.wait(timeout=60)
        return (
            "python3 - <<'PY'\nimport sqlite3, json\nfrom pathlib import Path\n"
            "Path('first-observation.json').write_text(json.dumps({'fresh': not Path('proof.db').exists(), 'future_file_present': Path('attachments/turn-02/delivery.txt').exists()}))\n"
            "assert not Path('proof.db').exists()\n"
            "assert not Path('attachments/turn-02/delivery.txt').exists()\n"
            "db=sqlite3.connect('proof.db')\n"
            "db.execute('create table proof (value text)')\n"
            "db.execute('insert into proof values (?)', (Path('attachments/turn-01/invoice.txt').read_text(),))\n"
            "db.commit()\n"
            "memory=Path('/opt/data/harbor/memories/MEMORY.md')\n"
            "memory.write_text(memory.read_text() + '\\nRetained dialogue memory.\\n')\nPY"
        )

    def reply(body):
        if is_second(body):
            history = json.dumps(body.get("messages", [])[:-1])
            return (
                "Remembered mango-271 and continued."
                if "mango-271" in history
                else "History was lost."
            )
        return "First turn recorded; send the delivery."

    preview_model.command = command
    preview_model.reply = reply
    monkeypatch.setenv("TEST_MODEL_API_KEY", "synthetic-key")
    config = ExperimentConfig(
        template=package,
        cases=[
            {
                "name": "follow-up",
                "turns": [
                    {
                        "question": "Start fixed case. Remember token mango-271.",
                        "attachments": [invoice],
                    },
                    {
                        "question": "Continue with the delivery. What was the token?",
                        "attachments": [delivery],
                        "verifier": verifier,
                    },
                ],
            }
        ],
        agent=AgentConfig(
            name="clawworker-preview",
            model_name="openai/test-model",
            base_url=preview_model.base_url,
            model_api_key_name="TEST_MODEL_API_KEY",
            model_api_format=ModelApiFormat.OPENAI_CHAT_COMPLETIONS,
        ),
        n_attempts=2,
        n_concurrent_trials=2,
    )
    async with asyncio.timeout(300):
        summary = await run_experiment(config, tmp_path / "experiment")
    assert summary["stats"]["n_errored_trials"] == 0
    assert summary["stats"]["n_completed_trials"] == 2
    assert len(summary["trials"]) == 2
    assert summary["n_concurrent_trials"] == 2
    sessions = []
    for trial in summary["trials"]:
        assert trial["execution_complete"] is True
        assert trial["verification_status"] == "passed"
        first, second = trial["turns"]
        assert first["reply"] == "First turn recorded; send the delivery."
        assert second["reply"] == "Remembered mango-271 and continued."
        assert first["session_id"] == second["session_id"]
        assert first["run_id"] != second["run_id"]
        assert first["verification_status"] == "unverified"
        assert second["verification_status"] == "passed"
        assert second["rewards"] == {"reward": 1.0}
        for turn in (first, second):
            calls = turn["model_requests"]
            assert [call["request_index"] for call in calls] == list(
                range(1, len(calls) + 1)
            )
            finished = [
                call
                for call in calls
                if call["status"] == 200 and call["duration_seconds"] is not None
            ]
            assert len(finished) >= 2
            assert all(
                call["usage"]["input_tokens"] == 10
                and call["usage"]["output_tokens"] == 10
                for call in finished
            )
        for turn, expected_seeded in ((first, True), (second, False)):
            metrics = json.loads(
                (Path(turn["logs"]) / "preview-metrics.json").read_text()
            )
            assert metrics["configuration"]["memorySeeded"] is expected_seeded
        assert all(
            t["llm_http_requests"] >= 2 and t["agent_execution_seconds"] > 0
            for t in trial["turns"]
        )
        assert trial["llm_http_requests"] == sum(
            t["llm_http_requests"] for t in trial["turns"]
        )
        sessions.append(first["session_id"])
    assert len(set(sessions)) == 2
    assert sum(t["llm_http_requests"] for t in summary["trials"]) == len(
        preview_model.requests
    )


@pytest.mark.asyncio
async def test_onboarding_containers_have_distinct_stable_worker_identities(tmp_path):
    package = tmp_path / "template.zip"
    package.write_bytes(package_bytes("onboarding-isolation", "1.0"))
    initial = template_image.build(package, tmp_path / "initial")
    examples = Path(__file__).resolve().parents[2] / "examples/clawworker-preview"
    fixture_image = "harbor-onboarding-fixture:" + tmp_path.name.lower()
    built = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(examples / "ap-onboarding.Dockerfile"),
            "--build-arg",
            "TEMPLATE_IMAGE=" + initial["image"],
            "-t",
            fixture_image,
            str(examples),
        ],
        text=True,
        capture_output=True,
    )
    assert built.returncode == 0, built.stderr
    # The experiment loads the selected template on top of the fixture again.
    image = template_image.build(package, tmp_path / "reloaded", fixture_image)
    code = """
import json,os,re,sqlite3,sys
from pathlib import Path
workspace=Path('/workspace/work').resolve()
worker=os.environ['CLAWWORKER_WORKER_ID']
assert re.fullmatch(r'prun_[0-9a-f]{32}',worker)
assert workspace.name==worker
assert workspace.parts[3]==os.environ['CLAWWORKER_OWNER_ID']
db=sqlite3.connect(workspace/'proof.db')
if sys.argv[1]=='first':
    assert not (workspace/'ap').exists()
    assert not Path('/opt/data/harbor/memories/MEMORY.md').exists()
    db.execute('create table proof(value text)')
    db.execute('insert into proof values (?)',(worker,))
    db.commit()
assert db.execute('select value from proof').fetchone()==(worker,)
db.close()
print(json.dumps({'worker':worker,'workspace':str(workspace)}))
"""

    async def probe():
        started = await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "run",
                "-d",
                "--network",
                "none",
                image["image"],
            ],
            text=True,
            capture_output=True,
        )
        assert started.returncode == 0, started.stderr
        container = started.stdout.strip()
        try:
            observations = []
            for phase in ("first", "repeat"):
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        "docker",
                        "exec",
                        "-u",
                        "server",
                        "-w",
                        "/workspace/work",
                        container,
                        "bash",
                        "-c",
                        template_image.PYTHON
                        + " -c "
                        + shlex.quote(code)
                        + " "
                        + phase,
                    ],
                    text=True,
                    capture_output=True,
                )
                assert result.returncode == 0, result.stderr
                observations.append(json.loads(result.stdout))
            assert observations[0] == observations[1]
            return observations[0]
        finally:
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "rm", "--force", container],
                capture_output=True,
                check=True,
            )

    async with asyncio.TaskGroup() as group:
        first = group.create_task(probe())
        second = group.create_task(probe())
    assert first.result()["worker"] != second.result()["worker"]
    assert first.result()["workspace"] != second.result()["workspace"]


@pytest.mark.asyncio
async def test_failed_turn_check_stops_only_its_case(
    tmp_path, monkeypatch, preview_model
):
    package = tmp_path / "template.zip"
    package.write_bytes(package_bytes("gated-worker", "1.0"))
    failed = tmp_path / "failed.sh"
    failed.write_text("#!/bin/sh\necho 0 > /logs/verifier/reward.txt\n")
    monkeypatch.setenv("TEST_MODEL_API_KEY", "synthetic-key")
    config = ExperimentConfig(
        template=package,
        cases=[
            {
                "name": "blocked",
                "turns": [
                    {"question": "Run the blocked case.", "verifier": failed},
                    {"question": "NEVER_SEND_THIS_FUTURE_ANSWER"},
                ],
            },
            {
                "name": "independent",
                "turns": [{"question": "Run an independent question."}],
            },
        ],
        agent=AgentConfig(
            name="clawworker-preview",
            model_name="openai/test-model",
            base_url=preview_model.base_url,
            model_api_key_name="TEST_MODEL_API_KEY",
            model_api_format=ModelApiFormat.OPENAI_CHAT_COMPLETIONS,
        ),
    )
    async with asyncio.timeout(300):
        summary = await run_experiment(config, tmp_path / "experiment")
    by_case = {trial["case"]: trial for trial in summary["trials"]}
    blocked = by_case["blocked"]
    assert blocked["verification_status"] == "failed"
    assert blocked["execution_complete"] is False
    assert blocked["turns"][0]["runtime_status"] == "completed"
    assert blocked["turns"][1]["runtime_status"] == "not_run"
    assert by_case["independent"]["execution_complete"] is True
    assert by_case["independent"]["verification_status"] == "unverified"
    assert "NEVER_SEND_THIS_FUTURE_ANSWER" not in json.dumps(preview_model.requests)
    independent_requests = [
        body
        for body in preview_model.requests
        if "Run an independent question." in json.dumps(body.get("messages", []))
    ]
    assert independent_requests
    assert all(
        "Run the blocked case." not in json.dumps(body["messages"])
        for body in independent_requests
    )
    assert summary["evaluation"]["passed"] == 0
    assert summary["evaluation"]["failed"] == 1
    assert summary["evaluation"]["unverified"] == 1
