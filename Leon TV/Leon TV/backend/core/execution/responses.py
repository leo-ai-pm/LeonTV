"""Strict stateless Responses text subset; no hosted tools or reasoning replay."""
import base64
import hashlib
import json
import re

from core.execution.policy import cost
from core.planner.protocol import parse_json

STATUSES = {"completed", "incomplete", "failed", "in_progress", "queued", "cancelled"}
ID = re.compile(r"[A-Za-z0-9_.:/-]{1,128}")


def request_wire(messages, policy):
    inputs = [{"role": "user" if message["role"] == "tool" else message["role"],
               "content": json.dumps(message["content"], ensure_ascii=False)} for message in messages]
    value = {"model": policy["model"], "input": inputs, "max_output_tokens": policy["max_output_tokens"],
             "stream": False, "store": False, "background": False, "tools": [], "tool_choice": "none",
             "parallel_tool_calls": False, "truncation": "disabled", "service_tier": "default"}
    return json.dumps(value, ensure_ascii=False)


def valid_id(value):
    return isinstance(value, str) and bool(ID.fullmatch(value))


def valid_text(value, limit=1048576):
    return isinstance(value, str) and len(value.encode("utf-8")) <= limit


def check_reasoning(item):
    if set(item) - {"id", "type", "summary", "content", "status", "encrypted_content"} or not isinstance(item.get("summary"), list):
        return False
    if "status" in item and item["status"] != "completed":
        return False
    for field, kind in [("summary", "summary_text"), ("content", "reasoning_text")]:
        values = item.get(field)
        if field == "content" and values is None:
            continue
        if not isinstance(values, list) or len(values) > 64:
            return False
        if any(not isinstance(value, dict) or set(value) != {"type", "text"} or value["type"] != kind or not valid_text(value["text"]) for value in values):
            return False
    encrypted = item.get("encrypted_content")
    return encrypted is None or valid_text(encrypted)


def output_text(value, byte_limit):
    outputs = value.get("output")
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= 16:
        return None, "RECEIPT_INCOMPLETE"
    text, seen = None, set()
    for item in outputs:
        if not isinstance(item, dict) or not valid_id(item.get("id")) or item["id"] in seen:
            return None, "RECEIPT_INCOMPLETE"
        seen.add(item["id"])
        if item.get("type") == "reasoning":
            if not check_reasoning(item):
                return None, "RECEIPT_INCOMPLETE"
            continue
        if item.get("type") != "message" or text is not None:
            return None, "RECEIPT_INCOMPLETE"
        if set(item) - {"id", "type", "status", "role", "content", "phase"} or item.get("role") != "assistant" or item.get("phase") not in {None, "final_answer"}:
            return None, "RECEIPT_INCOMPLETE"
        if item.get("status") != "completed":
            return None, "RECEIPT_TRUNCATED"
        content = item.get("content")
        if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
            return None, "RECEIPT_INCOMPLETE"
        block = content[0]
        if block.get("type") != "output_text" or set(block) - {"type", "text", "annotations", "logprobs"}:
            return None, "RECEIPT_INCOMPLETE"
        if block.get("annotations", []) != [] or block.get("logprobs", []) not in (None, []):
            return None, "RECEIPT_INCOMPLETE"
        text = block.get("text")
        if not valid_text(text, byte_limit):
            return None, "RECEIPT_OUT_OF_BOUNDS"
        if not text.strip():
            return None, "RECEIPT_INCOMPLETE"
    if text is None or "output_text" in value and value["output_text"] != text:
        return None, "RECEIPT_INCOMPLETE"
    return text, None


def detail_tokens(usage, input_tokens, output_tokens):
    cached, reasoning = None, None
    details = usage.get("input_tokens_details")
    if details is not None:
        if not isinstance(details, dict) or set(details) - {"cached_tokens", "cache_write_tokens"} or "cached_tokens" not in details:
            return None, None, "RECEIPT_INCOMPLETE"
        cached, written = details["cached_tokens"], details.get("cache_write_tokens", 0)
        if any(type(count) is not int or count < 0 for count in [cached, written]) or cached + written > input_tokens:
            return None, None, "RECEIPT_OUT_OF_BOUNDS"
        if written:
            return None, None, "RECEIPT_INCOMPLETE"  # Write pricing is not supported.
    details = usage.get("output_tokens_details")
    if details is not None:
        if not isinstance(details, dict) or set(details) != {"reasoning_tokens"}:
            return None, None, "RECEIPT_INCOMPLETE"
        reasoning = details["reasoning_tokens"]
        if type(reasoning) is not int or not 0 <= reasoning <= output_tokens:
            return None, None, "RECEIPT_OUT_OF_BOUNDS"
    return cached, reasoning, None


def normalized_response(raw, policy, hold_units, input_upper):
    result = {"provider_request_id": None, "provider_response_id": None, "input_tokens": None, "output_tokens": None,
              "finish_reason": None, "content": None, "content_hash": None, "local_units": None,
              "response_status": None, "cached_input_tokens": None, "reasoning_output_tokens": None}
    try:
        if raw["http_status"] != 200:
            return result, "RESPONSE_UNCONFIRMED"
        value = parse_json(base64.b64decode(raw["body_base64"], validate=True))
        if not isinstance(value, dict):
            return result, "RECEIPT_INCOMPLETE"
        status = value.get("status")
        result["response_status"] = status if isinstance(status, str) and status in STATUSES else None
        for key, candidate in [("provider_request_id", raw["headers"].get("x-request-id") or value.get("request_id")), ("provider_response_id", value.get("id"))]:
            if valid_id(candidate):
                result[key] = candidate
        if value.get("object") != "response" or "choices" in value:
            return result, "RECEIPT_INCOMPLETE"
        if status == "incomplete" or value.get("incomplete_details") is not None:
            return result, "RECEIPT_TRUNCATED"
        if status != "completed" or value.get("error") is not None:
            return result, "RESPONSE_UNCONFIRMED"
        if value.get("model") != policy["model"]:
            return result, "MODEL_MISMATCH"
        if value.get("service_tier") not in (None, "default"):
            return result, "RECEIPT_INCOMPLETE"
        # Token/cache dimensions outside the supported usage object cannot be
        # priced as ordinary input, even when the text and inner usage are valid.
        billing = re.compile(r"(?:^|_)(?:billing|billable|cost|price|pricing|charge|charges|fee|fees|invoice|bill|metering|cache|cached|caching|token|tokens)(?:$|_)")
        if "max_output_tokens" in value and (type(value["max_output_tokens"]) is not int or value["max_output_tokens"] != policy["max_output_tokens"]):
            return result, "RECEIPT_INCOMPLETE"
        if any(item is not None and key != "max_output_tokens" and (billing.search(key) or key in {"usage_details", "tool_usage", "audio_usage", "image_usage"}) for key, item in value.items()):
            return result, "RECEIPT_INCOMPLETE"
        if not result["provider_request_id"] or not result["provider_response_id"]:
            return result, "RECEIPT_INCOMPLETE"
        for field in ["store", "background", "parallel_tool_calls"]:
            if field in value and value[field] is not False:
                return result, "RECEIPT_INCOMPLETE"
        if value.get("previous_response_id") is not None or value.get("conversation") is not None:
            return result, "RECEIPT_INCOMPLETE"
        if "tools" in value and value["tools"] != [] or "tool_choice" in value and value["tool_choice"] != "none" or "truncation" in value and value["truncation"] != "disabled":
            return result, "RECEIPT_INCOMPLETE"
        content, error = output_text(value, policy["max_output_bytes"])
        if error:
            return result, error
        usage = value.get("usage")
        required = {"input_tokens", "output_tokens", "total_tokens"}
        if not isinstance(usage, dict) or not required <= set(usage) or set(usage) - required - {"input_tokens_details", "output_tokens_details"}:
            return result, "RECEIPT_INCOMPLETE"
        counts = [usage[key] for key in ["input_tokens", "output_tokens", "total_tokens"]]
        if any(type(count) is not int or count < 0 for count in counts):
            return result, "RECEIPT_OUT_OF_BOUNDS"
        input_tokens, output_tokens, total_tokens = counts
        if input_tokens > min(policy["max_input_tokens"], input_upper if input_upper is not None else policy["max_input_tokens"]) or output_tokens > policy["max_output_tokens"] or total_tokens != input_tokens + output_tokens:
            return result, "RECEIPT_OUT_OF_BOUNDS"
        cached, reasoning, error = detail_tokens(usage, input_tokens, output_tokens)
        if error:
            return result, error
        amount = cost(input_tokens, output_tokens, policy)
        if amount > hold_units:
            return result, "RECEIPT_OUT_OF_BOUNDS"
        result.update({"input_tokens": input_tokens, "output_tokens": output_tokens, "cached_input_tokens": cached,
                       "reasoning_output_tokens": reasoning, "content": content, "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(), "local_units": amount})
        return result, None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, ArithmeticError, RecursionError):
        return result, "RECEIPT_INCOMPLETE"
