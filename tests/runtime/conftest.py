"""Shared deterministic model protocol fixture; no external model calls."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def preview_model():
    requests = []
    fixture = SimpleNamespace(requests=requests, command=None)

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            used = any(m.get("role") == "tool" for m in body.get("messages", []))
            message = {"role": "assistant", "content": "Preview terminal verified."}
            finish = "stop"
            terminal = any(
                t.get("function", {}).get("name") == "terminal"
                for t in body.get("tools", [])
            )
            if body.get("response_format", {}).get("type") == "json_schema":
                message["content"] = json.dumps({"title": "Preview terminal check"})
            if terminal and not used:
                command = (
                    "python3 - <<'PY'\n"
                    "import json, os\n"
                    "from pathlib import Path\n"
                    "Path('preview-proof.json').write_text(json.dumps({"
                    "'native': True, 'workspace': os.getcwd(), "
                    "'adp_key_present': bool(os.environ.get('ADP_API_KEY'))}))\n"
                    "PY"
                )
                command = fixture.command or command
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_preview_proof",
                            "type": "function",
                            "function": {
                                "name": "terminal",
                                "arguments": json.dumps({"command": command}),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            data = {
                "id": "chatcmpl-preview-proof",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "test-model",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "total_tokens": 20,
                },
            }
            mime = "application/json"
            if body.get("stream"):
                delta = dict(message)
                if "tool_calls" in delta:
                    delta["tool_calls"][0]["index"] = 0
                data.update(
                    object="chat.completion.chunk",
                    choices=[{"index": 0, "delta": delta, "finish_reason": finish}],
                )
                raw = ("data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n").encode()
                mime = "text/event-stream"
            else:
                raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    model = ThreadingHTTPServer(("0.0.0.0", 0), Model)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    fixture.base_url = f"http://host.docker.internal:{model.server_port}/v1"
    try:
        yield fixture
    finally:
        model.shutdown()
        model.server_close()
        thread.join(timeout=5)
