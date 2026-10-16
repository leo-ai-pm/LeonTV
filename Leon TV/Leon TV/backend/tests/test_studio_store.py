"""Meaningful persistence and compatibility checks for the desktop studio."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from api.services.studio_store import StudioStore, project_from_session
from config import settings
from media_tools import ffmpeg_executable


class FakeEngine:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    def get_status_snapshot(self, session_id):
        return self.snapshot if session_id == "legacy-1" else None

    def list_saved_sessions(self):
        return [{"id": "legacy-1", "title": "旧项目", "date": 12}] if self.snapshot else []


class StudioStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StudioStore(self.temp.name)
        self.engine = FakeEngine()

    def tearDown(self):
        self.temp.cleanup()

    def test_blank_project_persists_and_rejects_stale_revision(self):
        created = self.store.create("第一部作品")
        project_id = created["id"]
        node = {"id": "intro", "type": "studio", "position": {"x": 4, "y": 8}, "data": {"kind": "text", "title": "开场"}}
        saved = self.store.save(project_id, self.engine, {
            "expected_revision": created["revision"], "title": "改名后", "nodes": [node], "edges": [],
        })
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(self.store.get(project_id, self.engine)["nodes"][0]["id"], "intro")
        with self.assertRaises(RuntimeError):
            self.store.save(project_id, self.engine, {
                "expected_revision": 1, "title": "过期编辑", "nodes": [], "edges": [],
            })
        self.assertEqual(self.store.get(project_id, self.engine)["title"], "改名后")

    def test_trash_and_restore_keeps_canvas(self):
        created = self.store.create("可恢复")
        project_id = created["id"]
        self.store.set_trashed(project_id, self.engine, True)
        self.assertEqual(self.store.list(self.engine), [])
        self.assertEqual(self.store.list(self.engine, trashed=True)[0]["id"], project_id)
        self.store.set_trashed(project_id, self.engine, False)
        self.assertEqual(self.store.list(self.engine)[0]["title"], "可恢复")

    def test_clone_is_independent_from_source(self):
        created = self.store.create("原作")
        cloned = self.store.clone(created["id"], self.engine)
        self.assertNotEqual(cloned["id"], created["id"])
        self.assertEqual(cloned["title"], "原作（副本）")
        self.store.save(cloned["id"], self.engine, {
            "expected_revision": 1, "title": "改好的副本", "nodes": [], "edges": [],
        })
        self.assertEqual(self.store.get(created["id"], self.engine)["title"], "原作")

    def test_legacy_projection_preserves_segment_links_and_selected_versions(self):
        snapshot = {
            "meta": {"idea": "深海来信"}, "status": {"storyboard": "completed"},
            "artifacts": {
                "script_generation": {"title": "深海来信", "logline": "一封迟到的信"},
                "storyboard": {"episodes": [{"episode_number": 1, "segments": [
                    {"segment_id": "seg_01_01", "segment_number": 1, "video_prompt": "海边远景", "shots": []}
                ]}]},
                "reference_generation": {"scenes": [{"id": "seg_01_01", "selected": "result/a.png", "versions": ["result/a.png", "result/b.png"]}]},
                "video_generation": {"clips": [{"id": "seg_01_01", "selected": "result/a.mp4", "versions": ["result/a.mp4"]}]},
            },
        }
        self.engine = FakeEngine(snapshot)
        projected = project_from_session("legacy-1", snapshot)
        self.assertEqual(projected["revision"], 0)
        self.assertEqual(projected["nodes"][0]["data"]["description"], "一封迟到的信")
        self.assertIn("shot-seg_01_01--reference-seg_01_01", {edge["id"] for edge in projected["edges"]})
        reference = next(node for node in projected["nodes"] if node["id"] == "reference-seg_01_01")
        self.assertEqual(reference["data"]["mediaPath"], "result/a.png")
        self.assertEqual(len(reference["data"]["versions"]), 2)
        self.assertFalse((self.store.root / "legacy-1.json").exists())

    def test_rejects_path_traversal_and_dangling_edges(self):
        with self.assertRaises(ValueError):
            self.store.get("../outside", self.engine)
        created = self.store.create("图")
        with self.assertRaises(ValueError):
            self.store.save(created["id"], self.engine, {
                "expected_revision": 1, "title": "图", "nodes": [],
                "edges": [{"id": "bad", "source": "missing", "target": "also-missing"}],
            })

    def test_export_requires_every_video_clip(self):
        created = self.store.create("缺少片段")
        node = {"id": "shot-1", "type": "studio", "position": {"x": 100, "y": 100},
                "data": {"kind": "video", "title": "镜头一", "status": "idle"}}
        saved = self.store.save(created["id"], self.engine, {
            "expected_revision": 1, "title": "缺少片段", "nodes": [node], "edges": [],
        })
        with self.assertRaisesRegex(ValueError, "no generated clip"):
            self.store.export(created["id"], self.engine, saved["revision"])

    def test_export_joins_video_nodes_in_canvas_order(self):
        with tempfile.TemporaryDirectory(dir=settings.CODE_DIR) as media_root:
            media_dir = Path(media_root)
            store = StudioStore(self.temp.name, export_root=media_dir / "exports")
            created = store.create("两段视频")
            nodes = []
            for index, color in enumerate(("red", "blue")):
                path = media_dir / f"{color}.mp4"
                subprocess.run([ffmpeg_executable(), "-y", "-hide_banner", "-loglevel", "error",
                                "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=25:d=1",
                                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1.04",
                                "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(path)],
                               check=True, capture_output=True, timeout=30)
                nodes.append({"id": f"shot-{index}", "type": "studio",
                              "position": {"x": 600 - index * 400, "y": index * 100},
                              "data": {"kind": "video", "title": color, "status": "completed",
                                       "mediaPath": str(path)}})
            saved = store.save(created["id"], self.engine, {
                "expected_revision": 1, "title": "两段视频", "nodes": list(reversed(nodes)), "edges": [],
            })
            exported = store.export(created["id"], self.engine, saved["revision"])
            self.assertEqual(exported["exports"][0]["clip_ids"], ["shot-0", "shot-1"])
            output = Path(settings.CODE_DIR) / exported["exports"][0]["path"]
            self.assertGreater(output.stat().st_size, 1000)
            subprocess.run([ffmpeg_executable(), "-v", "error", "-i", str(output),
                            "-f", "null", "-"], check=True, capture_output=True, timeout=30)
            from imageio_ffmpeg import count_frames_and_secs, read_frames
            reader = read_frames(output)
            try:
                metadata = next(reader)
            finally:
                reader.close()
            frames, _ = count_frames_and_secs(output)
            self.assertEqual(frames, 50)
            self.assertAlmostEqual(frames / metadata["fps"], 2.0, places=2)
            self.assertAlmostEqual(metadata["duration"], 2.0, places=2)


if __name__ == "__main__":
    unittest.main()
