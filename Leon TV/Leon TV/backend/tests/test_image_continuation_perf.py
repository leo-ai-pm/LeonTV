"""Each continued-control decision consumes one fresh native snapshot."""

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from test_image_code_continuation import activate, prepared

from core.execution import image_code_continuation as continuation
from core.execution.store import ExecutionStore


@pytest.mark.parametrize("decision", ["verify", "guard"])
def test_one_continued_authority_decision_reads_history_once(asset_control_case, monkeypatch, decision):
    case = prepared(asset_control_case)
    _, wrapped = activate(case)
    payload = case.env.runtime.store.read_only()
    scope = next(record for record in payload["media"].values() if record["kind"] == "scope")
    original_read = ExecutionStore.read_only
    reads = []

    def read_once(store):
        reads.append(store.root)
        return original_read(store)

    monkeypatch.setattr(ExecutionStore, "read_only", read_once)
    if decision == "verify":
        assert wrapped.verify_authority(for_dispatch=True)
    else:
        assert wrapped.guard(scope)
    assert len(reads) == 1, f"{decision} rebuilt the same native history {len(reads)} times"


def test_separate_decisions_and_standalone_manifest_keep_fresh_checks(asset_control_case, monkeypatch):
    case = prepared(asset_control_case)
    _, wrapped = activate(case)
    payload = case.env.runtime.store.read_only()
    scope = next(record for record in payload["media"].values() if record["kind"] == "scope")
    original_read = ExecutionStore.read_only
    reads = []

    def fresh_read(store):
        payload = original_read(store)
        reads.append(payload)
        return payload

    monkeypatch.setattr(ExecutionStore, "read_only", fresh_read)
    for _ in range(2):
        assert wrapped.verify_authority(for_dispatch=True)
        assert wrapped.guard(scope)
    assert len(reads) == 4
    assert len({id(payload) for payload in reads}) == 4
    manifest = continuation.current_code_manifest(case.control)
    assert len(reads) == 5
    assert manifest[case.changed_code.relative_to(case.backend).as_posix()]
    # Authority still precedes the independent manifest helper; no earlier
    # decision or pure file read can authorize a later call after revocation.
    case.media.revoke(case.task["id"], case.grant["id"])
    assert wrapped.guard(scope) is False
