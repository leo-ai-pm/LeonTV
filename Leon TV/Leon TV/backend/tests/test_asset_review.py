"""D20 metadata-only visual contracts, entirely synthetic and no image reads."""
import copy
import hashlib
import importlib
import json

import pytest
from test_planner_assets import synthetic_asset_case

from core.creator.store import digest
from core.planner.assets import validate_asset_output


def module():
    return importlib.import_module("core.planner.asset_review")


def synthetic_visual_case(verdict="rejected"):
    asset_payload, candidate = synthetic_asset_case()
    manifest = validate_asset_output(json.dumps(candidate, ensure_ascii=False), asset_payload)
    images = [{"shot_id": frame["shot_id"], "version": 1, "sha256": hashlib.sha256(("SYNTHETIC-" + frame["shot_id"]).encode()).hexdigest(),
        "width": 900, "height": 1600} for frame in manifest["frames"]]
    payload = module().build_visual_input(asset_payload, manifest, images,
        {"review_id": "SYNTHETIC-VISUAL/V01", "manifest_hash": manifest["manifest_hash"]})
    report = {"binding": copy.deepcopy(payload["binding"]),
        "shot_checks": [{"shot_id": image["shot_id"], "verdict": verdict if image["shot_id"] == "S02" else "accepted",
            "observation": "仅为合成判断样例，没有实际查看图片"} for image in images],
        "cross_checks": [{"shot_ids": ["S01", "S02", "S03"], "verdict": "accepted", "observation": "合成跨镜判断，非实际查看"}],
        "findings": [], "overall_verdict": verdict}
    if verdict == "rejected":
        report["findings"] = [{"finding_id": "SYN-F01", "criterion": "AS02", "shot_ids": ["S02"],
            "observation": "合成关键物件缺失", "repair_instruction": "只恢复原定义的合成物件，不改其他内容", "executable": True}]
    return payload, report


def encoded(report):
    return json.dumps(report, ensure_ascii=False)


def test_parent_images_and_complete_set_are_bound_without_claiming_pixel_inspection():
    payload, report = synthetic_visual_case()
    assert module().validate_visual_review(encoded(report), payload) == report
    assert payload["binding"]["image_set_hash"] == digest(payload["images"])
    assert [rule["criterion"] for rule in payload["rules"]["criteria"]] == ["AS01", "AS02", "AS03", "AS04", "AS05"]
    assert "调用者" in payload["rules"]["criteria"][3]["description"]
    assert "独立查看者" in payload["rules"]["criteria"][4]["description"]
    assert "viewed" not in payload and "files_verified" not in payload


@pytest.mark.parametrize("case", ["manifest", "image_version", "image_hash", "image_extra", "missing", "duplicate"])
def test_build_input_rejects_wrong_parent_or_image_metadata(case):
    payload, _ = synthetic_visual_case()
    manifest, images = payload["manifest"], payload["images"]
    binding = {"review_id": "SYN-V01", "manifest_hash": manifest["manifest_hash"]}
    if case == "manifest":
        binding["manifest_hash"] = "0" * 64
    elif case == "image_version":
        images[0]["version"] = True
    elif case == "image_hash":
        images[0]["sha256"] = "bad"
    elif case == "image_extra":
        images[0]["file_path"] = "SYNTHETIC"
    elif case == "missing":
        images.pop()
    else:
        images[1]["shot_id"] = images[0]["shot_id"]
    with pytest.raises(module().VisualContractError, match="^VISUAL_INVALID$"):
        module().build_visual_input(payload["asset_payload"], manifest, images, binding)


@pytest.mark.parametrize("case", ["wrong_binding", "aggregate", "unknown", "extra", "missing_shot", "cross_shape", "cross_unknown", "finding_duplicate", "finding_unknown", "unavailable_executable"])
def test_review_integrity_and_unknown_never_become_acceptance(case):
    payload, report = synthetic_visual_case()
    if case == "wrong_binding":
        report["binding"]["image_set_hash"] = "0" * 64
    elif case == "aggregate":
        report["overall_verdict"] = "accepted"
    elif case == "unknown":
        report["shot_checks"][1]["verdict"] = "unknown"
    elif case == "extra":
        report["permission"] = "call-model"
    elif case == "missing_shot":
        report["shot_checks"].pop()
    elif case == "cross_shape":
        report["cross_checks"] = [{"shot_id": "S01", "verdict": "accepted", "observation": "not a cross check"}]
    elif case == "cross_unknown":
        report["cross_checks"][0]["shot_ids"] = ["S01", "MISSING"]
    elif case == "finding_duplicate":
        report["findings"].append(copy.deepcopy(report["findings"][0]))
    elif case == "finding_unknown":
        report["findings"][0]["shot_ids"] = ["MISSING"]
    else:
        report["shot_checks"][1]["verdict"] = "needs_input"
        report["overall_verdict"] = "needs_input"
    with pytest.raises(module().VisualContractError):
        module().validate_visual_review(encoded(report), payload)


@pytest.mark.parametrize("raw", [b'{"binding":{},"binding":{}}', b'{"x":NaN}', b'{"x":1e309}', b'{} trailing', b'\xff', b'x' * 65537])
def test_raw_is_strict_json_and_errors_are_fixed(raw):
    payload, _ = synthetic_visual_case()
    with pytest.raises(module().VisualContractError) as error:
        module().validate_visual_review(raw, payload)
    assert str(error.value) == error.value.code == "VISUAL_INVALID" and error.value.__cause__ is None


@pytest.mark.parametrize("verdict,kind", [("accepted", None), ("needs_input", "needs_input")])
def test_pass_needs_no_manufactured_repair_and_missing_input_waits(verdict, kind):
    payload, report = synthetic_visual_case(verdict)
    result = module().build_image_repair(payload, report)
    if kind is None:
        assert result is None
    else:
        assert result["kind"] == kind and result["targets"] == [] and result["preserve"] == payload["images"]


def test_repair_only_targets_explicit_finding_and_increments_image_version():
    payload, report = synthetic_visual_case()
    original = copy.deepcopy((payload, report))
    scope = module().build_image_repair(payload, report)
    assert [target["shot_id"] for target in scope["targets"]] == ["S02"]
    assert [image["shot_id"] for image in scope["preserve"]] == ["S01", "S03"]
    revised = copy.deepcopy(payload["images"])
    revised[1].update(version=2, sha256=hashlib.sha256(b"SYNTHETIC-REVISED").hexdigest())
    result = module().validate_image_revision(scope, revised)
    assert result["recheck_required"] is True and result["parent_binding"]["manifest_hash"] == payload["binding"]["manifest_hash"]
    assert result["images"][0] == payload["images"][0] and result["images"][2] == payload["images"][2]
    assert (payload, report) == original


@pytest.mark.parametrize("case", ["noop", "version", "unauthorized", "untouched_size", "source_change", "scope_hash", "original_replaced", "target_parent", "missing_image"])
def test_revision_rejects_noop_stale_originals_and_out_of_scope_changes(case):
    payload, report = synthetic_visual_case()
    scope = module().build_image_repair(payload, report)
    revised = copy.deepcopy(payload["images"])
    revised[1].update(version=2, sha256="a" * 64)
    if case == "noop":
        revised[1]["sha256"] = payload["images"][1]["sha256"]
    elif case == "version":
        revised[1]["version"] = 3
    elif case == "unauthorized":
        revised[0]["sha256"] = "b" * 64
    elif case == "untouched_size":
        revised[2]["width"] += 1
    elif case == "source_change":
        revised[1]["source_snapshot_hash"] = "b" * 64
    elif case == "scope_hash":
        scope["binding"]["plan_hash"] = "b" * 64
    elif case == "original_replaced":
        scope["images"][1]["sha256"] = "c" * 64
    elif case == "target_parent":
        scope["targets"][0]["old_sha256"] = "b" * 64
    else:
        revised.pop()
    if case in {"original_replaced", "target_parent"}:
        scope["scope_hash"] = digest({key: value for key, value in scope.items() if key != "scope_hash"})
    with pytest.raises(module().VisualContractError):
        module().validate_image_revision(scope, revised)


def test_nonactionable_rejection_waits_without_changing_the_judgement():
    payload, report = synthetic_visual_case()
    report["findings"][0].update(executable=False, repair_instruction=None)
    scope = module().build_image_repair(payload, report)
    assert scope["kind"] == "needs_input" and scope["targets"] == [] and report["overall_verdict"] == "rejected"
