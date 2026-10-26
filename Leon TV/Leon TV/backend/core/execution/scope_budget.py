"""Retained allocations across native media namespaces in one execution journal."""
from core.creator.store import CreatorError, digest
from core.execution.policy import units


def require(value):
    if not value:
        raise CreatorError("CORRUPT_STORAGE", "Shared execution allocations are inconsistent", 409)


def _scopes(payload):
    return [(namespace, record) for namespace in ("media", "asset_design")
            for record in payload.get(namespace, {}).values() if record["kind"] == "scope"]


def _extensions(payload, account_id):
    if not any(record.get("kind") == "validity_continuation_intent"
               for record in payload.get("media", {}).values()):
        return []
    from core.execution.image_validity_continuation import retained_extensions

    result = []
    for intent in retained_extensions(payload, account_id):
        require(type(intent["additional_paid_calls"]) is int and intent["additional_paid_calls"] == 1
                and intent["additional_risk_cny"] == "1.000000")
        quote = payload["media"]["validity_continuation_quote:" + intent["quote_id"]]
        proposal = quote["proposal"]
        allocation = proposal["parent_allocation"]
        record = {"operation_key": allocation["operation_key"], "request": allocation["request"],
            "request_sha256": digest(allocation["request"]), "upper_fen": 100,
            "quote_ref": allocation["quote_ref"], "status": "reserved",
            "provider_task_id": None, "actual_charge_fen": None}
        budget = proposal["basis"]["budget"]
        result.append({"intent": intent, "allocation_hash": digest(record),
            "risk_units": units(intent["additional_risk_cny"]), "max_calls": intent["additional_paid_calls"],
            "account_limit_units": units(budget["proposed_retained_risk_cny"]),
            "account_max_calls": budget["proposed_max_calls"]})
    return result


def scope_envelope(payload, account_id):
    """Use frozen full envelopes, including unused calls, never actual-only totals."""
    account = payload["accounts"][account_id]
    scopes = [record for _, record in _scopes(payload) if record["account_id"] == account_id]
    extensions = _extensions(payload, account_id)
    return {
        "risk_units": max([account["limit_units"] + sum(units(scope["anchor"]["scope_risk_cny"]) for scope in scopes)
                           + sum(item["risk_units"] for item in extensions),
                           *[units(scope["anchor"]["account_limit_cny"]) for scope in scopes],
                           *[item["account_limit_units"] for item in extensions]]),
        "max_calls": max([account["max_calls"] + sum(scope["anchor"]["scope_max_calls"] for scope in scopes)
                          + sum(item["max_calls"] for item in extensions),
                          *[scope["anchor"]["account_max_calls"] for scope in scopes],
                          *[item["account_max_calls"] for item in extensions]]),
    }


def validate_shared_scopes(payload, previous=None):
    scopes = _scopes(payload)
    extensions = [item for account_id in payload["accounts"] for item in _extensions(payload, account_id)]
    allocations = [scope["anchor"]["parent_allocation_hash"] for _, scope in scopes] + [item["allocation_hash"] for item in extensions]
    require(len(allocations) == len(set(allocations)))
    _validate_fee_owners(payload)
    for account_id in payload["accounts"]:
        current = [(namespace, scope) for namespace, scope in scopes if scope["account_id"] == account_id]
        current_extensions = _extensions(payload, account_id)
        if not current and not current_extensions:
            continue
        require(scope_envelope(payload, account_id)["risk_units"] <= 100000000)
        if previous is None:
            continue
        new = [(namespace, scope) for namespace, scope in current
               if "scope:" + scope["id"] not in previous.get(namespace, {})]
        new_extensions = [item for item in current_extensions
                          if "validity_continuation_intent:" + item["intent"]["id"] not in previous.get("media", {})]
        # A single transaction cannot reserve two apparently independent
        # allocations against the same opening balance.
        require(len(new) + len(new_extensions) <= 1)
        if new:
            opening = scope_envelope(previous, account_id)
            anchor = new[0][1]["anchor"]
            require(units(anchor["opening_risk_cny"]) >= opening["risk_units"])
            require(anchor["opening_paid_call_count"] >= opening["max_calls"])
        if new_extensions:
            opening = scope_envelope(previous, account_id)
            extension = new_extensions[0]
            require(extension["account_limit_units"] == opening["risk_units"] + extension["risk_units"])
            require(extension["account_max_calls"] == opening["max_calls"] + extension["max_calls"])
    if previous is not None:
        _validate_paid_intents(payload, previous)


def _paid_intents(payload):
    result = [("text", op["id"], op["account_id"]) for op in payload["operations"].values()]
    for namespace, scope in _scopes(payload):
        result.extend((namespace, record["id"], scope["account_id"])
                      for record in payload.get(namespace, {}).values()
                      if record["kind"] == "operation" and record["scope_id"] == scope["id"])
    return result


def _validate_fee_owners(payload):
    owners = {}

    def claim(key, owner):
        require(key not in owners or owners[key] == owner)
        owners[key] = owner

    for operation in payload["operations"].values():
        evidence = operation.get("empirical", {}).get("fee_evidence")
        if evidence and evidence["platform_log_id"].startswith("tudou-request-sha256:"):
            claim(evidence["platform_log_id"], ("text", operation["id"]))
    for namespace in ("media", "asset_design"):
        for record in payload.get(namespace, {}).values():
            if record["kind"] == "claim":
                owner = "text" if record.get("owner") == "text" else namespace
                claim(record["id"], (owner, record["operation_id"]))
            elif namespace == "media" and record["kind"] == "fee":
                from core.execution.task_image_fees import claim_keys
                for key in claim_keys(record["candidate"]):
                    claim(key, (namespace, record["operation_id"]))


def _validate_paid_intents(payload, previous):
    from core.execution.media_ledger import account_halted, has_pending_fees, has_pending_unknown

    old = set(_paid_intents(previous))
    new = [item for item in _paid_intents(payload) if item not in old]
    for account_id in payload["accounts"]:
        additions = [item for item in new if item[2] == account_id]
        require(len(additions) <= 1)
        if additions:
            require(not account_halted(previous, account_id) and not account_halted(payload, account_id))
            require(not has_pending_unknown(previous, account_id) and not has_pending_unknown(payload, account_id))
            require(not has_pending_fees(previous, account_id))
            if additions[0][0] != "text":
                require(all(op["status"] == "settled" for op in previous["operations"].values()
                            if op["account_id"] == account_id))
