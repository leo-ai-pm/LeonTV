"""Fictional image descriptor/permission checks; no files, keys or network."""
import base64
import copy
import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from PIL import Image
from test_product_assets import asset_case

from core.creator.store import digest
from core.execution.image_policy import (
    KIND,
    SCOPE,
    FrozenImageRequest,
    ImagePolicyError,
    ImageToolPermit,
    ImageToolPolicy,
    MediaBudgetAnchor,
    build_image_query,
    build_image_submit,
)
from core.planner import product_assets

SYNTHETIC_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAkAAAAQCAIAAABLKsIUAAAAGUlEQVR4nGOUNPJmwAGYcEkwjMrhAvQOMwAi8QC2afs2FwAAAABJRU5ErkJggg==")


def inputs(shot_index=1):
    """Return independent explicit synthetic inputs; no authority is implied."""
    asset_input, candidate = asset_case(2)
    parent = asset_input["acceptance_payload"]
    parent["binding"]["task_id"] = "1" * 32
    report = asset_input["acceptance_report"]
    report["binding"] = copy.deepcopy(parent["binding"])
    asset_input = product_assets.build_asset_input("first_review", parent, report,
        {"asset_task_id": "SYNTHETIC-MEDIA-ASSETS", "manifest_version": 1})
    candidate["binding"] = copy.deepcopy(asset_input["binding"])
    manifest = product_assets.validate_asset_output(json.dumps(candidate, ensure_ascii=False), asset_input)
    job = product_assets.image_jobs(asset_input, manifest, expected_manifest_hash=manifest["manifest_hash"])[shot_index - 1]
    issued = datetime.now(timezone.utc) - timedelta(seconds=1)
    policy = ImageToolPolicy(id="synthetic-image-policy", version="1", origin="https://synthetic.invalid",
        max_submissions=2, max_queries=120, max_request_bytes=1048576, max_response_bytes=1048576,
        request_deadline_seconds=120, valid_until=(issued + timedelta(hours=1)).isoformat(),
        per_submission_risk_cny="1.000000", operator_risk_approved=True, validation_only=True)
    anchor = MediaBudgetAnchor(account_scope_id="synthetic-original100", parent_authorization_hash="a" * 64,
        parent_ledger_hash="b" * 64, parent_allocation_hash="c" * 64, parent_allocation_begin_hash="d" * 64,
        original_budget_cny="100", opening_risk_cny="75.37", opening_paid_call_count=47,
        scope_risk_cny="2", scope_max_calls=2, account_limit_cny="77.37", account_max_calls=49)
    permit = ImageToolPermit(operation_id="2" * 32, task_id="1" * 32, capture_id="2" * 32, nonce="3" * 32,
        action="submit", policy_hash=policy.frozen()["policy_hash"], budget_anchor_hash=anchor.frozen()["budget_anchor_hash"],
        **{key: getattr(anchor, key) for key in ("parent_authorization_hash", "parent_ledger_hash", "parent_allocation_hash", "parent_allocation_begin_hash")},
        grant_id="4" * 32, disclosure_hash="e" * 64, handoff_hash="f" * 64, provenance_hash="0" * 64,
        asset_input_hash=digest(asset_input), manifest_hash=manifest["manifest_hash"], job_hash=digest(job),
        shot_id=job["shot_id"], first_frame_version=1, **{key: job[key] for key in (
            "source_snapshot_hash", "plan_version", "plan_hash", "acceptance_input_hash", "acceptance_report_hash")},
        scope_submission_index=shot_index, query_index=0, paid_calls=1, risk_cny="1.000000",
        issued_at=issued.isoformat(), deadline_at=(issued + timedelta(seconds=120)).isoformat())
    reference = {"shot_id": "S01", "first_frame_version": 1, "mime_type": "image/png",
        "bytes_base64": base64.b64encode(SYNTHETIC_PNG).decode(), "sha256": hashlib.sha256(SYNTHETIC_PNG).hexdigest()}
    return {"policy": policy, "budget_anchor": anchor, "permit": permit, "asset_input": asset_input,
        "manifest": manifest, "shot_id": job["shot_id"], "references": [] if shot_index == 1 else [reference]}


def make_submit(shot_index=1):
    return build_image_submit(**inputs(shot_index))


def accepted_raw(request=None):
    request = request or make_submit()
    issued = datetime.fromisoformat(request.frozen()["permit"]["issued_at"])
    body = {"code": 0, "data": {"id": "synthetic-image-task-1"}}
    return {"http_status": 200, "headers": {"x-request-id": "synthetic-submit-request-1"},
        "body_base64": base64.b64encode(json.dumps(body).encode()).decode(),
        "received_at": (issued + timedelta(milliseconds=500)).isoformat()}


def make_query(submit=None):
    submit = submit or make_submit()
    raw = accepted_raw(submit)
    parent = submit.frozen()["permit"]
    issued = datetime.fromisoformat(raw["received_at"]) + timedelta(milliseconds=100)
    changed = {"permit_hash", "operation_id", "capture_id", "nonce", "action", "query_index", "paid_calls", "risk_cny", "issued_at", "deadline_at"}
    permit = {key: value for key, value in parent.items() if key not in changed}
    permit.update(operation_id="5" * 32, capture_id="5" * 32, nonce="6" * 32, action="query", query_index=1,
        paid_calls=0, risk_cny="0.000000", issued_at=issued.isoformat(), deadline_at=(issued + timedelta(seconds=120)).isoformat())
    return build_image_query(submit=submit, submit_raw=raw, permit=permit, query_index=1)


def reseal(value):
    value = copy.deepcopy(value)
    value["descriptor_hash"] = digest({key: item for key, item in value.items() if key != "descriptor_hash"})
    return value


def test_submit_uses_exact_compiled_job_without_path_url_or_additional_parameters():
    descriptor = make_submit()
    value = descriptor.frozen()
    assert descriptor.method == "POST" and descriptor.endpoint == "https://synthetic.invalid/v1/images/generations/async"
    body = json.loads(descriptor.wire)
    assert body == {"model": "gpt-image-2-all", "prompt": value["job"]["prompt"], "size": "9:16", "resolution": "1k", "quality": "medium"}
    assert value["kind"] == KIND and value["scope"] == SCOPE
    assert value["query_index"] == 0 and value["parent_submit"] is None and value["accepted_task_id"] is None
    assert descriptor.validation_only is True and descriptor.max_response_bytes == 1048576
    assert not {"billing_bound_verified", "api_key", "save_path"} & set(value)
    assert value["permit"]["paid_calls"] == 1 and value["budget_anchor"]["original_budget_cny"] == "100"


def test_reference_data_is_server_derived_from_exact_predecessor_bytes():
    descriptor = make_submit(2)
    value = descriptor.frozen()
    assert value["references"][0]["shot_id"] == "S01"
    assert json.loads(descriptor.wire)["images"] == ["data:image/png;base64," + base64.b64encode(SYNTHETIC_PNG).decode()]
    assert value["permit"]["scope_submission_index"] == 2


@pytest.mark.parametrize("change", ["mime", "webp", "invalid", "truncated", "oversized_dimensions", "oversized_bytes", "animated", "version_bool", "base64", "tuple"])
def test_reference_requires_valid_supported_single_image_bytes(change):
    arguments = inputs(2)
    reference = arguments["references"][0]
    if change == "mime":
        reference["mime_type"] = "image/jpeg"
    elif change == "webp":
        reference["mime_type"] = "image/webp"
    elif change in {"invalid", "truncated", "oversized_dimensions", "oversized_bytes", "animated"}:
        raw = b"not an image"
        if change == "truncated":
            raw = SYNTHETIC_PNG[:-20]
        elif change == "oversized_bytes":
            raw = SYNTHETIC_PNG + b"x" * 1048576
        elif change in {"oversized_dimensions", "animated"}:
            stream = BytesIO()
            image = Image.new("RGB", (4097, 1) if change == "oversized_dimensions" else (9, 16))
            options = {"save_all": True, "append_images": [Image.new("RGB", (9, 16), "red")], "duration": 100} if change == "animated" else {}
            image.save(stream, format="PNG", **options)
            raw = stream.getvalue()
        reference["bytes_base64"] = base64.b64encode(raw).decode()
        reference["sha256"] = hashlib.sha256(raw).hexdigest()
    elif change == "version_bool":
        reference["first_frame_version"] = True
    elif change == "base64":
        reference["bytes_base64"] += "\n"
    else:
        arguments["references"] = tuple(arguments["references"])
    with pytest.raises(ImagePolicyError):
        build_image_submit(**arguments)


def test_supported_jpeg_reference_passes_without_altering_the_actual_bytes():
    arguments = inputs(2)
    stream = BytesIO()
    Image.new("RGB", (9, 16), "blue").save(stream, format="JPEG")
    raw = stream.getvalue()
    arguments["references"][0].update(mime_type="image/jpeg", bytes_base64=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
    descriptor = build_image_submit(**arguments)
    assert json.loads(descriptor.wire)["images"] == ["data:image/jpeg;base64," + base64.b64encode(raw).decode()]


def test_historical_expired_descriptor_stays_inspectable_without_current_time_authority():
    arguments = inputs()
    permit = arguments["permit"].model_dump()
    policy = arguments["policy"].model_dump()
    permit.update(issued_at="2020-01-01T00:00:00+00:00", deadline_at="2020-01-01T00:02:00+00:00")
    policy["valid_until"] = "2020-01-01T01:00:00+00:00"
    permit["policy_hash"] = ImageToolPolicy.model_validate(policy).frozen()["policy_hash"]
    arguments.update(permit=permit, policy=policy)
    descriptor = build_image_submit(**arguments)
    assert FrozenImageRequest.from_frozen(descriptor.frozen()) == descriptor


def test_final_allowed_query_index_and_submission_index_preserve_own_original_limit():
    query = make_query(make_submit(2)).frozen()
    permit = {key: value for key, value in query["permit"].items() if key != "permit_hash"}
    permit["query_index"] = 120
    last = build_image_query(submit=query["parent_submit"], submit_raw=query["submit_raw"], permit=permit, query_index=120)
    assert last.frozen()["query_index"] == 120 and last.frozen()["permit"]["scope_submission_index"] == 2


def test_frozen_descriptor_is_immutable_and_detaches_all_nested_values():
    arguments = inputs()
    descriptor = build_image_submit(**arguments)
    original = descriptor.frozen()
    arguments["asset_input"]["constraints"]["max_images"] = 99
    original["policy"]["model"] = "changed"
    assert descriptor.frozen()["policy"]["model"] == "gpt-image-2-all"
    assert FrozenImageRequest.from_frozen(descriptor.frozen()) == descriptor
    with pytest.raises(FrozenInstanceError):
        descriptor._serialized = "changed"


@pytest.mark.parametrize("field,value", [("origin", "http://synthetic.invalid"), ("origin", "https://elsewhere.invalid"),
    ("model", "another-image-model"), ("ratio", "16:9"), ("resolution", "2k"), ("quality", "high"),
    ("max_submissions", 13), ("max_submissions", True), ("max_queries", 121), ("max_request_bytes", 1048577),
    ("max_response_bytes", 1048577), ("request_deadline_seconds", 121), ("validation_only", 0),
    ("risk_basis", "hard_billing_bound"), ("operator_risk_approved", False), ("schema_version", True)])
def test_policy_rejects_unsupported_models_origins_scopes_and_non_strict_bounds(field, value):
    arguments = inputs()
    policy = arguments["policy"].model_dump()
    policy[field] = value
    with pytest.raises(Exception):
        ImageToolPolicy.model_validate(policy)


def test_production_never_accepts_synthetic_origin_and_zero_risk_is_rejected():
    policy = inputs()["policy"].model_dump()
    policy["validation_only"] = False
    with pytest.raises(ImagePolicyError):
        ImageToolPolicy.model_validate(policy)
    policy["origin"] = "https://api.ai-tudou.net"
    assert ImageToolPolicy.model_validate(policy).validation_only is False
    policy["per_submission_risk_cny"] = "0"
    with pytest.raises(ImagePolicyError):
        ImageToolPolicy.model_validate(policy)


@pytest.mark.parametrize("change", ["original", "scope_risk", "account_limit", "opening_count", "scope_count", "negative", "boolean_count", "version"])
def test_parent_budget_is_inherited_exactly_and_cannot_reset_history(change):
    anchor = inputs()["budget_anchor"].model_dump()
    edits = {"original": ("original_budget_cny", "101"), "scope_risk": ("scope_risk_cny", "3"),
        "account_limit": ("account_limit_cny", "101"), "opening_count": ("opening_paid_call_count", 0),
        "scope_count": ("scope_max_calls", 13), "negative": ("opening_risk_cny", "-1"),
        "boolean_count": ("opening_paid_call_count", True), "version": ("schema_version", True)}
    field, value = edits[change]
    anchor[field] = value
    with pytest.raises(Exception):
        MediaBudgetAnchor.model_validate(anchor)


@pytest.mark.parametrize("change", ["scope", "kind", "task", "grant", "capture", "frame_version", "plan_version", "paid", "index", "query_index", "risk", "expiry", "deadline", "timezone", "anchor", "policy", "allocation", "begin", "handoff_shape", "extra"])
def test_submit_requires_explicit_exact_media_permit(change):
    arguments = inputs()
    permit = arguments["permit"].model_dump()
    if change == "scope":
        permit["scope"] = "frozen_brief_and_bounded_roles_empirical_risk_to_named_provider"
    elif change == "kind":
        permit["job_kind"] = "chat"
    elif change == "task":
        permit["task_id"] = "9" * 32
    elif change == "grant":
        permit["grant_id"] = "old-legacy-grant"
    elif change == "capture":
        permit["capture_id"] = "9" * 32
    elif change == "frame_version":
        permit["first_frame_version"] = True
    elif change == "plan_version":
        permit["plan_version"] = 2
    elif change == "paid":
        permit["paid_calls"] = 0
    elif change == "index":
        permit["scope_submission_index"] = 3
    elif change == "query_index":
        permit["query_index"] = 1
    elif change == "risk":
        permit["risk_cny"] = "0.1"
    elif change == "expiry":
        arguments["policy"] = dict(arguments["policy"].model_dump(), valid_until=permit["issued_at"])
        permit["policy_hash"] = ImageToolPolicy.model_validate(arguments["policy"]).frozen()["policy_hash"]
    elif change == "deadline":
        permit["deadline_at"] = (datetime.fromisoformat(permit["issued_at"]) + timedelta(seconds=121)).isoformat()
    elif change == "timezone":
        permit["deadline_at"] = "2030-01-01T00:00:00"
    elif change in {"anchor", "policy", "allocation", "begin"}:
        permit[{"anchor": "budget_anchor_hash", "policy": "policy_hash", "allocation": "parent_allocation_hash", "begin": "parent_allocation_begin_hash"}[change]] = "9" * 64
    elif change == "handoff_shape":
        permit["handoff_hash"] = "arbitrary-proof"
    else:
        permit["execute"] = True
    arguments["permit"] = permit
    with pytest.raises(ImagePolicyError):
        build_image_submit(**arguments)


@pytest.mark.parametrize("change", ["source", "manifest", "job", "shot", "reference", "reference_hash", "reference_path", "reference_version", "reference_duplicate", "limit", "count"])
def test_inputs_cannot_replace_compiled_job_or_internal_reference_set(change):
    arguments = inputs(2)
    if change == "source":
        arguments["asset_input"]["acceptance_payload"]["source"]["snapshot"]["script"] = "changed synthetic source"
    elif change == "manifest":
        arguments["manifest"]["frames"][1]["first_frame_prompt"] = "changed prompt"
    elif change == "job":
        arguments["permit"] = dict(arguments["permit"].model_dump(), job_hash="9" * 64)
    elif change == "shot":
        arguments["shot_id"] = "S03"
    elif change == "reference":
        arguments["references"][0]["shot_id"] = "S02"
    elif change == "reference_hash":
        arguments["references"][0]["sha256"] = "9" * 64
    elif change == "reference_path":
        arguments["references"][0]["path"] = "/tmp/unused-image.png"
    elif change == "reference_version":
        arguments["references"][0]["first_frame_version"] = 2
    elif change == "reference_duplicate":
        arguments["references"] *= 2
    elif change == "count":
        policy = dict(arguments["policy"].model_dump(), max_submissions=3)
        arguments["policy"] = policy
        arguments["permit"] = dict(arguments["permit"].model_dump(), policy_hash=ImageToolPolicy.model_validate(policy).frozen()["policy_hash"])
    else:
        policy = dict(arguments["policy"].model_dump(), max_request_bytes=128)
        arguments["policy"] = policy
        arguments["permit"] = dict(arguments["permit"].model_dump(), policy_hash=ImageToolPolicy.model_validate(policy).frozen()["policy_hash"])
    with pytest.raises(ImagePolicyError):
        build_image_submit(**arguments)


@pytest.mark.parametrize("change", ["wire", "endpoint", "method", "kind", "scope", "query_index", "parent", "task", "metadata", "extra", "seal", "tuple", "bool_schema"])
def test_rehashed_descriptor_tampering_is_rebuilt_independently(change):
    value = make_submit().frozen()
    if change == "wire":
        body = json.loads(value["wire"])
        body["n"] = 2
        value["wire"] = json.dumps(body, ensure_ascii=False)
    elif change == "endpoint":
        value["endpoint"] += "?key=unused"
    elif change == "method":
        value["method"] = "DELETE"
    elif change == "kind":
        value["kind"] = "text"
    elif change == "scope":
        value["scope"] = "old_text_scope"
    elif change == "query_index":
        value["query_index"] = 1
    elif change == "parent":
        value["parent_submit"] = copy.deepcopy(value)
    elif change == "task":
        value["accepted_task_id"] = "invented-task"
    elif change == "metadata":
        value["job"]["prompt"] = "changed"
    elif change == "extra":
        value["api_key"] = "synthetic-non-secret"
    elif change == "tuple":
        value["references"] = ()
    elif change == "bool_schema":
        value["schema_version"] = True
    else:
        value["policy"]["policy_hash"] = "9" * 64
    if change != "tuple":
        value = reseal(value)
    with pytest.raises(ImagePolicyError):
        FrozenImageRequest.from_frozen(value)


def test_binding_exposes_every_proof_and_request_identity_without_authorizing_a_call():
    value = make_submit()
    binding = value.binding()
    assert binding["kind"] == KIND and binding["scope"] == SCOPE and binding["method"] == "POST"
    assert binding["descriptor_hash"] == value.descriptor_hash and binding["wire_hash"] == digest(value.wire)
    assert binding["operation_id"] == binding["capture_id"] == "2" * 32
    assert binding["parent_descriptor_hash"] is binding["submit_raw_hash"] is None
    permit = value.frozen()["permit"]
    assert all(binding[key] == item for key, item in permit.items() if key.endswith("_hash"))


def test_query_is_separate_read_only_get_bound_to_actual_accepted_submit():
    query = make_query()
    value = query.frozen()
    parent = value["parent_submit"]
    assert query.method == "GET" and query.wire == ""
    assert query.endpoint == "https://synthetic.invalid/v1/tasks/synthetic-image-task-1"
    assert value["accepted_task_id"] == "synthetic-image-task-1" and value["query_index"] == 1
    assert value["permit"]["paid_calls"] == 0 and value["permit"]["risk_cny"] == "0.000000"
    assert value["permit"]["operation_id"] != parent["permit"]["operation_id"]
    assert query.binding()["parent_descriptor_hash"] == parent["descriptor_hash"]
    assert query.binding()["submit_raw_hash"] == digest(value["submit_raw"])
    assert FrozenImageRequest.from_frozen(value) == query


@pytest.mark.parametrize("change", ["unknown", "conflicting_task", "raw_time", "raw_too_late", "raw_http", "task_path", "parent_query", "same_operation", "same_nonce", "paid", "query_index", "query_limit", "grant", "job", "task_endpoint", "parent_metadata"])
def test_query_never_invents_task_or_replays_paid_permission(change):
    query = make_query()
    value = query.frozen()
    submit, raw, permit, index = value["parent_submit"], value["submit_raw"], value["permit"], value["query_index"]
    permit = {key: item for key, item in permit.items() if key != "permit_hash"}
    if change in {"unknown", "conflicting_task", "task_path"}:
        body = json.loads(base64.b64decode(raw["body_base64"]))
        if change == "unknown":
            body = {"code": 0, "data": {}}
        elif change == "conflicting_task":
            body["data"]["task_id"] = "another-task"
        else:
            body["data"]["id"] = "../another-task"
        raw["body_base64"] = base64.b64encode(json.dumps(body).encode()).decode()
    elif change == "raw_time":
        raw["received_at"] = (datetime.fromisoformat(submit["permit"]["issued_at"]) - timedelta(seconds=1)).isoformat()
    elif change == "raw_too_late":
        raw["received_at"] = (datetime.fromisoformat(permit["issued_at"]) + timedelta(seconds=1)).isoformat()
    elif change == "raw_http":
        raw["http_status"] = 502
    elif change == "parent_query":
        submit = query.frozen()
    elif change == "same_operation":
        permit["operation_id"] = permit["capture_id"] = submit["permit"]["operation_id"]
    elif change == "same_nonce":
        permit["nonce"] = submit["permit"]["nonce"]
    elif change == "paid":
        permit["paid_calls"] = 1
    elif change == "query_index":
        index = 2
    elif change == "query_limit":
        index = permit["query_index"] = 121
    elif change == "grant":
        permit["grant_id"] = "9" * 32
    elif change == "job":
        permit["job_hash"] = "9" * 64
    elif change == "task_endpoint":
        changed = query.frozen()
        changed["endpoint"] = "https://synthetic.invalid/v1/tasks/another-task"
        with pytest.raises(ImagePolicyError):
            FrozenImageRequest.from_frozen(reseal(changed))
        return
    else:
        changed = query.frozen()
        changed["job"]["prompt"] = "changed"
        with pytest.raises(ImagePolicyError):
            FrozenImageRequest.from_frozen(reseal(changed))
        return
    with pytest.raises(ImagePolicyError):
        build_image_query(submit=submit, submit_raw=raw, permit=permit, query_index=index)
