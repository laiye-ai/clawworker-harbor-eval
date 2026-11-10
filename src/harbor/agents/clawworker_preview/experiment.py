"""Run a small, file-configured template experiment using Harbor's existing Job."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
import toml
import yaml

from harbor.agents.factory import AgentFactory
from harbor.job import Job
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import AgentConfig, TaskConfig, VerifierConfig
from harbor.models.trial.result import TrialResult

from . import template_image

PREVIEW_AGENT = "harbor.agents.clawworker_preview.agent:ClawworkerPreview"
NATIVE_HARNESSES = {"hermes", "codex", "claude-code"}


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: Path
    agent: AgentConfig
    question: str = Field(min_length=1)
    attachments: list[Path] = Field(default_factory=list)
    verifier: Path | None = None
    base_image: str = template_image.BASE_IMAGE
    timeout_sec: float = Field(default=600, gt=0)
    n_attempts: int = Field(default=1, ge=1)


def load_config(path: Path) -> ExperimentConfig:
    config = ExperimentConfig.model_validate(yaml.safe_load(path.read_text()))
    parent = path.resolve().parent
    config.template = (parent / config.template.expanduser()).resolve()
    config.attachments = [
        (parent / p.expanduser()).resolve() for p in config.attachments
    ]
    if config.verifier is not None:
        config.verifier = (parent / config.verifier.expanduser()).resolve()
    return config


def select_agent(config: AgentConfig) -> AgentConfig:
    agent = config.model_copy(deep=True)
    if agent.name is not None and agent.import_path is not None:
        raise ValueError("Choose a harness name or import_path, not both")
    if agent.name == "clawworker-preview" or agent.import_path == PREVIEW_AGENT:
        release = template_image.LOCK["hermes_release"]
        if agent.kwargs.get("version") not in (None, release, "v" + release):
            raise ValueError("Preview harness uses the pinned version in preview.json")
        if agent.kwargs.get("toolsets") is not None:
            raise ValueError("Preview harness uses the pinned Host toolsets")
        agent.name = None
        agent.import_path = PREVIEW_AGENT
    elif agent.name not in NATIVE_HARNESSES or agent.import_path is not None:
        raise ValueError("No reviewed template loading adapter for this harness")
    return agent


def make_job(
    config: ExperimentConfig, image: str, definition: Path, output: Path
) -> JobConfig:
    agent = select_agent(config.agent)
    names = [p.name for p in config.attachments]
    if len(set(names)) != len(names) or any(
        not p.is_file() for p in config.attachments
    ):
        raise ValueError(
            "Each attachment must be an existing file with a unique filename"
        )
    if config.verifier is not None and not config.verifier.is_file():
        raise ValueError("Verifier must be an existing test.sh file")
    if (
        agent.import_path != PREVIEW_AGENT
        and agent.kwargs.get("prompt_template_path") is not None
    ):
        raise ValueError(
            "Template loading owns prompt_template_path in this experiment"
        )
    task = output / "task"
    task.mkdir(parents=True, exist_ok=False)
    environment = task / "environment"
    environment.mkdir()
    instruction = config.question
    if config.attachments:
        attachments = environment / "attachments"
        attachments.mkdir()
        instruction += "\n\nThe supplied input files are:\n"
        for path in config.attachments:
            destination = attachments / path.name
            shutil.copyfile(path, destination)
            destination.chmod(0o644)
            instruction += "- /workspace/work/attachments/" + path.name + "\n"
    (task / "instruction.md").write_text(instruction)
    (environment / "Dockerfile").write_text(
        "FROM "
        + image
        + "\n"
        + (
            "COPY --chown=server:server attachments/ /workspace/work/attachments/\n"
            if config.attachments
            else ""
        )
    )
    (environment / "docker-compose.yaml").write_text(
        'services:\n  main:\n    extra_hosts:\n      - "host.docker.internal:host-gateway"\n'
    )
    (task / "task.toml").write_text(
        toml.dumps(
            {
                "schema_version": "1.4",
                "task": {"name": "clawworker/experiment", "version": "1.0.0"},
                "agent": {"user": "server", "timeout_sec": config.timeout_sec},
                "environment": {"cpus": 2, "memory_mb": 4096, "storage_mb": 10240},
                "verifier": {"user": "server", "timeout_sec": 60},
            }
        )
    )
    if config.verifier is not None:
        (task / "tests").mkdir()
        shutil.copyfile(config.verifier, task / "tests/test.sh")
    if agent.import_path != PREVIEW_AGENT:
        # These harnesses already consume Harbor's prompt_template_path and
        # skills interfaces. This is distinct from Host-native template loading.
        content = (definition / "SOUL.md").read_text()
        if (definition / "MEMORY.md").exists():
            content += "\n\nTemplate memory:\n" + (definition / "MEMORY.md").read_text()
        prompt = output / "template-prompt.j2"
        prompt.write_text("{{ " + json.dumps(content) + " }}\n\n{{ instruction }}")
        agent.kwargs["prompt_template_path"] = str(prompt)
        skills = definition / "skills"
        if skills.exists():
            agent.skills.extend(str(p) for p in sorted(skills.iterdir()) if p.is_dir())
    return JobConfig(
        job_name="run",
        jobs_dir=output / "jobs",
        agents=[agent],
        tasks=[TaskConfig(path=task)],
        n_attempts=config.n_attempts,
        n_concurrent_trials=1,
        quiet=True,
        verifier=VerifierConfig(disable=config.verifier is None),
    )


async def run_experiment(config: ExperimentConfig, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    agent = select_agent(config.agent)
    AgentFactory.run_preflight(agent)
    started = time.monotonic()
    output.mkdir(parents=True)
    image = template_image.build(
        config.template, output / "template-build", config.base_image
    )
    build_seconds = time.monotonic() - started
    job_config = make_job(
        config, image["image"], output / "template-build/definition", output
    )
    inputs = [
        {
            "name": p.name,
            "sha256": hashlib.sha256(
                (output / "task/environment/attachments" / p.name).read_bytes()
            ).hexdigest(),
        }
        for p in config.attachments
    ]
    (output / "job.json").write_text(job_config.model_dump_json(indent=2))
    job = await Job.create(job_config)
    result = await job.run()
    trials = []
    for path in sorted(job.job_dir.glob("*/result.json")):
        trial = TrialResult.model_validate_json(path.read_text())
        metadata = (trial.agent_result.metadata or {}) if trial.agent_result else {}
        timing = trial.agent_execution
        trials.append(
            {
                "name": trial.trial_name,
                "agent": trial.agent_info.model_dump(mode="json"),
                "exception": trial.exception_info.model_dump(mode="json")
                if trial.exception_info
                else None,
                "rewards": trial.verifier_result.rewards
                if trial.verifier_result
                else None,
                "llm_http_requests": metadata.get("llm_http_requests"),
                "agent_execution_seconds": (
                    timing.finished_at - timing.started_at
                ).total_seconds()
                if timing and timing.started_at and timing.finished_at
                else None,
                "result": str(path),
            }
        )
    summary = {
        "template": image,
        "template_loading": "host-native"
        if agent.import_path == PREVIEW_AGENT
        else "harbor-prompt-and-skills",
        "harness": config.agent.name or config.agent.import_path,
        "model": config.agent.model_name,
        "question_sha256": hashlib.sha256(config.question.encode()).hexdigest(),
        "attachments": inputs,
        "scored": config.verifier is not None,
        "build_seconds": build_seconds,
        "elapsed_seconds": time.monotonic() - started,
        "stats": result.stats.model_dump(mode="json"),
        "trials": trials,
        "job_result": str(job.job_dir / "result.json"),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--output", required=True, type=Path, help="A new experiment directory"
    )
    args = parser.parse_args()
    summary = asyncio.run(
        run_experiment(load_config(args.config), args.output.resolve())
    )
    print(
        json.dumps(
            {
                "summary": str(args.output.resolve() / "summary.json"),
                "stats": summary["stats"],
            }
        )
    )
    if summary["stats"]["n_errored_trials"] or summary["stats"]["n_cancelled_trials"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
