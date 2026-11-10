"""Build Harbor's single-container image from the pinned Host build flow.

Run directly with Python; preparing the image does not require Harbor installed.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any

HERE = Path(__file__).resolve().parent


def export_host(repository: Path, commit: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(
        ["git", "-C", str(repository), "archive", "--format=tar", commit]
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        stream.extractall(destination, filter="data")


def prepare(
    host_repository: Path, hermes_repository: Path, destination: Path, architecture: str
) -> dict[str, Any]:
    lock = json.loads((HERE / "preview.json").read_text())
    if destination.exists():
        raise FileExistsError(destination)
    subprocess.run(
        [
            "git",
            "-C",
            str(hermes_repository),
            "cat-file",
            "-e",
            lock["hermes_commit"] + "^{commit}",
        ],
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="harbor-preview-source-") as temporary:
        root = Path(temporary)
        source = root / "host"
        export_host(host_repository, lock["host_commit"], source)
        (source / "hermes-agent").rmdir()
        (source / "hermes-agent").symlink_to(
            hermes_repository.resolve(), target_is_directory=True
        )
        # Install only the two build dependencies in this disposable export. The
        # original Host checkout, package files and node_modules remain untouched.
        packages = json.loads((source / "package-lock.json").read_text())["packages"]
        dependencies = root / "dependencies"
        dependencies.mkdir()
        subprocess.run(
            [
                "npm",
                "install",
                "--prefix",
                str(dependencies),
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--package-lock=false",
                "--no-save",
                *[
                    name + "@" + packages["node_modules/" + name]["version"]
                    for name in ["esbuild", "yaml"]
                ],
            ],
            check=True,
        )
        # esbuild includes module-relative paths in its output. Match the Host
        # layout physically, otherwise a symlink to our cache changes the bundle.
        shutil.move(dependencies / "node_modules", source / "node_modules")
        subprocess.run(
            [
                sys.executable,
                str(source / "scripts/worker-host/server-runtime-v22/build.py"),
                "--repo",
                str(source),
                "--output",
                str(destination),
            ],
            check=True,
        )
        # Reuse the Host package/content contract without Service download/signing
        # authorization: these are user-selected local benchmark ZIPs.
        subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                """
import { build } from 'esbuild';
import { join } from 'node:path';
const [entry, repo, output] = process.argv.slice(1);
await build({entryPoints:[entry], outfile:output, bundle:true, platform:'node',
  format:'esm', target:'node22', alias:{
    'clawworker-template-package':join(repo,'src/host/templates/package.ts'),
    'clawworker-template-definition':join(repo,'src/node/runtime-config/template-definition.ts')},
  banner:{js:'import { createRequire } from "node:module"; const require = createRequire(import.meta.url);'}});
""",
                str(HERE / "template_unpack.mjs"),
                str(source),
                str(destination / "template-unpack.mjs"),
            ],
            cwd=source,
            check=True,
        )
    if (
        hashlib.sha256((destination / "manifest.json").read_bytes()).hexdigest()
        != lock["runtime_manifest_sha256"]
    ):
        raise ValueError(
            "Rebuilt Host runtime manifest differs from the verified Preview manifest"
        )
    # Keep the original Host Dockerfile and manifest intact. Adapt only a second
    # Dockerfile so the copied manifest still describes the canonical Host inputs.
    original = (destination / "Dockerfile").read_text()
    original_base = lock["base_images"]["arm64"]
    if "FROM " + original_base not in original:
        raise ValueError("Pinned Host base image changed")
    dockerfile = original.replace(
        "FROM " + original_base,
        "FROM " + lock["base_images"][architecture] + " AS clawworker-preview-base",
        1,
    )
    dockerfile += """
# Harbor owns the container lifecycle. The Agent drives the native Gateway;
# there is no cloud supervisor, SSH computer, or second container.
FROM clawworker-preview-base
USER root
RUN install -d -m 0700 -o server -g server /opt/data/harbor \\
    && install -d -o server -g server /workspace/work /logs/agent /logs/verifier
COPY harbor-runtime.py preview.json template-unpack.mjs /opt/clawworker-harbor/
COPY definition /opt/clawworker-harbor/definition
USER server
WORKDIR /workspace/work
ENTRYPOINT []
CMD ["sleep", "infinity"]
"""
    (destination / "Dockerfile.harbor").write_text(dockerfile)
    shutil.copyfile(HERE / "runtime.py", destination / "harbor-runtime.py")
    shutil.copyfile(HERE / "preview.json", destination / "preview.json")
    definition = destination / "definition"
    definition.mkdir()
    (definition / "SOUL.md").write_text(
        "You are a Laiye Worker running a Harbor task. Use the available tools to complete the user's request and report the actual result.\n"
    )
    provenance = {
        **lock,
        "architecture": architecture,
        "test_base_image": lock["base_images"][architecture],
        "terminal_backend": "local",
        "template_unpack_sha256": hashlib.sha256(
            (destination / "template-unpack.mjs").read_bytes()
        ).hexdigest(),
    }
    (destination / "harbor-build.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-repo", type=Path, required=True)
    parser.add_argument(
        "--hermes-repo",
        type=Path,
        required=True,
        help="Git repository containing the pinned Hermes commit; it is only read",
    )
    parser.add_argument(
        "--context", type=Path, required=True, help="A new, nonexistent directory"
    )
    parser.add_argument(
        "--architecture",
        choices=["amd64", "arm64"],
        default="arm64" if platform.machine() in {"aarch64", "arm64"} else "amd64",
    )
    parser.add_argument("--tag", default="harbor-clawworker-preview:0.22.0")
    args = parser.parse_args()
    prepare(
        args.host_repo.resolve(),
        args.hermes_repo.resolve(),
        args.context.resolve(),
        args.architecture,
    )
    subprocess.run(
        [
            "docker",
            "build",
            "--platform",
            "linux/" + args.architecture,
            "-f",
            str(args.context / "Dockerfile.harbor"),
            "-t",
            args.tag,
            str(args.context),
        ],
        check=True,
    )
    print(
        json.dumps(
            {
                "image": args.tag,
                "context": str(args.context),
                "architecture": args.architecture,
            }
        )
    )


if __name__ == "__main__":
    main()
