"""Run a small, file-configured template experiment using Harbor's existing Job."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
import toml
import yaml

from harbor.agents.factory import AgentFactory
from harbor.job import Job
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import AgentConfig, TaskConfig, VerifierConfig
from harbor.models.trial.result import TrialResult

from . import template_image
from .report import summarize_trial

PREVIEW_AGENT = "examples.agents.clawworker_preview.agent:ClawworkerPreview"
NATIVE_HARNESSES = {"hermes", "codex", "claude-code"}


class TurnConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    attachments: list[Path] = Field(default_factory=list)
    verifier: Path | None = None


class CaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=64)
    turns: list[TurnConfig] = Field(min_length=1)


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: Path
    agent: AgentConfig
    question: str | None = Field(default=None, min_length=1)
    attachments: list[Path] = Field(default_factory=list)
    verifier: Path | None = None
    cases: list[CaseConfig] | None = Field(default=None, min_length=1)
    base_image: str = template_image.BASE_IMAGE
    timeout_sec: float = Field(default=600, gt=0)
    n_attempts: int = Field(default=1, ge=1)
    n_concurrent_trials: int = Field(default=1, ge=1, strict=True)

    @model_validator(mode="after")
    def validate_questions(self):
        if self.cases is None:
            if self.question is None:
                raise ValueError("Supply question or cases")
        else:
            if (
                self.question is not None
                or self.attachments
                or self.verifier is not None
            ):
                raise ValueError(
                    "With cases, put questions, attachments and verifiers in turns"
                )
            names = [case.name for case in self.cases]
            if len(set(names)) != len(names):
                raise ValueError("Case names must be unique")
        return self

    def conversations(self) -> list[CaseConfig]:
        if self.cases is not None:
            return self.cases
        if self.question is None:
            raise ValueError("Supply question or cases")
        return [
            CaseConfig(
                name="experiment",
                turns=[
                    TurnConfig(
                        question=self.question,
                        attachments=self.attachments,
                        verifier=self.verifier,
                    )
                ],
            )
        ]


def load_config(path: Path) -> ExperimentConfig:
    config = ExperimentConfig.model_validate(yaml.safe_load(path.read_text()))
    parent = path.resolve().parent
    config.template = (parent / config.template.expanduser()).resolve()
    config.attachments = [
        (parent / p.expanduser()).resolve() for p in config.attachments
    ]
    if config.verifier is not None:
        config.verifier = (parent / config.verifier.expanduser()).resolve()
    for case in config.cases or []:
        for turn in case.turns:
            turn.attachments = [
                (parent / p.expanduser()).resolve() for p in turn.attachments
            ]
            if turn.verifier is not None:
                turn.verifier = (parent / turn.verifier.expanduser()).resolve()
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
    cases = config.conversations()
    for case in cases:
        for turn in case.turns:
            names = [p.name for p in turn.attachments]
            if len(set(names)) != len(names) or any(
                not p.is_file() for p in turn.attachments
            ):
                raise ValueError(
                    "Each attachment must be an existing file with a unique filename"
                )
            if turn.verifier is not None and not turn.verifier.is_file():
                raise ValueError("Verifier must be an existing test.sh file")
    if (
        agent.import_path != PREVIEW_AGENT
        and agent.kwargs.get("prompt_template_path") is not None
    ):
        raise ValueError(
            "Template loading owns prompt_template_path in this experiment"
        )
    checked = any(turn.verifier is not None for case in cases for turn in case.turns)
    tasks = []
    for case in cases:
        task = output / "tasks" / case.name if config.cases else output / "task"
        environment = task / "environment"
        environment.mkdir(parents=True, exist_ok=False)
        spec: dict[str, Any] = {
            "schema_version": "1.4",
            "task": {"name": "clawworker/" + case.name, "version": "1.0.0"},
            "agent": {"user": "server", "timeout_sec": config.timeout_sec},
            "environment": {
                "cpus": 2,
                "memory_mb": 4096,
                "storage_mb": 10240,
                "workdir": "/workspace/work",
            },
            "verifier": {"user": "server", "timeout_sec": 60},
        }
        if config.cases:
            spec["steps"] = []
            spec["multi_step_reward_strategy"] = "final"
        for index, turn in enumerate(case.turns, start=1):
            name = f"turn-{index:02d}"
            step = task / "steps" / name if config.cases else task
            step.mkdir(parents=True, exist_ok=True)
            instruction = turn.question
            if turn.attachments:
                relative = "attachments/" + name if config.cases else "attachments"
                root = step / "workdir" if config.cases else environment
                attachments = root / relative
                attachments.mkdir(parents=True)
                instruction += "\n\nThe supplied input files are:\n"
                for path in turn.attachments:
                    destination = attachments / path.name
                    shutil.copyfile(path, destination)
                    destination.chmod(0o644)
                    instruction += f"- /workspace/work/{relative}/{path.name}\n"
            (step / "instruction.md").write_text(instruction)
            if turn.verifier is not None:
                (step / "tests").mkdir()
                shutil.copyfile(turn.verifier, step / "tests/test.sh")
            if config.cases:
                spec["steps"].append(
                    {"name": name, **({"min_reward": 1} if turn.verifier else {})}
                )
        if checked and not (task / "tests/test.sh").exists():
            (task / "tests").mkdir(exist_ok=True)
            (task / "tests/test.sh").write_text(
                "#!/bin/sh\nprintf '{}' > /logs/verifier/reward.json\n"
            )
        (environment / "Dockerfile").write_text(
            "FROM "
            + image
            + "\n"
            + (
                "COPY --chown=server:server attachments/ /workspace/work/attachments/\n"
                if not config.cases and config.attachments
                else ""
            )
        )
        (environment / "docker-compose.yaml").write_text(
            'services:\n  main:\n    extra_hosts:\n      - "host.docker.internal:host-gateway"\n'
        )
        (task / "task.toml").write_text(toml.dumps(spec))
        tasks.append(TaskConfig(path=task))
    if config.cases:
        agent.resume_trajectory = True
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
        tasks=tasks,
        n_attempts=config.n_attempts,
        n_concurrent_trials=config.n_concurrent_trials,
        quiet=True,
        verifier=VerifierConfig(disable=not checked),
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
    cases = config.conversations()
    inputs = []
    for case in cases:
        task = output / "tasks" / case.name if config.cases else output / "task"
        for index, turn in enumerate(case.turns, start=1):
            name = f"turn-{index:02d}"
            root = (
                task / "steps" / name / "workdir/attachments" / name
                if config.cases
                else task / "environment/attachments"
            )
            for attachment in turn.attachments:
                inputs.append(
                    {
                        "name": attachment.name,
                        "sha256": hashlib.sha256(
                            (root / attachment.name).read_bytes()
                        ).hexdigest(),
                        **({"case": case.name, "turn": name} if config.cases else {}),
                    }
                )
    (output / "job.json").write_text(job_config.model_dump_json(indent=2))
    job = await Job.create(job_config)
    result = await job.run()
    trials = []
    for path in sorted(job.job_dir.glob("*/result.json")):
        trial = TrialResult.model_validate_json(path.read_text())
        case = next(
            case for case in cases if trial.task_name == "clawworker/" + case.name
        )
        trials.append(
            summarize_trial(
                trial,
                path,
                case.name,
                [(t.question, t.verifier is not None) for t in case.turns],
            )
        )
    evaluation = {
        state: sum(t["verification_status"] == state for t in trials)
        for state in ("passed", "failed", "unverified", "incomplete")
    }
    scored_trials = sum(t["scored"] for t in trials)
    evaluation["pass_rate"] = (
        evaluation["passed"] / scored_trials if scored_trials else None
    )
    summary = {
        "template": image,
        "template_loading": "host-native"
        if agent.import_path == PREVIEW_AGENT
        else "harbor-prompt-and-skills",
        "harness": config.agent.name or config.agent.import_path,
        "model": config.agent.model_name,
        "n_concurrent_trials": config.n_concurrent_trials,
        "question_sha256": hashlib.sha256(config.question.encode()).hexdigest()
        if config.question
        else None,
        "attachments": inputs,
        "scored": any(t.verifier is not None for c in cases for t in c.turns),
        "evaluation": evaluation,
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
