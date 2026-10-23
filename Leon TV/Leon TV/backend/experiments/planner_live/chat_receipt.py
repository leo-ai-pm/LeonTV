"""Pure decoding of the explicitly scoped empirical Chat envelope.

Request IDs may be absent. A reasoning count is a subset of completion usage,
not a second charge. Nothing here verifies capture, authority, billing or tools.
Assistant content stays opaque for the original strict tool/feedback contract.
"""
import base64
import hashlib
import json
import math
import re
from datetime import datetime

SCOPE = "empirical_planner_text_once"
MAX_BODY_BYTES = 1048576
MAX_CONTENT_BYTES = 65536
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/-]{1,128}")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
SHA256 = re.compile(r"[0-9a-f]{64}")
ERROR_CODES = frozenset("EMPIRICAL_CHAT_" + suffix for suffix in (
    "SCOPE_REJECTED", "ARGUMENTS_INVALID", "HTTP_REJECTED", "ENVELOPE_INVALID", "BYTE_LIMIT",
    "MODEL_MISMATCH", "RESPONSE_ID_INVALID", "REQUEST_ID_INVALID", "REQUEST_ID_CONFLICT",
    "CHOICE_INVALID", "FINISH_REJECTED", "CONTENT_INVALID", "USAGE_INVALID", "REASONING_INVALID",
    "UNSUPPORTED_DIMENSION"))


class ChatReceiptDecodeError(ValueError):
    """Only a fixed code is exposed; never attach receipt or exception text."""

    def __init__(self, code="EMPIRICAL_CHAT_ENVELOPE_INVALID"):
        self.code = code if type(code) is str and code in ERROR_CODES else "EMPIRICAL_CHAT_ENVELOPE_INVALID"
        super().__init__(self.code)


def require(value, suffix):
    if not value:
        raise ChatReceiptDecodeError("EMPIRICAL_CHAT_" + suffix) from None


def valid_id(value, pattern=IDENTIFIER):
    return type(value) is str and pattern.fullmatch(value) is not None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "ENVELOPE_INVALID")
        result[key] = value
    return result


def finite_float(text):
    value = float(text)
    require(math.isfinite(value), "ENVELOPE_INVALID")
    return value


def reject_constant(_):
    raise ChatReceiptDecodeError("EMPIRICAL_CHAT_ENVELOPE_INVALID") from None


def request_id(headers, value):
    header = headers.get("x-request-id")
    body = value.get("request_id")
    if "x-request-id" in headers:
        require(valid_id(header), "REQUEST_ID_INVALID")
    if "request_id" in value:
        require(valid_id(body), "REQUEST_ID_INVALID")
    require(header is None or body is None or header == body, "REQUEST_ID_CONFLICT")
    return header if header is not None else body


def decode_chat_receipt(raw: dict, *, expected_model: str, max_output_tokens: int, scope: str) -> dict:
    """Decode one complete empirical receipt, without any external side effect.

    output_limit_observed only compares this receipt's completion count with the
    requested count. The caller owns capture binding, tool validation and billing.
    """
    try:
        require(type(scope) is str and scope == SCOPE, "SCOPE_REJECTED")
        require(valid_id(expected_model, MODEL) and type(max_output_tokens) is int and 1 <= max_output_tokens <= 8192, "ARGUMENTS_INVALID")
        required = {"http_status", "headers", "body_base64", "received_at"}
        require(type(raw) is dict and required <= set(raw) <= required | {"body_sha256"}, "ENVELOPE_INVALID")
        require(type(raw["http_status"]) is int and raw["http_status"] == 200, "HTTP_REJECTED")
        headers = raw["headers"]
        require(type(headers) is dict and set(headers) <= {"x-request-id"}, "ENVELOPE_INVALID")
        received = raw["received_at"]
        require(type(received) is str and len(received) <= 64, "ENVELOPE_INVALID")
        require(datetime.fromisoformat(received).tzinfo is not None, "ENVELOPE_INVALID")
        encoded = raw["body_base64"]
        require(type(encoded) is str, "ENVELOPE_INVALID")
        require(len(encoded) <= 4 * ((MAX_BODY_BYTES + 2) // 3), "BYTE_LIMIT")
        data = base64.b64decode(encoded, validate=True)
        require(len(data) <= MAX_BODY_BYTES, "BYTE_LIMIT")
        require(base64.b64encode(data).decode("ascii") == encoded, "ENVELOPE_INVALID")
        if "body_sha256" in raw:
            require(valid_id(raw["body_sha256"], SHA256) and hashlib.sha256(data).hexdigest() == raw["body_sha256"], "ENVELOPE_INVALID")
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
            parse_constant=reject_constant, parse_float=finite_float)
        require(type(value) is dict, "ENVELOPE_INVALID")
        require(set(value) <= {"id", "model", "choices", "usage", "object", "created", "request_id", "system_fingerprint", "provider_model"}, "UNSUPPORTED_DIMENSION")
        require(valid_id(value.get("id")), "RESPONSE_ID_INVALID")
        require(value.get("model") == expected_model, "MODEL_MISMATCH")
        if "object" in value:
            require(value["object"] == "chat.completion", "ENVELOPE_INVALID")
        if "created" in value:
            require(type(value["created"]) is int and value["created"] >= 0, "ENVELOPE_INVALID")
        if "system_fingerprint" in value:
            require(value["system_fingerprint"] is None or valid_id(value["system_fingerprint"]), "ENVELOPE_INVALID")
        if "provider_model" in value:
            require(valid_id(value["provider_model"]), "ENVELOPE_INVALID")
        provider_request_id = request_id(headers, value)

        choices = value.get("choices")
        require(type(choices) is list and len(choices) == 1 and type(choices[0]) is dict, "CHOICE_INVALID")
        choice = choices[0]
        require(set(choice) <= {"message", "finish_reason", "index", "logprobs"}, "UNSUPPORTED_DIMENSION")
        require({"message", "finish_reason"} <= set(choice), "CHOICE_INVALID")
        if "index" in choice:
            require(type(choice["index"]) is int and choice["index"] == 0, "CHOICE_INVALID")
        if "logprobs" in choice:
            require(choice["logprobs"] is None, "CHOICE_INVALID")
        require(choice["finish_reason"] == "stop", "FINISH_REJECTED")
        message = choice["message"]
        require(type(message) is dict, "CONTENT_INVALID")
        require(set(message) <= {"role", "content", "refusal"}, "UNSUPPORTED_DIMENSION")
        require(message.get("role") == "assistant", "CONTENT_INVALID")
        require("refusal" not in message or message["refusal"] is None, "CONTENT_INVALID")
        content = message.get("content")
        require(type(content) is str and bool(content.strip()), "CONTENT_INVALID")
        try:
            require(len(content.encode("utf-8")) <= MAX_CONTENT_BYTES, "BYTE_LIMIT")
        except UnicodeError:
            raise ChatReceiptDecodeError("EMPIRICAL_CHAT_CONTENT_INVALID") from None

        usage = value.get("usage")
        base = {"prompt_tokens", "completion_tokens", "total_tokens"}
        require(type(usage) is dict and base <= set(usage), "USAGE_INVALID")
        require(set(usage) <= base | {"completion_tokens_details"}, "UNSUPPORTED_DIMENSION")
        counts = {name: usage[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")}
        require(all(type(count) is int and count >= 0 for count in counts.values()), "USAGE_INVALID")
        require(counts["prompt_tokens"] + counts["completion_tokens"] == counts["total_tokens"], "USAGE_INVALID")
        reasoning = None
        if "completion_tokens_details" in usage:
            detail = usage["completion_tokens_details"]
            require(type(detail) is dict and set(detail) == {"reasoning_tokens"}, "REASONING_INVALID")
            reasoning = detail["reasoning_tokens"]
            require(type(reasoning) is int and 0 <= reasoning <= counts["completion_tokens"], "REASONING_INVALID")
        return {"parsed": value, "content": content, "provider_request_id": provider_request_id,
            "provider_response_id": value["id"], "usage": counts, "reasoning_tokens": reasoning,
            "output_limit_observed": counts["completion_tokens"] <= max_output_tokens, "finish_reason": "stop"}
    except ChatReceiptDecodeError:
        raise
    except Exception:
        raise ChatReceiptDecodeError("EMPIRICAL_CHAT_ENVELOPE_INVALID") from None
