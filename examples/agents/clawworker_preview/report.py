"""Report recorded turn results without treating an agent's success claim as a score."""

import json
from pathlib import Path
from typing import Any

from harbor.models.trial.result import StepResult, TrialResult


def summarize_turn(
    question: str, checked: bool, step: StepResult | None, logs: Path
) -> dict[str, Any]:
    native = (
        json.loads((logs / "result.json").read_text())
        if (logs / "result.json").exists()
        else {}
    )
    metadata = (step.agent_result.metadata or {}) if step and step.agent_result else {}
    timing = step.agent_execution if step else None
    exception = step.exception_info if step else None
    status = "not_run"
    if timing and timing.finished_at:
        status = native.get("status", "completed")
    if exception and not native.get("status"):
        status = "failed"
    rewards = step.verifier_result.rewards if step and step.verifier_result else None
    if step is None or (checked and status == "not_run"):
        verification = "not_run"
    elif not checked:
        verification = "unverified"
    else:
        verification = (
            "passed"
            if status == "completed"
            and not exception
            and rewards
            and rewards.get("reward", 0) >= 1
            else "failed"
        )
    reply = native.get("output")
    trajectory = logs / "trajectory.json"
    if reply is None and trajectory.exists():
        messages = json.loads(trajectory.read_text()).get("steps", [])
        reply = next(
            (
                m.get("message")
                for m in reversed(messages)
                if m.get("source") == "agent"
            ),
            None,
        )
    return {
        "question": question,
        "reply": reply,
        "runtime_status": status,
        "verification_status": verification,
        "rewards": rewards if checked else None,
        "exception": exception.model_dump(mode="json") if exception else None,
        "session_id": native.get("session_id"),
        "run_id": native.get("run_id"),
        "started_at": timing.started_at.isoformat()
        if timing and timing.started_at
        else None,
        "finished_at": timing.finished_at.isoformat()
        if timing and timing.finished_at
        else None,
        "agent_execution_seconds": (
            timing.finished_at - timing.started_at
        ).total_seconds()
        if timing and timing.started_at and timing.finished_at
        else None,
        "native_execution_seconds": metadata.get("agent_execution_seconds"),
        "llm_http_requests": metadata.get("llm_http_requests"),
        "model_requests": metadata.get("model_requests"),
        "usage": metadata.get("usage"),
        "logs": str(logs),
    }


def summarize_trial(
    trial: TrialResult, path: Path, case: str, turns: list[tuple[str, bool]]
) -> dict[str, Any]:
    steps = {step.step_name: step for step in trial.step_results or []}
    reports = []
    for index, (question, checked) in enumerate(turns, start=1):
        name = f"turn-{index:02d}"
        if trial.step_results is not None:
            step = steps.get(name)
            logs = path.parent / "steps" / name / "agent"
        else:
            step = StepResult(
                step_name=name,
                agent_result=trial.agent_result,
                agent_execution=trial.agent_execution,
                verifier_result=trial.verifier_result,
                exception_info=trial.exception_info,
            )
            logs = path.parent / "agent"
        reports.append({"name": name, **summarize_turn(question, checked, step, logs)})
    complete = not trial.exception_info and all(
        t["runtime_status"] == "completed" for t in reports
    )
    scored = any(checked for _, checked in turns)
    verification = "unverified"
    if any(t["verification_status"] == "failed" for t in reports):
        verification = "failed"
    elif not complete:
        verification = "incomplete"
    elif scored:
        verification = "passed"
    executed = [t for t in reports if t["runtime_status"] != "not_run"]
    counts = [t["llm_http_requests"] for t in executed]
    timings = [t["agent_execution_seconds"] for t in executed]
    return {
        "name": trial.trial_name,
        "case": case,
        "agent": trial.agent_info.model_dump(mode="json"),
        "exception": trial.exception_info.model_dump(mode="json")
        if trial.exception_info
        else None,
        "rewards": trial.verifier_result.rewards if trial.verifier_result else None,
        "scored": scored,
        "execution_complete": bool(complete),
        "verification_status": verification,
        "llm_http_requests": sum(counts)
        if counts and all(c is not None for c in counts)
        else None,
        "agent_execution_seconds": sum(timings)
        if timings and all(t is not None for t in timings)
        else None,
        "turns": reports,
        "result": str(path),
    }
