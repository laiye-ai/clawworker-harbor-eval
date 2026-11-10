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
            assert all(row["usage"] is None for row in relay.requests)
            assert "private-test-key" not in json.dumps(relay.requests)
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "streaming,body,expected",
    [
        (
            False,
            b'{"usage":{"prompt_tokens":120,"completion_tokens":7,"total_tokens":127,"prompt_cache_hit_tokens":100}}',
            {
                "input_tokens": 120,
                "output_tokens": 7,
                "total_tokens": 127,
                "cache_read_tokens": 100,
                "cache_write_tokens": None,
            },
        ),
        (
            False,
            b'{"usage":{"prompt_tokens":10,"completion_tokens":0,"prompt_tokens_details":{"cached_tokens":0}}}',
            {
                "input_tokens": 10,
                "output_tokens": 0,
                "total_tokens": None,
                "cache_read_tokens": 0,
                "cache_write_tokens": None,
            },
        ),
        (
            True,
            'data: {"choices":[{"delta":{"content":"你好"}}]}\r\n\r\ndata: {"choices":[],\r\ndata: "usage":{"prompt_tokens":43,"completion_tokens":2,"total_tokens":45}}\r\n\r\ndata: [DONE]\r\n\r\n'.encode(),
            {
                "input_tokens": 43,
                "output_tokens": 2,
                "total_tokens": 45,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
            },
        ),
        (
            True,
            b'data: {"usage":{"input_tokens":2,"output_tokens":3}}',
            {
                "input_tokens": 2,
                "output_tokens": 3,
                "total_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
            },
        ),
        (False, b'{"choices":[]}', None),
        (True, b'data: {"choices":[]}\n\ndata: [DONE]\n\n', None),
        (True, b'data: {"usage":{"prompt_tokens":99', None),
        (False, b'{"error":{"message":"not usage"}}', None),
    ],
)
def test_relay_captures_each_response_usage_without_changing_bytes(
    streaming, body, expected
):
    from harbor.agents.clawworker_preview.runtime import ModelRelay

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/event-stream" if streaming else "application/json"
            )
            self.end_headers()
            for offset in range(0, len(body), 7):
                self.wfile.write(body[offset : offset + 7])
                self.wfile.flush()

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with ModelRelay(
            f"http://127.0.0.1:{upstream.server_port}/v1", "test-key", "test-model"
        ) as relay:
            for _ in range(2):
                request = urllib.request.Request(
                    relay.url + "/chat/completions", data=b'{"model":"test-model"}'
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    assert response.read() == body
            rows = relay.requests
        assert [r["request_index"] for r in rows] == [1, 2]
        assert [r["usage"] for r in rows] == [expected, expected]
        assert all(r["status"] == 200 and r["duration_seconds"] >= 0 for r in rows)
        assert "test-key" not in json.dumps(rows)
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)


def test_usage_capture_handles_every_byte_boundary_and_keeps_only_numeric_counters():
    from harbor.agents.clawworker_preview.runtime import ResponseUsage

    capture = ResponseUsage("text/event-stream")
    raw = 'data: {"choices":[{"delta":{"content":"你"}}]}\n\ndata: {"usage":{"prompt_tokens":0,"completion_tokens":true,"total_tokens":-1,"prompt_cache_hit_tokens":4,"api_key":"secret"}}\n\n'.encode()
    for byte in raw:
        capture.feed(bytes([byte]))
    capture.finish()
    assert capture.usage == {
        "input_tokens": 0,
        "output_tokens": None,
        "total_tokens": None,
        "cache_read_tokens": 4,
        "cache_write_tokens": None,
    }


def test_stream_usage_snapshots_are_not_added_together():
    from harbor.agents.clawworker_preview.runtime import ResponseUsage

    capture = ResponseUsage("text/event-stream")
    capture.feed(b'data: {"usage":{"prompt_tokens":7,"completion_tokens":1}}\n\n')
    capture.feed(b'data: {"usage":{"prompt_tokens":11,"completion_tokens":3}}\n\n')
    capture.finish()
    assert capture.usage["input_tokens"] == 11
    assert capture.usage["output_tokens"] == 3
