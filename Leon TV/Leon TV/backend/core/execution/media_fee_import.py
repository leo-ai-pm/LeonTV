"""Explicit task-quota imports over the original store, without an executor."""
import base64
import copy

from core.creator.store import digest, utc_now
from core.execution.image_policy import FrozenImageRequest
from core.execution.media_ledger import candidate_claimed, require, unresolved_text_claims
from core.execution.task_image_fees import candidate_for_task, checked_task_source
from core.harness.store import unlock_file


def import_task_fee(store, task_id, operation_id, task_export_bytes, account_export_bytes, task_source, *, observed_at):
    """Atomically append a fee and its one owner; never mutate a sealed receipt."""
    source = checked_task_source(task_source)
    lock = store.execution_lock(task_id)
    require(lock is not None, "ACTION_NOT_ALLOWED")
    try:
        with store.transaction() as payload:
            operation = payload.get("media", {}).get("operation:" + operation_id)
            require(operation is not None and operation["task_id"] == task_id and operation["raw"] is not None,
                "MEDIA_RECEIPT_REQUIRED")
            scope = payload["media"]["scope:" + operation["scope_id"]]
            old = None
            if operation["fee_id"] is not None:
                old = payload["media"]["fee:" + operation["fee_id"]]
                require(old["candidate"]["kind"] == "image_task_fee_candidate_v1", "FEE_EVIDENCE_CONFLICT")
                # The original observation time is part of its identity. Reuse
                # it inside the lock so later/concurrent delivery is idempotent.
                observed_at = old["candidate"]["observed_at"]
            candidate = candidate_for_task(FrozenImageRequest.from_frozen(operation["descriptor"]), operation["raw"],
                task_export_bytes, account_export_bytes, scope["fee_source"], source, observed_at=observed_at)
            if old is not None:
                require(digest(old["candidate"]) == digest(candidate), "FEE_EVIDENCE_CONFLICT")
                return {"operation": copy.deepcopy(operation), "replayed": True}
            require(not candidate_claimed(payload, candidate, operation_id), "FEE_EVIDENCE_CONFLICT")
            require(not unresolved_text_claims(payload), "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED")
            fee_id = candidate["platform_log_id"]
            export_base64 = base64.b64encode(task_export_bytes).decode("ascii")
            common = {"schema_version": 1, "id": fee_id, "scope_id": scope["id"], "task_id": task_id,
                "created_at": utc_now(), "operation_id": operation_id}
            fee = {**common, "kind": "fee", "candidate": candidate, "export_base64": export_base64,
                "task_fee_source": source, "account_export_base64": base64.b64encode(account_export_bytes).decode("ascii")}
            claim = {**common, "kind": "claim", "owner": "media", "evidence_hash": digest(candidate),
                "export_base64": export_base64, "fee_source": copy.deepcopy(scope["fee_source"])}
            require("fee:" + fee_id not in payload["media"] and "claim:" + fee_id not in payload["media"], "FEE_EVIDENCE_CONFLICT")
            payload["media"].update({"fee:" + fee_id: fee, "claim:" + fee_id: claim})
            operation["fee_id"] = fee_id
            return {"operation": copy.deepcopy(operation), "replayed": False}
    finally:
        unlock_file(lock)
