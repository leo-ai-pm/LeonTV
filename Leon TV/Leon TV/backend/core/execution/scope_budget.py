"""Retained allocations across native media namespaces in one execution journal."""
from core.creator.store import CreatorError
from core.execution.policy import units


def require(value):
    if not value:
        raise CreatorError("CORRUPT_STORAGE", "Shared execution allocations are inconsistent", 409)


def _scopes(payload):
    return [(namespace, record) for namespace in ("media", "asset_design")
            for record in payload.get(namespace, {}).values() if record["kind"] == "scope"]


def scope_envelope(payload, account_id):
    """Use frozen full envelopes, including unused calls, never actual-only totals."""
    account = payload["accounts"][account_id]
    scopes = [record for _, record in _scopes(payload) if record["account_id"] == account_id]
    return {
        "risk_units": max([account["limit_units"] + sum(units(scope["anchor"]["scope_risk_cny"]) for scope in scopes),
                           *[units(scope["anchor"]["account_limit_cny"]) for scope in scopes]]),
        "max_calls": max([account["max_calls"] + sum(scope["anchor"]["scope_max_calls"] for scope in scopes),
                          *[scope["anchor"]["account_max_calls"] for scope in scopes]]),
    }


def validate_shared_scopes(payload, previous=None):
    scopes = _scopes(payload)
    allocations = [scope["anchor"]["parent_allocation_hash"] for _, scope in scopes]
    require(len(allocations) == len(set(allocations)))
    _validate_fee_owners(payload)
    for account_id in payload["accounts"]:
        current = [(namespace, scope) for namespace, scope in scopes if scope["account_id"] == account_id]
        if not current:
            continue
        require(scope_envelope(payload, account_id)["risk_units"] <= 100000000)
        if previous is None:
            continue
        new = [(namespace, scope) for namespace, scope in current
               if "scope:" + scope["id"] not in previous.get(namespace, {})]
        # A single transaction cannot reserve two apparently independent
        # allocations against the same opening balance.
        require(len(new) <= 1)
        if new:
            opening = scope_envelope(previous, account_id)
            anchor = new[0][1]["anchor"]
            require(units(anchor["opening_risk_cny"]) >= opening["risk_units"])
            require(anchor["opening_paid_call_count"] >= opening["max_calls"])
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
