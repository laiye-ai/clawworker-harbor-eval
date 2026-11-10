"""A model saying 'done' is separate from actual execution and verification."""

from datetime import UTC, datetime, timedelta
import json

from harbor.models.agent.context import AgentContext
from harbor.models.trial.result import ExceptionInfo, StepResult, TimingInfo
from harbor.models.verifier.result import VerifierResult


def test_turn_report_uses_actual_reply_usage_and_failed_check(tmp_path):
    from harbor.agents.clawworker_preview.report import summarize_turn

    (tmp_path / "result.json").write_text(
        json.dumps(
            {
                "output": "I completed it.",
                "status": "completed",
                "session_id": "native-session",
                "run_id": "run-2",
            }
        )
    )
    now = datetime.now(UTC)
    step = StepResult(
        step_name="turn-02",
        agent_result=AgentContext(
            metadata={
                "llm_http_requests": 3,
                "usage": {"input_tokens": 25},
                "agent_execution_seconds": 0.5,
            }
        ),
        agent_execution=TimingInfo(
            started_at=now, finished_at=now + timedelta(seconds=2)
        ),
        verifier_result=VerifierResult(rewards={"reward": 0}),
    )
    result = summarize_turn("Check the result.", True, step, tmp_path)
    assert result["reply"] == "I completed it."
    assert result["runtime_status"] == "completed"
    assert result["verification_status"] == "failed"
    assert result["rewards"] == {"reward": 0}
    assert result["llm_http_requests"] == 3
    assert result["agent_execution_seconds"] == 2
    assert result["native_execution_seconds"] == 0.5
    assert result["usage"] == {"input_tokens": 25}
    assert result["run_id"] == "run-2"
    assert result["session_id"] == "native-session"


def test_unexecuted_turn_is_not_zero_call_or_success(tmp_path):
    from harbor.agents.clawworker_preview.report import summarize_turn

    result = summarize_turn("An unsent answer.", True, None, tmp_path)
    assert result["runtime_status"] == "not_run"
    assert result["verification_status"] == "not_run"
    assert result["reply"] is None
    assert result["llm_http_requests"] is None


def test_verifier_error_does_not_rewrite_the_actual_native_run_status(tmp_path):
    from harbor.agents.clawworker_preview.report import summarize_turn

    (tmp_path / "result.json").write_text(
        json.dumps({"status": "completed", "output": "Actual reply"})
    )
    now = datetime.now(UTC)
    step = StepResult(
        step_name="turn-01",
        agent_execution=TimingInfo(started_at=now, finished_at=now),
        verifier=TimingInfo(started_at=now, finished_at=now),
        exception_info=ExceptionInfo.from_exception(RuntimeError("Verifier crashed")),
    )
    result = summarize_turn("Question", True, step, tmp_path)
    assert result["runtime_status"] == "completed"
    assert result["verification_status"] == "failed"
    assert result["exception"]["exception_message"] == "Verifier crashed"


def test_native_cli_reply_can_come_from_atif_but_call_count_is_unknown(tmp_path):
    from harbor.agents.clawworker_preview.report import summarize_turn

    (tmp_path / "trajectory.json").write_text(
        json.dumps(
            {
                "steps": [
                    {"source": "user", "message": "Question"},
                    {"source": "agent", "message": "Actual CLI reply"},
                ]
            }
        )
    )
    now = datetime.now(UTC)
    step = StepResult(
        step_name="turn-01", agent_execution=TimingInfo(started_at=now, finished_at=now)
    )
    result = summarize_turn("Question", False, step, tmp_path)
    assert result["reply"] == "Actual CLI reply"
    assert result["llm_http_requests"] is None
    assert result["verification_status"] == "unverified"
