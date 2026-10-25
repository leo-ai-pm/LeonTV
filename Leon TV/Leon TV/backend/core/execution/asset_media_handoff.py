"""Native manifest provenance for opt-in image scopes; no new authority or IO."""
import copy

from core.creator.store import CreatorError, digest
from core.planner.review import json_copy


def require(value):
    if not value:
        raise CreatorError("ASSET_MANIFEST_ORIGIN_INVALID", "A verified native asset manifest is required", 409)


def manifest_origin(payload, task_id, operation_id):
    from core.execution.asset_design_ledger import verified_manifest

    ready = verified_manifest(payload, task_id, operation_id)
    proof = ready["proof"]
    identity = {"schema_version": 1, "kind": "native_asset_manifest_v1", "task_id": task_id,
                "asset_operation_id": operation_id, "manifest_hash": ready["manifest"]["manifest_hash"],
                "asset_proof": copy.deepcopy(proof)}
    return {"manifest": copy.deepcopy(ready["manifest"]),
            "origin": {**identity, "origin_hash": digest(identity)}}


def check_manifest_origin(payload, scope):
    """Reconstruct the actual receipt/fee/manifest; self-declared hashes do not suffice."""
    try:
        origin = json_copy(scope["manifest_origin"])
        require(type(origin) is dict and set(origin) == {"schema_version", "kind", "task_id", "asset_operation_id",
                                                       "manifest_hash", "asset_proof", "origin_hash"})
        require(type(origin["schema_version"]) is int and origin["schema_version"] == 1)
        require(origin["kind"] == "native_asset_manifest_v1" and origin["task_id"] == scope["task_id"])
        actual = manifest_origin(payload, scope["task_id"], origin["asset_operation_id"])
        require(digest(actual["origin"]) == digest(origin) and digest(actual["manifest"]) == digest(scope["manifest"]))
        require(origin["asset_proof"]["asset_input_hash"] == digest(scope["asset_input"]))
        original_scope = payload["asset_design"]["scope:" + origin["asset_proof"]["scope_id"]]
        require(original_scope["account_id"] == scope["account_id"])
        return True
    except Exception:
        raise CreatorError("ASSET_MANIFEST_ORIGIN_INVALID", "A verified native asset manifest is required", 409) from None


def media_from_asset_design(runtime, task_id, operation_id, **settings):
    """Use a native manifest while requiring a separately authorized image scope."""
    from core.execution.media_runtime import MediaExecution

    require("manifest" not in settings and "manifest_origin" not in settings)
    ready = manifest_origin(runtime.store.read_only(), task_id, operation_id)
    return MediaExecution(runtime, manifest=ready["manifest"], manifest_origin=ready["origin"], **settings)
