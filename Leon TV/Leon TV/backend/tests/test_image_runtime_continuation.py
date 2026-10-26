"""A new exact runtime lease reuses, rather than reserves, D37 funding."""
import copy
from datetime import timedelta

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from test_image_validity_continuation import activate, prepare, prepared_validity_case, quote

from core.creator.store import CreatorError, digest
from core.execution import image_continuation_funding as funding
from core.execution import image_continuation_parent_io as parent_io
from core.execution import image_runtime_continuation as continuation
from core.execution.policy import now
from core.execution.scope_budget import scope_envelope
from core.execution.store import validate_state


def prepared_runtime_case(asset_control_case):
    case = prepared_validity_case(asset_control_case)
    q = quote(case)
    intent = prepare(case, q)
    activation = activate(case, q, intent)
    case.validity_quote = q["quote"]
    case.validity_intent = intent["intent"]
    case.validity_activation = activation["activation"]
    case.d37_payload = case.env.runtime.store.read_only()
    case.d37_parent_bytes = case.server_parent_path.read_bytes()
    case.runtime_valid_until = (now() + timedelta(hours=4)).isoformat()
    q = continuation.quote_runtime_continuation(case.control, valid_until=case.runtime_valid_until)
    a = continuation.activate_runtime_continuation(case.control, q["quote"]["id"],
        disclosure_hash=q["quote"]["disclosure_hash"], consent=True)
    case.runtime_quote, case.runtime_activation = q["quote"], a["activation"]
    case.runtime_authority = continuation.authority_for(case.env.runtime.store.read_only(), a["activation"]["id"])
    return case


def test_runtime_quote_activation_replays_retain_same_funding_and_history(asset_control_case, monkeypatch):
    case = prepared_runtime_case(asset_control_case)
    store, a = case.env.runtime.store, case.runtime_activation
    payload = store.read_only()
    value = case.runtime_authority
    assert value["effective_policy"]["max_submissions"] == value["original_policy"]["max_submissions"] + 1
    assert value["additional_risk_cny"] == "0.000000" and value["additional_paid_calls"] == 0
    assert value["original_budget_anchor"] == case.control.document.budget_anchor
    assert [item["role"] for item in value["allocations"]] == ["original", "continuation"]
    assert value["allocations"][0]["allocation_hash"] == case.control.document.budget_anchor["parent_allocation_hash"]
    assert value["allocations"][1]["allocation_hash"] == case.validity_activation["parent_receipt"]["allocation_hash"]
    original, checks = case.control.__class__._verified, []

    def checked(self, *args, **kwargs):
        checks.append(kwargs.get("for_dispatch"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(case.control.__class__, "_verified", checked)
    assert continuation.verify_current(case.control, payload, a["id"])
    assert continuation.verify_current(case.control, payload, a["id"])
    # A fresh check per decision, without rebuilding the same head again in
    # its internal funding collector or reusing an earlier permission result.
    assert checks == [False, False]
    assert payload["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert all(digest(payload[n]) == digest(v) for n, v in case.d37_payload.items() if n != "media")
    assert all(payload["media"][k] == v for k, v in case.d37_payload["media"].items())
    account = case.control.document.budget_anchor["account_scope_id"]
    assert scope_envelope(payload, account) == scope_envelope(case.d37_payload, account)
    before = store.path("state.json").read_bytes()
    assert continuation.quote_runtime_continuation(case.control, valid_until=case.runtime_valid_until)["replayed"]
    assert continuation.activate_runtime_continuation(case.control, case.runtime_quote["id"],
        disclosure_hash=case.runtime_quote["disclosure_hash"], consent=True)["replayed"]
    assert store.path("state.json").read_bytes() == before and case.server_parent_path.read_bytes() == case.d37_parent_bytes


def test_code_revision_uses_fact_only_funding_and_keeps_prior_authority_historical(asset_control_case):
    case = prepared_runtime_case(asset_control_case)
    store = case.env.runtime.store
    old = copy.deepcopy(case.runtime_authority)
    name = next(iter(case.control.document.code_manifest))
    (case.backend / name).write_text("# Fictional explicitly revised implementation.\n")
    before = store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        continuation.verify_current(case.control, store.read_only(), case.runtime_activation["id"])
    assert store.path("state.json").read_bytes() == before
    # The old D37 writer/authority contract remains strict, while its existing
    # funding can independently be established as a fact under a new code hash.
    with pytest.raises(CreatorError):
        parent_io.read_frozen_parent_evidence(case.control, case.validity_quote["proposal"])
    assert funding.read_funding(case.control, store.read_only(), case.validity_activation["id"])["intent_id"] == old["intent_id"]
    q = continuation.quote_runtime_continuation(case.control, valid_until=case.runtime_valid_until)["quote"]
    a = continuation.activate_runtime_continuation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
    payload = store.read_only()
    new = continuation.authority_for(payload, a["id"])
    assert a["activation_sequence"] == 2 and a["previous_activation_id"] == case.runtime_activation["id"]
    assert new["intent_id"] == old["intent_id"] and new["allocations"] == old["allocations"]
    assert continuation.authority_for(payload, case.runtime_activation["id"]) == old
    with pytest.raises(CreatorError):
        continuation.verify_current(case.control, payload, case.runtime_activation["id"])
    assert continuation.verify_current(case.control, payload, a["id"])
    assert case.server_parent_path.read_bytes() == case.d37_parent_bytes


def test_runtime_lease_may_follow_expired_d37_without_new_funding(asset_control_case, monkeypatch):
    case = prepared_runtime_case(asset_control_case)
    later = now() + timedelta(hours=6)
    monkeypatch.setattr(continuation, "now", lambda: later)
    until = (later + timedelta(hours=2)).isoformat()
    q = continuation.quote_runtime_continuation(case.control, valid_until=until)["quote"]
    a = continuation.activate_runtime_continuation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
    assert continuation.verify_current(case.control, case.env.runtime.store.read_only(), a["id"])
    assert case.server_parent_path.read_bytes() == case.d37_parent_bytes


def test_strict_consent_code_and_birth_checkpoint_cannot_be_skipped(asset_control_case):
    case = prepared_runtime_case(asset_control_case)
    until = (now() + timedelta(hours=5)).isoformat()
    q = continuation.quote_runtime_continuation(case.control, valid_until=until)["quote"]
    before = case.env.runtime.store.path("state.json").read_bytes()
    for consent in (False, 1, "true", None):
        with pytest.raises(CreatorError):
            continuation.activate_runtime_continuation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=consent)
        assert case.env.runtime.store.path("state.json").read_bytes() == before
    state = continuation._state(case.env.runtime.store)
    qkey = continuation.QUOTE + ":" + q["id"]
    state["frames"][-1]["delta"]["media"][qkey]["disclosure"]["checkpoint"]["head_revision"] -= 1
    # Resealing all superficial record/frame hashes must not repair the real
    # birth coordinate. The independently reconstructed prefix still wins.
    record = state["frames"][-1]["delta"]["media"][qkey]
    record["disclosure_hash"] = digest(record["disclosure"])
    record["id"] = digest({"kind": continuation.QUOTE, "scope_id": record["scope_id"],
        "disclosure": record["disclosure"], "disclosure_hash": record["disclosure_hash"]})[:32]
    state["frames"][-1]["delta"]["media"] = {continuation.QUOTE + ":" + record["id"]: record}
    frame = state["frames"][-1]
    frame["hash"] = digest({k: v for k, v in frame.items() if k != "hash"})
    state["head_hash"] = frame["hash"]
    with pytest.raises(CreatorError):
        validate_state(state)
