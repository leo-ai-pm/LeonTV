"""Pure accepted-image handoff; no pixels, execution, billing or permissions."""
import json
import re

from core.creator.store import digest
from core.planner.asset_review import checked_input, validate_visual_review
from core.planner.protocol import checked_plan
from core.planner.review import json_copy


class VideoBriefError(ValueError):
    def __init__(self):
        self.code = "VIDEO_BRIEF_INVALID"
        super().__init__(self.code)


def require(value):
    if not value:
        raise VideoBriefError() from None


def timecode(milliseconds):
    return f"{milliseconds // 1000:02d}.{milliseconds % 1000:03d}秒"


def compile_prompt(timeline, characters, scenes):
    lines = ["生成15秒、9:16、写实影视视频，严格使用以下原计划与参考图。",
        "每张参考图是对应镜头的动作起点；按原时码与原节奏硬切连接各镜。",
        "保持共享角色与场景定型，严格按各镜原画面、机位和音频表述呈现，不新增动作、对白或人物。",
        "不要把参考图排成拼贴或幻灯片。"]
    for character in characters:
        lines.append(f"共享角色{character['character_id']}（{character['name']}）："
            f"外貌={character['appearance']}；服饰={character['wardrobe']}。")
    for scene in scenes:
        lines.append(f"共享场景{scene['scene_id']}（{scene['name']}）："
            f"环境={scene['environment']}；照明={scene['lighting']}。")
    for shot in timeline:
        lines.extend([f"{timecode(shot['start_ms'])}—{timecode(shot['end_ms'])}，"
            f"镜头{shot['shot_id']}，参考图{shot['image_order']}对应本镜起点：",
            "原画面：" + shot["visual"], "原机位：" + shot["camera"], "原音频：" + shot["audio"]])
    return "\n".join(lines)


def build_video_brief(visual_payload, visual_report) -> dict:
    """Derive one model-neutral brief; acceptance remains an input judgement."""
    try:
        payload = checked_input(visual_payload)
        report = validate_visual_review(json.dumps(json_copy(visual_report), ensure_ascii=False, allow_nan=False), payload)
        require(report["overall_verdict"] == "accepted")
        asset = payload["asset_payload"]
        parent = asset["review_payload"]
        plan, error = checked_plan(parent["plan"]["artifact"], payload["binding"]["source_snapshot_hash"])
        require(error is None and plan["duration_ms"] == 15000 and plan["ratio"] == "9:16")
        require(sum(shot["duration_ms"] for shot in plan["shots"]) == 15000)
        manifest = payload["manifest"]
        frames, images = manifest["frames"], payload["images"]
        require(len(frames) == len(images) == len(plan["shots"]))
        styles = {frame["style"] for frame in frames}
        require(styles == {"realistic_cinematic"})
        timeline, start = [], 0
        for shot, frame, image in zip(plan["shots"], frames, images, strict=True):
            require(shot["id"] == frame["shot_id"] == image["shot_id"])
            end = start + shot["duration_ms"]
            timeline.append({"shot_id": shot["id"], "start_ms": start, "end_ms": end,
                **{name: shot[name] for name in ("duration_ms", "visual", "camera", "audio")},
                "image_order": frame["image_order"], "image_version": image["version"], "image_sha256": image["sha256"]})
            start = end
        require(start == 15000)
        text_binding = manifest["binding"]
        binding = {**payload["binding"], "review_hash": digest(report),
            "text_review_id": text_binding["review_id"], "text_review_hash": text_binding["review_hash"],
            "text_rule_version": text_binding["rule_version"], "text_rule_hash": text_binding["rule_hash"]}
        brief = {"binding": binding, "duration_ms": plan["duration_ms"], "ratio": plan["ratio"],
            "style": frames[0]["style"], "timeline": timeline,
            "characters": manifest["characters"], "scenes": manifest["scenes"],
            "prompt": compile_prompt(timeline, manifest["characters"], manifest["scenes"])}
        return {**brief, "brief_hash": digest(brief)}
    except Exception:
        raise VideoBriefError() from None


def checked_video_brief(brief, visual_payload, visual_report, expected_brief_hash) -> dict:
    """Rebuild from parents and require the caller's independently frozen hash."""
    try:
        brief = json_copy(brief)
        require(type(expected_brief_hash) is str and re.fullmatch(r"[0-9a-f]{64}", expected_brief_hash) is not None)
        rebuilt = build_video_brief(visual_payload, visual_report)
        require(brief == rebuilt)
        require(brief["brief_hash"] == expected_brief_hash == digest({k: v for k, v in brief.items() if k != "brief_hash"}))
        return rebuilt
    except Exception:
        raise VideoBriefError() from None
