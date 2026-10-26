"""Independent attacks on the narrow, typed continuation candidate exception."""
import copy
import socket

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from asset_operator_fixture import private_json
from image_operator_fixture import posts
from test_image_runtime_continuation import prepared_runtime_case

from core.creator.store import CreatorError, digest, utc_now
from core.execution import image_continuation_funding as funding
from core.execution import image_runtime_continuation as runtime_continuation
from core.execution.media_continuation_gate import candidate_gate
from core.execution.media_ledger import has_pending_fees, has_pending_unknown, records
from core.execution.media_runtime import MediaExecution
from core.execution.scope_budget import scope_envelope


def continuing_media(case):
    original = case.media
    return MediaExecution(case.env.runtime, policy=original.policy, budget_anchor=original.anchor,
        manifest=original.manifest, authority_guard=original.authority_guard,
        fee_source=original.fee_source, receipt_capture=case.settings,
        reference_profile=original.reference_profile, manifest_origin=original.manifest_origin,
        continuation_activation_id=case.runtime_activation["id"],
        # Fictional fixture only; the pure candidate gate is never replaced.
        continuation_guard=lambda scope, authority: authority == case.runtime_authority)


def reserved_case(asset_control_case):
    case = prepared_runtime_case(asset_control_case)
    case.continuing = continuing_media(case)
    target = case.runtime_authority["remaining_jobs"][0]
    case.replacement = case.continuing.reserve_submit(case.task["id"], case.grant["id"],
        target["shot_id"], key="independent-continuation-replacement")["operation"]
    return case


def test_only_the_exact_media_candidate_can_borrow_the_retained_unknown_exception(asset_control_case, monkeypatch):
    case = reserved_case(asset_control_case)
    store = case.env.runtime.store
    payload = store.read_only()
    candidate = payload["media"]["operation:" + case.replacement["id"]]
    scope = payload["media"]["scope:" + candidate["scope_id"]]
    account = scope["account_id"]
    before, parent_before = store.path("state.json").read_bytes(), case.server_parent_path.read_bytes()
    held, captured = scope_envelope(payload, account), len(posts(case))
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("Network in candidate-gate audit"))

    assert has_pending_unknown(payload, account) and has_pending_fees(payload, account)
    assert candidate["submission_index"] == 3
    assert candidate["descriptor"]["permit"]["attempt_version"] == 2
    assert candidate["descriptor"]["permit"]["replacement_of"] == case.unknown["id"]
    authority = candidate_gate(payload, candidate, exclude_candidate=True)
    authority["remaining_jobs"].clear()
    assert candidate_gate(payload, candidate, exclude_candidate=True) == case.runtime_authority

    attacks = []
    altered = copy.deepcopy(payload)
    altered["media"]["operation:" + case.unknown["id"]]["status"] = "submitting"
    attacks.append(("changed retained unknown", altered, copy.deepcopy(candidate)))
    altered = copy.deepcopy(payload)
    altered["media"]["grant:" + case.grant["id"]]["revoked_at"] = utc_now()
    attacks.append(("revoked original grant", altered, copy.deepcopy(candidate)))
    altered_candidate = copy.deepcopy(candidate)
    altered_candidate["scope_id"] = "f" * 32
    attacks.append(("unrelated scope", copy.deepcopy(payload), altered_candidate))
    altered_candidate = copy.deepcopy(candidate)
    altered_candidate["submission_index"] = 6
    attacks.append(("sixth cumulative call", copy.deepcopy(payload), altered_candidate))
    altered_candidate = copy.deepcopy(candidate)
    altered_candidate["status"] = "submitting"
    attacks.append(("different persisted candidate", copy.deepcopy(payload), altered_candidate))

    # A coincident identifier in a different owner namespace must still block.
    # The exception removes only the exact media record, never asset/text IDs.
    asset_operation = next(item for item in payload["asset_design"].values() if item["kind"] == "operation")
    for collision in (case.unknown["id"], candidate["id"]):
        altered = copy.deepcopy(payload)
        item = copy.deepcopy(asset_operation)
        item.update(id=collision, status="unknown", fee_id=None)
        altered["asset_design"]["operation:" + collision] = item
        attacks.append(("same ID belongs to an asset operation", altered, copy.deepcopy(candidate)))
    altered = copy.deepcopy(payload)
    native = next(item for item in altered["operations"].values() if item["account_id"] == account)
    native["status"] = "needs_reconciliation"
    attacks.append(("unsettled text operation", altered, copy.deepcopy(candidate)))
    for status in ("unknown", "accepted"):
        altered = copy.deepcopy(payload)
        foreign_scope = copy.deepcopy(scope)
        foreign_scope.update(id="b" * 32, scope_id="b" * 32)
        altered["media"]["scope:" + foreign_scope["id"]] = foreign_scope
        item = copy.deepcopy(case.first)
        item.update(id="e" * 32, scope_id=foreign_scope["id"], status=status, fee_id=None, delivery_id=None)
        altered["media"]["operation:" + item["id"]] = item
        attacks.append(("another media unknown or pending fee", altered, copy.deepcopy(candidate)))
    for status in ("reserved", "submitting", "unknown"):
        altered = copy.deepcopy(payload)
        item = copy.deepcopy(records(payload, "query")[0])
        item.update(id="d" * 32, status=status)
        altered["media"]["query:" + item["id"]] = item
        attacks.append(("another unresolved query", altered, copy.deepcopy(candidate)))

    for label, altered, proposed in attacks:
        try:
            candidate_gate(altered, proposed, exclude_candidate=True)
        except CreatorError:
            pass
        else:
            pytest.fail(label)
    assert store.path("state.json").read_bytes() == before
    assert case.server_parent_path.read_bytes() == parent_before
    assert scope_envelope(store.read_only(), account) == held and len(posts(case)) == captured
def test_original_paths_and_new_unknown_still_stop_without_rewriting_history(asset_control_case):
    case = reserved_case(asset_control_case)
    store = case.env.runtime.store
    payload = store.read_only()
    before, parent_before = store.path("state.json").read_bytes(), case.server_parent_path.read_bytes()
    captured = len(posts(case))
    with pytest.raises(CreatorError) as error:
        case.media.reserve_submit(case.task["id"], case.grant["id"], case.unknown["shot_id"],
            key="original-runtime-cannot-use-continuation-exception")
    assert error.value.code == "MEDIA_RESPONSE_UNCONFIRMED"
    assert store.path("state.json").read_bytes() == before

    # The same original store rejects editing the retained unknown, even with
    # a valid continuation already available; failure leaves its bytes intact.
    with pytest.raises(CreatorError):
        with store.transaction() as current:
            current["media"]["operation:" + case.unknown["id"]]["capture_started_at"] = utc_now()
    assert store.path("state.json").read_bytes() == before

    candidate_key = "operation:" + case.replacement["id"]
    with store.transaction() as current:
        current["media"][candidate_key].update(status="submitting", capture_started_at=utc_now())
    with store.transaction() as current:
        current["media"][candidate_key]["status"] = "unknown"
    stopped = store.path("state.json").read_bytes()
    for target in case.runtime_authority["remaining_jobs"][1:]:
        with pytest.raises(CreatorError):
            case.continuing.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"],
                key="new-unknown-stops-" + target["shot_id"])
        assert store.path("state.json").read_bytes() == stopped
    current = store.read_only()
    assert current["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert digest(current["media"][candidate_key]["descriptor"]) == digest(case.replacement["descriptor"])
    assert case.server_parent_path.read_bytes() == parent_before and len(posts(case)) == captured
    assert scope_envelope(current, payload["media"]["scope:" + case.replacement["scope_id"]]["account_id"]) == scope_envelope(payload,
        payload["media"]["scope:" + case.replacement["scope_id"]]["account_id"])


def test_funding_artifact_bytes_and_late_unknown_evidence_remain_fresh_checks(asset_control_case, monkeypatch):
    case = prepared_runtime_case(asset_control_case)
    store = case.env.runtime.store
    payload = store.read_only()
    native_before, parent_before = store.path("state.json").read_bytes(), case.server_parent_path.read_bytes()
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("Network during funding fact verification"))
    proposal_hash = case.validity_quote["proposal"]["proposal_hash"]
    artifact = case.control.roots["execution"] / "continuation-parent" / (proposal_hash + ".json")
    original_artifact = artifact.read_bytes()
    fact = funding.read_funding(case.control, payload, case.validity_activation["id"])
    assert fact["intent_id"] == case.validity_intent["id"]
    assert runtime_continuation.verify_current(case.control, payload, case.runtime_activation["id"])
    try:
        # JSON-semantic equality does not replace the recorded raw-file SHA.
        artifact.write_bytes(original_artifact + b" ")
        with pytest.raises(CreatorError):
            funding.read_funding(case.control, payload, case.validity_activation["id"])
        with pytest.raises(CreatorError):
            runtime_continuation.verify_current(case.control, payload, case.runtime_activation["id"])
    finally:
        artifact.write_bytes(original_artifact)
    assert funding.read_funding(case.control, payload, case.validity_activation["id"]) == fact
    assert runtime_continuation.verify_current(case.control, payload, case.runtime_activation["id"])

    collector = case.control.roots["execution"] / "media-receipts" / case.unknown["id"]
    collector.mkdir(mode=0o700)
    private_json(collector / "claim.json", {"fictional_late_evidence": True})
    with pytest.raises(CreatorError):
        runtime_continuation.verify_current(case.control, payload, case.runtime_activation["id"])
    # A late collector claim stops authority; it cannot erase actual funding.
    assert funding.read_funding(case.control, payload, case.validity_activation["id"]) == fact
    assert store.path("state.json").read_bytes() == native_before
    assert case.server_parent_path.read_bytes() == parent_before
    assert store.read_only()["media"]["operation:" + case.unknown["id"]] == case.unknown
