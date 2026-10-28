"""Versioned one-POST diagnostic in the original account; no implicit approval.

Historical inputs are exact references, not retry permissions. All production
I/O is explicit; quote/status/replay never load credentials or contact a provider.
"""

import base64
import copy
import hashlib
import json
import re
from datetime import timedelta

from core.creator.store import CreatorError, digest, utc_now
from core.execution.control import _json, _read
from core.execution.image_code_continuation import (
    _checkpoint,
    _collect_code_manifest,
    _lock,
    _state,
)
from core.execution.image_comparison_schema import (
    ACTIVATION,
    ARTIFACT,
    FEE,
    FIELDS,
    INTENT,
    KINDS,
    OPERATION,
    QUERY,
    QUOTE,
    record_fields,
)
from core.execution.image_control import LoadedImageControl
from core.execution.image_policy import FrozenImageRequest, ImageToolPolicy, timestamp
from core.execution.image_receipts import decode_submit
from core.execution.media_ledger import account_halted, records
from core.execution.policy import money, now, units
from core.execution.receipts import MAX_FILE, ReceiptStore, raw_body

PROPOSAL_FIELDS = {
    "schema_version",
    "kind",
    "control_hash",
    "scope_hash",
    "grant_id",
    "grant_hash",
    "account_id",
    "base_activation_id",
    "baseline_hash",
    "checkpoint",
    "code_manifest_hash",
    "funding_hash",
    "parent_before_base64",
    "parent_before_sha256",
    "parent_allocation",
    "retained_unknowns",
    "case",
    "max_posts",
    "budget",
    "effective_policy",
    "response_policy",
    "new_unknown_stops_batch",
    "formal_delivery_authorized",
    "automatic_followup_calls",
    "dispatch_available",
    "proposal_hash",
}
PURPOSE = "bounded_http403_diagnostic_only"
ERROR = "IMAGE_COMPARISON_UNAVAILABLE"


def require(value, code=ERROR):
    if not value:
        raise CreatorError(
            code, "Exact bounded comparison evidence or authority is unavailable", 409
        )


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def sealed(value, key):
    return {**value, key: digest(value)}


def original_view(payload):
    return {
        **payload,
        "media": {k: v for k, v in payload["media"].items() if v["kind"] not in KINDS},
    }


def _inventory(root):
    if not root.exists():
        return []
    require(root.is_dir() and not root.is_symlink() and root.resolve() == root)
    entries = sorted(root.iterdir(), key=lambda p: p.name)
    require(len(entries) <= 32)
    result = []
    for path in entries:
        require(
            re.fullmatch(
                r"(?:claim|worker|started|observer-closed|error)\.json|\.writer\.lock|[a-f0-9]{64}\.(?:record|commit|ready)\.json",
                path.name,
            )
        )
        data = _read(path, maximum=MAX_FILE)
        result.append({"name": path.name, "size": len(data), "sha256": sha(data)})
    return result


def collector(control, operation):
    """Freeze complete raw-present evidence; preserve old raw-absent rules."""
    from core.execution.image_retained_unknown import _collector

    if operation["raw"] is None:
        return {"mode": "raw_absent", "evidence": _collector(control, operation)}
    request = FrozenImageRequest.from_frozen(operation["descriptor"])
    receipts = ReceiptStore(control.roots["execution"] / "media-receipts")
    receipts.check_private(
        (
            control.backend_root / "code",
            control.backend_root.parent / "frontend/public",
            *control.static_roots,
        )
    )
    root = receipts.operation(operation["id"]).root
    before = _inventory(root)
    raw, inspection = receipts.ready_raw_binding(operation["id"], request.binding())
    require(
        inspection["status"] == "candidate_found"
        and inspection["candidate_count"] == 1
        and inspection["binding_verified"] is True
        and inspection["notification_present"] is True
    )
    require(digest(raw) == digest(operation["raw"]) and _inventory(root) == before)
    return {"mode": "raw_present", "evidence": {"files": before, "inspection": inspection}}


def _unknown(operation, proof):
    from core.execution.image_retained_unknown import _checked_collector
    from core.execution.image_retained_unknown import _unknown as old_unknown

    require(
        operation["status"] == "unknown"
        and operation["capture_started_at"] is not None
        and operation["fee_id"] is None
        and operation["delivery_id"] is None
    )
    require(type(proof) is dict and set(proof) == {"mode", "evidence"})
    if operation["raw"] is None:
        require(proof["mode"] == "raw_absent")
        _checked_collector(proof["evidence"], old_unknown(operation, proof["evidence"]))
    else:
        require(
            proof["mode"] == "raw_present" and set(proof["evidence"]) == {"files", "inspection"}
        )
        request = FrozenImageRequest.from_frozen(operation["descriptor"])
        receipt = decode_submit(request, operation["raw"])
        require(
            receipt["classification"] == "unconfirmed"
            and receipt["accepted_task_id"] is None
            and digest(receipt) == digest(operation["receipt"])
        )
        inspection = proof["evidence"]["inspection"]
        require(
            inspection["operation_id"] == operation["id"]
            and inspection["status"] == "candidate_found"
            and type(inspection["candidate_count"]) is int
            and inspection["candidate_count"] == 1
            and inspection["binding_verified"] is True
            and inspection["notification_present"] is True
            and inspection["body_sha256"] == sha(raw_body(operation["raw"]))
        )
        files = proof["evidence"]["files"]
        require(type(files) is list and 3 <= len(files) <= 32)
        for item in files:
            require(
                type(item) is dict
                and set(item) == {"name", "size", "sha256"}
                and type(item["size"]) is int
                and 0 <= item["size"] <= MAX_FILE
                and type(item["sha256"]) is str
                and re.fullmatch("[a-f0-9]{64}", item["sha256"])
            )
        names = [item["name"] for item in files]
        require(
            names == sorted(set(names))
            and sum(n.endswith(".record.json") for n in names) == 1
            and sum(n.endswith(".commit.json") for n in names) == 1
            and sum(n.endswith(".ready.json") for n in names) == 1
        )
    return {
        "operation_id": operation["id"],
        "operation_hash": digest(operation),
        "raw_hash": None if operation["raw"] is None else digest(operation["raw"]),
        "receipt_hash": None if operation["receipt"] is None else digest(operation["receipt"]),
        "retained_risk_cny": operation["descriptor"]["permit"]["risk_cny"],
        "risk_released_cny": "0.000000",
        "consumed_call_slots": 1,
        "collector": proof,
    }


def _allocation(scope, basis):
    request = {
        "kind": "image_bounded_comparison_scope_v1",
        "purpose": PURPOSE,
        "max_new_calls": 1,
        "scope_id": scope["id"],
        "account_id": scope["account_id"],
        "basis_hash": digest(basis),
    }
    key = "image-comparison-" + digest(request)[:32]
    return {
        "operation_key": key,
        "request": request,
        "request_sha256": digest(request),
        "upper_fen": 100,
        "quote_ref": "image-comparison-v1:" + digest(basis),
        "status": "reserved",
        "provider_task_id": None,
        "actual_charge_fen": None,
    }


def _proposal(
    payload,
    scope,
    grant,
    authority,
    funding_hash,
    parent_raw,
    proofs,
    code_hash,
    checkpoint,
    valid_until,
    response_policy,
):
    from core.execution.image_continuation_parent import _parent
    from core.execution.scope_budget import scope_envelope

    own = sorted(
        (r for r in records(payload, "operation") if r["scope_id"] == scope["id"]),
        key=lambda r: r["submission_index"],
    )
    require(
        authority["schema_version"] == 4
        and len(own) == authority["max_cumulative_submissions"] == 5
        and [r["submission_index"] for r in own] == [1, 2, 3, 4, 5]
    )
    require(grant["revoked_at"] is None and not account_halted(payload, scope["account_id"]))
    unknowns = [_unknown(r, proofs[r["id"]]) for r in own if r["status"] == "unknown"]
    require(len(unknowns) == 3 and len(proofs) == 3)
    failed = own[-1]
    require(
        failed["status"] == "unknown"
        and failed["raw"] is not None
        and failed["raw"]["http_status"] == 403
    )
    delivered = [
        r
        for r in own
        if r["status"] == "accepted" and r["fee_id"] is not None and r["delivery_id"] is not None
    ]
    require(len(delivered) == 2 and len(delivered) + len(unknowns) == len(own))
    source = delivered[-1]
    request = FrozenImageRequest.from_frozen(source["descriptor"])
    failed_request = FrozenImageRequest.from_frozen(failed["descriptor"])
    body, failure = _json(request.wire.encode()), _json(failed_request.wire.encode())
    require(
        request.method == failed_request.method == "POST"
        and request.endpoint == failed_request.endpoint
        and all(
            digest(body[k]) == digest(failure[k])
            for k in ("model", "size", "quality", "resolution")
        )
    )
    opening = scope_envelope(original_view(payload), scope["account_id"])
    parent, _, _ = _parent(parent_raw)
    risk = sum(r["upper_fen"] for r in parent["operations"].values()) * 10000
    from core.execution.control import _call_weight

    calls = sum(_call_weight(r) for r in parent["operations"].values())
    require(
        risk == opening["risk_units"]
        and calls == opening["max_calls"]
        and risk + 1000000 <= 100000000
    )
    require(
        parent["authorization_hash"] == scope["anchor"]["parent_authorization_hash"]
        and scope["policy"]["per_submission_risk_cny"] == "1.000000"
    )
    effective = {k: v for k, v in scope["policy"].items() if k != "policy_hash"}
    effective["valid_until"] = valid_until
    effective = ImageToolPolicy.model_validate(effective).frozen()
    basis = {
        "control_hash": authority["control_hash"],
        "scope_hash": digest(scope),
        "grant_id": grant["id"],
        "grant_hash": digest(grant),
        "account_id": scope["account_id"],
        "base_activation_id": authority["activation_id"],
        "baseline_hash": digest(original_view(payload)),
        "funding_hash": funding_hash,
        "case": {
            "purpose": PURPOSE,
            "source_operation_id": source["id"],
            "source_operation_hash": digest(source),
            "source_descriptor_hash": request.descriptor_hash,
            "wire_sha256": sha(request.wire.encode()),
            "reference_hash": digest(request.frozen()["references"]),
            "failed_operation_hash": digest(failed),
            "planned_submission_index": 6,
            "new_operation_id": None,
            "new_nonce": None,
            "variable": "historically_successful_input_at_current_time",
            "causal_attribution": "undetermined",
        },
        "retained_unknowns": unknowns,
    }
    proposal = {
        "schema_version": 1,
        "kind": "image_bounded_comparison_v1",
        **basis,
        "checkpoint": checkpoint,
        "code_manifest_hash": code_hash,
        "parent_before_base64": base64.b64encode(parent_raw).decode(),
        "parent_before_sha256": sha(parent_raw),
        "parent_allocation": _allocation(scope, basis),
        "max_posts": 1,
        "budget": {
            "original_budget_cny": "100.000000",
            "retained_risk_cny": money(risk),
            "retained_max_calls": calls,
            "proposed_additional_paid_calls": 1,
            "proposed_additional_risk_cny": "1.000000",
            "proposed_retained_risk_cny": money(risk + 1000000),
            "proposed_max_calls": calls + 1,
            "funded_new_posts": 0,
            "risk_released_cny": "0.000000",
            "final_bill": False,
        },
        "effective_policy": effective,
        "response_policy": response_policy,
        "new_unknown_stops_batch": True,
        "formal_delivery_authorized": False,
        "automatic_followup_calls": 0,
        "dispatch_available": False,
    }
    return sealed(proposal, "proposal_hash")


def _record(kind, scope, **values):
    identifier = digest({"kind": kind, "scope_id": scope["id"], **values})[:32]
    return {
        "schema_version": 1,
        "kind": kind,
        "id": identifier,
        "scope_id": scope["id"],
        "task_id": scope["task_id"],
        "created_at": utc_now(),
        **values,
    }


def _put(payload, record):
    key = record["kind"] + ":" + record["id"]
    require(key not in payload["media"])
    payload["media"][key] = record
    return copy.deepcopy(record)


def _base(control, payload):
    from core.execution.image_runtime_continuation import (
        _current_from_verified,
        authority_for,
        latest_activation_id,
    )

    require(type(control) is LoadedImageControl and control.document.mode == "enabled")
    _, _, ready, actual = control._verified(for_dispatch=False)
    require(digest(payload) == digest(actual))
    scopes = control._matching(actual, ready)
    require(len(scopes) == 1)
    scope = scopes[0]
    authority = authority_for(actual, latest_activation_id(actual, scope["id"]))
    scope, grant, _, _, funding = _current_from_verified(
        control, actual, ready, authority["validity_activation_id"]
    )
    return scope, grant, authority, funding


def quote_comparison(control, *, max_posts=1, valid_until=None, response_window_seconds=180):
    from core.execution.image_continuation_parent_io import server_parent_path
    from core.execution.image_response_lifecycle import make_response_policy
    from core.execution.store import ExecutionStore

    response_policy = make_response_policy(response_window_seconds)
    require(type(max_posts) is int and max_posts == 1)
    require(type(control) is LoadedImageControl)
    store = ExecutionStore(control.roots["execution"])
    with _lock(store, control.document.admission.task_id), store.transaction() as payload:
        # Before funding, verify the original D37 artifact and exact parent facts.
        own_quotes = [
            q for q in records(payload, QUOTE) if q["task_id"] == control.document.admission.task_id
        ]
        if own_quotes:
            q = own_quotes[-1]
            verify_current(control, q["id"], quote_only=True, allow_unfunded=True)
            require(
                valid_until is None
                or valid_until == q["proposal"]["effective_policy"]["valid_until"]
            )
            require(digest(q["proposal"]["response_policy"]) == digest(response_policy))
            return {"quote": copy.deepcopy(q), "replayed": True}
        scope, grant, authority, funding = _base(control, payload)
        until = (now() + timedelta(hours=4)).isoformat() if valid_until is None else valid_until
        require(now() < timestamp(until) <= now() + timedelta(hours=24))
        parent = _read(server_parent_path(control), maximum=8 * 1048576)
        proofs = {
            r["id"]: collector(control, r)
            for r in records(payload, "operation")
            if r["scope_id"] == scope["id"] and r["status"] == "unknown"
        }
        proposal = _proposal(
            payload,
            scope,
            grant,
            authority,
            funding["funding_hash"],
            parent,
            proofs,
            digest(_collect_code_manifest(control)),
            _checkpoint(_state(store), payload),
            until,
            response_policy,
        )
        record = _record(QUOTE, scope, proposal=proposal)
        _put(payload, record)
        return {"quote": record, "replayed": False}


def quote_for(payload, quote_id):
    return payload["media"][QUOTE + ":" + quote_id]


def _expected_parent(proposal):
    from core.execution.image_continuation_parent import _parent

    before = base64.b64decode(proposal["parent_before_base64"], validate=True)
    require(sha(before) == proposal["parent_before_sha256"])
    parent, _, _ = _parent(before)
    key = proposal["parent_allocation"]["operation_key"]
    require(key not in parent["operations"])
    parent["operations"][key] = copy.deepcopy(proposal["parent_allocation"])
    parent["reserved_upper_fen"] = sum(r["upper_fen"] for r in parent["operations"].values())
    raw = json.dumps(
        parent, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    _parent(raw)
    return raw


def verify_current(control, quote_id, *, quote_only=False, allow_unfunded=False):
    """Fresh source/code/old-record/collector/parent checks, never a permission cache."""
    require(
        type(control) is LoadedImageControl
        and type(quote_only) is bool
        and type(allow_unfunded) is bool
    )
    _, _, ready, payload = control._verified(for_dispatch=False)
    return _verify_from_current(
        control, payload, ready, quote_id, quote_only=quote_only, allow_unfunded=allow_unfunded
    )


def _verify_from_current(
    control, payload, ready, quote_id, *, quote_only=False, allow_unfunded=False
):
    from core.execution.image_continuation_parent_io import server_parent_path

    q = quote_for(payload, quote_id)
    p = q["proposal"]
    scope = payload["media"]["scope:" + q["scope_id"]]
    grant = payload["media"]["grant:" + p["grant_id"]]
    require(
        control._matching(payload, ready) == [scope]
        and grant["revoked_at"] is None
        and control.control_hash == p["control_hash"]
        and digest(original_view(payload)) == p["baseline_hash"]
        and digest(_collect_code_manifest(control)) == p["code_manifest_hash"]
        and now() < timestamp(p["effective_policy"]["valid_until"])
    )
    for item in p["retained_unknowns"]:
        op = payload["media"]["operation:" + item["operation_id"]]
        require(
            digest(op) == item["operation_hash"]
            and digest(collector(control, op)) == digest(item["collector"])
        )
    current = _read(server_parent_path(control), maximum=8 * 1048576)
    activations = [a for a in records(payload, ACTIVATION) if a["quote_id"] == q["id"]]
    if activations:
        require(
            len(activations) == 1
            and current == _expected_parent(p)
            and sha(current) == activations[0]["parent_after_sha256"]
        )
    else:
        require(quote_only or allow_unfunded)
        expected = base64.b64decode(p["parent_before_base64"], validate=True)
        # Exact same-allocation interrupted parent writes can only be resumed
        # after a real durable local intent; never treated as a free new grant.
        intents = [i for i in records(payload, INTENT) if i["quote_id"] == q["id"]]
        require(
            current == expected
            or (allow_unfunded and len(intents) == 1 and current == _expected_parent(p))
        )
    return payload, q


def prepare_comparison(control, quote_id, *, proposal_hash, consent):
    from core.execution.store import ExecutionStore

    require(consent is True)
    store = ExecutionStore(control.roots["execution"])
    with _lock(store, control.document.admission.task_id), store.transaction() as payload:
        actual, q = verify_current(control, quote_id, quote_only=True, allow_unfunded=True)
        require(
            digest(actual) == digest(payload) and q["proposal"]["proposal_hash"] == proposal_hash
        )
        existing = [
            r
            for r in records(payload, INTENT)
            if payload["media"]["scope:" + r["scope_id"]]["account_id"]
            == q["proposal"]["account_id"]
        ]
        if existing:
            require(len(existing) == 1 and existing[0]["quote_id"] == quote_id)
            return {"intent": copy.deepcopy(existing[0]), "replayed": True}
        scope = payload["media"]["scope:" + q["scope_id"]]
        record = _record(
            INTENT,
            scope,
            quote_id=quote_id,
            proposal_hash=proposal_hash,
            consent=True,
            risk_cny="1.000000",
            max_calls=1,
        )
        _put(payload, record)
        return {"intent": record, "replayed": False}


def activate_comparison(control, intent_id, *, proposal_hash, consent):
    from core.execution.image_continuation_parent_io import (
        _parent_lock,
        _replace_parent,
        server_parent_path,
    )
    from core.execution.store import ExecutionStore

    require(consent is True)
    store = ExecutionStore(control.roots["execution"])
    with _lock(store, control.document.admission.task_id):
        payload = store.read_only()
        intent = payload["media"][INTENT + ":" + intent_id]
        actual, q = verify_current(control, intent["quote_id"], allow_unfunded=True)
        require(
            digest(actual) == digest(payload)
            and intent["proposal_hash"] == proposal_hash == q["proposal"]["proposal_hash"]
        )
        existing = [a for a in records(payload, ACTIVATION) if a["intent_id"] == intent_id]
        if existing:
            require(len(existing) == 1)
            return {"activation": copy.deepcopy(existing[0]), "replayed": True}
        path = server_parent_path(control)
        expected = _expected_parent(q["proposal"])
        with _parent_lock(path):
            verify_current(control, q["id"], allow_unfunded=True)
            current = _read(path, maximum=8 * 1048576)
            if current != expected:
                require(sha(current) == q["proposal"]["parent_before_sha256"])
                _replace_parent(path, expected)
            require(_read(path, maximum=8 * 1048576) == expected)
        with store.transaction() as current:
            require(digest(current) == digest(payload))
            scope = current["media"]["scope:" + q["scope_id"]]
            record = _record(
                ACTIVATION,
                scope,
                quote_id=q["id"],
                intent_id=intent_id,
                proposal_hash=proposal_hash,
                consent=True,
                allocation_hash=digest(q["proposal"]["parent_allocation"]),
                parent_after_sha256=sha(expected),
            )
            _put(current, record)
            return {"activation": record, "replayed": False}


def authority_for(payload, activation_id):
    a = payload["media"][ACTIVATION + ":" + activation_id]
    q = quote_for(payload, a["quote_id"])
    p = q["proposal"]
    intent = payload["media"][INTENT + ":" + a["intent_id"]]
    value = {
        "schema_version": 1,
        "kind": "image_comparison_authority_v1",
        "activation_id": a["id"],
        "quote_id": q["id"],
        "intent_id": intent["id"],
        "intent_hash": digest(intent),
        "scope_id": q["scope_id"],
        "task_id": q["task_id"],
        "account_id": p["account_id"],
        "grant_id": p["grant_id"],
        "proposal_hash": p["proposal_hash"],
        "allocation_hash": a["allocation_hash"],
        "parent_after_sha256": a["parent_after_sha256"],
        "code_manifest_hash": p["code_manifest_hash"],
        "purpose": PURPOSE,
        "policy": p["effective_policy"],
        "response_policy": p["response_policy"],
        "source_descriptor_hash": p["case"]["source_descriptor_hash"],
        "wire_sha256": p["case"]["wire_sha256"],
        "submission_index": p["case"]["planned_submission_index"],
        "max_posts": 1,
    }
    return sealed(value, "authority_hash")


def retained_extensions(payload, account_id):
    result = []
    for i in records(payload, INTENT):
        q = quote_for(payload, i["quote_id"])
        p = q["proposal"]
        if p["account_id"] == account_id:
            result.append(
                {
                    "intent": i,
                    "allocation_hash": digest(p["parent_allocation"]),
                    "risk_units": 1000000,
                    "max_calls": 1,
                    "account_limit_units": units(p["budget"]["proposed_retained_risk_cny"]),
                    "account_max_calls": p["budget"]["proposed_max_calls"],
                }
            )
    return result


def candidate_gate(payload, activation_id, *, exclude_id=None):
    """Keep old unknowns in storage; exclude only their exact frozen facts here."""
    from core.execution.media_ledger import (
        has_pending_fees,
        has_pending_unknown,
        unresolved_text_claims,
    )

    a = payload["media"][ACTIVATION + ":" + activation_id]
    q = quote_for(payload, a["quote_id"])
    p = q["proposal"]
    scope = payload["media"]["scope:" + q["scope_id"]]
    require(
        digest(original_view(payload)) == p["baseline_hash"]
        and not account_halted(payload, scope["account_id"])
        and not unresolved_text_claims(payload)
    )
    excluded = {"operation:" + x["operation_id"] for x in p["retained_unknowns"]}
    for x in p["retained_unknowns"]:
        require(digest(payload["media"]["operation:" + x["operation_id"]]) == x["operation_hash"])
    if exclude_id is not None:
        excluded.add(OPERATION + ":" + exclude_id)
        excluded.add(QUERY + ":" + exclude_id)
    view = {**payload, "media": {k: v for k, v in payload["media"].items() if k not in excluded}}
    require(
        not has_pending_unknown(view, p["account_id"])
        and not has_pending_fees(view, p["account_id"])
        and all(
            x["status"] == "settled"
            for x in payload["operations"].values()
            if x["account_id"] == p["account_id"]
        )
    )
    require(
        not any(
            x["status"] in {"reserved", "submitting", "unknown"}
            and x["id"] != exclude_id
            and payload["media"]["scope:" + x["scope_id"]]["account_id"] == p["account_id"]
            for kind in ("query", QUERY)
            for x in records(payload, kind)
        )
    )
    return authority_for(payload, activation_id)


def validate_record(payload, record):
    """Native journal validation, including independent receipt/fee/image replay."""

    kind = record["kind"]
    require(
        kind in KINDS
        and set(record) == record_fields(record)
        and type(record["schema_version"]) is int
        and record["schema_version"] == 1
    )
    scope = payload["media"]["scope:" + record["scope_id"]]
    require(record["task_id"] == scope["task_id"])
    timestamp(record["created_at"])
    if kind == QUOTE:
        p = record["proposal"]
        require(
            type(p) is dict
            and set(p) == PROPOSAL_FIELDS
            and type(p["max_posts"]) is int
            and p["max_posts"] == 1
        )
        require(
            p["proposal_hash"] == digest({k: v for k, v in p.items() if k != "proposal_hash"})
            and p["new_unknown_stops_batch"] is True
            and p["formal_delivery_authorized"] is False
            and type(p["automatic_followup_calls"]) is int
            and p["automatic_followup_calls"] == 0
            and p["dispatch_available"] is False
        )
        require(
            timestamp(record["created_at"])
            < timestamp(p["effective_policy"]["valid_until"])
            <= timestamp(record["created_at"]) + timedelta(hours=24)
        )
    elif kind == INTENT:
        q = quote_for(payload, record["quote_id"])
        require(
            record["proposal_hash"] == q["proposal"]["proposal_hash"]
            and record["consent"] is True
            and record["risk_cny"] == "1.000000"
            and type(record["max_calls"]) is int
            and record["max_calls"] == 1
            and q["scope_id"] == scope["id"]
        )
        require(
            sum(
                payload["media"]["scope:" + x["scope_id"]]["account_id"] == scope["account_id"]
                for x in records(payload, INTENT)
            )
            == 1
        )
        require(
            timestamp(q["created_at"])
            <= timestamp(record["created_at"])
            < timestamp(q["proposal"]["effective_policy"]["valid_until"])
        )
    elif kind == ACTIVATION:
        q = quote_for(payload, record["quote_id"])
        i = payload["media"][INTENT + ":" + record["intent_id"]]
        require(
            record["consent"] is True
            and i["quote_id"] == q["id"]
            and i["scope_id"] == scope["id"]
            and record["proposal_hash"] == i["proposal_hash"] == q["proposal"]["proposal_hash"]
            and record["allocation_hash"] == digest(q["proposal"]["parent_allocation"])
            and record["parent_after_sha256"] == sha(_expected_parent(q["proposal"]))
        )
        require(
            sum(
                payload["media"]["scope:" + x["scope_id"]]["account_id"] == scope["account_id"]
                for x in records(payload, ACTIVATION)
            )
            == 1
        )
        require(
            timestamp(i["created_at"])
            <= timestamp(record["created_at"])
            < timestamp(q["proposal"]["effective_policy"]["valid_until"])
        )
    elif kind in {OPERATION, QUERY}:
        from core.execution.image_comparison_policy import KIND
        from core.execution.image_receipts import decode_query

        request = FrozenImageRequest.from_frozen(record["descriptor"])
        data = request.frozen()
        permit = data["permit"]
        authority = authority_for(payload, record["activation_id"])
        require(
            data["kind"] == KIND
            and digest(data["comparison_authority"]) == digest(authority)
            and permit["operation_id"] == record["id"]
            and permit["task_id"] == record["task_id"]
            and authority["scope_id"] == scope["id"]
            and record["status"]
            in {
                "reserved",
                "submitting",
                "unknown",
                "accepted",
                "rejected",
                "pending",
                "completed",
                "failed",
            }
        )
        require(
            type(record["idempotency_key"]) is str
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", record["idempotency_key"])
        )
        require(
            timestamp(permit["issued_at"])
            <= timestamp(record["created_at"])
            <= timestamp(permit["deadline_at"])
        )
        require(
            timestamp(payload["media"][ACTIVATION + ":" + record["activation_id"]]["created_at"])
            <= timestamp(permit["issued_at"])
        )
        if record["capture_started_at"] is None:
            require(record["status"] == "reserved" and record["raw"] is None)
        else:
            require(
                timestamp(record["created_at"])
                <= timestamp(record["capture_started_at"])
                <= timestamp(permit["deadline_at"])
            )
        if record["raw"] is not None:
            receipt = (decode_submit if kind == OPERATION else decode_query)(request, record["raw"])
            require(
                digest(receipt) == digest(record["receipt"])
                and record["status"]
                == (
                    "unknown"
                    if receipt["classification"] == "unconfirmed"
                    else receipt["classification"]
                )
            )
        else:
            require(
                record["receipt"] is None
                and record["status"] in {"reserved", "submitting", "unknown"}
            )
        if kind == OPERATION:
            require(
                request.method == "POST"
                and record["purpose"] == PURPOSE
                and type(record["submission_index"]) is int
                and record["submission_index"] == 6
            )
            require(
                sum(
                    payload["media"]["scope:" + x["scope_id"]]["account_id"] == scope["account_id"]
                    for x in records(payload, OPERATION)
                )
                <= 1
            )
            for field, target_kind in (("fee_id", FEE), ("artifact_id", ARTIFACT)):
                if record[field] is not None:
                    require(
                        payload["media"][target_kind + ":" + record[field]]["operation_id"]
                        == record["id"]
                    )
        else:
            op = payload["media"][OPERATION + ":" + record["operation_id"]]
            require(
                request.method == "GET"
                and op["activation_id"] == record["activation_id"]
                and type(record["query_index"]) is int
                and record["query_index"] == data["query_index"]
                and digest(data["parent_submit"]) == digest(op["descriptor"])
                and digest(data["submit_raw"]) == digest(op["raw"])
            )
    elif kind == FEE:
        from core.execution.image_fees import candidate_for

        op = payload["media"][OPERATION + ":" + record["operation_id"]]
        require(
            op["status"] == "accepted"
            and op["scope_id"] == scope["id"]
            and op["fee_id"] == record["id"]
        )
        if record["candidate"]["kind"] == "image_task_fee_candidate_v1":
            from core.execution.task_image_fees import candidate_for_task

            candidate = candidate_for_task(
                FrozenImageRequest.from_frozen(op["descriptor"]),
                op["raw"],
                base64.b64decode(record["export_base64"], validate=True),
                base64.b64decode(record["account_export_base64"], validate=True),
                scope["fee_source"],
                record["task_fee_source"],
                observed_at=record["candidate"]["observed_at"],
            )
        else:
            candidate = candidate_for(
                FrozenImageRequest.from_frozen(op["descriptor"]),
                op["raw"],
                base64.b64decode(record["export_base64"], validate=True),
                scope["fee_source"],
                observed_at=record["candidate"]["observed_at"],
            )
        require(
            digest(candidate) == digest(record["candidate"])
            and record["id"] == digest(candidate["platform_log_id"])[:32]
        )
    else:
        from core.execution.image_receipts import verify_delivery

        op = payload["media"][OPERATION + ":" + record["operation_id"]]
        query = payload["media"][QUERY + ":" + record["query_id"]]
        image = base64.b64decode(record["bytes_base64"], validate=True)
        require(
            len(image) <= 2 * 1048576
            and record["purpose"] == PURPOSE
            and op["artifact_id"] == record["id"]
            and op["fee_id"] is not None
            and query["operation_id"] == op["id"]
            and query["status"] == "completed"
            and op["scope_id"] == scope["id"]
        )
        require(
            digest(
                verify_delivery(
                    FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"], image
                )
            )
            == digest(record["artifact"])
        )
    if kind in {QUOTE, INTENT, ACTIVATION}:
        values = {k: record[k] for k in FIELDS[kind]}
        require(record["id"] == digest({"kind": kind, "scope_id": scope["id"], **values})[:32])


def _validate_quote_birth(payload, record):
    from core.execution.image_continuation_funding import frozen_funding
    from core.execution.image_runtime_continuation import authority_for as old_authority

    p = record["proposal"]
    scope = payload["media"]["scope:" + record["scope_id"]]
    authority = old_authority(payload, p["base_activation_id"])
    grant = payload["media"]["grant:" + p["grant_id"]]
    funding = frozen_funding(payload, authority["validity_activation_id"])
    require(
        type(p["checkpoint"]) is dict
        and set(p["checkpoint"]) == {"head_revision", "head_hash", "payload_hash"}
    )
    require(
        timestamp(record["created_at"])
        < timestamp(p["effective_policy"]["valid_until"])
        <= timestamp(record["created_at"]) + timedelta(hours=24)
    )
    from core.execution.image_response_lifecycle import validate_response_policy

    validate_response_policy(p["response_policy"])
    expected = _proposal(
        original_view(payload),
        scope,
        grant,
        authority,
        funding["funding_hash"],
        base64.b64decode(p["parent_before_base64"], validate=True),
        {x["operation_id"]: x["collector"] for x in p["retained_unknowns"]},
        p["code_manifest_hash"],
        p["checkpoint"],
        p["effective_policy"]["valid_until"],
        p["response_policy"],
    )
    require(digest(expected) == digest(p))


def validate_transition(payload, previous=None):
    if previous is None:
        return
    old = previous.get("media", {})
    new = [r for k, r in payload["media"].items() if k not in old and r["kind"] in KINDS]
    for r in new:
        kind = r["kind"]
        if kind in {QUOTE, INTENT, ACTIVATION}:
            key = kind + ":" + r["id"]
            require(
                len(new) == 1
                and digest(
                    {**payload, "media": {k: v for k, v in payload["media"].items() if k != key}}
                )
                == digest(previous)
            )
            if kind == QUOTE:
                _validate_quote_birth(previous, r)
                require(r["proposal"]["checkpoint"]["payload_hash"] == digest(previous))
            else:
                require(QUOTE + ":" + r["quote_id"] in old)
                q = quote_for(previous, r["quote_id"])
                require(digest(original_view(previous)) == q["proposal"]["baseline_hash"])
                if kind == ACTIVATION:
                    require(INTENT + ":" + r["intent_id"] in old)
        elif kind in {OPERATION, QUERY}:
            require(
                r["status"] == "reserved"
                and all(r[k] is None for k in ("capture_started_at", "raw", "receipt"))
            )
            require(ACTIVATION + ":" + r["activation_id"] in old)
            candidate_gate(previous, r["activation_id"])
            if kind == QUERY:
                op = old[OPERATION + ":" + r["operation_id"]]
                require(op["status"] == "accepted" and op["fee_id"] is not None)
                indices = [
                    x["query_index"]
                    for x in records(previous, QUERY)
                    if x["operation_id"] == op["id"]
                ]
                require(r["query_index"] == len(indices) + 1)
            nonce = r["descriptor"]["permit"]["nonce"]
            require(
                not any(
                    x["descriptor"]["permit"]["nonce"] == nonce
                    for x in old.values()
                    if x["kind"] in {"operation", "query", OPERATION, QUERY}
                )
            )
            require(
                not any(
                    x["kind"] == kind and x["idempotency_key"] == r["idempotency_key"]
                    for x in old.values()
                )
            )
        elif kind == FEE:
            require(old[OPERATION + ":" + r["operation_id"]]["raw"] is not None)
        elif kind == ARTIFACT:
            require(old[QUERY + ":" + r["query_id"]]["status"] == "completed")
    for key, before in old.items():
        if before["kind"] not in KINDS:
            continue
        if before["kind"] not in {OPERATION, QUERY}:
            require(digest(before) == digest(payload["media"][key]))
            continue
        after = payload["media"][key]
        mutable = {"status", "capture_started_at", "raw", "receipt"} | (
            {"fee_id", "artifact_id"} if before["kind"] == OPERATION else set()
        )
        require(
            digest({k: v for k, v in before.items() if k not in mutable})
            == digest({k: v for k, v in after.items() if k not in mutable})
        )
        for k in mutable - {"status"}:
            require(before[k] is None or digest(before[k]) == digest(after[k]))
        allowed = {
            "reserved": {"reserved", "submitting"},
            "submitting": {
                "submitting",
                "unknown",
                "accepted",
                "rejected",
                "pending",
                "completed",
                "failed",
            },
            "unknown": {"unknown", "accepted", "rejected", "pending", "completed", "failed"},
        }
        require(after["status"] in allowed.get(before["status"], {before["status"]}))


def validate_history(state, payload):
    for frame in state["frames"]:
        for r in frame["delta"].get("media", {}).values():
            if r["kind"] == QUOTE:
                c = r["proposal"]["checkpoint"]
                require(
                    type(c["head_revision"]) is int
                    and c["head_revision"] == frame["revision"] - 1
                    and c["head_hash"] == frame["previous_hash"]
                )


def operator_review(args, backend):
    from core.execution.image_control import read_image_control

    require(bool(args.control) and not args.retain_unknown_risk and not args.disclosure_hash)
    control = read_image_control(args.control, backend)
    control.verify_authority(for_dispatch=False)
    if args.command == "comparison-quote":
        require(not any((args.quote, args.intent, args.proposal_hash, args.consent)))
        result = quote_comparison(
            control,
            max_posts=1 if args.max_comparison_posts is None else args.max_comparison_posts,
            valid_until=args.valid_until,
            response_window_seconds=180
            if args.response_window_seconds is None
            else args.response_window_seconds,
        )
        q, replayed = result["quote"], result["replayed"]
    else:
        require(
            args.max_comparison_posts is None
            and args.valid_until is None
            and args.response_window_seconds is None
        )
        if args.command == "comparison-prepare":
            require(
                bool(args.quote)
                and bool(args.proposal_hash)
                and not args.intent
                and args.consent is True
            )
            result = prepare_comparison(
                control, args.quote, proposal_hash=args.proposal_hash, consent=args.consent
            )
            _, q = verify_current(control, args.quote, quote_only=True, allow_unfunded=True)
            replayed = result["replayed"]
        elif args.command == "comparison-activate":
            require(
                bool(args.intent)
                and bool(args.proposal_hash)
                and not args.quote
                and args.consent is True
            )
            result = activate_comparison(
                control, args.intent, proposal_hash=args.proposal_hash, consent=args.consent
            )
            _, q = verify_current(control, result["activation"]["quote_id"])
            replayed = result["replayed"]
        else:
            require(
                args.command == "comparison-status"
                and bool(args.quote)
                and not any((args.intent, args.proposal_hash, args.consent))
            )
            _, q = verify_current(control, args.quote, quote_only=True, allow_unfunded=True)
            replayed = True
    result = status_summary(control, q["id"])
    # Review/funding commands never execute or imply that a quote auto-starts.
    result.update(
        mode="bounded_comparison_review",
        replayed=replayed,
        paid_calls=0,
        execution_contract_implemented=True,
        budget_proposal=q["proposal"]["budget"],
    )
    return result


def require_dispatch(control, quote_id):
    payload, q = verify_current(control, quote_id, quote_only=True, allow_unfunded=True)
    found = [a for a in records(payload, ACTIVATION) if a["quote_id"] == q["id"]]
    require(len(found) == 1, "IMAGE_COMPARISON_NOT_FUNDED")
    return candidate_gate(payload, found[0]["id"])


def status_summary(control, quote_id):
    """Safe verified scalars; acceptance, billing, diagnosis and production differ."""
    from core.execution.scope_budget import scope_envelope

    _, _, ready, payload = control._verified(for_dispatch=False)
    q = quote_for(payload, quote_id)
    p = q["proposal"]
    activations = [a for a in records(payload, ACTIVATION) if a["quote_id"] == q["id"]]
    intents = [i for i in records(payload, INTENT) if i["quote_id"] == q["id"]]
    own = [op for op in records(payload, OPERATION) if op["scope_id"] == q["scope_id"]]
    queries = [r for r in records(payload, QUERY) if r["scope_id"] == q["scope_id"]]
    scope = payload["media"]["scope:" + q["scope_id"]]
    valid = False
    try:
        _verify_from_current(control, payload, ready, q["id"], quote_only=True, allow_unfunded=True)
        valid = True
    except Exception:
        pass
    op = own[0] if own else None
    fee = payload["media"].get(FEE + ":" + op["fee_id"]) if op and op["fee_id"] else None
    diagnostic = None
    if op is not None:
        receipt = op["receipt"]
        diagnostic = {
            "raw_captured": op["raw"] is not None,
            "http_status": None if receipt is None else receipt["http_status"],
            "receipt_classification": None if receipt is None else receipt["classification"],
            "accepted_task_present": receipt is not None
            and receipt["accepted_task_id"] is not None,
            "retry_authorized": False,
        }
    stage = "not_run" if op is None else op["status"]
    if op and op["status"] == "accepted":
        stage = (
            "diagnostic_artifact_delivered"
            if op["artifact_id"]
            else "accepted_fee_observed"
            if fee
            else "accepted_fee_pending"
        )
    envelope = scope_envelope(payload, p["account_id"])
    dispatch = query_available = False
    if valid and activations:
        try:
            candidate_gate(
                payload,
                activations[0]["id"],
                exclude_id=op["id"] if op and op["status"] == "reserved" else None,
            )
            dispatch = bool(
                op
                and op["status"] == "reserved"
                and now() < timestamp(op["descriptor"]["permit"]["deadline_at"])
            )
            query_available = bool(
                op
                and op["status"] == "accepted"
                and fee
                and not op["artifact_id"]
                and len(queries) < p["effective_policy"]["max_queries"]
            )
        except Exception:
            pass
    return {
        "resource_id": q["id"],
        "proposal_hash": p["proposal_hash"],
        "intent_id": intents[0]["id"] if intents else None,
        "activation_id": activations[0]["id"] if activations else None,
        "code_manifest_hash": p["code_manifest_hash"],
        "valid_until": p["effective_policy"]["valid_until"],
        "formal_delivery_authorized": False,
        "purpose": PURPOSE,
        "current_authority_valid": valid and bool(activations),
        "dispatch_available": dispatch,
        "query_available": query_available,
        "response_policy": p["response_policy"],
        "max_posts": 1,
        "overall_post_upper_bound": 2,
        "b_status": "deferred_no_funding_or_admission",
        "funded_new_posts": 1 if activations else 0,
        "consumed_diagnostic_intents": len(own),
        "remaining_funded_posts": max(0, 1 - len(own)) if activations else 0,
        "original_post_intents": 5,
        "total_native_image_post_intents": 5 + len(own),
        "retained_unknown_count": len(p["retained_unknowns"]),
        "new_unknown_count": sum(r["status"] == "unknown" for r in own),
        "retained_risk_cny": money(envelope["risk_units"]),
        "retained_max_calls": envelope["max_calls"],
        "platform_observed_cny": None if fee is None else fee["candidate"]["platform_fee_cny"],
        "final_bill": False,
        "risk_released": False,
        "stage": stage,
        "response_diagnostic": diagnostic,
        "causal_attribution": "undetermined",
        "production_target_resolved": False,
        "formal_delivered_count": sum(
            r["scope_id"] == scope["id"] for r in records(payload, "delivery")
        ),
        "formal_required_count": len(scope["manifest"]["frames"]),
        "quality_status": "not_reviewed",
        "human_acceptance": "pending",
        "automatic_followup_calls": 0,
    }
