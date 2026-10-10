"""Current workflow contract: generated ticks permit replay with a result object."""
from event_backtest import recording, runner, workflow
from event_backtest.history_check import require_complete, tick_warning


def test_generated_tick_warning_and_current_result_object(tmp_path, monkeypatch):
    capture = dict(start="2025-09-01", end="2025-10-01", history_missing=[],
                   tick_evidence={"actual": "MIXED_OR_GENERATED"})
    require_complete([capture])
    assert "생성 틱" in tick_warning(capture, "BAR")
    monkeypatch.setattr(runner, "runtime_config", lambda scenario: {})
    monkeypatch.setattr(workflow, "proposal", lambda *args, **kwargs: dict(record=[], approval_token="same"))
    monkeypatch.setattr(recording, "prepare", lambda *args, **kwargs: [capture])
    calls = []
    result = dict(status="COMPLETE", result_mode="ALERT_ONLY")

    def replay(*args, **kwargs):
        calls.append(kwargs["captures"])
        return result

    monkeypatch.setattr(runner, "run", replay)
    assert workflow.execute(dict(strategies=["SPECIAL1"]), tmp_path) is result
    assert calls == [[capture]]
