"""Template selection changes the installed bytes and provenance, not runner code."""

import hashlib
import io
import json
import subprocess
import zipfile

import pytest


def package_bytes(template_id="worker-alpha", version="1.0", extra=None):
    files = {
        "SOUL.md": f"You are {template_id}, version {version}.\n".encode(),
        "MEMORY.md": b"Keep the actual task results.\n",
        "mcp.json": b"[]",
        "schedules.json": b"[]",
        "skills/check/SKILL.md": b"---\nname: check\ndescription: Check actual results.\n---\nUse terminal to check results.\n",
        **(extra or {}),
    }
    manifest = {
        "formatVersion": 1,
        "templateId": template_id,
        "version": version,
        "files": [
            {
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in sorted(files.items())
        ],
    }
    files["manifest.json"] = json.dumps(manifest).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def install_fixture(tmp_path, template_id="worker-alpha", version="1.0"):
    raw = package_bytes(template_id, version)
    definition = tmp_path / "definition"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        archive.extractall(definition)
    receipt = tmp_path / "template.json"
    receipt.write_text(
        json.dumps(
            {
                "template_id": template_id,
                "template_version": version,
                "package_sha256": hashlib.sha256(raw).hexdigest(),
                "manifest_sha256": hashlib.sha256(
                    (definition / "manifest.json").read_bytes()
                ).hexdigest(),
            }
        )
    )
    return definition, receipt


@pytest.mark.parametrize(
    ("template_id", "version"), [("worker-alpha", "1.0"), ("other-worker", "2.7")]
)
def test_installed_template_identity_comes_from_actual_package(
    tmp_path, template_id, version
):
    from harbor.agents.clawworker_preview.runtime import verify_template

    definition, receipt = install_fixture(tmp_path, template_id, version)
    result = verify_template(definition, receipt)
    assert result["template_id"] == template_id
    assert result["template_version"] == version


@pytest.mark.parametrize("mutation", ["changed", "leftover", "missing", "no-receipt"])
def test_template_verification_rejects_drift(tmp_path, mutation):
    from harbor.agents.clawworker_preview.runtime import verify_template

    definition, receipt = install_fixture(tmp_path)
    if mutation == "changed":
        (definition / "SOUL.md").write_text("Different instructions")
    elif mutation == "leftover":
        (definition / "old-only.py").write_text("print('retired')")
    elif mutation == "missing":
        (definition / "MEMORY.md").unlink()
    else:
        receipt.unlink()
    with pytest.raises(ValueError, match="[Tt]emplate"):
        verify_template(definition, receipt)


def test_template_context_is_derived_from_selected_zip(tmp_path, monkeypatch):
    from harbor.agents.clawworker_preview import template_image

    def docker(command, **kwargs):
        if "inspect" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout=b"sha256:base-image-id\n"
            )
        if "verify" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(template_image.LOCK).encode()
            )
        return subprocess.CompletedProcess(command, 0, stdout=b'{"validated":true}')

    monkeypatch.setattr(template_image.subprocess, "run", docker)
    package = tmp_path / "selected.zip"
    raw = package_bytes(
        "other-worker", "2.7", {"skills/check/scripts/new.py": b"print('new')\n"}
    )
    package.write_bytes(raw)
    context = tmp_path / "context"
    result = template_image.prepare(package, context, "preview-base:test")
    assert result["template_id"] == "other-worker"
    assert result["base_image_id"] == "sha256:base-image-id"
    assert result["template_version"] == "2.7"
    assert result["package_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (
        context / "definition/skills/check/scripts/new.py"
    ).read_bytes() == b"print('new')\n"
    assert "other-worker" in result["image"]
    assert json.loads((context / "template.json").read_text()) == result
    assert (
        "image: " + json.dumps(result["image"])
        in (context / "harbor-image.yaml").read_text()
    )
    assert not (context / "selected.zip").exists()


def test_rejected_package_creates_no_build_context(tmp_path, monkeypatch):
    from harbor.agents.clawworker_preview import template_image

    def docker(command, **kwargs):
        if "verify" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(template_image.LOCK).encode()
            )
        raise subprocess.CalledProcessError(
            1, command, stderr=b"template_package_invalid"
        )

    monkeypatch.setattr(template_image.subprocess, "run", docker)
    package = tmp_path / "selected.zip"
    package.write_bytes(package_bytes())
    with pytest.raises(subprocess.CalledProcessError):
        template_image.prepare(package, tmp_path / "context", "preview-base:test")
    assert not (tmp_path / "context").exists()


def test_existing_context_is_preserved(tmp_path):
    from harbor.agents.clawworker_preview import template_image

    context = tmp_path / "context"
    context.mkdir()
    (context / "keep").write_text("keep")
    with pytest.raises(FileExistsError):
        template_image.prepare(tmp_path / "unused.zip", context, "preview-base:test")
    assert (context / "keep").read_text() == "keep"
