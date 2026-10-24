"""D21 pure video handoff using synthetic metadata, never actual images."""
import copy
import json

import pytest
from test_asset_review import synthetic_visual_case

from core.creator.store import digest
from core.planner.asset_review import build_visual_input
from core.planner.assets import validate_asset_output
from core.planner.video_brief import VideoBriefError, build_video_brief, checked_video_brief


def rehash(brief):
    brief["brief_hash"] = digest({key: value for key, value in brief.items() if key != "brief_hash"})


def test_accepted_brief_exactly_binds_parents_frames_and_original_timeline():
    payload, report = synthetic_visual_case("accepted")
    before = copy.deepcopy((payload, report))
    brief = build_video_brief(payload, report)
    plan = payload["asset_payload"]["review_payload"]["plan"]["artifact"]
    text = payload["manifest"]["binding"]
    assert brief["binding"] == {**payload["binding"], "review_hash": digest(report),
        "text_review_id": text["review_id"], "text_review_hash": text["review_hash"],
        "text_rule_version": text["rule_version"], "text_rule_hash": text["rule_hash"]}
    assert brief["duration_ms"] == 15000 and brief["ratio"] == "9:16" and brief["style"] == "realistic_cinematic"
    assert brief["characters"] == payload["manifest"]["characters"] and brief["scenes"] == payload["manifest"]["scenes"]
    assert [(row["start_ms"], row["end_ms"]) for row in brief["timeline"]] == [(0, 5000), (5000, 10000), (10000, 15000)]
    for index, (row, shot, image) in enumerate(zip(brief["timeline"], plan["shots"], payload["images"], strict=True), 1):
        assert row == {"shot_id": shot["id"], "start_ms": (index - 1) * 5000, "end_ms": index * 5000,
            **{key: shot[key] for key in ("duration_ms", "visual", "camera", "audio")},
            "image_order": index, "image_version": image["version"], "image_sha256": image["sha256"]}
        assert f"参考图{index}对应本镜起点" in brief["prompt"]
        assert "原画面：" + shot["visual"] in brief["prompt"]
        assert "原机位：" + shot["camera"] in brief["prompt"]
        assert "原音频：" + shot["audio"] in brief["prompt"]
    for phrase in ("00.000秒—05.000秒", "05.000秒—10.000秒", "10.000秒—15.000秒", "原节奏硬切", "拼贴或幻灯片"):
        assert phrase in brief["prompt"]
    assert all(phrase not in brief["prompt"] for phrase in ("字幕", "旁白", "音轨", "已经生成", "已获授权", "实际查看"))
    assert checked_video_brief(brief, payload, report, brief["brief_hash"]) == brief
    assert (payload, report) == before
    brief["characters"][0]["wardrobe"] = "only the returned copy changes"
    assert (payload, report) == before


@pytest.mark.parametrize("verdict", ["rejected", "needs_input"])
def test_unaccepted_visual_review_cannot_create_a_video_brief(verdict):
    payload, report = synthetic_visual_case(verdict)
    with pytest.raises(VideoBriefError, match="^VIDEO_BRIEF_INVALID$"):
        build_video_brief(payload, report)


@pytest.mark.parametrize("case", ["plan_version", "image_version", "image_hash", "source", "manifest", "rule"])
def test_stale_or_modified_parent_chain_is_rejected(case):
    payload, report = synthetic_visual_case("accepted")
    if case == "plan_version":
        payload["binding"]["plan_version"] += 1
    elif case == "image_version":
        payload["images"][1]["version"] += 1
    elif case == "image_hash":
        payload["images"][1]["sha256"] = "a" * 64
    elif case == "source":
        payload["asset_payload"]["review_payload"]["source"]["snapshot"]["script"] += " source changed"
    elif case == "manifest":
        payload["manifest"]["characters"][0]["wardrobe"] = "changed shared costume"
    else:
        payload["rules"]["criteria"][0]["description"] = "changed review rule"
    with pytest.raises(VideoBriefError, match="^VIDEO_BRIEF_INVALID$"):
        build_video_brief(payload, report)


@pytest.mark.parametrize("case", ["image_version", "image_hash", "manifest"])
def test_new_self_consistent_image_set_or_manifest_cannot_reuse_old_visual_report(case):
    payload, report = synthetic_visual_case("accepted")
    manifest, images = copy.deepcopy(payload["manifest"]), copy.deepcopy(payload["images"])
    if case == "image_version":
        images[1]["version"] += 1
    elif case == "image_hash":
        images[1]["sha256"] = "b" * 64
    else:
        candidate = {key: manifest[key] for key in ("binding", "characters", "scenes")}
        candidate["frames"] = [{key: frame[key] for key in ("shot_id", "character_ids", "scene_id", "image_order", "reference_shot_ids", "prompt")}
            for frame in manifest["frames"]]
        candidate["characters"][0]["wardrobe"] = "合成新版本外套"
        manifest = validate_asset_output(json.dumps(candidate, ensure_ascii=False), payload["asset_payload"])
    revised = build_visual_input(payload["asset_payload"], manifest, images,
        {"review_id": payload["binding"]["review_id"], "manifest_hash": manifest["manifest_hash"]})
    with pytest.raises(VideoBriefError):
        build_video_brief(revised, report)


@pytest.mark.parametrize("case", ["prompt", "timeline", "frames", "characters", "numeric_type", "extra"])
def test_recomputed_hash_cannot_make_a_hand_edited_brief_valid(case):
    payload, report = synthetic_visual_case("accepted")
    brief = build_video_brief(payload, report)
    frozen = brief["brief_hash"]
    if case == "prompt":
        brief["prompt"] += "\n擅自新增动作。"
    elif case == "timeline":
        brief["timeline"][0]["start_ms"] = 1
    elif case == "frames":
        brief["timeline"][1]["image_sha256"] = "c" * 64
    elif case == "characters":
        brief["characters"][0]["wardrobe"] = "wrong wardrobe"
    elif case == "numeric_type":
        brief["duration_ms"] = 15000.0
    else:
        brief["authorized"] = True
    rehash(brief)
    for expected in (frozen, brief["brief_hash"]):
        with pytest.raises(VideoBriefError, match="^VIDEO_BRIEF_INVALID$"):
            checked_video_brief(brief, payload, report, expected)


def test_independent_frozen_hash_rejects_a_new_valid_parent_and_brief():
    payload, report = synthetic_visual_case("accepted")
    old = build_video_brief(payload, report)
    images = copy.deepcopy(payload["images"])
    images[1].update(version=2, sha256="d" * 64)
    revised = build_visual_input(payload["asset_payload"], payload["manifest"], images,
        {"review_id": "SYNTHETIC-VISUAL/V02", "manifest_hash": payload["manifest"]["manifest_hash"]})
    report["binding"] = copy.deepcopy(revised["binding"])
    new = build_video_brief(revised, report)
    assert new["brief_hash"] != old["brief_hash"]
    with pytest.raises(VideoBriefError):
        checked_video_brief(new, revised, report, old["brief_hash"])
    with pytest.raises(VideoBriefError):
        checked_video_brief(old, revised, report, old["brief_hash"])


@pytest.mark.parametrize("case", ["duration", "sum", "ratio"])
def test_non_fifteen_second_or_non_vertical_plan_is_rejected(case):
    payload, report = synthetic_visual_case("accepted")
    plan = payload["asset_payload"]["review_payload"]["plan"]["artifact"]
    if case == "duration":
        plan["duration_ms"] = 14999
    elif case == "sum":
        plan["shots"][0]["duration_ms"] -= 1
    else:
        plan["ratio"] = "16:9"
    with pytest.raises(VideoBriefError):
        build_video_brief(payload, report)


@pytest.mark.parametrize("case", ["tuple", "duplicate_json", "non_json", "hash"])
def test_strict_containers_raw_duplicate_keys_and_invalid_hash_have_neutral_errors(case):
    payload, report = synthetic_visual_case("accepted")
    brief = build_video_brief(payload, report)
    expected = brief["brief_hash"]
    if case == "tuple":
        payload["images"] = tuple(payload["images"])
    elif case == "duplicate_json":
        report = '{"binding":{},"binding":{}}'
    elif case == "non_json":
        report["findings"] = set()
    else:
        expected = True
    with pytest.raises(VideoBriefError) as error:
        checked_video_brief(brief, payload, report, expected)
    assert str(error.value) == error.value.code == "VIDEO_BRIEF_INVALID" and error.value.__cause__ is None


def test_handoff_is_pure_and_does_not_read_images_or_execute_tools(monkeypatch):
    import builtins
    import os
    import subprocess

    import core.planner.protocol as protocol

    payload, report = synthetic_visual_case("accepted")

    def forbidden(*args, **kwargs):
        pytest.fail("Pure video handoff attempted external state or tool execution")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(protocol, "execute_tool", forbidden)
        brief = build_video_brief(payload, report)
        assert checked_video_brief(brief, payload, report, brief["brief_hash"]) == brief
