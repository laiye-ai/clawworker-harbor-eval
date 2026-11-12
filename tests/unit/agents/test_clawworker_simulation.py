"""Exercise the dynamic conversation independently of a real model account."""

import pytest

from examples.agents.clawworker_preview.simulation import (
    DialogueCase,
    UserAction,
    run_dialogue,
)


@pytest.mark.asyncio
async def test_long_dialogue_keeps_history_and_verifies_each_task(tmp_path):
    case = DialogueCase.model_validate(
        {
            "name": "long-dialogue",
            "tasks": [
                {
                    "name": "setup",
                    "goal": "Set up the Worker",
                    "verifier": tmp_path / "setup.sh",
                },
                {
                    "name": "match",
                    "goal": "Match the invoice",
                    "verifier": tmp_path / "match.sh",
                },
            ],
        }
    )
    requests = []
    replies = []
    verdicts = iter([0, 1, 1])

    async def choose(task, history, feedback):
        assert len(history) == len(replies)
        requests.append((task.name, len(history), feedback))
        if (
            (task.name == "setup" and len(history) == 15 and not feedback)
            or (task.name == "setup" and len(history) == 16)
            or (task.name == "match" and len(history) == 32)
        ):
            return UserAction(action="complete")
        return UserAction(action="send", message=f"Message {len(history) + 1}")

    async def send(action, paths, index):
        assert not paths
        assert index == len(replies) + 1
        replies.append(f"Worker response {index}")
        return {"reply": replies[-1]}

    async def verify(task, attempt):
        return {"reward": next(verdicts)}

    snapshots = []
    result = await run_dialogue(case, choose, send, verify, snapshots.append)
    assert result["stop_reason"] == "completed"
    assert len(result["turns"]) == 32
    assert [t["status"] for t in result["tasks"]] == ["passed", "passed"]
    assert result["tasks"][0]["checks"] == [{"reward": 0}, {"reward": 1}]
    assert result["tasks"][0]["repairs"] == 1
    assert (
        requests[16][2]
        == "The task did not pass verification. Check the conversation and try to resolve it."
    )
    assert snapshots[-1]["stop_reason"] == "completed"


@pytest.mark.asyncio
async def test_attachments_are_selected_by_task_alias_and_turn_limit(tmp_path):
    case = DialogueCase.model_validate(
        {
            "name": "documents",
            "max_turns": 1,
            "tasks": [
                {
                    "name": "match",
                    "goal": "Match",
                    "attachments": {"invoice": tmp_path / "invoice.pdf"},
                }
            ],
        }
    )
    seen = []

    async def choose(task, history, feedback):
        return UserAction(
            action="send", message="Please match", attachments=["invoice"]
        )

    async def send(action, paths, index):
        seen.extend(paths)
        return {"reply": "Please supply more details."}

    async def verify(task, attempt):
        pytest.fail("Unchecked tasks must not invoke the verifier")

    result = await run_dialogue(case, choose, send, verify, lambda _: None)
    assert seen == [tmp_path / "invoice.pdf"]
    assert result["stop_reason"] == "turn_limit"
    assert result["tasks"][0]["status"] == "incomplete"


def test_visible_reply_only_exposes_current_choice_labels():
    import json

    from examples.agents.clawworker_preview.simulation import visible_reply

    def choice(question):
        return {
            "role": "tool",
            "tool_name": "ask_choice",
            "content": json.dumps(
                {
                    "success": True,
                    "question": question,
                    "options": [{"label": "Drafts only", "value": "hidden-id"}],
                    "internal": "private",
                }
            ),
        }

    messages = [
        {"role": "user", "content": "old message"},
        choice("Old question"),
        {"role": "user", "content": "current message"},
        {"role": "tool", "tool_name": "terminal", "content": "secret log"},
        choice("How should bills be handled?"),
    ]
    assert (
        visible_reply(None, messages) == "How should bills be handled?\n- Drafts only"
    )


@pytest.mark.asyncio
async def test_time_limit_keeps_partial_turn_and_stops_following_tasks():
    import asyncio

    case = DialogueCase.model_validate(
        {
            "name": "timed",
            "max_elapsed_sec": 0.01,
            "tasks": [{"name": "first", "goal": "A"}, {"name": "second", "goal": "B"}],
        }
    )

    async def choose(task, history, feedback):
        return UserAction(action="send", message="Begin")

    async def send(action, paths, index):
        await asyncio.Event().wait()
        return {"reply": "Unexpected"}

    async def verify(task, attempt):
        pytest.fail("A timed out turn must not be verified")

    result = await run_dialogue(case, choose, send, verify, lambda _: None)
    assert result["stop_reason"] == "time_limit"
    assert result["turns"][0]["runtime_status"] == "incomplete"
    assert [t["status"] for t in result["tasks"]] == ["incomplete", "not_run"]


def test_shipped_scenarios_load_from_unrelated_directory(monkeypatch, tmp_path):
    from pathlib import Path

    from examples.agents.clawworker_preview.experiment import load_config

    profiles = (
        Path(__file__).resolve().parents[3]
        / "examples/clawworker-preview/simulated-users"
    )
    monkeypatch.chdir(tmp_path)
    for name, count in [("ap-onboarding", 1), ("ap-matching", 6)]:
        config = load_config(profiles / f"{name}.yaml")
        assert config.simulated_user is not None
        assert len(config.simulated_user.cases) == count
        assert len(config.simulated_user.souls) == 3
        assert all(path.is_file() for path in config.simulated_user.souls.values())
        assert all(
            task.verifier.is_file()
            for case in config.simulated_user.cases
            for task in case.tasks
        )


@pytest.mark.asyncio
async def test_unknown_attachment_is_never_uploaded():
    case = DialogueCase.model_validate(
        {"name": "aliases", "tasks": [{"name": "match", "goal": "Match"}]}
    )

    attempts = []

    async def choose(task, history, feedback):
        attempts.append(feedback)
        return UserAction(
            action="send", message="Use this file", attachments=["../../private.txt"]
        )

    async def send(action, paths, index):
        pytest.fail("The Worker must not receive an unknown attachment")

    async def verify(task, attempt):
        pytest.fail("The task did not reach verification")

    result = await run_dialogue(case, choose, send, verify, lambda _: None)
    assert result["stop_reason"] == "simulated_user_attachment_limit"
    assert result["failure_source"] == "simulated_user"
    assert len(attempts) == 3
    assert result["tasks"][0]["repairs"] == 0
    assert result["turns"] == []


@pytest.mark.asyncio
async def test_simulated_user_can_correct_an_attachment_without_spending_a_worker_turn(
    tmp_path,
):
    invoice = tmp_path / "invoice.pdf"
    case = DialogueCase.model_validate(
        {
            "name": "correct-alias",
            "max_turns": 1,
            "tasks": [
                {
                    "name": "match",
                    "goal": "Match",
                    "max_repairs": 0,
                    "attachments": {"invoice": invoice},
                }
            ],
        }
    )
    actions = iter(
        [
            UserAction(
                action="send", message="Use this file", attachments=["invoice.pdf"]
            ),
            UserAction(
                action="send",
                message="Use the allowed invoice",
                attachments=["invoice"],
            ),
            UserAction(action="complete"),
        ]
    )
    decisions = []
    deliveries = []

    async def choose(task, history, feedback):
        decisions.append((len(history), feedback))
        return next(actions)

    async def send(action, paths, index):
        deliveries.append((action.message, paths, index))
        return {"reply": "Invoice received"}

    async def verify(task, attempt):
        pytest.fail("Unchecked tasks must not invoke the verifier")

    result = await run_dialogue(case, choose, send, verify, lambda _: None)
    assert result["stop_reason"] == "completed"
    assert decisions[0] == (0, None)
    assert decisions[1][0] == 0 and '"invoice"' in decisions[1][1]
    assert decisions[2] == (1, None)
    assert deliveries == [("Use the allowed invoice", [invoice], 1)]
    assert result["tasks"][0]["turn_count"] == 1
    assert result["tasks"][0]["repairs"] == 0
    assert result["tasks"][0]["attachment_rejections"] == [["invoice.pdf"]]
