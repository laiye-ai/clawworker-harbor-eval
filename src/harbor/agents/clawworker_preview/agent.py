"""Harbor import-path agent for the pinned Clawworker Preview image."""

import json
from pathlib import Path
import shlex
from typing import override

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import with_prompt_template
from harbor.agents.installed.hermes import Hermes
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.model_api_format import ModelApiFormat

LOCK = json.loads(Path(__file__).with_name("preview.json").read_text())
PYTHON = "/opt/hermes/.venv/bin/python"
RUNNER = "/opt/clawworker-harbor/harbor-runtime.py"


class ClawworkerPreview(Hermes):
    """Reuse Harbor model auth/ATIF, but keep Host's build/config/native Runs."""

    capabilities = AgentCapabilities(base_url=True, atif=True)

    @staticmethod
    @override
    def name() -> str:
        return "clawworker-preview"

    @override
    def version(self) -> str:
        return LOCK["hermes_release"] + "+clawworker." + LOCK["host_commit"][:8]

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        result = await environment.exec(
            command=f"{PYTHON} {RUNNER} verify",
            user="server",
            cwd="/workspace/work",
            timeout_sec=30,
        )
        if result.return_code != 0:
            raise ValueError("Clawworker Preview runtime verification failed")
        identity = json.loads(result.stdout or "{}")
        if any(
            identity.get(field) != LOCK[field]
            for field in ["host_commit", "hermes_commit", "runtime_manifest_sha256"]
        ):
            raise ValueError("Clawworker Preview image identity mismatch")

    @override
    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        connection = self.model_connection
        if connection.model_api_format not in {
            None,
            ModelApiFormat.OPENAI_CHAT_COMPLETIONS,
        }:
            raise ValueError(
                "Clawworker Preview requires the Host's OpenAI Chat Completions compatible model endpoint"
            )
        endpoint = connection.execution_base_url or connection.candidate_base_url
        if not endpoint or not connection.api_key:
            raise ValueError("Clawworker Preview requires a model endpoint and API key")
        if self.model_name is None:
            raise ValueError("Clawworker Preview requires a model name")
        _, model = self._parse_model_name(self.model_name)
        command = f"{PYTHON} {RUNNER} run --model {shlex.quote(model)}"
        if self.options.max_turns is not None:
            command += f" --max-turns {int(self.options.max_turns)}"
        env = {
            "PREVIEW_MODEL_BASE_URL": endpoint,
            "PREVIEW_MODEL_API_KEY": connection.api_key,
            "PREVIEW_INSTRUCTION": instruction,
        }
        adp = self._get_env("ADP_API_KEY")
        if adp:
            env["ADP_API_KEY"] = adp
        result = await environment.exec(
            command=command, env=env, user="server", cwd="/workspace/work"
        )
        if result.return_code != 0:
            raise RuntimeError(
                f"Clawworker Preview native Gateway failed (exit {result.return_code}); see agent logs"
            )

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        super().populate_context_post_run(context)
        path = self.logs_dir / "preview-metrics.json"
        if not path.exists():
            return
        metrics = json.loads(path.read_text())
        context.metadata = {**(context.metadata or {}), **metrics}
        usage = metrics.get("usage") or {}
        context.n_input_tokens = usage.get(
            "prompt_tokens", usage.get("input_tokens", context.n_input_tokens)
        )
        context.n_output_tokens = usage.get(
            "completion_tokens", usage.get("output_tokens", context.n_output_tokens)
        )
