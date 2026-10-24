"""Single HTTP attempt with an overall deadline and conservative private receipts."""
import base64
import hashlib
import json
import re
import threading
from datetime import datetime

from core.creator.store import digest, utc_now
from core.execution.policy import (
    SCOPE,
    TrustedInputBounder,
    chat_output_parameter,
    cost,
    now,
    protocol_endpoint_matches,
    wire_api,
)
from core.execution.responses import normalized_response, request_wire
from core.planner.protocol import parse_json

TRANSPORT_DIAGNOSTIC_PHASES = {
    "PERMIT_REJECTED": "preflight",
    "CONNECT_TIMEOUT": "connect",
    "READ_TIMEOUT": "read",
    "WRITE_TIMEOUT": "write",
    "POOL_TIMEOUT": "pool",
    "TRANSPORT_ERROR": "unknown",
    "OVERALL_DEADLINE": "deadline",
    "RECEIPT_TOO_LARGE": "receipt",
}


class ResponseUnconfirmed(Exception):
    """Fixed private classification, never an upstream exception message."""

    def __init__(self, code="TRANSPORT_ERROR"):
        self.code = code if type(code) is str and code in TRANSPORT_DIAGNOSTIC_PHASES else "TRANSPORT_ERROR"
        self.phase = TRANSPORT_DIAGNOSTIC_PHASES[self.code]
        super().__init__(self.code)

    @property
    def diagnostic(self):
        return {"code": self.code, "phase": self.phase}


def unconfirmed_code(error):
    import httpx

    if isinstance(error, ResponseUnconfirmed):
        return ResponseUnconfirmed(error.code).code
    for error_type, code in [(httpx.ConnectTimeout, "CONNECT_TIMEOUT"), (httpx.ReadTimeout, "READ_TIMEOUT"),
                             (httpx.WriteTimeout, "WRITE_TIMEOUT"), (httpx.PoolTimeout, "POOL_TIMEOUT")]:
        if isinstance(error, error_type):
            return code
    return "TRANSPORT_ERROR"


def wire_for(messages, policy):
    if wire_api(policy) == "responses":
        return request_wire(messages, policy)
    payload = {"model": policy["model"], chat_output_parameter(policy): policy["max_output_tokens"], "messages": [
        {"role": "user" if message["role"] == "tool" else message["role"], "content": json.dumps(message["content"], ensure_ascii=False)}
        for message in messages]}
    return json.dumps(payload, ensure_ascii=False)


class ControlledTransport:
    def __init__(self, transport=None, input_bounder=None):
        self.transport = transport
        self.input_bounder = input_bounder

    def _preflight(self, operation, policy):
        from core.execution.roles import validate_operation_tags

        validate_operation_tags(operation, policy, verify_context=True)
        permit = operation["permit"]
        pairs = {"operation_id": "id", "task_id": "task_id", "snapshot_hash": "snapshot_hash", "context_hash": "context_hash", "wire_hash": "wire_hash", "grant_id": "grant_id", "quote_id": "quote_id", "account_id": "account_id", "policy_hash": "policy_hash", "hold_units": "hold_units"}
        valid = operation["permit_hash"] == digest(permit) and all(permit[key] == operation[field] for key, field in pairs.items())
        tagged = "wire_api" in policy
        valid = valid and ("wire_api" in operation) == tagged and ("wire_api" in permit) == tagged
        valid = valid and wire_api(policy) == wire_api(operation) == wire_api(permit) and protocol_endpoint_matches(policy)
        parameter_tagged = "chat_output_parameter" in policy
        valid = valid and ("chat_output_parameter" in operation) == parameter_tagged and ("chat_output_parameter" in permit) == parameter_tagged
        valid = valid and chat_output_parameter(policy) == chat_output_parameter(operation) == chat_output_parameter(permit)
        valid = valid and operation["context_hash"] == digest(operation["context"]) and operation["wire_hash"] == digest(operation["wire"]) and operation["wire"] == wire_for(operation["context"], policy)
        valid = valid and policy["policy_hash"] == digest({key: value for key, value in policy.items() if key != "policy_hash"}) and permit["policy_hash"] == policy["policy_hash"] and permit["model"] == policy["model"] and permit["endpoint"] == policy["endpoint"] and permit["scope"] == SCOPE and permit["account_id"] == policy["account_scope_id"]
        valid = valid and permit["hold_units"] == cost(policy["max_input_tokens"], policy["max_output_tokens"], policy) and len(operation["wire"].encode("utf-8")) <= policy["max_input_bytes"]
        valid = valid and all(permit[key] == policy[key] for key in ["currency", "max_input_tokens", "max_output_tokens", "max_input_bytes", "max_output_bytes"])
        bounder = self.input_bounder
        upper = bounder.estimate(operation["wire"], policy) if isinstance(bounder, TrustedInputBounder) else None
        valid = valid and type(upper) is int and upper <= policy["max_input_tokens"] and upper == permit["input_token_upper"] and bounder.verification_ref == permit["bounder_ref"]
        deadline = datetime.fromisoformat(permit["deadline_at"])
        issued = datetime.fromisoformat(permit["issued_at"])
        valid = valid and (deadline - issued).total_seconds() <= policy["request_deadline_seconds"] and deadline <= min(datetime.fromisoformat(policy["valid_until"]), datetime.fromisoformat(permit["grant_expires_at"]), datetime.fromisoformat(permit["quote_expires_at"]))
        if not valid:
            raise ResponseUnconfirmed("PERMIT_REJECTED")
        remaining = (datetime.fromisoformat(permit["deadline_at"]) - now()).total_seconds()
        if remaining <= 0:
            raise ResponseUnconfirmed("OVERALL_DEADLINE")
        return remaining, deadline

    def perform(self, operation, policy, api_key):
        try:
            remaining, deadline = self._preflight(operation, policy)
        except ResponseUnconfirmed:
            raise
        except Exception:
            raise ResponseUnconfirmed("PERMIT_REJECTED") from None
        completed, box = threading.Event(), {}

        def request_once():
            import httpx
            client = None
            try:
                if now() >= deadline:
                    raise ResponseUnconfirmed("OVERALL_DEADLINE")
                client = httpx.Client(transport=self.transport, timeout=remaining, follow_redirects=False, trust_env=False)
                with client.stream("POST", policy["endpoint"], content=operation["wire"].encode("utf-8"),
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key, "X-Client-Operation-Id": operation["id"]}) as response:
                    try:
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            if now() >= deadline:
                                raise ResponseUnconfirmed("OVERALL_DEADLINE")
                            data.extend(chunk)
                            if len(data) > 1048576:
                                raise ResponseUnconfirmed("RECEIPT_TOO_LARGE")
                        box["value"] = {"http_status": response.status_code, "headers": {key.lower(): value for key, value in response.headers.items() if key.lower() == "x-request-id"},
                                        "body_base64": base64.b64encode(data).decode("ascii"), "received_at": utc_now()}
                    except Exception as error:
                        box["error"] = unconfirmed_code(error)
            except Exception as error:
                box.setdefault("error", unconfirmed_code(error))
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception as error:
                        box.setdefault("error", unconfirmed_code(error))
                completed.set()

        # The observer returns at the absolute deadline even if a transport
        # ignores phase timeouts. A late request is not called canceled/refunded;
        # its hold remains unresolved and it cannot be resubmitted.
        thread = threading.Thread(target=request_once, name="bounded-text-request", daemon=True)
        thread.start()
        if not completed.wait(remaining) or now() >= deadline:
            raise ResponseUnconfirmed("OVERALL_DEADLINE")
        if "error" in box or "value" not in box:
            raise ResponseUnconfirmed(box.get("error", "TRANSPORT_ERROR"))
        return box["value"]


def normalized(raw, policy, hold_units, input_upper=None):
    if wire_api(policy) == "responses":
        return normalized_response(raw, policy, hold_units, input_upper)
    empty = {"provider_request_id": None, "provider_response_id": None, "input_tokens": None, "output_tokens": None,
             "finish_reason": None, "content": None, "content_hash": None, "local_units": None}
    result = dict(empty)
    try:
        if raw["http_status"] != 200:
            return result, "RESPONSE_UNCONFIRMED"
        value = parse_json(base64.b64decode(raw["body_base64"], validate=True))
        for key, candidate in [("provider_request_id", raw["headers"].get("x-request-id") or value.get("request_id")), ("provider_response_id", value.get("id"))]:
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", candidate):
                result[key] = candidate
        if not isinstance(value.get("choices"), list) or len(value["choices"]) != 1 or not isinstance(value["choices"][0], dict):
            return result, "RECEIPT_INCOMPLETE"
        choice = value["choices"][0]
        result["finish_reason"] = choice["finish_reason"] if choice["finish_reason"] in {"stop", "length", "content_filter", "tool_calls", "function_call"} else None
        content = choice["message"]["content"]
        if isinstance(content, str) and len(content.encode("utf-8")) <= policy["max_output_bytes"]:
            result["content"], result["content_hash"] = content, hashlib.sha256(content.encode("utf-8")).hexdigest()
        if value.get("model") != policy["model"]:
            return result, "MODEL_MISMATCH"
        if result["finish_reason"] != "stop":
            return result, "RECEIPT_TRUNCATED"
        usage = value["usage"]
        if not isinstance(usage, dict) or set(usage) != {"prompt_tokens", "completion_tokens", "total_tokens"}:
            return result, "RECEIPT_INCOMPLETE"
        values = [usage[key] for key in ["prompt_tokens", "completion_tokens", "total_tokens"]]
        if any(type(number) is not int or number < 0 for number in values):
            return result, "RECEIPT_OUT_OF_BOUNDS"
        input_tokens, output_tokens, total_tokens = values
        if input_tokens > min(policy["max_input_tokens"], input_upper if input_upper is not None else policy["max_input_tokens"]) or output_tokens > policy["max_output_tokens"] or total_tokens != input_tokens + output_tokens:
            return result, "RECEIPT_OUT_OF_BOUNDS"
        result["input_tokens"], result["output_tokens"] = input_tokens, output_tokens
        if result["content"] is None:
            return result, "RECEIPT_OUT_OF_BOUNDS"
        if not all(isinstance(result[key], str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", result[key]) for key in ["provider_request_id", "provider_response_id"]):
            return result, "RECEIPT_INCOMPLETE"
        amount = cost(input_tokens, output_tokens, policy)
        if amount > hold_units:
            return result, "RECEIPT_OUT_OF_BOUNDS"
        result["local_units"] = amount
        return result, None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, ArithmeticError, RecursionError):
        return result, "RECEIPT_INCOMPLETE"
