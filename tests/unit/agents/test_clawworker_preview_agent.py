"""Harbor uses the pinned native Gateway instead of installing upstream Hermes."""

import json
from unittest.mock import AsyncMock

import pytest

from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext


@pytest.mark.asyncio
async def test_setup_rejects_another_image(tmp_path):
    from harbor.agents.clawworker_preview.agent import ClawworkerPreview

    environment = AsyncMock()
    environment.exec.return_value = ExecResult(
        return_code=0,
        stdout=json.dumps({"hermes_commit": "wrong", "host_commit": "wrong"}),
    )
    agent = ClawworkerPreview(logs_dir=tmp_path)
    with pytest.raises(ValueError, match="identity"):
        await agent.setup(environment)


@pytest.mark.asyncio
async def test_run_passes_model_and_adp_credentials_to_native_entry(
    tmp_path, monkeypatch
):
    from harbor.agents.clawworker_preview.agent import ClawworkerPreview

    monkeypatch.setenv("OPENAI_API_KEY", "model-private")
    monkeypatch.setenv("ADP_API_KEY", "adp-private")
    agent = ClawworkerPreview(
        logs_dir=tmp_path,
        model_name="openai/test-model",
        base_url="https://model.example/v1",
    )
    environment = AsyncMock()
    environment.exec.return_value = ExecResult(return_code=0)
    await agent.run("Use the actual files", environment, AgentContext())
    call = environment.exec.call_args.kwargs
    assert call["user"] == "server"
    assert call["cwd"] == "/workspace/work"
    assert call["env"]["PREVIEW_MODEL_BASE_URL"] == "https://model.example/v1"
    assert call["env"]["PREVIEW_MODEL_API_KEY"] == "model-private"
    assert call["env"]["ADP_API_KEY"] == "adp-private"
    assert call["env"]["PREVIEW_INSTRUCTION"] == "Use the actual files"
    assert "harbor-runtime.py run" in call["command"]
    assert "model-private" not in call["command"]
    assert "adp-private" not in call["command"]


@pytest.mark.asyncio
async def test_run_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    from harbor.agents.clawworker_preview.agent import ClawworkerPreview

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = ClawworkerPreview(
        logs_dir=tmp_path,
        model_name="openai/test-model",
        base_url="https://model.example/v1",
    )
    environment = AsyncMock()
    environment.exec.return_value = ExecResult(return_code=2)
    with pytest.raises(RuntimeError, match="exit 2"):
        await agent.run("task", environment, AgentContext())


@pytest.mark.asyncio
async def test_resume_uses_native_previous_session_not_a_fresh_conversation(
    tmp_path, monkeypatch
):
    from harbor.agents.clawworker_preview.agent import ClawworkerPreview

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = ClawworkerPreview(
        logs_dir=tmp_path,
        model_name="openai/test-model",
        base_url="https://model.example/v1",
    )
    environment = AsyncMock()
    environment.exec.return_value = ExecResult(return_code=0)
    assert agent.capabilities.resume is True
    await agent.resume("Second user message", environment, AgentContext())
    call = environment.exec.call_args.kwargs
    assert " --resume" in call["command"]
    assert call["env"]["PREVIEW_INSTRUCTION"] == "Second user message"
    await agent.run("Independent question", environment, AgentContext())
    assert " --resume" not in environment.exec.call_args.kwargs["command"]


def test_metrics_preserve_actual_request_count_and_native_usage(tmp_path):
    from harbor.agents.clawworker_preview.agent import ClawworkerPreview

    (tmp_path / "hermes-session.jsonl").write_text(
        json.dumps(
            {
                "session_id": "native-id",
                "messages": [
                    {"role": "user", "content": "task"},
                    {"role": "assistant", "content": "done"},
                ],
            }
        )
    )
    (tmp_path / "preview-metrics.json").write_text(
        json.dumps(
            {
                "runtime": {"verified_patches": 14},
                "llm_http_requests": 3,
                "elapsed_seconds": 1.5,
                "usage": {"prompt_tokens": 17, "completion_tokens": 4},
                "status": "completed",
            }
        )
    )
    agent = ClawworkerPreview(logs_dir=tmp_path)
    context = AgentContext()
    agent.populate_context_post_run(context)
    assert context.n_input_tokens == 17
    assert context.n_output_tokens == 4
    assert context.metadata["llm_http_requests"] == 3
    assert context.metadata["runtime"]["verified_patches"] == 14
    assert (tmp_path / "trajectory.json").exists()
