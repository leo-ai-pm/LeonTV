"""Versioned reference proofs in the original journal, never paid generation."""
import base64
import copy
import hashlib
import io

import pytest
from media_ledger_fixture import authorize, image_fee, media_case, posts, reserve
from PIL import Image
from test_execution_empirical import empirical_env as empirical_env
from test_reference_derivation import source_for

from core.creator.store import CreatorError, digest
from core.execution import media_ledger, reference_derivation
from core.execution.image_policy import ImageToolPolicy, build_image_submit
from core.execution.media_runtime import MediaExecution
from core.execution.reference_derivation import ReferenceProfile
from core.execution.store import validate_state


def profiled_case(empirical_env, *, allow_lossy=False, max_wire=None):
    case = media_case(empirical_env)
    policy = case.policy
    if max_wire is not None:
        policy = ImageToolPolicy.model_validate({**case.policy.model_dump(), "max_request_bytes": max_wire})
    case.policy = policy
    profile = ReferenceProfile(allow_lossy_jpeg=allow_lossy).frozen()

    def authority(scope):
        return case.guard["allowed"] and digest(scope["policy"]) == digest(policy.frozen()) and digest(scope["anchor"]) == digest(case.anchor.frozen())

    case.media = MediaExecution(case.env.runtime, policy=policy, budget_anchor=case.anchor, manifest=case.manifest,
        authority_guard=authority, fee_source=case.source, receipt_capture=case.settings, reference_profile=profile)
    return case


def deliver_first(case, grant, source=None):
    operation = reserve(case, grant)
    submitted = case.media.submit(case.task["id"], operation["id"])["operation"]
    image_fee(case, submitted)
    query = case.media.query(case.task["id"], operation["id"], key="derived-reference-query-001")["query"]
    source = source or source_for()
    case.media.record_delivery(case.task["id"], operation["id"], query["id"], base64.b64decode(source["bytes_base64"]))
    return operation, query


def test_default_profile_retains_d30_scope_quote_key_and_operation_contract(empirical_env):
    case = media_case(empirical_env)
    quote, grant = authorize(case)
    operation = reserve(case, grant)
    payload = case.media.store.read_only()
    scope = next(record for record in payload["media"].values() if record["kind"] == "scope")
    identity = {"task_id": scope["task_id"], "policy_hash": scope["policy"]["policy_hash"],
                "budget_anchor_hash": scope["anchor"]["budget_anchor_hash"], "manifest_hash": scope["manifest"]["manifest_hash"],
                "handoff_hash": scope["handoff"]["handoff_hash"]}
    assert scope["id"] == digest(identity)[:32]
    assert "reference_profile" not in scope and "reference_profile" not in quote["disclosure"]
    assert "reference_profile_hash" not in operation and "reference_derivations" not in operation
    assert set(scope) == media_ledger.COMMON | media_ledger.FIELDS["scope"]
    assert set(operation) == media_ledger.COMMON | media_ledger.FIELDS["operation"]
    assert quote["disclosure_hash"] == digest({key: value for key, value in quote.items() if key != "disclosure_hash"})
    key = payload["media"]["key:quote:synthetic-media-quote-001"]
    assert key["request"] == {"task_id": case.task["id"]} and key["request_hash"] == digest(key["request"])
    assert not media_ledger.records(payload, "reference_derivation") and posts(case) == []


def test_new_profile_is_disclosed_and_webp_proof_is_born_with_exact_reserved_post(empirical_env):
    case = profiled_case(empirical_env)
    old_core = copy.deepcopy(case.media.store.read_only())
    quote, grant = authorize(case)
    assert quote["disclosure"]["reference_profile"]["allow_lossy_jpeg"] is False
    first, query = deliver_first(case, grant)
    before = case.media.store.read_only()
    original = copy.deepcopy(before["media"]["delivery:" + before["media"]["operation:" + first["id"]]["delivery_id"]])
    operation = reserve(case, grant, "S02", "derived-reference-post-002")
    after = case.media.store.read_only()
    proofs = media_ledger.records(after, "reference_derivation")
    assert len(proofs) == 1 and operation["reference_profile_hash"] == quote["disclosure"]["reference_profile"]["profile_hash"]
    proof = proofs[0]["proof"]
    assert proof["target_operation_id"] == operation["id"] and proof["source_operation_id"] == first["id"]
    assert proof["source_query_id"] == query["id"] and proof["source_delivery_id"] == original["id"]
    assert proof["source_sha256"] == original["delivery"]["delivery_sha256"]
    assert proof["source_format"] == "WEBP" and proof["output_format"] == "PNG" and proof["pixel_equivalent"] is True
    assert operation["descriptor"]["references"][0]["sha256"] == proof["output_sha256"]
    assert operation["reference_derivations"] == [{"id": proofs[0]["id"], "derivation_hash": proof["derivation_hash"]}]
    assert after["media"]["delivery:" + original["id"]] == original
    assert all(digest(after[key]) == digest(value) for key, value in old_core.items())
    assert len(posts(case)) == 2
    state = case.media.store.path("state.json").read_text()
    assert validate_state(__import__("json").loads(state))["media"]["operation:" + operation["id"]] == operation
    assert case.media.reserve_submit(case.task["id"], grant["id"], "S02", key="derived-reference-post-002")["replayed"] is True
    assert len(media_ledger.records(case.media.store.read_only(), "reference_derivation")) == 1


def test_conversion_failure_does_not_create_a_paid_intent_or_reference_record(empirical_env):
    case = profiled_case(empirical_env, max_wire=65000)
    _, grant = authorize(case)
    deliver_first(case, grant, source_for(size=(180, 320), noise=True))
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        reserve(case, grant, "S02", "conversion-cannot-fit-002")
    assert error.value.code == "MEDIA_REFERENCE_WIRE_LIMIT"
    assert case.media.store.path("state.json").read_bytes() == before
    assert not media_ledger.records(case.media.store.read_only(), "reference_derivation") and len(posts(case)) == 2


def test_profile_never_accepts_agent_supplied_reference_bytes(empirical_env):
    case = profiled_case(empirical_env)
    _, grant = authorize(case)
    deliver_first(case, grant)
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        case.media.reserve_submit(case.task["id"], grant["id"], "S02", references=[{"shot_id": "S01", "bytes_base64": "arbitrary"}],
                                  key="agent-injected-ref-002")
    assert error.value.code == "MEDIA_REFERENCE_REQUIRED"
    assert case.media.store.path("state.json").read_bytes() == before


def test_current_birth_rejects_rehashed_unnecessary_lossy_representation(empirical_env):
    import json

    case = profiled_case(empirical_env, allow_lossy=True)
    _, grant = authorize(case)
    deliver_first(case, grant)
    operation = reserve(case, grant, "S02", "tamper-birth-post-002")
    state = json.loads(case.media.store.path("state.json").read_text())
    delta = state["frames"][-1]["delta"]["media"]
    stored_op = delta["operation:" + operation["id"]]
    reference_record = next(record for record in delta.values() if record["kind"] == "reference_derivation")
    proof = reference_record["proof"]
    output = io.BytesIO()
    Image.new("RGB", (90, 160), "red").save(output, format="JPEG", quality=85, subsampling=2, optimize=True, progressive=False)
    raw = output.getvalue()
    rgba = Image.open(io.BytesIO(raw)).convert("RGBA")
    proof.update(output_bytes_base64=base64.b64encode(raw).decode(), output_sha256=hashlib.sha256(raw).hexdigest(),
                 output_size_bytes=len(raw), output_format="JPEG", output_mime_type="image/jpeg",
                 output_decoded_pixel_sha256=hashlib.sha256(b"90x160|RGBA8|" + rgba.tobytes()).hexdigest(), pixel_equivalent=False)
    proof["transform"].update(method="jpeg", quality=85, subsampling=2)
    proof["local_attempts"].append({"method": "jpeg", "quality": 85, "sha256": proof["output_sha256"], "size_bytes": len(raw)})
    proof["derivation_hash"] = digest({key: value for key, value in proof.items() if key != "derivation_hash"})
    stored_op["reference_derivations"][0]["derivation_hash"] = proof["derivation_hash"]
    data = stored_op["descriptor"]
    refs = copy.deepcopy(data["references"])
    refs[0].update(bytes_base64=proof["output_bytes_base64"], sha256=proof["output_sha256"], mime_type="image/jpeg")
    stored_op["descriptor"] = build_image_submit(policy=data["policy"], budget_anchor=data["budget_anchor"], permit=data["permit"],
        asset_input=data["asset_input"], manifest=data["manifest"], shot_id="S02", references=refs).frozen()
    previous = None
    for frame in state["frames"]:
        frame["previous_hash"] = previous
        frame["hash"] = digest({key: value for key, value in frame.items() if key != "hash"})
        previous = frame["hash"]
    state["head_hash"] = previous
    with pytest.raises(CreatorError):
        validate_state(state)


def test_historical_encoder_change_preserves_stored_representation_bytes(empirical_env, monkeypatch):
    case = profiled_case(empirical_env)
    _, grant = authorize(case)
    deliver_first(case, grant)
    reserve(case, grant, "S02", "historical-ref-post-002")
    before = case.media.store.path("state.json").read_bytes()
    upgraded = dict(reference_derivation.encoder_identity(), pillow="synthetic-upgraded-encoder")
    monkeypatch.setattr(reference_derivation, "encoder_identity", lambda: upgraded)
    monkeypatch.setattr(media_ledger, "encoder_identity", lambda: upgraded)
    assert case.media.get(case.task["id"])["media"]["paid_call_count"] == 2
    assert case.media.store.path("state.json").read_bytes() == before


def test_legacy_scope_keeps_the_original_eleven_mib_delivery_rejection(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        case.media.record_delivery(case.task["id"], operation["id"], "f" * 32, b"x" * (11 * 1048576))
    assert error.value.code == "MEDIA_DELIVERY_TOO_LARGE"
    assert case.media.store.path("state.json").read_bytes() == before and posts(case) == []


def test_new_reference_profile_cannot_upgrade_a_legacy_grant_or_rewrite_its_quote(empirical_env):
    case = media_case(empirical_env)
    quote, grant = authorize(case)
    before = case.media.store.path("state.json").read_bytes()
    upgraded = MediaExecution(case.env.runtime, policy=case.policy, budget_anchor=case.anchor, manifest=case.manifest,
        authority_guard=case.authority, fee_source=case.source, receipt_capture=case.settings,
        reference_profile=ReferenceProfile(allow_lossy_jpeg=True).frozen())
    assert upgraded.quote(case.task["id"], key="synthetic-media-quote-001")["quote"] == quote
    with pytest.raises(CreatorError) as error:
        upgraded.reserve_submit(case.task["id"], grant["id"], "S01", key="upgrade-grant-must-fail")
    assert error.value.code == "MEDIA_POLICY_STALE"
    assert case.media.store.path("state.json").read_bytes() == before and posts(case) == []
