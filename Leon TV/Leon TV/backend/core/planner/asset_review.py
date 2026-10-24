"""Pure visual judgements and image-version scopes; never opens an image."""
import json
import re

from core.creator.store import digest
from core.planner.assets import checked_asset_payload, image_jobs
from core.planner.protocol import parse_json
from core.planner.review import json_copy

RULE_VERSION = "D20-AS-v0.1"
RULES = {"version": RULE_VERSION, "criteria": [
    {"criterion": "AS01", "description": "来源、主体与动作起点忠实，不新增剧情或把首帧误作动作结尾。"},
    {"criterion": "AS02", "description": "角色外貌、服饰、关键道具、场景与光线符合定型及原镜头，并保持应有连续性。"},
    {"criterion": "AS03", "description": "构图、景别、机位及视点符合原计划，记录可见生成缺陷，不用宽景替代必要特写。"},
    {"criterion": "AS04", "description": "技术交付、文件尺寸与哈希由调用者实际验证，不将技术完整性当作内容通过。"},
    {"criterion": "AS05", "description": "独立查看者实际逐镜及跨镜视觉核验，包括可见生成缺陷；缺图或不可判断时needs_input，合同不证明看过图片。"}]}
_RULES_JSON = json.dumps(RULES, ensure_ascii=False)
RULE_HASH = digest(RULES)
STATES = {"accepted", "rejected", "needs_input"}
IMAGE_KEYS = {"shot_id", "version", "sha256", "width", "height"}
REPORT_KEYS = {"binding", "shot_checks", "cross_checks", "findings", "overall_verdict"}
BINDING_KEYS = {"review_id", "source_snapshot_hash", "plan_version", "plan_hash", "manifest_hash", "image_set_hash", "rule_version", "rule_hash"}


class VisualContractError(ValueError):
    def __init__(self):
        self.code = "VISUAL_INVALID"
        super().__init__(self.code)


def require(value):
    if not value:
        raise VisualContractError() from None


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected))


def text(value):
    require(type(value) is str and bool(value.strip()) and len(value) <= 2000)
    value.encode("utf-8")


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def imageset(images, shot_ids):
    images = json_copy(images)
    require(type(images) is list and len(images) == len(shot_ids))
    by_id = {}
    for image in images:
        keys(image, IMAGE_KEYS)
        require(image["shot_id"] in shot_ids and image["shot_id"] not in by_id)
        sha(image["sha256"])
        require(all(type(image[name]) is int and image[name] > 0 for name in ("version", "width", "height")))
        require(image["width"] * 16 == image["height"] * 9)
        by_id[image["shot_id"]] = image
    return [by_id[shot_id] for shot_id in shot_ids]


def build_visual_input(asset_payload, manifest, images, binding):
    try:
        asset_payload, manifest, binding = json_copy(asset_payload), json_copy(manifest), json_copy(binding)
        keys(binding, {"review_id", "manifest_hash"})
        require(type(binding["review_id"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", binding["review_id"]) is not None)
        sha(binding["manifest_hash"])
        # Explicit new profile; legacy D19 schema and output remain unchanged.
        if type(asset_payload) is dict and asset_payload.get("contract_version") == "product-assets-v1":
            from core.planner.product_assets import checked_asset_payload as checked_product
            from core.planner.product_assets import image_jobs as product_jobs
            checked_product(asset_payload)
            jobs = product_jobs(asset_payload, manifest, expected_manifest_hash=binding["manifest_hash"])
        else:
            checked_asset_payload(asset_payload)
            jobs = image_jobs(asset_payload, manifest, expected_manifest_hash=binding["manifest_hash"])
        images = imageset(images, [job["shot_id"] for job in jobs])
        parent = manifest["binding"]
        frozen = {"review_id": binding["review_id"], **{name: parent[name] for name in ("source_snapshot_hash", "plan_version", "plan_hash")},
            "manifest_hash": binding["manifest_hash"], "image_set_hash": digest(images), "rule_version": RULE_VERSION, "rule_hash": RULE_HASH}
        return {"binding": frozen, "asset_payload": asset_payload, "manifest": manifest, "images": images, "rules": json.loads(_RULES_JSON)}
    except Exception:
        raise VisualContractError() from None


def checked_input(payload):
    payload = json_copy(payload)
    keys(payload, {"binding", "asset_payload", "manifest", "images", "rules"})
    rebuilt = build_visual_input(payload["asset_payload"], payload["manifest"], payload["images"],
        {name: payload["binding"][name] for name in ("review_id", "manifest_hash")})
    require(digest(payload) == digest(rebuilt))
    return rebuilt


def targets(value, shot_ids, minimum=1):
    require(type(value) is list and minimum <= len(value) <= len(shot_ids) and len(set(value)) == len(value))
    require(all(type(item) is str and item in shot_ids for item in value))


def validate_visual_review(raw, payload):
    try:
        payload = checked_input(payload)
        require(type(raw) in {str, bytes})
        data = raw.encode("utf-8") if type(raw) is str else raw
        require(len(data) <= 65536)
        report = json_copy(parse_json(data.decode("utf-8")))
        keys(report, REPORT_KEYS)
        require(digest(report["binding"]) == digest(payload["binding"]))
        shot_ids = [image["shot_id"] for image in payload["images"]]
        checks = report["shot_checks"]
        require(type(checks) is list and len(checks) == len(shot_ids))
        require([check["shot_id"] for check in checks] == shot_ids)
        statuses, failed, rejected = [], set(), set()
        cross = report["cross_checks"]
        require(type(cross) is list and len(cross) <= 16 and (len(shot_ids) == 1 or bool(cross)))
        for check, single in [(item, True) for item in checks] + [(item, False) for item in cross]:
            keys(check, {"shot_id" if single else "shot_ids", "verdict", "observation"})
            selected = [check["shot_id"]] if single else check["shot_ids"]
            targets(selected, shot_ids, 1 if single else 2)
            require(check["verdict"] in STATES)
            text(check["observation"])
            statuses.append(check["verdict"])
            if check["verdict"] != "accepted":
                failed.update(selected)
            if check["verdict"] == "rejected":
                rejected.update(selected)
        require(type(report["findings"]) is list and len(report["findings"]) <= 64)
        seen = set()
        for finding in report["findings"]:
            keys(finding, {"finding_id", "criterion", "shot_ids", "observation", "repair_instruction", "executable"})
            text(finding["finding_id"])
            require(len(finding["finding_id"]) <= 128 and finding["finding_id"] not in seen)
            seen.add(finding["finding_id"])
            require(finding["criterion"] in {"AS01", "AS02", "AS03", "AS04", "AS05"})
            targets(finding["shot_ids"], shot_ids)
            require(set(finding["shot_ids"]) <= failed and type(finding["executable"]) is bool)
            text(finding["observation"])
            if finding["repair_instruction"] is not None:
                text(finding["repair_instruction"])
            if finding["executable"]:
                require(set(finding["shot_ids"]) <= rejected and finding["repair_instruction"] is not None)
        aggregate = "rejected" if "rejected" in statuses else "needs_input" if "needs_input" in statuses else "accepted"
        require(report["overall_verdict"] == aggregate)
        return report
    except Exception:
        raise VisualContractError() from None


def build_image_repair(payload, report):
    try:
        payload = checked_input(payload)
        report = validate_visual_review(json.dumps(json_copy(report), ensure_ascii=False, allow_nan=False), payload)
        if report["overall_verdict"] == "accepted":
            return None
        selected = {}
        if report["overall_verdict"] == "rejected":
            for finding in report["findings"]:
                if finding["executable"]:
                    for shot_id in finding["shot_ids"]:
                        selected.setdefault(shot_id, []).append(finding)
        repair = []
        for image in payload["images"]:
            found = selected.get(image["shot_id"], [])
            if found:
                repair.append({"shot_id": image["shot_id"], "old_version": image["version"], "old_sha256": image["sha256"],
                    "next_version": image["version"] + 1, "finding_ids": [f["finding_id"] for f in found],
                    "repair_instructions": [f["repair_instruction"] for f in found]})
        scope = {"kind": "image_repair_ready" if repair else "needs_input", "binding": {**payload["binding"], "report_hash": digest(report)},
            "images": payload["images"], "targets": repair, "preserve": [i for i in payload["images"] if i["shot_id"] not in selected]}
        return {**scope, "scope_hash": digest(scope)}
    except Exception:
        raise VisualContractError() from None


def validate_image_revision(scope, revised_images):
    try:
        scope = json_copy(scope)
        keys(scope, {"kind", "binding", "images", "targets", "preserve", "scope_hash"})
        require(scope["kind"] == "image_repair_ready")
        sha(scope["scope_hash"])
        require(digest({k: v for k, v in scope.items() if k != "scope_hash"}) == scope["scope_hash"])
        keys(scope["binding"], BINDING_KEYS | {"report_hash"})
        require(type(scope["binding"]["plan_version"]) is int and scope["binding"]["plan_version"] > 0)
        require(scope["binding"]["rule_version"] == RULE_VERSION and scope["binding"]["rule_hash"] == RULE_HASH)
        for name in ("source_snapshot_hash", "plan_hash", "manifest_hash", "image_set_hash", "report_hash"):
            sha(scope["binding"][name])
        shot_ids = [i["shot_id"] for i in scope["images"]]
        original = imageset(scope["images"], shot_ids)
        require(digest(original) == scope["binding"]["image_set_hash"])
        require(type(scope["targets"]) is list and 0 < len(scope["targets"]) <= len(original))
        selected = {}
        for target in scope["targets"]:
            keys(target, {"shot_id", "old_version", "old_sha256", "next_version", "finding_ids", "repair_instructions"})
            require(target["shot_id"] in shot_ids and target["shot_id"] not in selected)
            before = original[shot_ids.index(target["shot_id"])]
            require(type(target["old_version"]) is int and target["old_version"] == before["version"] and target["old_sha256"] == before["sha256"])
            require(type(target["next_version"]) is int and target["next_version"] == before["version"] + 1)
            require(type(target["finding_ids"]) is list and type(target["repair_instructions"]) is list
                and 0 < len(target["finding_ids"]) == len(target["repair_instructions"]))
            for value in [*target["finding_ids"], *target["repair_instructions"]]:
                text(value)
            selected[target["shot_id"]] = target
        require(digest(scope["preserve"]) == digest([i for i in original if i["shot_id"] not in selected]))
        revised = imageset(revised_images, shot_ids)
        for before, after in zip(original, revised, strict=True):
            if before["shot_id"] in selected:
                require(after["version"] == before["version"] + 1 and after["sha256"] != before["sha256"])
            else:
                require(digest(before) == digest(after))
        return {"parent_binding": scope["binding"], "scope_hash": scope["scope_hash"], "images": revised,
            "image_set_hash": digest(revised), "recheck_required": True}
    except Exception:
        raise VisualContractError() from None
