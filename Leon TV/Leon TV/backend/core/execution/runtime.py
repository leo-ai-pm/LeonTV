"""Durable execution control in one journal; legacy Planner documents stay read-only."""
import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from core.creator.store import CreatorError, digest, utc_now
from core.execution.policy import (
    SCOPE,
    TrustedInputBounder,
    chat_output_parameter,
    cost,
    money,
    now,
    protocol_endpoint_matches,
    safe_policy,
    units,
    wire_api,
)
from core.execution.receipts import ObservationEnded, ReceiptCaptureSettings, ReceiptStore
from core.execution.store import ExecutionStore, totals
from core.execution.transport import (
    ControlledTransport,
    ResponseUnconfirmed,
    normalized,
    unconfirmed_code,
    wire_for,
)
from core.harness.store import unlock_file
from core.planner.protocol import LIMITS, execute_tool
from core.planner.runtime import SYSTEM


class ExecutionRuntime:
    def __init__(self, root, planner, *, policy=None, enabled=False, api_key=None, transport=None, input_bounder=None, receipt_capture=None):
        self.store, self.planner = ExecutionStore(root), planner
        self.policy = policy.frozen() if policy is not None else None
        self.enabled, self.api_key, self.transport = enabled, api_key, transport
        self.input_bounder = input_bounder
        if receipt_capture is not None and not isinstance(receipt_capture, ReceiptCaptureSettings):
            raise ValueError("Explicit server receipt capture settings required")
        self.receipt_capture = receipt_capture
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="controlled-planner")

    def inspect_receipts(self, task_id, operation_id):
        payload = self.store.read_only()
        operation = payload["operations"].get(operation_id)
        if operation is None or operation["task_id"] != task_id:
            raise CreatorError("NOT_FOUND", "Execution operation not found", 404)
        return {"inspection": ReceiptStore(self.store.root / "receipts").inspect(operation)}

    def close(self):
        self.pool.shutdown(wait=True)

    def availability(self, payload=None):
        if not self.enabled:
            return "LIVE_DISABLED"
        if self.policy is None or not self.api_key or datetime.fromisoformat(self.policy["valid_until"]) <= now():
            return "POLICY_UNAVAILABLE"
        if not protocol_endpoint_matches(self.policy):
            return "POLICY_UNAVAILABLE"
        if not isinstance(self.input_bounder, TrustedInputBounder) or self.input_bounder.policy_hash != self.policy["policy_hash"] or not self.input_bounder.verification_ref or not callable(self.input_bounder.upper_bound):
            return "POLICY_UNAVAILABLE"
        if payload is not None and payload["accounts"] and self.policy["account_scope_id"] not in payload["accounts"]:
            return "POLICY_UNAVAILABLE"
        if self.policy["validation_only"]:
            import httpx
            if not isinstance(self.transport, httpx.MockTransport):
                return "POLICY_UNAVAILABLE"
        return None

    def environment(self):
        if not self.enabled:
            return "disabled"
        return "synthetic_test" if self.policy and self.policy["validation_only"] else "live"

    def policy_view(self):
        with self.store.transaction() as payload:
            reason = self.availability(payload)
            return {"available": reason is None, "blocked_reason": reason, "environment": self.environment(),
                    "policy": safe_policy(self.policy) if reason is None else None}

    def base(self, task_id):
        task = self.planner.get(task_id)["task"]
        if task["mode"] != "normal":
            raise CreatorError("TASK_NOT_ELIGIBLE", "Offline tasks cannot enter controlled execution", 409)
        return task

    @staticmethod
    def bump(task, event, code=None, operation_id=None):
        task["revision"] += 1
        task["updated_at"] = utc_now()
        task["events"].append({"sequence": task["revision"], "type": event, "execution_revision": task["revision"],
                               "created_at": task["updated_at"], "operation_id": operation_id, "code": code})

    @staticmethod
    def pending(payload, task):
        return next((op for op in payload["operations"].values() if op["task_id"] == task["id"] and op["status"] != "settled"), None)

    def quote_status(self, quote):
        if datetime.fromisoformat(quote["expires_at"]) <= now():
            return "expired"
        if self.policy is None or quote["policy"]["policy_hash"] != self.policy["policy_hash"]:
            return "policy_stale"
        return "valid"

    def quote_view(self, quote):
        result = {key: copy.deepcopy(value) for key, value in quote.items() if key != "policy"}
        result["policy"] = safe_policy(quote["policy"])
        result["status"] = self.quote_status(quote)
        return result

    def grant_status(self, grant):
        if grant["revoked_at"] is not None:
            return "revoked"
        if datetime.fromisoformat(grant["expires_at"]) <= now():
            return "expired"
        if self.policy is None or grant["policy_hash"] != self.policy["policy_hash"]:
            return "policy_stale"
        return "active"

    def grant_view(self, grant):
        return copy.deepcopy(grant) | {"status": self.grant_status(grant)}

    def gate(self, payload, task):
        reason = self.availability(payload)
        if reason:
            return reason
        if self.pending(payload, task) is not None:
            return "RESPONSE_UNCONFIRMED"
        grant = payload["grants"].get(task["active_grant_id"])
        if grant is None:
            return "AUTHORIZATION_REQUIRED"
        status = self.grant_status(grant)
        if status != "active":
            return {"revoked": "GRANT_REVOKED", "expired": "GRANT_EXPIRED", "policy_stale": "POLICY_STALE"}[status]
        quote = payload["quotes"][grant["quote_id"]]
        if self.quote_status(quote) != "valid":
            return "QUOTE_EXPIRED" if self.quote_status(quote) == "expired" else "POLICY_STALE"
        if grant["snapshot_hash"] != task["snapshot_hash"] or grant["scope"] != SCOPE or quote["policy"]["policy_hash"] != self.policy["policy_hash"]:
            return "POLICY_STALE"
        account_id = quote["policy"]["account_scope_id"]
        account = payload["accounts"][account_id]
        used, shared = totals(payload, task_id=task["id"]), totals(payload, account_id=account_id)
        hold = units(quote["per_call_hold_cny"])
        if used["calls"] >= min(grant["max_calls"], task["max_calls"], 6) or shared["calls"] >= account["max_calls"]:
            return "CALL_LIMIT"
        if used["accounted"] + used["held"] + hold > min(units(grant["total_limit_cny"]), task["task_limit_units"]) or shared["accounted"] + shared["held"] + hold > account["limit_units"]:
            return "BUDGET_EXHAUSTED"
        if task["repair_count"] >= 2:
            return "REPAIR_LIMIT"
        return None

    def detail(self, payload, task_id, base=None):
        task = payload["tasks"].get(task_id)
        if task is None:
            account = next(iter(payload["accounts"].values()), None)
            shared = totals(payload, account_id=account["id"]) if account else {"accounted": 0, "held": 0, "calls": 0}
            return {"task_id": task_id, "revision": 0, "environment": self.environment(), "status": "not_authorized",
                    "blocked_reason": self.availability(payload) or "AUTHORIZATION_REQUIRED", "allowed_actions": [],
                    "source": base["source"], "policy_available": self.availability(payload) is None, "active_grant_id": None,
                    "quotes": [], "grants": [], "ledger": {"currency": "CNY", "locally_accounted_cny": "0.000000", "held_cny": "0.000000", "task_limit_cny": None,
                    "call_count": 0, "max_calls": 0, "account_locally_accounted_cny": money(shared["accounted"]), "account_held_cny": money(shared["held"]), "account_limit_cny": money(account["limit_units"]) if account else None,
                    "account_call_count": shared["calls"], "account_max_calls": account["max_calls"] if account else 0, "provider_bill_confirmed": False}, "operations": [], "plan": None,
                    "quality_status": "not_reviewed", "created_at": None, "updated_at": None}
        used = totals(payload, task_id=task_id)
        quote = payload["quotes"].get(payload["grants"].get(task["active_grant_id"], {}).get("quote_id"))
        account_id = quote["policy"]["account_scope_id"] if quote else task["account_id"]
        shared, account = totals(payload, account_id=account_id), payload["accounts"][account_id]
        pending = self.pending(payload, task)
        status, reason = task["status"], task["reason"]
        actions = []
        if status in {"queued", "running"}:
            actions = ["stop"]
        elif pending:
            actions = ["stop"] if status != "stopped" else []
            if pending["raw"] is not None and status not in {"running", "queued"}:
                actions.append("reconcile")
        elif status != "plan_ready" and self.gate(payload, task) is None:
            actions = ["resume"]
        if status in {"ready", "blocked", "not_authorized", "stopped"} and pending is None:
            gate_reason = self.gate(payload, task)
            if gate_reason:
                status, reason, actions = "blocked" if task["active_grant_id"] else "not_authorized", gate_reason, []
        operations = []
        for op in payload["operations"].values():
            if op["task_id"] != task_id:
                continue
            receipt = op["normalized"] or {}
            operations.append({"id": op["id"], "step": op["step"], "status": op["status"], "grant_id": op["grant_id"], "quote_id": op["quote_id"],
                "wire_api": wire_api(payload["quotes"][op["quote_id"]]["policy"]),
                **({"chat_output_parameter": chat_output_parameter(payload["quotes"][op["quote_id"]]["policy"])} if wire_api(payload["quotes"][op["quote_id"]]["policy"]) == "chat_completions" else {}),
                "policy_hash": op["policy_hash"], "context_hash": op["context_hash"], "hold_cny": money(op["hold_units"]),
                "locally_accounted_cny": money(op["local_units"]) if op["local_units"] is not None else None,
                **{key: receipt.get(key) for key in ["provider_request_id", "provider_response_id", "input_tokens", "output_tokens", "finish_reason", "response_status", "cached_input_tokens", "reasoning_output_tokens"]},
                "receipt_status": "confirmed_local" if op["status"] == "settled" else "received" if op["status"] == "received" else "unconfirmed" if op["status"] == "needs_reconciliation" else "none",
                "created_at": op["created_at"], "received_at": op["raw"]["received_at"] if op["raw"] else None})
        return {"task_id": task_id, "revision": task["revision"], "environment": self.environment(), "status": status, "blocked_reason": reason,
            "allowed_actions": actions, "source": self.planner.source_summary(task["source"]), "policy_available": self.availability(payload) is None,
            "active_grant_id": task["active_grant_id"], "quotes": [self.quote_view(q) for q in payload["quotes"].values() if q["task_id"] == task_id],
            "grants": [self.grant_view(g) for g in payload["grants"].values() if g["task_id"] == task_id],
            "ledger": {"currency": "CNY", "locally_accounted_cny": money(used["accounted"]), "held_cny": money(used["held"]), "task_limit_cny": money(task["task_limit_units"]),
                "call_count": used["calls"], "max_calls": task["max_calls"], "account_locally_accounted_cny": money(shared["accounted"]),
                "account_held_cny": money(shared["held"]), "account_limit_cny": money(account["limit_units"]), "account_call_count": shared["calls"],
                "account_max_calls": account["max_calls"], "provider_bill_confirmed": False},
            "operations": operations, "plan": copy.deepcopy(task["plan"]), "quality_status": "not_reviewed",
            "created_at": task["created_at"], "updated_at": task["updated_at"]}

    @staticmethod
    def replay(payload, namespace, key, request):
        record = payload["keys"].get(namespace + ":" + key)
        if record and record["request_hash"] != digest({"key": namespace + ":" + key, **request}):
            raise CreatorError("KEY_CONFLICT", "Execution key belongs to another request", 409)
        return record

    @staticmethod
    def remember(payload, namespace, key, request, task, resource_id=None):
        name = namespace + ":" + key
        payload["keys"][name] = {"task_id": task["id"], "request": copy.deepcopy(request), "request_hash": digest({"key": name, **request}),
            "resource_id": resource_id, "applied_revision": task["revision"]}

    @staticmethod
    def check_revision(task, expected):
        actual = task["revision"] if task else 0
        if actual != expected:
            raise CreatorError("REVISION_CONFLICT", "Refresh execution state before a new action", 409, actual)

    def resource_result(self, payload, task_id, namespace, record, replayed):
        result = {"execution": self.detail(payload, task_id), "replayed": replayed, "applied_revision": record["applied_revision"]}
        if namespace == "quote":
            result["quote"] = self.quote_view(payload["quotes"][record["resource_id"]])
        elif namespace in {"grant", "revoke"}:
            result["grant"] = self.grant_view(payload["grants"][record["resource_id"]])
        return result

    def quote(self, task_id, body):
        request = {"task_id": task_id, **body}
        with self.store.transaction() as payload:
            record = self.replay(payload, "quote", body["client_request_key"], request)
            if record:
                return self.resource_result(payload, task_id, "quote", record, True)
            reason = self.availability(payload)
            if reason:
                raise CreatorError(reason, "Controlled execution is unavailable until server policy is ready", 409)
            base = self.base(task_id)
            task = payload["tasks"].get(task_id)
            self.check_revision(task, body["expected_execution_revision"])
            if body["expected_task_revision"] != base["revision"]:
                raise CreatorError("REVISION_CONFLICT", "Refresh the saved Planner task", 409, base["revision"])
            policy = copy.deepcopy(self.policy)
            account_id = policy["account_scope_id"]
            account = {"id": account_id, "limit_units": units(policy["account_limit_cny"]), "max_calls": policy["account_max_calls"]}
            if payload["accounts"].get(account_id, account) != account:
                raise CreatorError("POLICY_UNAVAILABLE", "Account scope ceilings require a separate server review", 409)
            payload["accounts"][account_id] = account
            if task is None:
                source = self.planner.source(task_id)["source"]
                task = {"id": task_id, "source": source, "snapshot_hash": source["snapshot_hash"], "planner_task_revision": base["revision"],
                    "revision": 0, "created_at": utc_now(), "updated_at": utc_now(), "status": "not_authorized", "reason": "AUTHORIZATION_REQUIRED",
                    "account_id": account_id, "task_limit_units": units(policy["task_limit_cny"]), "max_calls": policy["max_calls"],
                    "messages": [{"role": "system", "content": copy.deepcopy(SYSTEM)}, {"role": "user", "content": {"task_id": task_id, "source_snapshot_hash": source["snapshot_hash"]}}],
                    "read_source": False, "repair_count": 0, "active_grant_id": None, "plan": None, "events": []}
                payload["tasks"][task_id] = task
            quote = {"id": uuid.uuid4().hex, "task_id": task_id, "planner_task_revision": base["revision"], "snapshot_hash": task["snapshot_hash"],
                "policy": policy, "per_call_hold_cny": money(cost(policy["max_input_tokens"], policy["max_output_tokens"], policy)),
                "total_limit_cny": money(min(task["task_limit_units"], units(policy["task_limit_cny"]))), "max_calls": min(task["max_calls"], policy["max_calls"]),
                "expires_at": policy["valid_until"], "created_at": utc_now()}
            quote["disclosure_hash"] = digest(quote)
            payload["quotes"][quote["id"]] = quote
            self.bump(task, "quote_created")
            self.remember(payload, "quote", body["client_request_key"], request, task, quote["id"])
            return self.resource_result(payload, task_id, "quote", payload["keys"]["quote:" + body["client_request_key"]], False)

    def grant(self, task_id, body):
        request = {"task_id": task_id, **body}
        with self.store.transaction() as payload:
            record = self.replay(payload, "grant", body["client_request_key"], request)
            if record:
                return self.resource_result(payload, task_id, "grant", record, True)
            reason = self.availability(payload)
            if reason:
                raise CreatorError(reason, "Controlled execution policy is unavailable", 409)
            task = payload["tasks"].get(task_id)
            if task is None:
                raise CreatorError("NOT_FOUND", "Execution quote not found", 404)
            self.check_revision(task, body["expected_execution_revision"])
            quote = payload["quotes"].get(body["quote_id"])
            if quote is None or quote["task_id"] != task_id:
                raise CreatorError("NOT_FOUND", "Execution quote not found", 404)
            status = self.quote_status(quote)
            if status != "valid":
                raise CreatorError("QUOTE_EXPIRED" if status == "expired" else "POLICY_STALE", "Quote no longer authorizes a new grant", 409)
            if body["accepted_disclosure_hash"] != quote["disclosure_hash"] or body["accepted_scope"] != SCOPE or body["consent"] is not True:
                raise CreatorError("CONSENT_MISMATCH", "Explicit consent must match the displayed server disclosure", 409)
            grant = {"id": uuid.uuid4().hex, "task_id": task_id, "quote_id": quote["id"], "snapshot_hash": task["snapshot_hash"],
                "policy_hash": quote["policy"]["policy_hash"], "disclosure_hash": quote["disclosure_hash"], "scope": SCOPE,
                "total_limit_cny": quote["total_limit_cny"], "max_calls": quote["max_calls"], "expires_at": quote["expires_at"], "created_at": utc_now(), "revoked_at": None}
            payload["grants"][grant["id"]] = grant
            task["active_grant_id"] = grant["id"]
            if task["plan"] is None and self.pending(payload, task) is None:
                task["status"], task["reason"] = "ready", None
            self.bump(task, "grant_created")
            self.remember(payload, "grant", body["client_request_key"], request, task, grant["id"])
            return self.resource_result(payload, task_id, "grant", payload["keys"]["grant:" + body["client_request_key"]], False)

    def revoke(self, task_id, grant_id, body):
        request = {"task_id": task_id, "grant_id": grant_id, **body}
        with self.store.transaction() as payload:
            record = self.replay(payload, "revoke", body["client_action_key"], request)
            if record:
                return self.resource_result(payload, task_id, "revoke", record, True)
            task, grant = payload["tasks"].get(task_id), payload["grants"].get(grant_id)
            if task is None or grant is None or grant["task_id"] != task_id:
                raise CreatorError("NOT_FOUND", "Execution grant not found", 404)
            self.check_revision(task, body["expected_execution_revision"])
            grant["revoked_at"] = grant["revoked_at"] or utc_now()
            self.bump(task, "grant_revoked", "GRANT_REVOKED")
            self.remember(payload, "revoke", body["client_action_key"], request, task, grant_id)
            return self.resource_result(payload, task_id, "revoke", payload["keys"]["revoke:" + body["client_action_key"]], False)

    def recover(self, task_id):
        with self.store.transaction() as payload:
            task = payload["tasks"].get(task_id)
            if not task or task["status"] not in {"queued", "running"}:
                return
        lock = self.store.execution_lock(task_id)
        if lock is None:
            return
        try:
            with self.store.transaction() as payload:
                task = payload["tasks"].get(task_id)
                if task and task["status"] in {"queued", "running"}:
                    pending = self.pending(payload, task)
                    if pending and pending["status"] != "received":
                        pending["status"] = "needs_reconciliation"
                    task["status"], task["reason"] = ("needs_reconciliation", "RESPONSE_UNCONFIRMED") if pending and pending["status"] != "received" else ("stopped", "RECOVERY_REQUIRES_RESUME")
                    self.bump(task, "execution_recovered", task["reason"], pending["id"] if pending else None)
        finally:
            unlock_file(lock)

    def get(self, task_id):
        with self.store.transaction() as payload:
            exists = task_id in payload["tasks"]
        base = None if exists else self.base(task_id)
        self.recover(task_id)
        with self.store.transaction() as payload:
            return {"execution": self.detail(payload, task_id, base)}

    def by_key(self, task_id, namespace, key):
        with self.store.transaction() as payload:
            record = payload["keys"].get(namespace + ":" + key)
            if not record or record["task_id"] != task_id:
                raise CreatorError("NOT_FOUND", "Execution request key not found", 404)
            result = self.resource_result(payload, task_id, namespace, record, True)
            return {namespace: result[namespace]}

    def events(self, task_id, after, limit):
        self.get(task_id)
        with self.store.transaction() as payload:
            events = payload["tasks"].get(task_id, {}).get("events", [])
            page = copy.deepcopy(events[after:after + limit])
            return {"events": page, "next_sequence": page[-1]["sequence"] if page else after, "has_more": after + limit < len(events)}

    def process_receipt(self, payload, task, operation):
        if operation["raw"] is None:
            return False
        policy = payload["quotes"][operation["quote_id"]]["policy"]
        receipt, reason = normalized(operation["raw"], policy, operation["hold_units"], operation["permit"]["input_token_upper"])
        if self.api_key and any(isinstance(value, str) and self.api_key in value for value in receipt.values()):
            receipt = {key: None if isinstance(value, str) and self.api_key in value else value for key, value in receipt.items()}
            receipt["content"], receipt["content_hash"], receipt["local_units"] = None, None, None
            reason = "RECEIPT_INCOMPLETE"
        operation["normalized"] = receipt
        if reason:
            operation["status"] = "needs_reconciliation"
            task["status"], task["reason"] = "needs_reconciliation", reason
            self.bump(task, "receipt_unconfirmed", reason, operation["id"])
            return False
        feedback, plan, read = execute_tool(receipt["content"], task["source"], task["read_source"])
        operation["status"], operation["local_units"], operation["tool_result"] = "settled", receipt["local_units"], feedback
        task["messages"].extend([{"role": "assistant", "content": receipt["content"]}, {"role": "tool", "content": feedback}])
        task["read_source"] = read
        if plan is not None:
            task["plan"], task["status"], task["reason"] = plan, "plan_ready", None
        elif not feedback["ok"]:
            task["repair_count"] += 1
            if task["repair_count"] >= 2:
                task["status"], task["reason"] = "blocked", "REPAIR_LIMIT"
        if task["status"] not in {"running", "plan_ready", "blocked", "stopped"}:
            task["status"], task["reason"] = "ready", None
        self.bump(task, "receipt_locally_accounted", feedback.get("code"), operation["id"])
        return True

    def action(self, task_id, body):
        self.get(task_id)
        request = {"task_id": task_id, **body}
        lock = None
        try:
            with self.store.transaction() as payload:
                record = self.replay(payload, "action", body["client_action_key"], request)
                if record:
                    return self.resource_result(payload, task_id, "action", record, True)
                task = payload["tasks"].get(task_id)
                self.check_revision(task, body["expected_execution_revision"])
                if task is None:
                    raise CreatorError("ACTION_NOT_ALLOWED", "Execution has no authorization", 409)
                action = body["action"]
                pending = self.pending(payload, task)
                if action not in self.detail(payload, task_id)["allowed_actions"]:
                    raise CreatorError("ACTION_NOT_ALLOWED", "Execution action is unavailable in this state", 409)
                if action == "resume":
                    reason = self.gate(payload, task)
                    if reason or task["status"] in {"running", "queued", "plan_ready"}:
                        raise CreatorError("ACTION_NOT_ALLOWED", "Execution is blocked by " + (reason or "current state"), 409)
                    lock = self.store.execution_lock(task_id)
                    if lock is None:
                        raise CreatorError("ACTION_NOT_ALLOWED", "Execution is already active", 409)
                    task["status"], task["reason"] = "queued", None
                elif action == "stop":
                    if task["status"] == "plan_ready":
                        raise CreatorError("ACTION_NOT_ALLOWED", "Execution is already complete", 409)
                    task["status"], task["reason"] = "stopped", "RESPONSE_UNCONFIRMED" if pending else "STOPPED"
                elif action == "reconcile":
                    if pending is None or pending["raw"] is None:
                        raise CreatorError("ACTION_NOT_ALLOWED", "No durable response can be reconciled; unresolved holds remain", 409)
                    lock = self.store.execution_lock(task_id)
                    if lock is None:
                        raise CreatorError("ACTION_NOT_ALLOWED", "Execution response is still being processed", 409)
                    task["status"] = "ready"
                    self.process_receipt(payload, task, pending)
                self.bump(task, "execution_" + action, task["reason"])
                self.remember(payload, "action", body["client_action_key"], request, task)
                result = self.resource_result(payload, task_id, "action", payload["keys"]["action:" + body["client_action_key"]], False)
            if lock is not None and body["action"] == "resume":
                self.pool.submit(self.worker, task_id, lock)
                lock = None
            return result
        finally:
            if lock is not None:
                unlock_file(lock)

    def worker(self, task_id, lock):
        try:
            with self.store.transaction() as payload:
                task = payload["tasks"][task_id]
                if task["status"] != "queued":
                    return
                task["status"] = "running"
                self.bump(task, "execution_started")
            while True:
                with self.store.transaction() as payload:
                    task = payload["tasks"][task_id]
                    if task["status"] != "running":
                        return
                    reason = self.gate(payload, task)
                    policy = copy.deepcopy(self.policy)
                    context = copy.deepcopy(task["messages"])
                    wire = wire_for(context, policy) if policy else ""
                    if len(json.dumps(context, ensure_ascii=False).encode("utf-8")) > LIMITS["max_context_bytes"] or policy and len(wire.encode("utf-8")) > policy["max_input_bytes"]:
                        reason = "CONTEXT_LIMIT"
                    input_upper = self.input_bounder.estimate(wire, policy) if policy and self.input_bounder else None
                    if type(input_upper) is not int or input_upper > policy["max_input_tokens"]:
                        reason = reason or "CONTEXT_LIMIT"
                    if reason:
                        task["status"], task["reason"] = "blocked", reason
                        self.bump(task, "dispatch_blocked", reason)
                        return
                    grant = payload["grants"][task["active_grant_id"]]
                    quote = payload["quotes"][grant["quote_id"]]
                    operation_id = uuid.uuid4().hex
                    deadline = min(now() + timedelta(seconds=policy["request_deadline_seconds"]), datetime.fromisoformat(grant["expires_at"]), datetime.fromisoformat(quote["expires_at"]), datetime.fromisoformat(policy["valid_until"]))
                    permit = {"operation_id": operation_id, "task_id": task_id, "snapshot_hash": task["snapshot_hash"], "context_hash": digest(context), "wire_hash": digest(wire),
                        "grant_id": grant["id"], "quote_id": quote["id"], "policy_hash": policy["policy_hash"], "endpoint": policy["endpoint"], "model": policy["model"],
                        "scope": SCOPE, "account_id": policy["account_scope_id"], "input_token_upper": input_upper, "bounder_ref": self.input_bounder.verification_ref,
                        "hold_units": units(quote["per_call_hold_cny"]), "deadline_at": deadline.isoformat(), "issued_at": utc_now(),
                        "grant_expires_at": grant["expires_at"], "quote_expires_at": quote["expires_at"], **{key: policy[key] for key in ["currency", "max_input_tokens", "max_output_tokens", "max_input_bytes", "max_output_bytes"]}, "nonce": uuid.uuid4().hex}
                    protocol_tag = {"wire_api": policy["wire_api"]} if "wire_api" in policy else {}
                    if "chat_output_parameter" in policy:
                        protocol_tag["chat_output_parameter"] = policy["chat_output_parameter"]
                    permit.update(protocol_tag)
                    operation = {"id": operation_id, "task_id": task_id, "step": totals(payload, task_id=task_id)["calls"] + 1,
                        "grant_id": grant["id"], "quote_id": quote["id"], "account_id": policy["account_scope_id"], "snapshot_hash": task["snapshot_hash"], "policy_hash": policy["policy_hash"],
                        "context": context, "context_hash": digest(context), "wire": wire, "wire_hash": digest(wire), "permit": permit, "permit_hash": digest(permit),
                        "status": "submitting", "hold_units": permit["hold_units"], "local_units": None, "raw": None, "normalized": None, "tool_result": None, "created_at": utc_now(), **protocol_tag}
                    payload["operations"][operation_id] = operation
                    self.bump(task, "operation_reserved", operation_id=operation_id)
                try:
                    if self.receipt_capture is None:
                        transport = ControlledTransport(self.transport, self.input_bounder)
                    else:
                        from core.execution.receipt_transport import CapturedTransport
                        transport = CapturedTransport(self.transport, self.input_bounder, self.store, self.receipt_capture)
                    raw = transport.perform(copy.deepcopy(operation), policy, self.api_key)
                except Exception as error:
                    diagnostic = None if isinstance(error, ObservationEnded) else ResponseUnconfirmed(unconfirmed_code(error)).diagnostic
                    with self.store.transaction() as payload:
                        task, current = payload["tasks"][task_id], payload["operations"][operation_id]
                        current["status"] = "needs_reconciliation"
                        if diagnostic is not None and "transport_diagnostic" not in current:
                            current["transport_diagnostic"] = diagnostic
                        task["status"] = "stopped" if task["status"] == "stopped" else "needs_reconciliation"
                        task["reason"] = "RESPONSE_UNCONFIRMED"
                        self.bump(task, "response_unconfirmed", task["reason"], operation_id)
                    return
                with self.store.transaction() as payload:
                    task, current = payload["tasks"][task_id], payload["operations"][operation_id]
                    current["raw"], current["status"] = raw, "received"
                    self.bump(task, "response_received", operation_id=operation_id)
                with self.store.transaction() as payload:
                    task, current = payload["tasks"][task_id], payload["operations"][operation_id]
                    if task["status"] == "stopped":
                        return
                    if not self.process_receipt(payload, task, current) or task["status"] != "running":
                        return
        finally:
            unlock_file(lock)
