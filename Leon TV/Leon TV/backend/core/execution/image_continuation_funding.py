"""Read already retained continuation funding; never reserve or grant dispatch.

Unlike D37's writer contract, factual funding survives code changes and expiry.
Fresh runtime authority must be obtained separately for every new request.
"""
import copy
import hashlib

from core.creator.store import CreatorError, digest
from core.execution.control import _json, _read
from core.execution.image_control import LoadedImageControl
from core.planner.review import json_copy

KIND = "image_continuation_funding_fact_v1"
FIELDS = {"schema_version", "kind", "control_hash", "scope_id", "intent_id", "intent_hash", "validity_activation_id",
    "validity_activation_hash", "parent_receipt_hash", "authority_hash", "before_evidence_hash", "before_artifact_sha256",
    "after_parent_sha256", "parent_record_count", "retained_risk_cny", "retained_max_calls", "allocations", "funding_hash"}


def unavailable():
    return CreatorError("IMAGE_CONTINUATION_FUNDING_UNAVAILABLE", "Existing image continuation funding is unavailable", 409)


def require(value):
    if not value:
        raise unavailable()


def frozen_funding(payload, activation_id):
    """Pure reconstruction from validated D37 records, not live provenance."""
    try:
        from core.execution import image_validity_continuation as validity

        activation = payload["media"][validity.ACTIVATION + ":" + activation_id]
        quote = payload["media"][validity.QUOTE + ":" + activation["quote_id"]]
        intent = payload["media"][validity.INTENT + ":" + activation["intent_id"]]
        # The store independently validates the D37 quote at its real birth.
        # Reconstructing a funding reference must not recursively rebuild that
        # same full descriptor history for each later media record.
        for item in (intent, activation):
            validity.validate_record(payload, item)
        scope = payload["media"]["scope:" + activation["scope_id"]]
        receipt, anchor = activation["parent_receipt"], scope["anchor"]
        allocations = [
            {"role": "original", "allocation_hash": anchor["parent_allocation_hash"],
             "begin_hash": anchor["parent_allocation_begin_hash"], "risk_cny": anchor["scope_risk_cny"], "max_calls": anchor["scope_max_calls"]},
            {"role": "continuation", "allocation_hash": receipt["allocation_hash"],
             "begin_hash": receipt["begin"]["begin_hash"], "risk_cny": receipt["new_risk_cny"], "max_calls": receipt["new_max_calls"]}]
        value = {"schema_version": 1, "kind": KIND, "control_hash": quote["proposal"]["basis"]["control_hash"],
            "scope_id": scope["id"], "intent_id": intent["id"], "intent_hash": digest(intent),
            "validity_activation_id": activation_id, "validity_activation_hash": digest(activation),
            "parent_receipt_hash": receipt["receipt_hash"], "authority_hash": receipt["authority_hash"],
            "before_evidence_hash": receipt["before_evidence_hash"],
            "before_artifact_sha256": quote["parent_evidence_ref"]["artifact_sha256"],
            "after_parent_sha256": receipt["after_parent_sha256"], "parent_record_count": receipt["old_operation_count"] + 1,
            "retained_risk_cny": receipt["retained_risk_cny"], "retained_max_calls": receipt["max_calls"], "allocations": allocations}
        value["funding_hash"] = digest(value)
        return copy.deepcopy(value)
    except Exception:
        raise unavailable() from None


def read_funding(control, payload, activation_id):
    """Fresh fixed-path artifact, old-plus-one parent and persisted proof check.

    Reads no credential, calls no old writer, and does not rely on D37's expired
    lease or current-code equality. The supplied payload must be the actual head.
    """
    try:
        require(type(control) is LoadedImageControl and control.document.mode == "enabled")
        _, _, _, actual = control._verified(for_dispatch=False)
        require(digest(payload) == digest(actual))
        return _collect_funding(control, actual, activation_id)
    except Exception:
        raise unavailable() from None


def _collect_funding(control, payload, activation_id):
    """Read funding facts after this decision freshly verified the actual head.

    This internal collector carries no independent authority and retains no
    cached proof. Standalone callers must use read_funding instead.
    """
    try:
        from core.execution import image_continuation_parent_io as parent_io
        from core.execution import image_validity_continuation as validity
        from core.execution.image_continuation_parent import MAX_EVIDENCE_BYTES, MAX_PARENT_BYTES

        require(type(control) is LoadedImageControl and control.document.mode == "enabled")
        actual = payload
        fact = frozen_funding(actual, activation_id)
        activation = actual["media"][validity.ACTIVATION + ":" + activation_id]
        quote = actual["media"][validity.QUOTE + ":" + activation["quote_id"]]
        intent = actual["media"][validity.INTENT + ":" + activation["intent_id"]]
        proposal = quote["proposal"]
        require(fact["control_hash"] == control.control_hash)
        require(proposal["basis"]["original_code_manifest_hash"] == digest(control.document.code_manifest))
        artifact_path = parent_io._proof_path(control, proposal)
        artifact_raw = _read(artifact_path, maximum=MAX_EVIDENCE_BYTES)
        artifact = _json(artifact_raw)
        require(type(artifact) is dict and set(artifact) == parent_io.ARTIFACT_FIELDS)
        before = parent_io._before(control, proposal, artifact["evidence"])
        require(digest(artifact) == digest(parent_io._artifact(control, proposal, before)))
        require(hashlib.sha256(artifact_raw).hexdigest() == fact["before_artifact_sha256"])
        require(digest(parent_io._reference(artifact_path, artifact)) == digest(quote["parent_evidence_ref"]))
        saved = _json(_read(parent_io._proof_path(control, proposal, receipt=True), maximum=parent_io.MAX_RECEIPT_BYTES))
        require(digest(saved) == digest(activation["parent_receipt"]))
        actual_raw = _read(parent_io.server_parent_path(control), maximum=MAX_PARENT_BYTES)
        require(parent_io._verify_saved(control, proposal, before, saved, intent, actual_raw) is True)
        original = _json(_read(control.document.authority.parent_ledger_path, maximum=MAX_PARENT_BYTES))
        selected = original["operations"][control.document.authority.allocation_key]
        require(selected["status"] == "reserved" and selected["provider_task_id"] is None
            and selected["actual_charge_fen"] is None and selected["request"].get("scope_status") == "open")
        return json_copy(fact)
    except Exception:
        raise unavailable() from None
