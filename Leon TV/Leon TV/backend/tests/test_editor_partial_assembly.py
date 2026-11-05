"""部分出片：缺镜不再阻断后期，且必须如实报告缺了哪些。"""
import os

from core.agents.editor_agent import plan_assembly_selection


def _touch(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"fake")
    return str(path)


def test_missing_clip_is_reported_but_does_not_block_the_rest(tmp_path):
    good = _touch(tmp_path, "seg_01_02.mp4")
    usable, missing = plan_assembly_selection([
        {"id": "seg_01_01", "selected": ""},
        {"id": "seg_01_02", "selected": good},
    ])
    assert usable == [("seg_01_02", good)]
    assert missing == ["seg_01_01"]


def test_a_path_that_no_longer_exists_counts_as_missing(tmp_path):
    usable, missing = plan_assembly_selection([
        {"id": "seg_01_01", "selected": str(tmp_path / "gone.mp4")},
    ])
    assert usable == []
    assert missing == ["seg_01_01"]


def test_fully_available_set_reports_no_missing(tmp_path):
    first = _touch(tmp_path, "a.mp4")
    second = _touch(tmp_path, "b.mp4")
    usable, missing = plan_assembly_selection([
        {"id": "a", "selected": first},
        {"id": "b", "selected": second},
    ])
    assert [item[0] for item in usable] == ["a", "b"]
    assert missing == []


def test_legacy_selected_clips_mapping_is_supported(tmp_path):
    good = _touch(tmp_path, "shot.mp4")
    usable, missing = plan_assembly_selection([], {"shot-1": good, "shot-2": ""})
    assert usable == [("shot-1", good)]
    assert missing == ["shot-2"]


def test_nothing_usable_is_still_detectable_by_the_caller(tmp_path):
    usable, missing = plan_assembly_selection([{"id": "only", "selected": ""}])
    assert usable == []
    assert missing == ["only"]
