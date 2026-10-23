"""D19 synthetic material manifests; no real data, model, image or network."""
import copy
import importlib
import json

import pytest
from test_planner_review import synthetic_review_case

from core.creator.store import digest, source_hash
from core.planner.review import build_review_input


def assets_module():
    return importlib.import_module("core.planner.assets")


def synthetic_asset_case(*, with_character=True):
    """Return(payload, raw candidate), using fictional source/plan/review only."""
    review_payload, report = synthetic_review_case()
    source = review_payload["source"]["snapshot"]
    source["script"] = ("虚构样例：灰外套的林走进房间，把木方放在桌上，然后站到窗边。不是实际用户剧本。" if with_character
        else "虚构空镜样例：一个无人的房间，木桌上放着木方，窗边出现光影。不是实际用户剧本。")
    source["source_hash"] = source_hash(source["script"])
    source["snapshot_hash"] = digest({key: value for key, value in source.items() if key != "snapshot_hash"})
    plan = review_payload["plan"]["artifact"]
    plan["source_snapshot_hash"] = source["snapshot_hash"]
    visuals = ["林走进房间", "林把木方放在桌上", "林站到窗边"] if with_character else ["无人房间", "木桌上放着木方", "窗边出现光影"]
    for shot, visual in zip(plan["shots"], visuals, strict=True):
        shot["visual"] = visual
    binding = review_payload["binding"]
    binding.update(source_snapshot_hash=source["snapshot_hash"], plan_hash=digest(plan))
    review_payload = build_review_input(source, plan, binding)
    report["binding"] = copy.deepcopy(binding)
    payload = assets_module().build_asset_input(review_payload, report, {"asset_task_id": "SYNTHETIC-ASSETS/A01", "manifest_version": 1})
    candidate = {"binding": copy.deepcopy(payload["binding"]),
        "characters": [{"character_id": "CHAR001", "name": "林", "appearance": "成年虚构人物，短黑发，面部特征固定",
            "wardrobe": "灰色外套，深色长裤", "design_notes": "外套来自样例，未说明的发型为本清单一次性设计补充", "source_segment_ids": ["SRC0001"]}],
        "scenes": [{"scene_id": "SCENE001", "name": "虚构房间", "environment": "素色墙面，木桌与右侧窗户，布局固定",
            "lighting": "右侧窗户柔和日光", "design_notes": "窗户与木桌基于动作需要作固定设计补充", "source_segment_ids": ["SRC0001"]}], "frames": []}
    if not with_character:
        candidate["characters"] = []
    for index, shot in enumerate(plan["shots"], 1):
        candidate["frames"].append({"shot_id": shot["id"], "character_ids": ["CHAR001"] if with_character else [], "scene_id": "SCENE001",
            "image_order": index, "reference_shot_ids": [] if index == 1 else [plan["shots"][index - 2]["id"]],
            "prompt": {"framing": "中景，主体处于画面中央，保持原机位关系", "action_start": "只表现原动作起点的单个瞬间"}})
    return payload, candidate


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def test_asset_role_seam_builds_bound_first_frames_and_internal_reference_jobs():
    payload, candidate = synthetic_asset_case()
    assets = assets_module()
    manifest = assets.validate_asset_output(encoded(candidate), payload)
    jobs = assets.image_jobs(payload, manifest, expected_manifest_hash=manifest["manifest_hash"])
    assert len(jobs) == len(payload["review_payload"]["plan"]["artifact"]["shots"]) == 3
    assert [job["shot_id"] for job in jobs] == ["S01", "S02", "S03"]
    assert jobs[0]["reference_shot_ids"] == [] and jobs[1]["reference_shot_ids"] == ["S01"]
    assert all(job["ratio"] == "9:16" and job["style"] == "realistic_cinematic" for job in jobs)
    assert manifest["manifest_hash"] == digest({key: value for key, value in manifest.items() if key != "manifest_hash"})


def test_each_frame_uses_exact_global_character_wardrobe_scene_and_frozen_plan():
    payload, candidate = synthetic_asset_case()
    manifest = assets_module().validate_asset_output(encoded(candidate), payload)
    original = payload["review_payload"]["plan"]["artifact"]
    for frame, shot in zip(manifest["frames"], original["shots"], strict=True):
        assert frame["duration_ms"] == shot["duration_ms"] and frame["depends_on"] == shot["depends_on"]
        prompt = frame["first_frame_prompt"]
        for text in (candidate["characters"][0]["appearance"], candidate["characters"][0]["wardrobe"],
            candidate["scenes"][0]["environment"], candidate["scenes"][0]["lighting"], shot["visual"], shot["camera"]):
            assert text in prompt
        assert frame["definition_hashes"]["characters"]["CHAR001"] == digest(candidate["characters"][0])
        assert frame["definition_hashes"]["scene"] == digest(candidate["scenes"][0])


def test_fresh_asset_context_has_only_system_and_parent_artifacts():
    payload, _ = synthetic_asset_case()
    before = copy.deepcopy(payload)
    messages = assets_module().asset_messages(payload)
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[1]["content"] == payload and messages[0]["content"]["output_schema"] == assets_module().ASSET_OUTPUT_SCHEMA
    assert "不可信" in messages[0]["content"]["instruction"] and payload == before


@pytest.mark.parametrize("case", ["rejected", "unknown", "binding", "plan", "source", "rule", "version", "asset_extra"])
def test_input_requires_matching_accepted_review_and_strict_unchanged_parent(case):
    payload, _ = synthetic_asset_case()
    parent, report = payload["review_payload"], payload["review_report"]
    asset_binding = {"asset_task_id": "SYNTHETIC-ASSETS/A01", "manifest_version": 1}
    if case in {"rejected", "unknown"}:
        _, report = synthetic_review_case(case)
        report["binding"] = copy.deepcopy(parent["binding"])
    elif case == "binding":
        report["binding"]["plan_hash"] = "0" * 64
    elif case == "plan":
        parent["plan"]["artifact"]["shots"][0]["duration_ms"] = 4999
    elif case == "source":
        parent["source"]["snapshot"]["script"] = "changed"
    elif case == "rule":
        parent["rules"]["criteria"][0]["accepted"] = "changed"
    elif case == "version":
        asset_binding["manifest_version"] = True
    else:
        asset_binding["api_key"] = "SYNTHETIC ONLY"
    with pytest.raises(assets_module().AssetValidationError, match="^ASSET_INVALID$"):
        assets_module().build_asset_input(parent, report, asset_binding)


@pytest.mark.parametrize("case", ["missing_shot", "duplicate_shot", "order", "duration", "character", "scene", "duplicate_character", "unused_character", "local_wardrobe", "unknown_source", "reference_self", "reference_future", "reference_unknown", "reference_duplicate", "no_later_reference", "first_reference", "wrong_binding"])
def test_manifest_coverage_global_reuse_order_and_reference_graph_fail_closed(case):
    payload, candidate = synthetic_asset_case()
    frame = candidate["frames"][1]
    if case == "missing_shot":
        candidate["frames"].pop()
    elif case == "duplicate_shot":
        frame["shot_id"] = "S01"
    elif case == "order":
        frame["image_order"] = 3
    elif case == "duration":
        frame["duration_ms"] = 9000
    elif case == "character":
        frame["character_ids"] = ["CHAR999"]
    elif case == "scene":
        frame["scene_id"] = "SCENE999"
    elif case == "duplicate_character":
        frame["character_ids"] *= 2
    elif case == "unused_character":
        other = copy.deepcopy(candidate["characters"][0])
        other["character_id"] = "CHAR002"
        candidate["characters"].append(other)
    elif case == "local_wardrobe":
        frame["wardrobe"] = "different coat"
    elif case == "unknown_source":
        candidate["scenes"][0]["source_segment_ids"] = ["SRC9999"]
    elif case == "reference_self":
        frame["reference_shot_ids"] = ["S02"]
    elif case == "reference_future":
        frame["reference_shot_ids"] = ["S03"]
    elif case == "reference_unknown":
        frame["reference_shot_ids"] = ["S99"]
    elif case == "reference_duplicate":
        frame["reference_shot_ids"] *= 2
    elif case == "no_later_reference":
        frame["reference_shot_ids"] = []
    elif case == "first_reference":
        candidate["frames"][0]["reference_shot_ids"] = ["S03"]
    else:
        candidate["binding"]["review_hash"] = "0" * 64
    with pytest.raises(assets_module().AssetValidationError):
        assets_module().validate_asset_output(encoded(candidate), payload)


@pytest.mark.parametrize("text", ["https://synthetic.invalid/image.png", "www.synthetic.invalid", "127.0.0.1:9000", "/Users/SYNTHETIC/config.yaml", "Bearer SYNTHETIC-TOKEN", "api_key=SYNTHETIC", "sk-SYNTHETIC-KEY-ONLY-123456", "base_url=https://synthetic.invalid"])
def test_manifest_text_never_contains_network_locations_credentials_or_configuration(text):
    payload, candidate = synthetic_asset_case()
    candidate["frames"][0]["prompt"]["framing"] = text
    with pytest.raises(assets_module().AssetValidationError) as error:
        assets_module().validate_asset_output(encoded(candidate), payload)
    assert str(error.value) == "ASSET_INVALID" and "SYNTHETIC" not in str(error.value)


@pytest.mark.parametrize("bad", [b'{"binding":{},"binding":{}}', b'{"x":NaN}', b'{} trailing', b'\xff', b'```json\n{}\n```', b'x' * 65537])
def test_raw_output_is_strict_json_with_fixed_safe_errors(bad):
    payload, _ = synthetic_asset_case()
    with pytest.raises(assets_module().AssetValidationError, match="^ASSET_INVALID$"):
        assets_module().validate_asset_output(bad, payload)


@pytest.mark.parametrize("case", ["global", "prompt", "ratio", "hash", "reference"])
def test_image_jobs_rebuild_the_sealed_manifest_and_reject_hand_edits(case):
    payload, candidate = synthetic_asset_case()
    manifest = assets_module().validate_asset_output(encoded(candidate), payload)
    if case == "global":
        manifest["characters"][0]["wardrobe"] = "hand changed"
    elif case == "prompt":
        manifest["frames"][0]["first_frame_prompt"] += "hand changed"
    elif case == "ratio":
        manifest["frames"][0]["ratio"] = "16:9"
    elif case == "reference":
        manifest["frames"][1]["reference_shot_ids"] = []
    else:
        manifest["manifest_hash"] = "0" * 64
    with pytest.raises(assets_module().AssetValidationError):
        assets_module().image_jobs(payload, manifest)


def test_independently_frozen_hash_rejects_a_new_self_consistent_character_design():
    payload, candidate = synthetic_asset_case()
    original = assets_module().validate_asset_output(encoded(candidate), payload)
    candidate["characters"][0]["wardrobe"] = "新提案的蓝色外套"
    replacement = assets_module().validate_asset_output(encoded(candidate), payload)
    assert replacement["manifest_hash"] != original["manifest_hash"]
    with pytest.raises(assets_module().AssetValidationError):
        assets_module().image_jobs(payload, replacement, expected_manifest_hash=original["manifest_hash"])


def test_no_character_scene_only_shots_are_valid_without_inventing_extra_portraits():
    payload, candidate = synthetic_asset_case(with_character=False)
    result = assets_module().validate_asset_output(encoded(candidate), payload)
    assert result["characters"] == [] and len(assets_module().image_jobs(payload, result)) == 3


def test_asset_apis_are_pure_and_preserve_all_inputs(monkeypatch):
    import builtins
    import os
    import subprocess

    import core.planner.protocol as protocol

    payload, candidate = synthetic_asset_case()
    assets = assets_module()
    before = copy.deepcopy((payload, candidate))

    def forbidden(*args, **kwargs):
        pytest.fail("Asset text API attempted external state or tool execution")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(protocol, "execute_tool", forbidden)
        assets.asset_messages(payload)
        manifest = assets.validate_asset_output(encoded(candidate), payload)
        jobs = assets.image_jobs(payload, manifest, expected_manifest_hash=manifest["manifest_hash"])
    assert len(jobs) == 3 and (payload, candidate) == before
