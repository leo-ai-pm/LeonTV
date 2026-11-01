"""Controlled capture calibration, not evidence of historical COMP01 cause."""
import json
from datetime import timedelta

import pytest
from legacy_reserve_trace import ReserveTrace
from media_ledger_fixture import authorize, media_case
from test_execution_empirical import empirical_env as empirical_env
from test_planner_execution import no_network as no_network

from core.creator.store import CreatorError
from core.execution import media_runtime
from core.execution.image_policy import timestamp


@pytest.mark.parametrize("collector_fault", ["none", "result_write", "source_read"])
def test_actual_reserve_controlled_negative_preserves_original_error(empirical_env, tmp_path, monkeypatch, collector_fault):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    before = case.media.store.path("state.json").read_bytes()
    original = media_runtime.MediaExecution._record

    def bad_operation(kind, *args, **kwargs):
        result = original(kind, *args, **kwargs)
        if kind == "operation":
            result["created_at"] = (timestamp(result["descriptor"]["permit"]["issued_at"]) - timedelta(microseconds=1)).isoformat()
        return result

    monkeypatch.setattr(media_runtime.MediaExecution, "_record", staticmethod(bad_operation))
    if collector_fault == "result_write":
        import legacy_reserve_trace

        writer = legacy_reserve_trace.write_private
        def fail_result(path, value):
            if path.name == "result.json":
                raise OSError("controlled collector IO failure")
            return writer(path, value)
        monkeypatch.setattr(legacy_reserve_trace, "write_private", fail_result)
    elif collector_fault == "source_read":
        import legacy_reserve_trace

        monkeypatch.setattr(legacy_reserve_trace.linecache, "getline", lambda *args: (_ for _ in ()).throw(OSError("controlled stack source failure")))
    trace = ReserveTrace(tmp_path / "controlled-capture")
    with pytest.raises(CreatorError) as error, trace:
        case.media.reserve_submit(case.task["id"], grant["id"], "S01", key="controlled-capture-reserve")
    assert error.value.code == "CORRUPT_STORAGE"
    assert case.media.store.path("state.json").read_bytes() == before
    if collector_fault != "source_read":
        captured = json.loads((trace.output / "candidate-first-error.json").read_bytes())
        assert any('timestamp(grant["created_at"])' in f["source"] for f in captured["first_exception"]["assertion"])
        assert trace.captured
    if collector_fault != "none":
        assert "OSError" in trace.capture_errors


def test_runner_preserves_partial_timeout_output_and_failure(tmp_path, monkeypatch):
    import subprocess

    from run_legacy_reserve_diagnosis import run

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"partial stdout\xff", stderr=b"partial stderr")

    monkeypatch.setattr(subprocess, "run", timeout)
    output = tmp_path / "timeout-output"
    assert run(output, 1, True, 0, "batch") == 124
    result = json.loads((output / "runner-result.json").read_bytes())
    assert result["timed_out"] and result["exit_code"] == 124
    assert result["partial_output_preserved"]
    text = (output / "pytest.log").read_text()
    assert "partial stdout" in text and "partial stderr" in text
