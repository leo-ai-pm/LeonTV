"""D16 pure experimental Chat decoding; only synthetic envelopes and tools."""
import base64
import copy
import hashlib
import importlib
import json

import pytest

SCOPE = "empirical_planner_text_once"
MODEL = "synthetic-text-model"
ACTION = '{"tool":"read_frozen_brief","arguments":{}}'


def module():
    return importlib.import_module("experiments.planner_live.chat_receipt")


def body():
    return {"id": "response_synthetic_01", "model": MODEL, "object": "chat.completion", "created": 1,
        "provider_model": "synthetic-provider/alias:v1", "system_fingerprint": None,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": ACTION, "refusal": None},
            "finish_reason": "stop", "logprobs": None}],
        "usage": {"prompt_tokens": 17, "completion_tokens": 11, "total_tokens": 28,
            "completion_tokens_details": {"reasoning_tokens": 4}}}


def raw(value=None, *, headers=None, data=None, with_sha=False):
    data = json.dumps(body() if value is None else value, ensure_ascii=False).encode() if data is None else data
    value = {"http_status": 200, "headers": {} if headers is None else headers,
        "body_base64": base64.b64encode(data).decode(), "received_at": "2026-10-23T00:00:00+00:00"}
    if with_sha:
        value["body_sha256"] = hashlib.sha256(data).hexdigest()
    return value


def decode(value, **changes):
    return module().decode_chat_receipt(value, expected_model=changes.pop("expected_model", MODEL),
        max_output_tokens=changes.pop("max_output_tokens", 2048), scope=changes.pop("scope", SCOPE), **changes)


def rejected(value, code=None, **changes):
    with pytest.raises(module().ChatReceiptDecodeError) as error:
        decode(value, **changes)
    assert str(error.value) == error.value.code
    assert str(error.value).startswith("EMPIRICAL_CHAT_")
    if code is not None:
        assert error.value.code == "EMPIRICAL_CHAT_" + code
    return error.value


def test_actual_decode_seam_accepts_missing_http_id_and_reasoning_subset():
    original = raw(with_sha=True)
    before = copy.deepcopy(original)
    result = decode(original)
    assert set(result) == {"parsed", "content", "provider_request_id", "provider_response_id", "usage",
        "reasoning_tokens", "output_limit_observed", "finish_reason"}
    assert result["provider_request_id"] is None and result["provider_response_id"] == "response_synthetic_01"
    assert result["usage"] == {"prompt_tokens": 17, "completion_tokens": 11, "total_tokens": 28}
    assert result["reasoning_tokens"] == 4 and result["usage"]["total_tokens"] == 28
    assert result["content"] == ACTION and result["finish_reason"] == "stop" and result["output_limit_observed"] is True
    assert result["parsed"]["provider_model"] == "synthetic-provider/alias:v1"
    assert result["parsed"]["model"] == MODEL and original == before
    result["parsed"]["usage"]["prompt_tokens"] = 999
    assert original == before


@pytest.mark.parametrize("header,body_id,expected", [(None, None, None), ("http_synthetic", None, "http_synthetic"),
    (None, "body_request_synthetic", "body_request_synthetic"), ("same_synthetic", "same_synthetic", "same_synthetic")])
def test_real_id_sources_are_optional_and_separate_from_response_and_platform_ids(header, body_id, expected):
    value = body()
    if body_id is not None:
        value["request_id"] = body_id
    result = decode(raw(value, headers={} if header is None else {"x-request-id": header}))
    assert result["provider_request_id"] == expected
    assert result["provider_response_id"] == value["id"] == "response_synthetic_01"


@pytest.mark.parametrize("case", ["conflict", "null_header", "empty_header", "bad_header", "null_body", "bad_body", "missing_response", "invalid_response"])
def test_conflicting_or_malformed_ids_are_not_replaced(case):
    value, headers = body(), {}
    if case == "conflict":
        value["request_id"], headers["x-request-id"] = "body_synthetic", "http_synthetic"
    elif case in {"null_header", "empty_header", "bad_header"}:
        headers["x-request-id"] = {"null_header": None, "empty_header": "", "bad_header": "SYNTHETIC PRIVATE\nID"}[case]
    elif case in {"null_body", "bad_body"}:
        value["request_id"] = None if case == "null_body" else 42
    elif case == "missing_response":
        value.pop("id")
        headers["x-request-id"] = "cannot_substitute_for_response_id"
    else:
        value["id"] = ""
    rejected(raw(value, headers=headers))


@pytest.mark.parametrize("reasoning", [None, 0, 4, 11])
def test_reasoning_is_optional_inclusive_subcount_never_added_to_usage(reasoning):
    value = body()
    if reasoning is None:
        value["usage"].pop("completion_tokens_details")
    else:
        value["usage"]["completion_tokens_details"]["reasoning_tokens"] = reasoning
    result = decode(raw(value))
    assert result["reasoning_tokens"] == reasoning and result["usage"] == {"prompt_tokens": 17, "completion_tokens": 11, "total_tokens": 28}


@pytest.mark.parametrize("case", ["bool_prompt", "float_output", "negative", "total", "missing", "reason_bool", "reason_negative", "reason_over", "reason_float", "details_null", "details_empty", "details_extra", "cache", "audio", "billing", "tools"])
def test_invalid_counts_and_unknown_billing_dimensions_fail_closed(case):
    value = body()
    usage = value["usage"]
    if case == "bool_prompt":
        usage["prompt_tokens"] = True
    elif case == "float_output":
        usage["completion_tokens"] = 11.0
    elif case == "negative":
        usage["prompt_tokens"] = -1
    elif case == "total":
        usage["total_tokens"] = 32
    elif case == "missing":
        usage.pop("total_tokens")
    elif case.startswith("reason_"):
        usage["completion_tokens_details"]["reasoning_tokens"] = {"reason_bool": True, "reason_negative": -1, "reason_over": 12, "reason_float": 4.0}[case]
    elif case == "details_null":
        usage["completion_tokens_details"] = None
    elif case == "details_empty":
        usage["completion_tokens_details"] = {}
    elif case == "details_extra":
        usage["completion_tokens_details"]["accepted_prediction_tokens"] = 0
    else:
        usage[{"cache": "prompt_tokens_details", "audio": "audio_tokens", "billing": "billed_tokens", "tools": "tool_calls"}[case]] = 0
    rejected(raw(value))


@pytest.mark.parametrize("limit,observed", [(10, False), (11, True), (2048, True)])
def test_output_limit_is_receipt_observation_and_no_billing_or_authority_claim(limit, observed):
    result = decode(raw(), max_output_tokens=limit)
    assert result["output_limit_observed"] is observed
    assert result["usage"]["completion_tokens"] == 11 and result["reasoning_tokens"] == 4
    assert not {"verified", "local_units", "held", "settled", "billing_bound_verified"} & set(result)


@pytest.mark.parametrize("case", ["http", "bool_http", "model", "alias_override", "alias_bad", "alias_null", "two_choices", "finish", "role", "blank", "array_content", "hosted_tool", "function_call", "unknown_body", "unknown_choice", "refusal", "created_bool"])
def test_envelope_structure_model_and_unknown_tool_dimensions_are_strict(case):
    value = body()
    choice = value["choices"][0]
    message = choice["message"]
    if case in {"http", "bool_http"}:
        envelope = raw(value)
        envelope["http_status"] = 524 if case == "http" else True
        rejected(envelope, "HTTP_REJECTED")
        return
    if case in {"model", "alias_override"}:
        value["model"] = "other_model"
        if case == "alias_override":
            value["provider_model"] = MODEL
    elif case in {"alias_bad", "alias_null"}:
        value["provider_model"] = "SYNTHETIC PRIVATE\nALIAS" if case == "alias_bad" else None
    elif case == "two_choices":
        value["choices"].append(copy.deepcopy(choice))
    elif case == "finish":
        choice["finish_reason"] = "length"
    elif case == "role":
        message["role"] = "tool"
    elif case == "blank":
        message["content"] = "  \n"
    elif case == "array_content":
        message["content"] = [{"type": "text", "text": ACTION}]
    elif case == "hosted_tool":
        message["tool_calls"] = []
    elif case == "function_call":
        message["function_call"] = None
    elif case == "unknown_body":
        value["billing"] = {"cost": 0}
    elif case == "unknown_choice":
        choice["tool_usage"] = 0
    elif case == "refusal":
        message["refusal"] = "SYNTHETIC refusal"
    else:
        value["created"] = True
    rejected(raw(value))


@pytest.mark.parametrize("content", ["```json\n" + ACTION + "\n```", "not JSON at all", '{"tool":"read_frozen_brief","tool":"submit_shot_plan","arguments":{}}', '{"tool":', '{"tool":"read_frozen_brief","arguments":{"extra":1}}'])
def test_opaque_business_content_reaches_original_tool_rejection_with_real_feedback(content):
    from core.planner.protocol import execute_tool

    value = body()
    value["choices"][0]["message"]["content"] = content
    result = decode(raw(value))
    assert result["content"] == content
    feedback, plan, read = execute_tool(result["content"], {"snapshot_hash": "a" * 64}, False, detailed_feedback=True)
    assert feedback["ok"] is False and feedback["code"] == "INVALID_ACTION" and feedback["validation_issues"]
    assert plan is None and read is False


def test_valid_tool_content_still_uses_original_tool_contract_after_decode():
    from core.planner.protocol import execute_tool

    source = {"snapshot_hash": "a" * 64, "script": "SYNTHETIC ONLY"}
    result = decode(raw())
    feedback, plan, read = execute_tool(result["content"], source, False, detailed_feedback=True)
    assert feedback["ok"] and feedback["source"] == source and plan is None and read


@pytest.mark.parametrize("data", [b'{"id":"first","id":"second"}', b'{"usage":{"total_tokens":1,"total_tokens":2}}', b'{"x":NaN}', b'{"x":Infinity}', b'{"created":1e309}', b'{"incomplete":', b'{} trailing', b'\xff', b'\xef\xbb\xbf{}'])
def test_outer_json_stays_strict_and_errors_do_not_echo_input(data):
    error = rejected(raw(data=data))
    assert "first" not in str(error) and not hasattr(error, "raw") and error.__cause__ is None


@pytest.mark.parametrize("case", ["invalid_base64", "bad_sha", "wrong_sha", "extra_raw", "bad_headers", "naive_time"])
def test_raw_archive_envelope_and_optional_body_sha_are_actually_verified(case):
    envelope = raw(with_sha=True)
    if case == "invalid_base64":
        envelope["body_base64"] += "!"
    elif case == "bad_sha":
        envelope["body_sha256"] = "SYNTHETIC PRIVATE SHA"
    elif case == "wrong_sha":
        envelope["body_sha256"] = "0" * 64
    elif case == "extra_raw":
        envelope["platform_log_id"] = "cannot_become_HTTP_id"
    elif case == "bad_headers":
        envelope["headers"] = {"Authorization": "SYNTHETIC-SECRET"}
    else:
        envelope["received_at"] = "2026-10-23T00:00:00"
    error = rejected(envelope)
    assert "SYNTHETIC" not in str(error)


def test_body_sha_is_over_exact_original_body_bytes_not_reserialized_json():
    data = json.dumps(body(), ensure_ascii=False, separators=(",", ":")).encode()
    envelope = raw(data=data, with_sha=True)
    assert decode(envelope)["provider_response_id"] == "response_synthetic_01"
    envelope["body_base64"] = base64.b64encode(b" " + data).decode()
    rejected(envelope)


def test_body_and_multibyte_content_boundaries_are_bytes_not_characters():
    value = body()
    value["choices"][0]["message"]["content"] = "中" * 21845 + "a"
    assert len(decode(raw(value))["content"].encode()) == 65536
    value["choices"][0]["message"]["content"] += "b"
    rejected(raw(value), "BYTE_LIMIT")
    data = json.dumps(body(), ensure_ascii=False).encode()
    exact = b" " * (1048576 - len(data)) + data
    assert decode(raw(data=exact))["provider_response_id"] == "response_synthetic_01"
    rejected(raw(data=b" " + exact), "BYTE_LIMIT")


@pytest.mark.parametrize("changes", [{"scope": "formal"}, {"scope": None}, {"expected_model": "bad\nmodel"}, {"max_output_tokens": True}, {"max_output_tokens": 0}, {"max_output_tokens": 8193}])
def test_explicit_experimental_scope_and_arguments_are_required(changes):
    rejected(raw(), **changes)


def test_decoder_is_pure_and_does_not_normalize_execute_price_or_read_state(monkeypatch):
    import builtins
    import os

    import core.execution.policy as policy
    import core.execution.transport as transport
    import core.planner.protocol as protocol

    decoder = module().decode_chat_receipt
    envelope = raw()
    before = copy.deepcopy(envelope)
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            pytest.fail("Pure decoder attempted a side effect or formal operation")

        guard.setattr(builtins, "open", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(policy, "cost", forbidden)
        guard.setattr(transport, "normalized", forbidden)
        guard.setattr(protocol, "execute_tool", forbidden)
        result = decoder(envelope, expected_model=MODEL, max_output_tokens=2048, scope=SCOPE)
    assert result["provider_request_id"] is None and envelope == before


def test_original_formal_normalizer_keeps_missing_http_id_details_and_limit_rejections():
    from test_planner_execution import policy

    from core.execution.policy import cost
    from core.execution.transport import normalized

    p = policy().frozen()
    cases = []
    missing_id = body()
    missing_id["usage"].pop("completion_tokens_details")
    cases.append((raw(missing_id), "RECEIPT_INCOMPLETE"))
    cases.append((raw(headers={"x-request-id": "http_synthetic"}), "RECEIPT_INCOMPLETE"))
    over = copy.deepcopy(missing_id)
    over["usage"] = {"prompt_tokens": 17, "completion_tokens": 2001, "total_tokens": 2018}
    cases.append((raw(over, headers={"x-request-id": "http_synthetic"}), "RECEIPT_OUT_OF_BOUNDS"))
    for envelope, expected_error in cases:
        result, error = normalized(envelope, p, cost(p["max_input_tokens"], p["max_output_tokens"], p))
        assert error == expected_error and result["local_units"] is None


@pytest.mark.parametrize("thought", [None, "", "SYNTHETIC PRIVATE THOUGHT", "中" * 21845 + "a"], ids=["null", "empty", "text", "utf8_boundary"])
def test_optional_reasoning_text_stays_only_in_private_parsed_body(thought):
    value = body()
    value["choices"][0]["message"]["reasoning_content"] = thought
    result = decode(raw(value))
    assert len(result) == 8 and result["content"] == ACTION
    assert result["parsed"]["choices"][0]["message"]["reasoning_content"] == thought
    assert "reasoning_content" not in {key for key in result if key != "parsed"}


@pytest.mark.parametrize("thought", [False, 1, [], {}, "a" * 65537, "\ud800"], ids=["bool", "int", "list", "dict", "oversize", "invalid_utf8"])
def test_reasoning_text_type_utf8_and_byte_limit_remain_strict(thought):
    value = body()
    value["choices"][0]["message"]["reasoning_content"] = thought
    data = json.dumps(value, ensure_ascii=True).encode()
    rejected(raw(data=data))


@pytest.mark.parametrize("finish,content,classification", [("stop", ACTION, "complete"), ("length", '{"partial":', "truncated"), ("length", "", "truncated")])
def test_inspection_distinguishes_complete_and_truncated_without_executable_tail(finish, content, classification):
    value = body()
    value["choices"][0]["finish_reason"] = finish
    value["choices"][0]["message"].update(content=content, reasoning_content="SYNTHETIC PRIVATE THOUGHT")
    envelope = raw(value)
    result = module().inspect_chat_receipt(envelope, expected_model=MODEL, max_output_tokens=2048, scope=SCOPE)
    assert set(result) == {"classification", "metadata", "decoded"} and result["classification"] == classification
    assert set(result["metadata"]) == {"provider_request_id", "provider_response_id", "usage", "reasoning_tokens", "output_limit_observed", "finish_reason"}
    assert "SYNTHETIC PRIVATE THOUGHT" not in json.dumps(result["metadata"])
    if classification == "truncated":
        assert result["decoded"] is None and "partial" not in json.dumps(result)
        rejected(envelope, "FINISH_REJECTED")
    else:
        assert result["decoded"] == decode(envelope)


@pytest.mark.parametrize("case", ["usage", "unknown_message", "reasoning_text", "id", "http"])
def test_truncated_inspection_still_validates_complete_envelope_before_classification(case):
    value = body()
    value["choices"][0]["finish_reason"] = "length"
    if case == "usage":
        value["usage"]["total_tokens"] += 1
    elif case == "unknown_message":
        value["choices"][0]["message"]["tool_calls"] = []
    elif case == "reasoning_text":
        value["choices"][0]["message"]["reasoning_content"] = {}
    elif case == "id":
        value.pop("id")
    envelope = raw(value)
    if case == "http":
        envelope["http_status"] = 524
    with pytest.raises(module().ChatReceiptDecodeError):
        module().inspect_chat_receipt(envelope, expected_model=MODEL, max_output_tokens=2048, scope=SCOPE)
