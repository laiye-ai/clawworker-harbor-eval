"""Container entry for the canonical Preview Gateway with local tool execution.

This file also runs standalone inside the image; it does not import Harbor.
"""

import argparse
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from typing import Any, cast, override
import urllib.error
import urllib.request

ROOT = Path("/opt/clawworker-v22")
HERMES = Path("/opt/hermes")
HERE = Path(__file__).resolve().parent


def verify_runtime(hermes: Path, runtime: Path, lock: dict[str, Any]) -> dict[str, Any]:
    stamp = json.loads((hermes / "install-stamp.json").read_text())
    if stamp.get("commit") != lock["hermes_commit"]:
        raise ValueError("Preview Hermes commit mismatch")
    if stamp.get("baseVersion", "").removeprefix("v") != lock["hermes_release"]:
        raise ValueError("Preview Hermes release mismatch")
    raw = (runtime / "manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != lock["runtime_manifest_sha256"]:
        raise ValueError("Preview runtime manifest mismatch")
    manifest = json.loads(raw)
    if manifest.get("hermesCommit") != lock["hermes_commit"]:
        raise ValueError("Preview manifest commit mismatch")
    patches = json.loads((runtime / "patches/manifest.json").read_text())
    if not patches or patches != manifest["buildPatches"]:
        raise ValueError("Preview patch manifest mismatch")
    for patch in patches:
        path = hermes / patch["targetRelativePath"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != patch["afterSha256"]:
            raise ValueError("Preview patch mismatch: " + patch["id"])
    for name, digest in manifest["files"].items():
        if name.startswith(("assets/", "runtime/")):
            if hashlib.sha256((runtime / name).read_bytes()).hexdigest() != digest:
                raise ValueError("Preview runtime asset mismatch: " + name)
    return {
        "host_commit": lock["host_commit"],
        "hermes_commit": lock["hermes_commit"],
        "hermes_release": lock["hermes_release"],
        "runtime_manifest_sha256": lock["runtime_manifest_sha256"],
        "verified_patches": len(patches),
        "terminal_backend": "local",
    }


def localize_config(config: dict[str, Any], workspace: str) -> None:
    terminal = config.setdefault("terminal", {})
    for key in list(terminal):
        if key.startswith("ssh_"):
            del terminal[key]
    terminal.update(backend="local", cwd=workspace)


def verify_template(definition: Path, receipt: Path) -> dict[str, Any] | None:
    manifest_path = definition / "manifest.json"
    if not receipt.exists():
        if manifest_path.exists():
            raise ValueError(
                "Template build identity missing; rebuild the template image"
            )
        return None
    identity = json.loads(receipt.read_text())
    if not manifest_path.is_file():
        raise ValueError("Template manifest missing")
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != identity["manifest_sha256"]:
        raise ValueError("Template manifest mismatch")
    manifest = json.loads(raw)
    if (manifest["templateId"], manifest["version"]) != (
        identity["template_id"],
        identity["template_version"],
    ):
        raise ValueError("Template identity mismatch")
    expected = {"manifest.json"}
    for entry in manifest["files"]:
        path = definition / entry["path"]
        expected.add(entry["path"])
        if not path.is_file() or path.is_symlink():
            raise ValueError("Template file missing or linked: " + entry["path"])
        content = path.read_bytes()
        if (
            len(content) != entry["size"]
            or hashlib.sha256(content).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Template file mismatch: " + entry["path"])
    actual = {
        p.relative_to(definition).as_posix()
        for p in definition.rglob("*")
        if p.is_file()
    }
    if actual != expected or any(p.is_symlink() for p in definition.rglob("*")):
        raise ValueError("Template contains unexpected or linked files")
    return identity


class ModelRelay:
    """Forward the Host's OpenAI-compatible route and count actual HTTP attempts."""

    def __init__(self, base_url: str, key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.model = model
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def __enter__(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            @override
            def log_message(self, format: str, *_args: Any) -> None:
                pass

            def do_GET(self):
                if self.path != "/v1/models":
                    self.send_error(404)
                    return
                raw = json.dumps(
                    {"object": "list", "data": [{"id": relay.model, "object": "model"}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                started = time.monotonic()
                row = {"status": None, "duration_seconds": None}
                with relay.lock:
                    relay.requests.append(row)
                try:
                    request = urllib.request.Request(
                        relay.base_url + "/chat/completions",
                        data=self.rfile.read(int(self.headers["Content-Length"])),
                        headers={
                            "Authorization": "Bearer " + relay.key,
                            "Content-Type": "application/json",
                        },
                    )
                    try:
                        upstream = urllib.request.urlopen(request, timeout=600)
                    except urllib.error.HTTPError as error:
                        upstream = error
                    with contextlib.closing(upstream):
                        row["status"] = cast(int, upstream.status)
                        self.send_response(row["status"])
                        self.send_header(
                            "Content-Type",
                            upstream.headers.get("Content-Type", "application/json"),
                        )
                        self.send_header("Connection", "close")
                        self.end_headers()
                        while chunk := upstream.read1(65536):
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except (OSError, ValueError):
                    if row["status"] is None:
                        self.send_error(502, "Model upstream unavailable")
                finally:
                    row["duration_seconds"] = time.monotonic() - started

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def api(url: str, key: str, body=None):
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def configure(
    home: Path,
    definition: Path,
    workspace: Path,
    model: str,
    relay: str,
    port: int,
    max_turns: int | None,
):
    # Use the parser shipped by the pinned Hermes image, not Harbor's PyYAML.
    from ruamel.yaml import YAML  # ty: ignore[unresolved-import] -- supplied by the pinned Hermes image

    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    (home / "config.yaml").write_text("{}\n")
    worker = "harbor-preview"
    (home / "framework-contract.json").write_text(
        json.dumps(
            {
                "worker_id": worker,
                "hermes_home": str(home),
                "sqlite_path": str(home / "state.db"),
                "storage_kind": "local",
                "vm_workspace": str(workspace),
            }
        )
    )
    manifest_sha = hashlib.sha256((ROOT / "manifest.json").read_bytes()).hexdigest()
    request = {
        "workerId": worker,
        "home": str(home),
        "definition": str(definition),
        "runtimeAssets": str(ROOT / "assets"),
        "runtimeProfile": "conversation-skills-web-image-v1",
        "runtimeManifestSha256": manifest_sha,
        "seedTemplateMemory": True,
        "settings": {
            "model": model,
            "instructions": "",
            "tools": ["terminal", "file", "skills", "todo"],
            "network": True,
            "skills": [],
            "mcp": [],
        },
        "models": [{"id": model, "ownedBy": "Harbor test endpoint"}],
        "modelBaseUrl": relay,
        "ssh": {"host": "127.0.0.1", "user": "server", "workspace": str(workspace)},
    }
    rendered = subprocess.run(
        ["node", str(ROOT / "runtime/worker_configuration.mjs")],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "API_SERVER_PORT": str(port)},
    )
    receipt = json.loads(rendered.stdout)
    yaml = YAML(typ="safe")
    config = yaml.load((home / "config.yaml").read_text())
    localize_config(config, str(workspace))
    if max_turns is not None:
        config.setdefault("agent", {})["max_turns"] = max_turns
    with (home / "config.yaml").open("w") as stream:
        yaml.dump(config, stream)
    return {
        **receipt,
        "terminalBackend": "local",
        "adaptation": "SSH tools execute in the Harbor container",
    }


def run(args, identity):
    home = Path("/opt/data/harbor")
    workspace = Path.cwd()
    logs = Path("/logs/agent")
    logs.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    started = time.monotonic()
    status = {}
    with ModelRelay(
        os.environ["PREVIEW_MODEL_BASE_URL"],
        os.environ["PREVIEW_MODEL_API_KEY"],
        args.model,
    ) as relay:
        receipt = configure(
            home,
            Path(args.definition),
            workspace,
            args.model,
            relay.url,
            port,
            args.max_turns,
        )
        env = {
            **os.environ,
            "HERMES_HOME": str(home),
            "HOME": "/home/server",
            "API_SERVER_HOST": "127.0.0.1",
            "API_SERVER_PORT": str(port),
            "API_SERVER_KEY": key,
            "CLAWWORKER_MANAGED_API_KEY": key,
            "OPENAI_API_KEY": key,
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "HERMES_GATEWAY_SESSION": "1",
            "TERMINAL_ENV": "local",
        }
        env.pop("PREVIEW_MODEL_API_KEY")
        with (logs / "gateway.log").open("w") as log:
            process = subprocess.Popen(
                [str(HERMES / ".venv/bin/python"), "-m", "gateway.run"],
                cwd=HERMES,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Preview Gateway exited during startup; see gateway.log"
                        )
                    try:
                        api(base + "/health", key)
                        break
                    except (OSError, ValueError):
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                "Preview Gateway did not become ready"
                            ) from None
                        time.sleep(0.2)
                execution_started = time.monotonic()
                admitted = api(
                    base + "/v1/runs",
                    key,
                    {
                        "input": os.environ["PREVIEW_INSTRUCTION"],
                        "model": args.model,
                        "provider": "clawworker-managed",
                        "terminal_cwd": str(workspace),
                        "privacy_review": {"workspace_root": str(workspace)},
                    },
                )
                while True:
                    status = api(base + "/v1/runs/" + admitted["run_id"], key)
                    if status["status"] in {"completed", "failed", "cancelled"}:
                        break
                    time.sleep(0.2)
                execution_seconds = time.monotonic() - execution_started
                session_id = status.get("session_id") or admitted.get("session_id")
                messages = []
                offset = 0
                while session_id:
                    page = api(
                        base
                        + f"/api/sessions/{session_id}/messages?limit=500&offset={offset}&order=oldest",
                        key,
                    )
                    messages.extend(page["data"])
                    if len(page["data"]) < 500:
                        break
                    offset += len(page["data"])
                (logs / "hermes-session.jsonl").write_text(
                    json.dumps({"session_id": session_id, "messages": messages}) + "\n"
                )
                (logs / "result.json").write_text(json.dumps(status, indent=2))
                if status["status"] != "completed":
                    raise RuntimeError(
                        "Preview native run " + status["status"] + "; see result.json"
                    )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                metrics = {
                    "runtime": identity,
                    "configuration": receipt,
                    "llm_http_requests": len(relay.requests),
                    "model_requests": relay.requests,
                    "elapsed_seconds": time.monotonic() - started,
                    "agent_execution_seconds": locals().get("execution_seconds"),
                    "usage": status.get("usage"),
                    "status": status.get("status", "startup_failed"),
                }
                (logs / "preview-metrics.json").write_text(
                    json.dumps(metrics, indent=2)
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["verify", "run"])
    parser.add_argument("--model")
    parser.add_argument("--definition", default="/opt/clawworker-harbor/definition")
    parser.add_argument("--max-turns", type=int)
    args = parser.parse_args()
    lock = json.loads((HERE / "preview.json").read_text())
    identity = verify_runtime(HERMES, ROOT, lock)
    identity["template"] = verify_template(
        Path(args.definition), HERE / "template.json"
    )
    if args.action == "verify":
        print(json.dumps(identity))
    else:
        run(args, identity)


if __name__ == "__main__":
    main()
