"""Actual Docker template replacement and a complete config-driven Harbor job."""

import asyncio
import io
import json
import stat
import subprocess
import zipfile

import pytest

from harbor.agents.clawworker_preview import template_image
from harbor.agents.clawworker_preview.experiment import ExperimentConfig, run_experiment
from harbor.models.model_api_format import ModelApiFormat
from harbor.models.trial.config import AgentConfig
from tests.unit.agents.test_clawworker_template_image import package_bytes

pytestmark = [pytest.mark.runtime, pytest.mark.integration]


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
    config = ExperimentConfig(
        template=package,
        question="Check the attached input and report the actual result.",
        attachments=[attachment],
        verifier=verifier,
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
    sent = json.dumps(preview_model.requests)
    assert "You are experiment-worker, version 3.2." in sent
    assert "Keep the actual task results." in sent
    saved = json.loads((tmp_path / "experiment/summary.json").read_text())
    assert saved == summary
    assert "synthetic-model-key" not in json.dumps(summary)
    assert "synthetic-adp-key" not in json.dumps(summary)
