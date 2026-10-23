"""Independent visual-contract checks using fictional in-memory fixtures only."""

import copy
import json

import pytest
from test_planner_assets import synthetic_asset_case

from core.creator.store import digest
from core.planner.asset_review import (
    VisualContractError,
    build_image_repair,
    build_visual_input,
    validate_image_revision,
    validate_visual_review,
)
from core.planner.assets import validate_asset_output


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


@pytest.fixture
def visual_case():
    asset_payload, candidate = synthetic_asset_case()
    manifest = validate_asset_output(encoded(candidate), asset_payload)
    images = [{"shot_id": frame["shot_id"], "version": 1,
               "sha256": digest({"synthetic_image": frame["shot_id"]}), "width": 864, "height": 1536}
              for frame in manifest["frames"]]
    return build_visual_input(asset_payload, manifest, images,
                              {"review_id": "SYNTHETIC-VISUAL/R01", "manifest_hash": manifest["manifest_hash"]})


def rebuild(payload, images):
    return build_visual_input(payload["asset_payload"], payload["manifest"], images,
                              {name: payload["binding"][name] for name in ("review_id", "manifest_hash")})


def synthetic_report(payload, verdict="accepted"):
    shot_ids = [image["shot_id"] for image in payload["images"]]
    report = {"binding": copy.deepcopy(payload["binding"]),
              "shot_checks": [{"shot_id": shot_id, "verdict": "accepted", "observation": "合成逐镜合同样例"}
                              for shot_id in shot_ids],
              "cross_checks": [{"shot_ids": shot_ids, "verdict": "accepted", "observation": "合成跨镜连续性样例"}],
              "findings": [], "overall_verdict": verdict}
    if verdict != "accepted":
        report["shot_checks"][1]["verdict"] = verdict
        report["findings"] = [{"finding_id": "SYNTHETIC-F01", "criterion": "AS03" if verdict == "rejected" else "AS05",
                               "shot_ids": [shot_ids[1]], "observation": "合成的单镜问题或缺证说明",
                               "repair_instruction": "仅修正该镜头的合成构图问题" if verdict == "rejected" else None,
                               "executable": verdict == "rejected"}]
    return report


def next_images(payload):
    revised = copy.deepcopy(payload["images"])
    revised[1].update(version=revised[1]["version"] + 1, sha256=digest({"synthetic_revision": "S02"}))
    return revised


def test_complete_image_set_is_canonical_and_bound_without_mutating_inputs(visual_case):
    images = list(reversed(copy.deepcopy(visual_case["images"])))
    before = copy.deepcopy((visual_case, images))
    rebuilt = rebuild(visual_case, images)
    assert rebuilt["images"] == visual_case["images"]
    assert rebuilt["binding"]["image_set_hash"] == digest(visual_case["images"])
    assert rebuilt["binding"]["manifest_hash"] == visual_case["manifest"]["manifest_hash"]
    assert (visual_case, images) == before


def test_missing_or_duplicate_image_cannot_stand_in_for_the_complete_set(visual_case):
    missing = copy.deepcopy(visual_case["images"][:-1])
    duplicate = copy.deepcopy(visual_case["images"])
    duplicate[-1] = copy.deepcopy(duplicate[0])
    for images in (missing, duplicate):
        with pytest.raises(VisualContractError, match="^VISUAL_INVALID$"):
            rebuild(visual_case, images)


@pytest.mark.parametrize("stage", ["input", "revision"])
def test_landscape_metadata_is_rejected_at_both_image_boundaries(visual_case, stage):
    images = copy.deepcopy(visual_case["images"]) if stage == "input" else next_images(visual_case)
    images[1].update(width=1536, height=864)
    if stage == "input":
        with pytest.raises(VisualContractError):
            rebuild(visual_case, images)
    else:
        scope = build_image_repair(visual_case, synthetic_report(visual_case, "rejected"))
        with pytest.raises(VisualContractError):
            validate_image_revision(scope, images)


def test_valid_cross_shot_review_accepts_and_creates_no_repair(visual_case):
    report = synthetic_report(visual_case)
    accepted = validate_visual_review(encoded(report).encode("utf-8"), visual_case)
    assert accepted == report and accepted["overall_verdict"] == "accepted"
    assert build_image_repair(visual_case, accepted) is None


def test_cross_checks_container_must_be_a_list(visual_case):
    report = synthetic_report(visual_case)
    report["cross_checks"] = report["cross_checks"][0]
    with pytest.raises(VisualContractError):
        validate_visual_review(encoded(report), visual_case)


def test_cross_shot_check_cannot_be_encoded_as_a_single_shot_check(visual_case):
    for cross in ({"shot_id": "S01", "verdict": "accepted", "observation": "合成单镜不是跨镜核验"},
                  {"shot_ids": ["S01"], "verdict": "accepted", "observation": "合成单镜不是跨镜核验"}):
        report = synthetic_report(visual_case)
        report["cross_checks"] = [cross]
        with pytest.raises(VisualContractError):
            validate_visual_review(encoded(report), visual_case)


def test_needs_input_produces_only_a_non_executable_scope(visual_case):
    report = synthetic_report(visual_case, "needs_input")
    validated = validate_visual_review(encoded(report), visual_case)
    scope = build_image_repair(visual_case, validated)
    assert scope["kind"] == "needs_input" and scope["targets"] == []
    assert scope["preserve"] == visual_case["images"]
    with pytest.raises(VisualContractError):
        validate_image_revision(scope, next_images(visual_case))


def test_needs_input_finding_cannot_claim_an_executable_repair(visual_case):
    report = synthetic_report(visual_case, "needs_input")
    report["findings"][0].update(executable=True, repair_instruction="合成缺证不能直接执行返工")
    with pytest.raises(VisualContractError):
        build_image_repair(visual_case, report)


def test_rejected_report_repairs_only_the_explicit_shot_and_requires_recheck(visual_case):
    report = synthetic_report(visual_case, "rejected")
    before = copy.deepcopy((visual_case, report))
    scope = build_image_repair(visual_case, report)
    assert scope["kind"] == "image_repair_ready"
    assert [target["shot_id"] for target in scope["targets"]] == ["S02"]
    assert scope["targets"][0]["next_version"] == 2
    assert scope["preserve"] == [visual_case["images"][0], visual_case["images"][2]]
    revised = next_images(visual_case)
    revised[1].update(width=1728, height=3072)
    result = validate_image_revision(scope, revised)
    assert result["images"] == revised and result["recheck_required"] is True
    assert result["parent_binding"]["report_hash"] == digest(report)
    assert result["image_set_hash"] != visual_case["binding"]["image_set_hash"]
    assert (visual_case, report) == before


def test_target_revision_requires_exactly_one_version_step_and_a_new_hash(visual_case):
    scope = build_image_repair(visual_case, synthetic_report(visual_case, "rejected"))
    unchanged_hash = next_images(visual_case)
    unchanged_hash[1]["sha256"] = visual_case["images"][1]["sha256"]
    skipped_version = next_images(visual_case)
    skipped_version[1]["version"] = 3
    for revised in (unchanged_hash, skipped_version):
        with pytest.raises(VisualContractError):
            validate_image_revision(scope, revised)


def test_unrelated_image_metadata_cannot_change_during_a_targeted_repair(visual_case):
    scope = build_image_repair(visual_case, synthetic_report(visual_case, "rejected"))
    revised = next_images(visual_case)
    revised[0].update(width=1728, height=3072)
    with pytest.raises(VisualContractError):
        validate_image_revision(scope, revised)


def test_old_report_cannot_be_reused_for_a_new_image_hash(visual_case):
    old_report = synthetic_report(visual_case)
    revised_payload = rebuild(visual_case, next_images(visual_case))
    assert revised_payload["binding"]["image_set_hash"] != old_report["binding"]["image_set_hash"]
    with pytest.raises(VisualContractError):
        validate_visual_review(encoded(old_report), revised_payload)
    with pytest.raises(VisualContractError):
        build_image_repair(revised_payload, old_report)


def test_duplicate_json_binding_keys_are_rejected_without_repair(visual_case):
    report = synthetic_report(visual_case)
    duplicate = '{"binding":' + encoded(report["binding"]) + ',' + encoded(report)[1:]
    with pytest.raises(VisualContractError, match="^VISUAL_INVALID$"):
        validate_visual_review(duplicate, visual_case)


def test_extra_report_fields_are_rejected(visual_case):
    report = synthetic_report(visual_case)
    report["automatic_generation_authorized"] = True
    with pytest.raises(VisualContractError, match="^VISUAL_INVALID$"):
        validate_visual_review(encoded(report), visual_case)
