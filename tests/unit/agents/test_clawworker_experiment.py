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


@pytest.mark.parametrize("concurrent", [1, 2, 4])
def test_configured_concurrency_and_attempts_reach_harbor(tmp_path, concurrent):
    from harbor.agents.clawworker_preview.experiment import load_config, make_job

    profile = tmp_path / "experiment.yaml"
    profile.write_text(
        "template: selected.zip\nagent:\n  name: clawworker-preview\n"
        "question: Check the documents.\nn_attempts: 6\n"
        f"n_concurrent_trials: {concurrent}\n"
    )
    config = load_config(profile)
    job = make_job(
        config, "harbor-template:worker", definition_at(tmp_path), tmp_path / "run"
    )
    assert job.n_concurrent_trials == concurrent
    assert job.n_attempts == 6


def test_omitted_concurrency_keeps_serial_execution(tmp_path):
    from harbor.agents.clawworker_preview.experiment import ExperimentConfig, make_job

    config = ExperimentConfig(
        template=tmp_path / "selected.zip",
        agent=AgentConfig(name="clawworker-preview"),
        question="Check the documents.",
    )
    job = make_job(
        config, "harbor-template:worker", definition_at(tmp_path), tmp_path / "run"
    )
    assert job.n_concurrent_trials == 1


@pytest.mark.parametrize("invalid", [0, -1, 1.5, True, "2"])
def test_concurrency_requires_a_positive_integer(invalid):
    from harbor.agents.clawworker_preview.experiment import ExperimentConfig

    with pytest.raises(ValueError):
        ExperimentConfig(
            template="selected.zip",
            agent=AgentConfig(name="clawworker-preview"),
            question="Check the documents.",
            n_concurrent_trials=invalid,
        )


def test_fixed_cases_use_native_steps_and_stage_only_current_turn_inputs(tmp_path):
    from harbor.agents.clawworker_preview.experiment import ExperimentConfig, make_job
    from harbor.models.task.task import Task

    invoice = tmp_path / "invoice.pdf"
    invoice.write_bytes(b"invoice")
    delivery = tmp_path / "delivery.pdf"
    delivery.write_bytes(b"delivery")
    verifier = tmp_path / "check.sh"
    verifier.write_text("echo 1 > /logs/verifier/reward.txt\n")
    config = ExperimentConfig(
        template=tmp_path / "template.zip",
        agent=AgentConfig(name="clawworker-preview", model_name="openai/test"),
        cases=[
            {
                "name": "missing-document",
                "turns": [
                    {"question": "Match this invoice.", "attachments": [invoice]},
                    {
                        "question": "Here is the missing delivery.",
                        "attachments": [delivery],
                        "verifier": verifier,
                    },
                ],
            },
            {"name": "other-question", "turns": [{"question": "Show the status."}]},
        ],
    )
    job = make_job(
        config, "harbor-template:worker", definition_at(tmp_path), tmp_path / "run"
    )
    assert job.agents[0].resume_trajectory is True
    assert len(job.tasks) == 2
    task = Task(job.tasks[0].path)
    assert [step.name for step in task.config.steps] == ["turn-01", "turn-02"]
    assert task.config.steps[0].min_reward is None
    assert task.config.steps[1].min_reward == 1
    assert not list(task.paths.environment_dir.rglob("*.pdf"))
    assert (
        task.paths.step_workdir_dir("turn-01") / "attachments/turn-01/invoice.pdf"
    ).read_bytes() == b"invoice"
    assert (
        task.paths.step_workdir_dir("turn-02") / "attachments/turn-02/delivery.pdf"
    ).read_bytes() == b"delivery"
    assert "delivery.pdf" not in task.step_instruction("turn-01")
    assert "/workspace/work/attachments/turn-02/delivery.pdf" in task.step_instruction(
        "turn-02"
    )
    assert (
        task.paths.step_test_path_for(
            "turn-02", task.config.environment.os
        ).read_bytes()
        == verifier.read_bytes()
    )
    assert "{}" in (task.paths.tests_dir / "test.sh").read_text()
    assert job.verifier.disable is False


def test_fixed_case_paths_resolve_relative_to_config(tmp_path):
    from harbor.agents.clawworker_preview.experiment import load_config

    path = tmp_path / "dialogue.yaml"
    path.write_text("""template: template.zip
agent:
  name: clawworker-preview
cases:
  - name: follow-up
    turns:
      - question: Continue with this file.
        attachments: [delivery.pdf]
        verifier: check.sh
""")
    config = load_config(path)
    turn = config.cases[0].turns[0]
    assert turn.attachments == [tmp_path / "delivery.pdf"]
    assert turn.verifier == tmp_path / "check.sh"


@pytest.mark.parametrize(
    "extra",
    [
        {
            "question": "ambiguous",
            "cases": [{"name": "one", "turns": [{"question": "q"}]}],
        },
        {"cases": []},
        {"cases": [{"name": "one", "turns": []}]},
        {"cases": [{"name": "../escape", "turns": [{"question": "q"}]}]},
        {"cases": [{"name": "same", "turns": [{"question": "q"}]}] * 2},
        {
            "cases": [{"name": "one", "turns": [{"question": "q"}]}],
            "attachments": ["unassigned.pdf"],
        },
    ],
)
def test_fixed_cases_reject_ambiguous_or_invalid_scenarios(extra):
    from harbor.agents.clawworker_preview.experiment import ExperimentConfig

    with pytest.raises(ValueError):
        ExperimentConfig(
            template="template.zip",
            agent=AgentConfig(name="clawworker-preview"),
            **extra,
        )


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
