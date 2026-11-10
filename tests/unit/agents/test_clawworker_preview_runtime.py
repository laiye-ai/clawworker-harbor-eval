"""Behavioral checks for the single-container Preview runtime boundary."""

import hashlib
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def preview_tree(tmp_path):
    hermes = tmp_path / "hermes"
    runtime = tmp_path / "runtime"
    (hermes / "agent").mkdir(parents=True)
    (runtime / "patches").mkdir(parents=True)
    (hermes / "install-stamp.json").write_text(
        json.dumps({"commit": "a" * 40, "baseVersion": "0.22.0"})
    )
    patched = b"value = 'patched'\n"
    (hermes / "agent/example.py").write_bytes(patched)
    patches = [
        {
            "id": "example",
            "targetRelativePath": "agent/example.py",
            "afterSha256": hashlib.sha256(patched).hexdigest(),
        }
    ]
    (runtime / "patches/manifest.json").write_text(json.dumps(patches))
    manifest = json.dumps(
        {
            "hermesCommit": "a" * 40,
            "hermesRelease": "0.22.0",
            "buildPatches": patches,
            "files": {},
        }
    ).encode()
    (runtime / "manifest.json").write_bytes(manifest)
    lock = {
        "host_commit": "b" * 40,
        "hermes_commit": "a" * 40,
        "hermes_release": "0.22.0",
        "runtime_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
    }
    return hermes, runtime, lock


def test_runtime_rejects_changed_patch_even_with_correct_version(preview_tree):
    from harbor.agents.clawworker_preview.runtime import verify_runtime

    hermes, runtime, lock = preview_tree
    assert verify_runtime(hermes, runtime, lock)["verified_patches"] == 1
    (hermes / "agent/example.py").write_text("value = 'upstream'\n")
    with pytest.raises(ValueError, match="patch"):
        verify_runtime(hermes, runtime, lock)


def test_runtime_rejects_another_upstream_commit(preview_tree):
    from harbor.agents.clawworker_preview.runtime import verify_runtime

    hermes, runtime, lock = preview_tree
    (hermes / "install-stamp.json").write_text(
        json.dumps({"commit": "c" * 40, "baseVersion": "0.22.0"})
    )
    with pytest.raises(ValueError, match="commit"):
        verify_runtime(hermes, runtime, lock)


def test_local_backend_preserves_host_policies():
    from harbor.agents.clawworker_preview.runtime import localize_config

    config = {
        "terminal": {
            "backend": "ssh",
            "ssh_host": "remote",
            "ssh_user": "worker",
            "ssh_key": "/key",
            "timeout": 180,
        },
        "memory": {"memory_enabled": True, "memory_char_limit": 4000},
        "approvals": {"mode": "off"},
        "delegation": {"max_spawn_depth": 1},
        "skills": {"external_dirs": ["/definition/skills"]},
    }
    localize_config(config, "/workspace/work")
    assert config["terminal"] == {
        "backend": "local",
        "timeout": 180,
        "cwd": "/workspace/work",
    }
    assert config["memory"] == {"memory_enabled": True, "memory_char_limit": 4000}
    assert config["approvals"] == {"mode": "off"}
    assert config["delegation"] == {"max_spawn_depth": 1}
    assert config["skills"]["external_dirs"] == ["/definition/skills"]


def test_model_relay_counts_real_requests_and_preserves_errors():
    from harbor.agents.clawworker_preview.runtime import ModelRelay

    received = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers.get("Authorization"),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"rate limited"}}')

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with ModelRelay(
            f"http://127.0.0.1:{upstream.server_port}/custom/v1",
            "private-test-key",
            "test-model",
        ) as relay:
            for _ in range(2):
                request = urllib.request.Request(
                    relay.url + "/chat/completions",
                    data=b'{"model":"test-model"}',
                    headers={"Content-Type": "application/json"},
                )
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                assert error.value.code == 429
                assert (
                    json.loads(error.value.read())["error"]["message"] == "rate limited"
                )
            assert len(received) == 2
            assert all(
                row[:2] == ("/custom/v1/chat/completions", "Bearer private-test-key")
                for row in received
            )
            assert len(relay.requests) == 2
            assert all(row["status"] == 429 for row in relay.requests)
            assert "private-test-key" not in json.dumps(relay.requests)
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)
