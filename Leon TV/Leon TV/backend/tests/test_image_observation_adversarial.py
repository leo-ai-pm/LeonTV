"""Independent attacks on the explicit, single-parent GET-only continuation."""
import base64
import copy
import json
import socket
import uuid

import httpx
import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import posts
from test_image_observation_continuation import prepared_observation_case
from test_image_receipts import query_raw

from core.creator.store import CreatorError, digest, utc_now
from core.execution import image_observation_continuation as observation
from core.execution import image_operator
from core.execution.image_observation_control import ImageObservationControl
from core.execution.image_observation_policy import build_observation_query, make_observation_permit
from core.execution.image_policy import FrozenImageRequest
from core.execution.image_receipts import decode_query, decode_submit
from core.execution.media_ledger import has_pending_fees, has_pending_unknown, records
from core.execution.receipts import ReceiptStore
from core.execution.scope_budget import scope_envelope


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Observation audit attempted external network")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def proposed_query(case, payload, *, index=1, operation_id=None, authority=None):
    authority = authority or case.observation_authority
    source = payload["media"]["operation:" + authority["source_operation_id"]]
    scope = payload["media"]["scope:" + authority["scope_id"]]
    grant = payload["media"]["grant:" + authority["grant_id"]]
    query_id = operation_id or uuid.uuid4().hex
    permit = make_observation_permit(scope, grant, source, authority,
        operation_id=query_id, query_index=index)
    descriptor = build_observation_query(submit=source["descriptor"], submit_raw=source["raw"],
        permit=permit, observation_authority=authority, query_index=index)
    return {"schema_version": 1, "kind": "query", "id": query_id, "scope_id": scope["id"],
        "task_id": scope["task_id"], "created_at": utc_now(), "descriptor": descriptor.frozen(),
        "operation_id": source["id"], "query_index": index, "status": "reserved",
        "capture_started_at": None, "raw": None, "receipt": None}


def changed_nonce(candidate, nonce):
    changed = copy.deepcopy(candidate)
    data = changed["descriptor"]
    permit = data["permit"]
    permit["nonce"] = nonce
    permit["permit_hash"] = digest({k: v for k, v in permit.items() if k != "permit_hash"})
    changed["descriptor"] = build_observation_query(submit=data["parent_submit"], submit_raw=data["submit_raw"],
        permit=permit, observation_authority=data["observation_authority"], query_index=data["query_index"]).frozen()
    return changed


def test_only_the_exact_get_can_use_the_disclosed_parent_and_unknown_exceptions(asset_control_case, monkeypatch):
    case = prepared_observation_case(asset_control_case)
    store = case.env.runtime.store
    payload = store.read_only()
    candidate = proposed_query(case, payload)
    authority = case.observation_authority
    source_id = authority["source_operation_id"]
    scope = payload["media"]["scope:" + authority["scope_id"]]
    account = scope["account_id"]
    native_before, parent_before = store.path("state.json").read_bytes(), case.server_parent_path.read_bytes()
    captured_before, envelope_before = len(posts(case)), scope_envelope(payload, account)
    assert has_pending_unknown(payload, account) and has_pending_fees(payload, account)
    returned = observation.candidate_gate(payload, candidate)
    returned["effective_policy"]["max_queries"] = 0
    assert observation.candidate_gate(payload, candidate) == authority

    # A valid native envelope is not a substitute for the original collector's
    # exact bound, uniquely committed and notified evidence on each fresh check.
    collector = ReceiptStore(case.control.roots["execution"] / "media-receipts")
    original_request = FrozenImageRequest.from_frozen(payload["media"]["operation:" + source_id]["descriptor"])
    original_raw, inspection = collector.ready_raw_binding(source_id, original_request.binding())
    assert observation.verify_current(case.control, payload, authority["activation_id"])
    altered_raw = copy.deepcopy(original_raw)
    altered_raw["http_status"] = 418
    observations = [(None, copy.deepcopy(inspection)), (altered_raw, copy.deepcopy(inspection))]
    for name, value in (("binding_verified", False), ("notification_present", False), ("candidate_count", 2)):
        altered_inspection = copy.deepcopy(inspection)
        altered_inspection[name] = value
        observations.append((copy.deepcopy(original_raw), altered_inspection))
    for observed_raw, observed_inspection in observations:
        with monkeypatch.context() as patch:
            patch.setattr(ReceiptStore, "ready_raw_binding", lambda *a, **k: (observed_raw, observed_inspection))
            with pytest.raises(CreatorError):
                observation.verify_current(case.control, payload, authority["activation_id"])

    attacks = []
    changed = copy.deepcopy(candidate)
    changed["kind"] = "operation"
    attacks.append(("GET authority cannot create a POST record", copy.deepcopy(payload), changed, False))
    changed = copy.deepcopy(candidate)
    changed["scope_id"] = "a" * 32
    attacks.append(("foreign scope", copy.deepcopy(payload), changed, False))
    changed = copy.deepcopy(candidate)
    changed["operation_id"] = case.unknown["id"]
    attacks.append(("different source operation", copy.deepcopy(payload), changed, False))
    changed = copy.deepcopy(candidate)
    changed["query_index"] = True
    attacks.append(("boolean is not a cumulative query index", copy.deepcopy(payload), changed, False))
    attacks.append(("skip cumulative query index", copy.deepcopy(payload), proposed_query(case, payload, index=2), False))

    changed_payload = copy.deepcopy(payload)
    changed_payload["media"]["grant:" + authority["grant_id"]]["revoked_at"] = utc_now()
    attacks.append(("revoked original grant", changed_payload, copy.deepcopy(candidate), False))
    changed_payload = copy.deepcopy(payload)
    changed_payload["media"]["operation:" + case.unknown["id"]]["capture_started_at"] = utc_now()
    attacks.append(("retained unknown was edited", changed_payload, copy.deepcopy(candidate), False))
    changed_payload = copy.deepcopy(payload)
    changed_source = changed_payload["media"]["operation:" + source_id]
    raw = changed_source["raw"]
    body = json.loads(base64.b64decode(raw["body_base64"]))
    body["data"]["id"] = "resealed-foreign-accepted-task"
    raw["body_base64"] = base64.b64encode(json.dumps(body).encode()).decode()
    changed_source["receipt"] = decode_submit(FrozenImageRequest.from_frozen(changed_source["descriptor"]), raw)
    assert changed_source["receipt"]["classification"] == "accepted"
    attacks.append(("self-consistent replacement receipt is not original raw", changed_payload, copy.deepcopy(candidate), False))

    # The exact self may be excluded only after it is durably present, with
    # full record equality. An arbitrary ignore-ID must not bypass a query.
    attacks.append(("missing persisted self", copy.deepcopy(payload), copy.deepcopy(candidate), True))
    persisted_payload = copy.deepcopy(payload)
    persisted_payload["media"]["query:" + candidate["id"]] = copy.deepcopy(candidate)
    assert observation.candidate_gate(persisted_payload, candidate, exclude_candidate=True) == authority
    changed = copy.deepcopy(candidate)
    changed["status"] = "submitting"
    changed["capture_started_at"] = utc_now()
    attacks.append(("exclude a different candidate value", persisted_payload, changed, True))

    for kind in ("operation", "query"):
        old_nonce = records(payload, kind)[0]["descriptor"]["permit"]["nonce"]
        attacks.append(("reuse a historical " + kind + " nonce", copy.deepcopy(payload), changed_nonce(candidate, old_nonce), False))

    asset = next(r for r in payload["asset_design"].values() if r["kind"] == "operation")
    for collision in (case.unknown["id"], source_id):
        changed_payload = copy.deepcopy(payload)
        item = copy.deepcopy(asset)
        item.update(id=collision, status="unknown", fee_id=None)
        changed_payload["asset_design"]["operation:" + collision] = item
        attacks.append(("same ID in asset namespace stays blocked", changed_payload, copy.deepcopy(candidate), False))
    changed_payload = copy.deepcopy(payload)
    text = next(r for r in changed_payload["operations"].values() if r["account_id"] == account)
    text["status"] = "needs_reconciliation"
    attacks.append(("unsettled text role", changed_payload, copy.deepcopy(candidate), False))
    changed_payload = copy.deepcopy(payload)
    text = next(r for r in changed_payload["operations"].values() if r["account_id"] == account)
    text["empirical"]["fee_status"] = "overrun"
    attacks.append(("account risk overrun", changed_payload, copy.deepcopy(candidate), False))

    for status in ("unknown", "accepted"):
        changed_payload = copy.deepcopy(payload)
        foreign_scope = copy.deepcopy(scope)
        foreign_scope.update(id="b" * 32, scope_id="b" * 32)
        changed_payload["media"]["scope:" + foreign_scope["id"]] = foreign_scope
        item = copy.deepcopy(payload["media"]["operation:" + source_id])
        item.update(id="c" * 32, scope_id=foreign_scope["id"], status=status, fee_id=None, delivery_id=None)
        changed_payload["media"]["operation:" + item["id"]] = item
        attacks.append(("unrelated media unknown or unpaid operation", changed_payload, copy.deepcopy(candidate), False))
    for status in ("reserved", "submitting", "unknown"):
        changed_payload = copy.deepcopy(payload)
        query = copy.deepcopy(records(payload, "query")[0])
        query.update(id="d" * 32, status=status)
        changed_payload["media"]["query:" + query["id"]] = query
        attacks.append(("unresolved account query", changed_payload, copy.deepcopy(candidate), False))

    # Exhaust the original ceiling using distinct, fully built historical GET
    # descriptors. A new observation lease is not a new counter namespace.
    exhausted = copy.deepcopy(payload)
    limit = authority["effective_policy"]["max_queries"]
    for index in range(1, limit + 1):
        query = proposed_query(case, payload, index=index)
        descriptor = FrozenImageRequest.from_frozen(query["descriptor"])
        raw = query_raw(descriptor)
        query.update(status="completed", capture_started_at=query["created_at"], raw=raw, receipt=decode_query(descriptor, raw))
        exhausted["media"]["query:" + query["id"]] = query
    attacks.append(("historical query ceiling retained", exhausted, proposed_query(case, payload, index=limit), False))

    for label, altered_payload, altered_candidate, exclude in attacks:
        try:
            observation.candidate_gate(altered_payload, altered_candidate, exclude_candidate=exclude)
        except CreatorError:
            pass
        else:
            pytest.fail(label)
    assert store.path("state.json").read_bytes() == native_before
    assert case.server_parent_path.read_bytes() == parent_before
    assert scope_envelope(store.read_only(), account) == envelope_before and len(posts(case)) == captured_before


def test_revocation_during_key_boundary_prevents_collector_without_reusing_prekey_authority(asset_control_case, monkeypatch):
    case = prepared_observation_case(asset_control_case)
    store = case.env.runtime.store
    authority = case.observation_authority
    source_id = authority["source_operation_id"]
    control = ImageObservationControl(case.control, authority["activation_id"])
    parent_before = case.server_parent_path.read_bytes()
    payload_before = store.read_only()
    source_before = copy.deepcopy(payload_before["media"]["operation:" + source_id])
    captured_before = len(posts(case))
    stages, checks = [], []
    old_stage = image_operator.capture_stage
    old_verified = case.control.__class__._verified

    def checked(self, *args, **kwargs):
        checks.append((stages[-1] if stages else None, kwargs.get("for_dispatch")))
        return old_verified(self, *args, **kwargs)

    def stage(name):
        old_stage(name)
        stages.append(name)
        if name == "credential_check":
            with store.transaction() as current:
                current["media"]["grant:" + authority["grant_id"]]["revoked_at"] = utc_now()

    def no_real_key():
        pytest.fail("Synthetic observation requested a real credential")

    with image_operator.build_image_session(control, receipt_capture=case.settings, credential_loader=no_real_key) as session:
        monkeypatch.setattr(case.control.__class__, "_verified", checked)
        monkeypatch.setattr(image_operator, "capture_stage", stage)
        result = session.media.query(case.task["id"], source_id, key="audit-revoke-after-key")["query"]
        assert result["status"] == "unknown" and result["raw"] is None
        assert result["capture_started_at"] is not None
        assert stages.index("credential_check") < stages.index("control_after_key")
        assert any(stage_name == "control_after_key" for stage_name, _ in checks)
        assert all(for_dispatch is False for _, for_dispatch in checks)
        assert not session.media.capture.receipts.operation(result["id"]).root.exists()
        for method, args in ((session.media.quote, (case.task["id"],)),
                (session.media.grant, (case.task["id"], case.grant["id"])),
                (session.media.reserve_submit, (case.task["id"], case.grant["id"], case.unknown["shot_id"])),
                (session.media.submit, (case.task["id"], source_id)),
                (session.media.revoke, (case.task["id"], case.grant["id"]))):
            with pytest.raises(CreatorError) as error:
                method(*args)
            assert error.value.code == "IMAGE_OBSERVATION_GET_ONLY"
    after = store.read_only()
    assert after["media"]["operation:" + source_id] == source_before
    assert after["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert len(records(after, "operation")) == len(records(payload_before, "operation"))
    assert case.server_parent_path.read_bytes() == parent_before and len(posts(case)) == captured_before
