"""Real patched Hermes, local terminal and Harbor grading; synthetic model only."""

import asyncio
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from harbor.models.model_api_format import ModelApiFormat
from harbor.models.trial.config import AgentConfig, TaskConfig, TrialConfig
from harbor.trial.trial import Trial

pytestmark = [pytest.mark.runtime, pytest.mark.integration, pytest.mark.asyncio]


async def test_configuration_uses_the_provisioned_worker_identity():
    worker = "prun_0123456789abcdef0123456789abcdef"
    code = """
import importlib.util,json,os
from pathlib import Path
spec=importlib.util.spec_from_file_location('runtime','/opt/clawworker-harbor/harbor-runtime.py')
runtime=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
home=Path('/opt/data/harbor')
runtime.configure(home,Path('/opt/clawworker-harbor/definition'),Path.cwd(),'fixture','http://127.0.0.1:5001/v1',5002,None)
contract=json.loads((home/'framework-contract.json').read_text())
assert contract['worker_id']==os.environ['CLAWWORKER_WORKER_ID'],contract
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-e",
            f"CLAWWORKER_WORKER_ID={worker}",
            "--entrypoint",
            "/opt/hermes/.venv/bin/python",
            "harbor-clawworker-preview:0.22.0",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def test_preview_gateway_executes_in_harbor_container(
    tmp_path, monkeypatch, preview_model
):
    monkeypatch.setenv("TEST_MODEL_API_KEY", "synthetic-model-key")
    monkeypatch.setenv("ADP_API_KEY", "synthetic-adp-key")
    task = (
        Path(__file__).resolve().parents[2] / "examples/tasks/clawworker-preview-smoke"
    )
    async with asyncio.timeout(240):
        trial = await Trial.create(
            TrialConfig(
                task=TaskConfig(path=task),
                trial_name="preview-"
                + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12],
                trials_dir=tmp_path / "trials",
                agent=AgentConfig(
                    import_path="examples.agents.clawworker_preview.agent:ClawworkerPreview",
                    model_name="openai/test-model",
                    base_url=preview_model.base_url,
                    model_api_key_name="TEST_MODEL_API_KEY",
                    model_api_format=ModelApiFormat.OPENAI_CHAT_COMPLETIONS,
                ),
            )
        )
        result = await trial.run()
    assert result.exception_info is None, result.exception_info
    assert result.verifier_result.rewards == {"reward": 1.0}
    metadata = result.agent_result.metadata
    assert metadata["runtime"]["verified_patches"] == 14
    assert metadata["runtime"]["terminal_backend"] == "local"
    # The native Gateway may also ask the model for a session title.
    assert metadata["llm_http_requests"] == len(preview_model.requests) >= 2
    assert metadata["status"] == "completed"
    assert result.agent_result.n_input_tokens > 0
    assert (trial.paths.agent_dir / "trajectory.json").exists()
    assert any(
        m.get("role") == "tool" for r in preview_model.requests for m in r["messages"]
    )
    assert "synthetic-adp-key" not in json.dumps(metadata)
    assert "synthetic-model-key" not in json.dumps(metadata)
