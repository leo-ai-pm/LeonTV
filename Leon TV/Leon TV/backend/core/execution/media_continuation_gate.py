"""Candidate-scoped continuation checks; global unknown facts stay unchanged."""
import copy

from core.creator.store import CreatorError, digest


def require(value):
    if not value:
        raise CreatorError("MEDIA_CONTINUATION_BLOCKED", "The exact continuation candidate is not permitted", 409)


def is_continuation(record):
    return record.get("descriptor", {}).get("kind") in {"controlled_image_http_v2", "controlled_image_http_v3"}


def candidate_gate(payload, candidate, *, exclude_candidate=False):
    """Ignore only the disclosed media unknown for this fully bound candidate.

    This is a pure transition check, not a current code/clock/credential grant.
    A temporary media-only view is used for fact queries; no record is edited,
    and identically named text or asset operations are never excluded.
    """
    from core.execution.image_policy import FrozenImageRequest
    from core.execution.image_runtime_continuation import authority_for, latest_activation_id
    from core.execution.media_ledger import (
        account_halted,
        has_pending_fees,
        has_pending_unknown,
        records,
        unresolved_text_claims,
    )

    require(type(exclude_candidate) is bool and type(candidate) is dict)
    require(candidate.get("kind") == "operation" and is_continuation(candidate))
    data = FrozenImageRequest.from_frozen(candidate["descriptor"]).frozen()
    permit = data["permit"]
    authority = authority_for(payload, permit["runtime_activation_id"])
    require(latest_activation_id(payload, authority["scope_id"]) == authority["activation_id"])
    require(digest(data["continuation_authority"]) == digest(authority))
    scope = payload["media"]["scope:" + authority["scope_id"]]
    grant = payload["media"]["grant:" + authority["grant_id"]]
    require(candidate["scope_id"] == scope["id"] == authority["scope_id"])
    require(candidate["task_id"] == scope["task_id"] == authority["task_id"])
    require(candidate["grant_id"] == grant["id"] == permit["grant_id"] and grant["revoked_at"] is None)
    require(candidate["quote_id"] == grant["quote_id"] and grant["scope_id"] == scope["id"])
    require(digest(scope) == authority["scope_hash"])
    require(candidate["id"] == permit["operation_id"] and data["method"] == "POST")
    require(candidate["submission_index"] == permit["scope_submission_index"])
    targets = [target for target in authority["remaining_jobs"] if target["shot_id"] == candidate["shot_id"]]
    require(len(targets) == 1)
    target = targets[0]
    require(target["planned_submission_index"] == candidate["submission_index"])
    require(target["job_hash"] == digest(data["job"]) and data["job"]["shot_id"] == candidate["shot_id"])
    require(permit["attempt_version"] == target["attempt_version"]
            and permit["replacement_of"] == target["replacement_of"])
    own = [op for op in records(payload, "operation") if op["scope_id"] == scope["id"] and op["id"] != candidate["id"]]
    require(candidate["submission_index"] == len(own) + 1 <= authority["max_cumulative_submissions"])
    require(not any(is_continuation(op) and op["shot_id"] == candidate["shot_id"] for op in own))
    unknown = authority["unknown_disposition"]
    old_key = "operation:" + unknown["operation_id"]
    old = payload["media"][old_key]
    require(old["scope_id"] == scope["id"] and old["task_id"] == scope["task_id"])
    require(digest(old) == unknown["operation_hash"] and old["status"] == "unknown"
            and old["raw"] is None and old["fee_id"] is None and old["delivery_id"] is None)
    require(candidate["id"] != old["id"] and permit["nonce"] != old["descriptor"]["permit"]["nonce"])
    require(all(op["descriptor"]["permit"]["nonce"] != permit["nonce"]
                for kind in ("operation", "query") for op in records(payload, kind) if op["id"] != candidate["id"]))
    account_id = scope["account_id"]
    require(not account_halted(payload, account_id) and not unresolved_text_claims(payload))
    require(all(op["status"] == "settled" for op in payload["operations"].values() if op["account_id"] == account_id))
    require(not any(query["status"] in {"reserved", "submitting", "unknown"}
                    and payload["media"]["scope:" + query["scope_id"]]["account_id"] == account_id
                    for query in records(payload, "query")))
    media = dict(payload["media"])
    del media[old_key]
    candidate_key = "operation:" + candidate["id"]
    if exclude_candidate:
        persisted = media.get(candidate_key)
        require(persisted is not None and digest(persisted) == digest(candidate)
                and persisted["status"] in {"reserved", "submitting"})
        del media[candidate_key]
    else:
        require(candidate_key not in media)
    view = {**payload, "media": media}
    require(not has_pending_unknown(view, account_id) and not has_pending_fees(view, account_id))
    # Return a defensive value only; a caller cannot mutate persisted authority.
    return copy.deepcopy(authority)
