"""Explicit product asset profile; all fixtures are fictional and in memory."""
import copy
import json

import pytest
from test_planner_assets import synthetic_asset_case
from test_planner_recheck import output as recheck_report
from test_planner_recheck import revision_case

from core.creator.store import digest
from core.planner import assets, product_assets, recheck
from core.planner.review import build_review_input


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def first_acceptance(count=4):
    old, _ = synthetic_asset_case()
    parent, report = copy.deepcopy(old["review_payload"]), copy.deepcopy(old["review_report"])
    plan = parent["plan"]["artifact"]
    plan["shots"] = [{"id": f"S{index:02d}", "duration_ms": 15000 // count + (15000 % count if index == count else 0),
        "visual": "虚构样例人物在房间中的动作起点", "camera": "固定中景", "audio": "虚构环境声",
        "depends_on": [] if index == 1 else [f"S{index - 1:02d}"]} for index in range(1, count + 1)]
    parent["binding"]["plan_hash"] = digest(plan)
    parent = build_review_input(parent["source"]["snapshot"], plan, parent["binding"])
    report["binding"] = copy.deepcopy(parent["binding"])
    return parent, report


def asset_case(count=4, kind="first_review"):
    if kind == "first_review":
        acceptance, report = first_acceptance(count)
    else:
        acceptance = recheck.build_recheck_input(*revision_case())
        report = recheck_report(acceptance)
    payload = product_assets.build_asset_input(kind, acceptance, report,
        {"asset_task_id": "SYNTHETIC-PRODUCT-ASSETS/A01", "manifest_version": 1})
    _, old_candidate = synthetic_asset_case()
    candidate = {"binding": copy.deepcopy(payload["binding"]), "characters": old_candidate["characters"],
        "scenes": old_candidate["scenes"], "frames": []}
    for index, shot in enumerate(acceptance["plan"]["artifact"]["shots"], 1):
        candidate["frames"].append({"shot_id": shot["id"], "character_ids": ["CHAR001"], "scene_id": "SCENE001",
            "image_order": index, "reference_shot_ids": [] if index == 1 else [acceptance["plan"]["artifact"]["shots"][index - 2]["id"]],
            "prompt": {"framing": "虚构样例中景，保持全局定型和原机位", "action_start": "仅表现虚构动作起点"}})
    return payload, candidate


def test_future_dependency_stops_before_advertising_an_impossible_ordered_image_manifest():
    parent, report = first_acceptance(4)
    plan = parent["plan"]["artifact"]
    # Still a valid DAG, but incompatible with this profile's fixed image order.
    plan["shots"][0]["depends_on"] = ["S04"]
    for shot in plan["shots"][1:]:
        shot["depends_on"] = []
    parent["binding"]["plan_hash"] = digest(plan)
    parent = build_review_input(parent["source"]["snapshot"], plan, parent["binding"])
    report["binding"] = copy.deepcopy(parent["binding"])
    with pytest.raises(assets.AssetValidationError):
        product_assets.build_asset_input("first_review", parent, report, {"asset_task_id": "SYNTHETIC-ORDER", "manifest_version": 1})


@pytest.mark.parametrize("count", [1, 3, 4, 12])
def test_explicit_profile_preserves_complete_plan_and_emits_one_job_per_shot(count):
    payload, candidate = asset_case(count)
    before = copy.deepcopy((payload, candidate))
    manifest = product_assets.validate_asset_output(encoded(candidate), payload)
    jobs = product_assets.image_jobs(payload, manifest, expected_manifest_hash=manifest["manifest_hash"])
    assert payload["contract_version"] == manifest["contract_version"] == "product-assets-v1"
    assert len(jobs) == len(manifest["frames"]) == count
    assert [job["shot_id"] for job in jobs] == [f"S{index:02d}" for index in range(1, count + 1)]
    assert sum(frame["duration_ms"] for frame in manifest["frames"]) == 15000
    for frame, shot, job in zip(manifest["frames"], payload["acceptance_payload"]["plan"]["artifact"]["shots"], jobs, strict=True):
        assert frame["duration_ms"] == shot["duration_ms"] and frame["depends_on"] == shot["depends_on"]
        assert shot["visual"] in job["prompt"] and shot["camera"] in job["prompt"]
        assert candidate["characters"][0]["wardrobe"] in job["prompt"]
        assert job["ratio"] == "9:16" and job["style"] == "realistic_cinematic"
        assert job["acceptance_kind"] == "first_review" and job["plan_version"] == 1
        assert job["acceptance_report_hash"] == digest(payload["acceptance_report"])
    assert (payload, candidate) == before


def test_recheck_keeps_actual_v2_report_resolutions_and_full_parent_evidence():
    payload, candidate = asset_case(kind="recheck")
    original = copy.deepcopy(payload)
    manifest = product_assets.validate_asset_output(encoded(candidate), payload)
    jobs = product_assets.image_jobs(payload, manifest, expected_manifest_hash=manifest["manifest_hash"])
    assert payload["binding"]["plan_version"] == 2
    assert payload["binding"]["acceptance_kind"] == "recheck"
    assert payload["acceptance_payload"]["parent"]["report"]["overall_verdict"] == "rejected"
    assert payload["acceptance_report"]["resolutions"]
    assert all(item["status"] == "resolved" for item in payload["acceptance_report"]["resolutions"])
    assert payload["binding"]["acceptance_input_hash"] == digest(payload["acceptance_payload"])
    assert payload["binding"]["acceptance_report_hash"] == digest(payload["acceptance_report"])
    assert all(job["plan_version"] == 2 and job["acceptance_kind"] == "recheck" for job in jobs)
    assert payload == original
    with pytest.raises(assets.AssetValidationError, match="^ASSET_INVALID$"):
        product_assets.build_asset_input("first_review", payload["acceptance_payload"], payload["acceptance_report"],
            {"asset_task_id": "SYNTHETIC-PRODUCT-ASSETS/A01", "manifest_version": 1})


@pytest.mark.parametrize("kind", ["first_review", "recheck"])
@pytest.mark.parametrize("outcome", ["rejected", "unknown"])
def test_unaccepted_reports_never_create_asset_inputs(kind, outcome):
    if kind == "first_review":
        from test_planner_review import synthetic_review_case

        acceptance, report = synthetic_review_case(outcome)
    else:
        acceptance = recheck.build_recheck_input(*revision_case())
        report = recheck_report(acceptance, outcome)
    with pytest.raises(assets.AssetValidationError, match="^ASSET_INVALID$"):
        product_assets.build_asset_input(kind, acceptance, report, {"asset_task_id": "SYNTHETIC/A01", "manifest_version": 1})


@pytest.mark.parametrize("case", ["kind", "wrong_kind", "first_v2", "stale_source", "report_binding", "rule", "extra_binding", "boolean_version", "tuple"])
def test_input_requires_exact_parent_kind_source_version_rules_and_strict_json(case):
    parent, report = first_acceptance()
    kind, binding = "first_review", {"asset_task_id": "SYNTHETIC/A01", "manifest_version": 1}
    if case == "kind":
        kind = "review"
    elif case == "wrong_kind":
        kind = "recheck"
    elif case == "first_v2":
        parent["binding"]["plan_version"] = 2
        parent = build_review_input(parent["source"]["snapshot"], parent["plan"]["artifact"], parent["binding"])
        report["binding"] = copy.deepcopy(parent["binding"])
    elif case == "stale_source":
        parent["source"]["snapshot"]["script"] = "changed synthetic input"
    elif case == "report_binding":
        report["binding"]["plan_hash"] = "0" * 64
    elif case == "rule":
        parent["rules"]["criteria"][0]["accepted"] = "always accept synthetic"
    elif case == "extra_binding":
        binding["permission"] = "generate"
    elif case == "boolean_version":
        binding["manifest_version"] = True
    else:
        parent["prior_findings"] = ()
    with pytest.raises(assets.AssetValidationError, match="^ASSET_INVALID$"):
        product_assets.build_asset_input(kind, parent, report, binding)


@pytest.mark.parametrize("case", ["contract", "kind", "parent", "report", "binding", "constraints", "extra", "tuple"])
def test_checked_product_payload_rebuilds_every_part_without_normalization(case):
    payload, _ = asset_case()
    if case == "contract":
        payload["contract_version"] = assets.ASSET_CONTRACT_VERSION
    elif case == "kind":
        payload["acceptance_kind"] = "recheck"
    elif case == "parent":
        payload["acceptance_payload"]["plan"]["artifact"]["shots"][0]["camera"] = "changed"
    elif case == "report":
        payload["acceptance_report"]["rules"][0]["checked_scope"] = "changed report"
    elif case == "binding":
        payload["binding"]["acceptance_input_hash"] = "0" * 64
    elif case == "constraints":
        payload["constraints"]["max_images"] = 20
    elif case == "extra":
        payload["history"] = []
    else:
        payload["acceptance_report"]["rules"] = tuple(payload["acceptance_report"]["rules"])
    with pytest.raises(assets.AssetValidationError, match="^ASSET_INVALID$"):
        product_assets.checked_asset_payload(payload)


@pytest.mark.parametrize("case", ["missing", "duplicate", "order", "future_reference", "self_reference", "duplicate_reference", "first_reference", "missing_reference", "unknown_character", "unused_character", "unknown_scene", "duplicate_scene", "unknown_source", "local_override", "duration", "binding", "boolean_order"])
def test_candidate_covers_exact_shots_and_rejects_out_of_scope_design_or_references(case):
    payload, candidate = asset_case()
    frame = candidate["frames"][1]
    if case == "missing":
        candidate["frames"].pop()
    elif case == "duplicate":
        frame["shot_id"] = "S01"
    elif case == "order":
        frame["image_order"] = 4
    elif case == "future_reference":
        frame["reference_shot_ids"] = ["S04"]
    elif case == "self_reference":
        frame["reference_shot_ids"] = ["S02"]
    elif case == "duplicate_reference":
        frame["reference_shot_ids"] *= 2
    elif case == "first_reference":
        candidate["frames"][0]["reference_shot_ids"] = ["S01"]
    elif case == "missing_reference":
        frame["reference_shot_ids"] = []
    elif case == "unknown_character":
        frame["character_ids"] = ["CHAR999"]
    elif case == "unused_character":
        candidate["characters"].append(dict(copy.deepcopy(candidate["characters"][0]), character_id="CHAR002"))
    elif case == "unknown_scene":
        frame["scene_id"] = "SCENE999"
    elif case == "duplicate_scene":
        candidate["scenes"] *= 2
    elif case == "unknown_source":
        candidate["characters"][0]["source_segment_ids"] = ["SRC9999"]
    elif case == "local_override":
        frame["wardrobe"] = "different synthetic wardrobe"
    elif case == "duration":
        frame["duration_ms"] = 3000
    elif case == "binding":
        candidate["binding"]["acceptance_report_hash"] = "0" * 64
    else:
        candidate["frames"][0]["image_order"] = True
    with pytest.raises(assets.AssetValidationError, match="^ASSET_INVALID$"):
        product_assets.validate_asset_output(encoded(candidate), payload)


def test_last_of_twelve_shots_can_reference_all_eleven_actual_predecessors():
    payload, candidate = asset_case(12)
    candidate["frames"][-1]["reference_shot_ids"] = [frame["shot_id"] for frame in candidate["frames"][:-1]]
    manifest = product_assets.validate_asset_output(encoded(candidate), payload)
    assert len(manifest["frames"][-1]["reference_shot_ids"]) == 11
    too_many = copy.deepcopy(candidate)
    too_many["frames"][-1]["reference_shot_ids"].append("S12")
    with pytest.raises(assets.AssetValidationError):
        product_assets.validate_asset_output(encoded(too_many), payload)


def test_no_character_scene_only_plan_does_not_invent_extra_portraits():
    payload, candidate = asset_case()
    candidate["characters"] = []
    for frame in candidate["frames"]:
        frame["character_ids"] = []
    manifest = product_assets.validate_asset_output(encoded(candidate), payload)
    assert not manifest["characters"] and len(manifest["frames"]) == 4
    assert all("本镜不新增人物" in frame["first_frame_prompt"] for frame in manifest["frames"])


@pytest.mark.parametrize("value", ["https://synthetic.invalid/image.png", "www.synthetic.invalid", "/Users/SYNTHETIC/config.yaml", "127.0.0.1:9000", "Bearer SYNTHETIC-TOKEN", "api_key=SYNTHETIC", "sk-SYNTHETIC-KEY-ONLY-123456"])
def test_candidate_cannot_embed_network_paths_secrets_or_provider_configuration(value):
    payload, candidate = asset_case()
    candidate["frames"][0]["prompt"]["framing"] = value
    with pytest.raises(assets.AssetValidationError, match="^ASSET_INVALID$"):
        product_assets.validate_asset_output(encoded(candidate), payload)


@pytest.mark.parametrize("raw", [b'{"binding":{},"binding":{}}', b'{"x":NaN}', b'{"x":1e309}', b'{} trailing', b'\xff', b'```json\n{}\n```', b'x' * 65537, {}, 1])
def test_output_is_bounded_strict_json_with_fixed_safe_error(raw):
    payload, _ = asset_case()
    with pytest.raises(assets.AssetValidationError) as error:
        product_assets.validate_asset_output(raw, payload)
    assert error.value.code == str(error.value) == "ASSET_INVALID" and error.value.__cause__ is None


@pytest.mark.parametrize("case", ["prompt", "ratio", "global", "contract", "binding", "hash"])
def test_jobs_rebuild_complete_manifest_even_when_caller_recomputes_its_hash(case):
    payload, candidate = asset_case()
    manifest = product_assets.validate_asset_output(encoded(candidate), payload)
    if case == "prompt":
        manifest["frames"][0]["first_frame_prompt"] += "changed"
    elif case == "ratio":
        manifest["frames"][0]["ratio"] = "16:9"
    elif case == "global":
        manifest["characters"][0]["wardrobe"] = "changed synthetic wardrobe"
    elif case == "contract":
        manifest["contract_version"] = assets.ASSET_CONTRACT_VERSION
    elif case == "binding":
        manifest["binding"]["acceptance_report_hash"] = "0" * 64
    else:
        manifest["manifest_hash"] = "0" * 64
    if case != "hash":
        manifest["manifest_hash"] = digest({key: value for key, value in manifest.items() if key != "manifest_hash"})
    with pytest.raises(assets.AssetValidationError, match="^ASSET_INVALID$"):
        product_assets.image_jobs(payload, manifest)


def test_independently_pinned_manifest_rejects_another_self_consistent_design():
    payload, candidate = asset_case()
    original = product_assets.validate_asset_output(encoded(candidate), payload)
    candidate["characters"][0]["wardrobe"] = "虚构的另一版蓝色服装"
    replacement = product_assets.validate_asset_output(encoded(candidate), payload)
    assert replacement["manifest_hash"] != original["manifest_hash"]
    with pytest.raises(assets.AssetValidationError):
        product_assets.image_jobs(payload, replacement, expected_manifest_hash=original["manifest_hash"])


def test_new_profile_cannot_change_legacy_schema_artifacts_or_three_shot_limit():
    old_payload, old_candidate = synthetic_asset_case()
    old_manifest = assets.validate_asset_output(encoded(old_candidate), old_payload)
    before = copy.deepcopy((assets.ASSET_OUTPUT_SCHEMA, old_payload, old_manifest))
    payload, candidate = asset_case(4)
    product_assets.asset_messages(payload)
    product_assets.validate_asset_output(encoded(candidate), payload)
    assert (assets.ASSET_OUTPUT_SCHEMA, old_payload, assets.validate_asset_output(encoded(old_candidate), old_payload)) == before
    assert assets.ASSET_CONTRACT_VERSION == "D19-assets-v0.1"
    assert assets.ASSET_OUTPUT_SCHEMA["properties"]["frames"]["maxItems"] == 3
    assert product_assets.ASSET_OUTPUT_SCHEMA["properties"]["frames"]["maxItems"] == 12
    with pytest.raises(assets.AssetValidationError):
        assets.build_asset_input(payload["acceptance_payload"], payload["acceptance_report"],
            {"asset_task_id": "SYNTHETIC/A01", "manifest_version": 1})


@pytest.mark.parametrize("kind", ["first_review", "recheck"])
def test_fresh_context_and_all_product_functions_are_pure_and_do_not_mutate_inputs(monkeypatch, kind):
    import builtins
    import os
    import socket
    import subprocess

    import core.planner.protocol as protocol

    payload, candidate = asset_case(kind=kind)
    before = copy.deepcopy((payload, candidate))

    def forbidden(*args, **kwargs):
        pytest.fail("Pure product asset contract attempted external state or tool execution")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(protocol, "execute_tool", forbidden)
        messages = product_assets.asset_messages(payload)
        manifest = product_assets.validate_asset_output(encoded(candidate), payload)
        jobs = product_assets.image_jobs(payload, manifest, expected_manifest_hash=manifest["manifest_hash"])
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[1]["content"] == payload
    assert "不可信" in messages[0]["content"]["instruction"] and "不授权" in messages[0]["content"]["instruction"]
    assert messages[0]["content"]["output_schema"] == product_assets.ASSET_OUTPUT_SCHEMA
    assert len(jobs) == len(candidate["frames"]) and (payload, candidate) == before
    messages[1]["content"]["acceptance_payload"]["plan"]["artifact"]["shots"][0]["visual"] = "changed only returned copy"
    assert (payload, candidate) == before
