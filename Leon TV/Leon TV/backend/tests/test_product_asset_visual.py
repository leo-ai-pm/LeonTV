"""New explicit asset profile through the existing visual revision contract."""
import copy
import json

import pytest
from test_product_assets import asset_case

from core.creator.store import digest
from core.planner import asset_review, assets, product_assets


def case(verdict="accepted", kind="first_review"):
    context, candidate = asset_case(kind=kind)
    manifest = product_assets.validate_asset_output(json.dumps(candidate), context)
    images = [{"shot_id": item["shot_id"], "version": 1, "sha256": digest({"fictional": item["shot_id"]}),
               "width": 720, "height": 1280} for item in manifest["frames"]]
    payload = asset_review.build_visual_input(context, manifest, images, {"review_id": "SYNTHETIC-VISUAL-01", "manifest_hash": manifest["manifest_hash"]})
    report = {"binding": copy.deepcopy(payload["binding"]),
              "shot_checks": [{"shot_id": item["shot_id"], "verdict": verdict if index == 1 else "accepted", "observation": "合成判断，不是真实看图"}
                              for index, item in enumerate(images)],
              "cross_checks": [{"shot_ids": [item["shot_id"] for item in images], "verdict": "accepted", "observation": "合成跨镜判断"}],
              "findings": [], "overall_verdict": verdict}
    if verdict == "rejected":
        report["findings"] = [{"finding_id": "SYNTHETIC-F01", "criterion": "AS02", "shot_ids": [images[1]["shot_id"]],
                               "observation": "合成道具缺失", "repair_instruction": "只修正该镜的合成道具", "executable": True}]
    return payload, report


@pytest.mark.parametrize("kind", ["first_review", "recheck"])
def test_explicit_profile_reuses_visual_checks_without_rewriting_acceptance(kind):
    payload, report = case(kind=kind)
    assert asset_review.validate_visual_review(json.dumps(report), payload) == report
    assert payload["asset_payload"]["acceptance_kind"] == kind
    assert asset_review.build_image_repair(payload, report) is None


def test_four_image_repair_preserves_three_others_and_requires_new_complete_set_review():
    payload, report = case("rejected")
    assert len(payload["images"]) == 4
    scope = asset_review.build_image_repair(payload, report)
    assert [item["shot_id"] for item in scope["targets"]] == ["S02"]
    assert [item["shot_id"] for item in scope["preserve"]] == ["S01", "S03", "S04"]
    changed = copy.deepcopy(payload["images"])
    changed[1].update(version=2, sha256=digest("fictional revised image"))
    result = asset_review.validate_image_revision(scope, changed)
    assert result["recheck_required"] is True
    assert [changed[index] for index in (0, 2, 3)] == scope["preserve"]
    replacement = asset_review.build_visual_input(payload["asset_payload"], payload["manifest"], changed,
        {"review_id": "SYNTHETIC-VISUAL-02", "manifest_hash": payload["manifest"]["manifest_hash"]})
    assert replacement["binding"]["image_set_hash"] != payload["binding"]["image_set_hash"]
    with pytest.raises(asset_review.VisualContractError):
        asset_review.validate_visual_review(json.dumps(report), replacement)


@pytest.mark.parametrize("change", ["missing_fourth", "duplicate", "source", "manifest", "acceptance", "downgrade"])
def test_full_set_and_explicit_profile_cannot_be_downgraded_or_rebound(change):
    payload, _ = case()
    if change == "missing_fourth":
        payload["images"].pop()
    elif change == "duplicate":
        payload["images"][-1] = copy.deepcopy(payload["images"][0])
    elif change == "source":
        payload["asset_payload"]["binding"]["source_snapshot_hash"] = "f" * 64
    elif change == "manifest":
        payload["manifest"]["frames"][0]["first_frame_prompt"] = "changed"
    elif change == "acceptance":
        payload["asset_payload"]["binding"]["acceptance_report_hash"] = "f" * 64
    else:
        payload["asset_payload"]["contract_version"] = assets.ASSET_CONTRACT_VERSION
    with pytest.raises(asset_review.VisualContractError):
        asset_review.checked_input(payload)


def test_missing_visual_evidence_never_creates_image_repair_targets():
    payload, report = case("needs_input")
    scope = asset_review.build_image_repair(payload, report)
    assert scope["kind"] == "needs_input" and scope["targets"] == []
    assert scope["preserve"] == payload["images"]
