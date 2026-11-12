"""Build a reusable Worker template layer on the verified Preview base image."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
from typing import Any
import zipfile

from .runtime import verify_template

LOCK = json.loads(Path(__file__).with_name("preview.json").read_text())
PYTHON = "/opt/hermes/.venv/bin/python"
RUNNER = "/opt/clawworker-harbor/harbor-runtime.py"
BASE_IMAGE = "harbor-clawworker-preview:0.22.0"


def prepare(
    package: Path, destination: Path, base_image: str, tag: str | None = None
) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError(destination)
    if package.stat().st_size > 50 * 1024**2:
        raise ValueError("Template ZIP exceeds the Host package size limit")
    raw = package.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw), metadata_encoding="utf-8") as archive:
        if archive.getinfo("manifest.json").file_size > 256 * 1024:
            raise ValueError("Template manifest exceeds the Host size limit")
        manifest_raw = archive.read("manifest.json")
        manifest = json.loads(manifest_raw)
        identity = {
            "template_id": manifest["templateId"],
            "template_version": manifest["version"],
            "package_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "base_image": base_image,
        }
        verified = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                PYTHON,
                base_image,
                RUNNER,
                "verify",
            ],
            capture_output=True,
            check=True,
        )
        runtime = json.loads(verified.stdout)
        for key in ("host_commit", "hermes_commit", "runtime_manifest_sha256"):
            if runtime.get(key) != LOCK[key]:
                raise ValueError("Template base image does not match Preview: " + key)
            identity[key] = runtime[key]
        request = {
            "templateId": identity["template_id"],
            "templateVersion": identity["template_version"],
            "package": {"manifestSha256": identity["manifest_sha256"]},
        }
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--entrypoint",
                "node",
                base_image,
                "/opt/clawworker-harbor/template-unpack.mjs",
                json.dumps(request),
            ],
            input=raw,
            capture_output=True,
            check=True,
        )
        inspected = subprocess.run(
            ["docker", "image", "inspect", base_image, "--format", "{{.Id}}"],
            capture_output=True,
            check=True,
        )
        identity["base_image_id"] = inspected.stdout.decode().strip()
        identity["image"] = tag or (
            "harbor-template:"
            + (identity["template_id"] + "-" + identity["template_version"])[:115]
            + "-"
            + identity["package_sha256"][:12]
        )
        destination.mkdir(parents=True)
        definition = destination / "definition"
        definition.mkdir()
        # The canonical Host validator has checked names, links and the complete
        # manifest before any user-selected package bytes are written here.
        for member in archive.infolist():
            if member.is_dir():
                continue
            path = definition / member.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(member))
            path.chmod(0o644)
        for path in (definition, *(p for p in definition.rglob("*") if p.is_dir())):
            path.chmod(0o755)
    (destination / "template.json").write_text(json.dumps(identity, indent=2) + "\n")
    verify_template(definition, destination / "template.json")
    (destination / "Dockerfile").write_text(
        "ARG BASE_IMAGE=" + json.dumps(base_image) + "\nFROM ${BASE_IMAGE}\nUSER root\n"
        # This clears only the new image layer's inherited definition. It never
        # deletes a host directory or changes the selected base image.
        "RUN rm -rf /opt/clawworker-harbor/definition\n"
        "COPY definition /opt/clawworker-harbor/definition\n"
        "COPY template.json /opt/clawworker-harbor/template.json\n"
        + "\n".join(
            "LABEL io.clawworker."
            + key.replace("_", ".")
            + "="
            + json.dumps(str(identity[key]))
            for key in (
                "template_id",
                "template_version",
                "package_sha256",
                "manifest_sha256",
            )
        )
        + "\nUSER server\nWORKDIR /workspace/work\n"
    )
    (destination / ".dockerignore").write_text(
        "*\n!Dockerfile\n!definition/\n!definition/**\n!template.json\n"
    )
    (destination / "harbor-image.yaml").write_text(
        "services:\n  main:\n    image: " + json.dumps(identity["image"]) + "\n"
    )
    return identity


def build(
    package: Path,
    destination: Path,
    base_image: str = BASE_IMAGE,
    tag: str | None = None,
) -> dict[str, Any]:
    identity = prepare(package, destination, base_image, tag)
    subprocess.run(
        [
            "docker",
            "build",
            "--build-arg",
            "BASE_IMAGE=" + base_image,
            "-t",
            identity["image"],
            str(destination),
        ],
        check=True,
    )
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--context", required=True, type=Path, help="A new directory")
    parser.add_argument("--base-image", default=BASE_IMAGE)
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.template.resolve(),
                args.context.resolve(),
                args.base_image,
                args.tag,
            )
        )
    )


if __name__ == "__main__":
    main()
