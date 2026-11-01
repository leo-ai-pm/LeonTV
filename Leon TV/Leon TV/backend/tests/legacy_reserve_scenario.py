"""Fresh native text/fee fixture; repeated real first reserve, no image HTTP."""
import hashlib
import json
import os
import statistics
import time
from pathlib import Path

from legacy_reserve_trace import ReserveTrace
from media_ledger_fixture import authorize, media_case
from test_execution_empirical import empirical_env as empirical_env


def test_native_legacy_reserve_batch(empirical_env, tmp_path, monkeypatch):
    assert os.environ.get("LEONTV_RESERVE_DIAGNOSTIC") == "1"
    output = Path(os.environ["RESERVE_DIAGNOSTIC_OUTPUT"])
    count = int(os.environ.get("RESERVE_DIAGNOSTIC_ITERATIONS", "8"))
    delay_ms = int(os.environ.get("RESERVE_DIAGNOSTIC_BUILD_DELAY_MS", "0"))
    if delay_ms:
        from core.execution import media_runtime

        original = media_runtime.build_image_submit
        def scheduling_window(*args, **kwargs):
            result = original(*args, **kwargs)
            # Scheduling amplification only: actual clock functions, metadata
            # and every validator remain unchanged. No network is entered.
            time.sleep(delay_ms / 1000)
            return result
        monkeypatch.setattr(media_runtime, "build_image_submit", scheduling_window)
    case = media_case(empirical_env, fault="unknown")
    _, grant = authorize(case)
    store, task_id = case.media.store, case.task["id"]
    assert store.root.is_relative_to(tmp_path)
    initial = store.read_only()
    scope = next(r for r in initial["media"].values() if r["kind"] == "scope")
    assert scope["policy"]["validation_only"] is True and scope["policy"]["origin"] == "https://synthetic.invalid"
    state = store.path("state.json")
    baseline = state.read_bytes()
    results, failures = [], []
    try:
        for index in range(count):
            state.write_bytes(baseline)
            state.chmod(0o600)
            trace = ReserveTrace(output / ("attempt-%04d" % index), clock_returns=os.environ.get("RESERVE_DIAGNOSTIC_CLOCKS", "1") == "1")
            started = time.monotonic()
            try:
                with trace:
                    operation = case.media.reserve_submit(task_id, grant["id"], "S01", key="synthetic-media-submit-001")["operation"]
                assert operation["status"] == "reserved" and "production_persistence_activation_id" not in operation
            except Exception as error:
                failures.append({"index": index, "type": type(error).__name__, "code": getattr(error, "code", None)})
            results.append({"index": index, "seconds": time.monotonic() - started,
                            "failed": bool(failures and failures[-1]["index"] == index), **trace.result})
            if failures:
                break
    finally:
        state.write_bytes(baseline)
        state.chmod(0o600)
        summary = {"iterations_requested": count, "iterations_completed": len(results), "failures": failures,
                   "baseline_sha256": hashlib.sha256(baseline).hexdigest(), "baseline_restored": state.read_bytes() == baseline,
                   "median_reserve_seconds": statistics.median(r["seconds"] for r in results) if results else None,
                   "max_reserve_seconds": max((r["seconds"] for r in results), default=None),
                   "results": results, "image_submit_calls": 0, "image_queries": 0,
                   "source": "fresh public synthetic fixture; no historical evidence dependency"}
        summary["controlled_scheduling_delay_ms"] = delay_ms
        summary["independent_fresh_accounts"] = 1
        summary["iterations_are_synthetic_prefix_branches"] = True
        (output / "batch-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    assert not failures, failures
