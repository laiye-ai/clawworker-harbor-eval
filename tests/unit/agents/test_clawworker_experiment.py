"""One experiment config selects the harness, template image, model and task."""

import json
from pathlib import Path

import pytest

from harbor.models.trial.config import AgentConfig


def definition_at(tmp_path):
    definition = tmp_path / "definition"
    (definition / "skills/check").mkdir(parents=True)
    (definition / "SOUL.md").write_text(
        "Use the selected template. {{literal}} {% endraw %}\n"
    )
    (definition / "MEMORY.md").write_text("Preserve actual results.\n")
    (definition / "skills/check/SKILL.md").write_text(
        "---\nname: check\ndescription: Check results.\n---\nCheck results.\n"
    )
    return definition


def test_preview_job_uses_selected_image_model_question_and_inputs(tmp_path):
    from harbor.agents.clawworker_preview.experiment import ExperimentConfig, make_job

    attachment = tmp_path / "invoice.pdf"
    attachment.write_bytes(b"fixture-pdf")
    definition = definition_at(tmp_path)
    config = ExperimentConfig(
        template=tmp_path / "chosen.zip",
        question="Match these actual documents.",
        attachments=[attachment],
        agent=AgentConfig(
            name="clawworker-preview",
            model_name="openai/chosen-model",
            base_url="https://model.example/v1",
            model_api_key_name="CHOSEN_API_KEY",
        ),
    )
    job = make_job(
        config, "harbor-template:other-worker-2.7", definition, tmp_path / "run"
    )
    agent = job.agents[0]
    assert agent.name is None
    assert (
        agent.import_path == "harbor.agents.clawworker_preview.agent:ClawworkerPreview"
    )
    assert agent.model_name == "openai/chosen-model"
    assert agent.base_url == "https://model.example/v1"
    assert agent.model_api_key_name == "CHOSEN_API_KEY"
    assert not agent.skills
    assert "prompt_template_path" not in agent.kwargs
    task = job.tasks[0].path
    assert "Match these actual documents." in (task / "instruction.md").read_text()
    assert (
        "/workspace/work/attachments/invoice.pdf"
        in (task / "instruction.md").read_text()
    )
    assert (task / "environment/attachments/invoice.pdf").read_bytes() == b"fixture-pdf"
    assert (
        "FROM harbor-template:other-worker-2.7"
        in (task / "environment/Dockerfile").read_text()
    )
    assert job.verifier.disable is True
    from harbor.models.task.task import Task

    assert (
        Task(task, disable_verification=True).config.task.name
        == "clawworker/experiment"
    )


@pytest.mark.parametrize("harness", ["hermes", "codex", "claude-code"])
def test_native_harness_uses_harbor_prompt_and_skills_interfaces(tmp_path, harness):
    from harbor.agents.clawworker_preview.experiment import ExperimentConfig, make_job
    from harbor.utils.templating import render_prompt_template

    definition = definition_at(tmp_path)
    config = ExperimentConfig(
        template=tmp_path / "chosen.zip",
        question="actual question",
        agent=AgentConfig(name=harness, model_name="openai/chosen-model"),
    )
    job = make_job(config, "harbor-template:worker-2.7", definition, tmp_path / "run")
    agent = job.agents[0]
    assert agent.name == harness
    assert agent.model_name == "openai/chosen-model"
    assert agent.skills == [str(definition / "skills/check")]
    rendered = render_prompt_template(
        Path(agent.kwargs["prompt_template_path"]), "actual question"
    )
    assert "Use the selected template. {{literal}} {% endraw %}" in rendered
    assert "Preserve actual results." in rendered
    assert rendered.endswith("actual question")


def test_verifier_is_used_only_when_supplied(tmp_path):
    from harbor.agents.clawworker_preview.experiment import ExperimentConfig, make_job

    definition = definition_at(tmp_path)
    verifier = tmp_path / "check.sh"
    verifier.write_text("#!/bin/sh\necho 1 > /logs/verifier/reward.txt\n")
    config = ExperimentConfig(
        template=tmp_path / "chosen.zip",
        question="question",
        verifier=verifier,
        agent=AgentConfig(name="clawworker-preview", model_name="openai/chosen-model"),
    )
    job = make_job(config, "harbor-template:worker-2.7", definition, tmp_path / "run")
    assert job.verifier.disable is False
    assert (job.tasks[0].path / "tests/test.sh").read_bytes() == verifier.read_bytes()


def test_config_paths_are_relative_to_config_not_current_directory(
    tmp_path, monkeypatch
):
    from harbor.agents.clawworker_preview.experiment import load_config

    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "template": "package.zip",
                "question": "question",
                "attachments": ["invoice.pdf"],
                "agent": {
                    "name": "clawworker-preview",
                    "model_name": "openai/custom",
                    "model_api_key_name": "PRIVATE_MODEL_KEY",
                },
            }
        )
    )
    monkeypatch.chdir(tmp_path.parent)
    config = load_config(profile)
    assert config.template == tmp_path / "package.zip"
    assert config.attachments == [tmp_path / "invoice.pdf"]
    assert config.agent.model_api_key_name == "PRIVATE_MODEL_KEY"


def test_unsupported_harness_fails_before_writing_task(tmp_path):
    from harbor.agents.clawworker_preview.experiment import ExperimentConfig, make_job

    config = ExperimentConfig(
        template=tmp_path / "package.zip",
        question="question",
        agent=AgentConfig(name="oracle"),
    )
    with pytest.raises(ValueError, match="template loading"):
        make_job(
            config, "harbor-template:worker-2.7", tmp_path / "unused", tmp_path / "run"
        )
    assert not (tmp_path / "run/task").exists()


@pytest.mark.parametrize(
    "agent",
    [
        {
            "name": "codex",
            "import_path": "harbor.agents.clawworker_preview.agent:ClawworkerPreview",
        },
        {"name": "clawworker-preview", "import_path": "some.module:AnotherAgent"},
        {"name": "clawworker-preview", "kwargs": {"version": "different-version"}},
        {"name": "clawworker-preview", "kwargs": {"toolsets": "other-tools"}},
    ],
)
def test_harness_selection_is_never_silently_overridden(agent):
    from harbor.agents.clawworker_preview.experiment import select_agent

    with pytest.raises(ValueError):
        select_agent(AgentConfig(**agent))


def test_attachment_names_cannot_silently_replace_each_other(tmp_path):
    from harbor.agents.clawworker_preview.experiment import ExperimentConfig, make_job

    paths = []
    for folder in ("one", "two"):
        path = tmp_path / folder / "same.pdf"
        path.parent.mkdir()
        path.write_bytes(folder.encode())
        paths.append(path)
    config = ExperimentConfig(
        template=tmp_path / "package.zip",
        question="question",
        attachments=paths,
        agent=AgentConfig(name="clawworker-preview"),
    )
    with pytest.raises(ValueError, match="attachment"):
        make_job(
            config, "harbor-template:worker-2.7", tmp_path / "unused", tmp_path / "run"
        )
    assert not (tmp_path / "run/task").exists()
