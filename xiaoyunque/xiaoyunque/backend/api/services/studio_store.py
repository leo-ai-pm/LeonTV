"""Persist desktop studio canvases without modifying legacy workflow sessions."""

from __future__ import annotations

import json
import copy
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from config import settings


PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
STAGES = (
    "script_generation",
    "character_design",
    "storyboard",
    "reference_generation",
    "video_generation",
    "post_production",
)


def _node(node_id: str, kind: str, title: str, x: int, y: int, **data: Any) -> dict:
    return {
        "id": node_id,
        "type": "studio",
        "position": {"x": x, "y": y},
        "data": {"kind": kind, "title": title, **data},
    }


def _edge(source: str, target: str) -> dict:
    return {"id": f"{source}--{target}", "source": source, "target": target, "type": "smoothstep"}


def project_from_session(session_id: str, snapshot: dict) -> dict:
    """Create an editable view of a saved six-stage project, keyed by legacy IDs."""
    meta = snapshot.get("meta") or {}
    artifacts = snapshot.get("artifacts") or {}
    status = snapshot.get("status") or {}
    script = artifacts.get("script_generation") or {}
    title = script.get("title") or meta.get("idea") or "未命名作品"
    nodes: list[dict] = []
    edges: list[dict] = []
    script_id = "stage-script"
    nodes.append(_node(script_id, "script", script.get("title") or "剧本", 80, 220,
                       description=script.get("logline") or meta.get("idea") or "",
                       sourceStage="script_generation", status=status.get("script_generation", "pending")))

    design = artifacts.get("character_design") or {}
    assets = [*(design.get("characters") or []), *(design.get("settings") or [])]
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("id") or index)
        node_id = f"asset-{asset_id}"
        nodes.append(_node(node_id, "character" if index < len(design.get("characters") or []) else "scene",
                           asset.get("name") or "角色/场景", 420, 45 + index * 170,
                           description=asset.get("description") or "", mediaPath=asset.get("selected") or "",
                           versions=asset.get("versions") or [], sourceStage="character_design", sourceId=asset_id,
                           status=asset.get("status") or status.get("character_design", "pending")))
        edges.append(_edge(script_id, node_id))

    segments: list[dict] = []
    storyboard = artifacts.get("storyboard") or {}
    for episode in storyboard.get("episodes") or []:
        if isinstance(episode, dict):
            for segment in episode.get("segments") or []:
                if isinstance(segment, dict):
                    segments.append({**segment, "episode_number": episode.get("episode_number")})
    refs = {str(item.get("id")): item for item in (artifacts.get("reference_generation") or {}).get("scenes", []) if isinstance(item, dict)}
    clips = {str(item.get("id")): item for item in (artifacts.get("video_generation") or {}).get("clips", []) if isinstance(item, dict)}
    for index, segment in enumerate(segments):
        segment_id = str(segment.get("segment_id") or f"segment-{index}")
        y = 45 + index * 200
        shot_id = f"shot-{segment_id}"
        nodes.append(_node(shot_id, "storyboard", f"第 {segment.get('episode_number') or 1} 集 · 片段 {segment.get('segment_number') or index + 1}",
                           780, y, description=segment.get("video_prompt") or segment.get("visual_prompt") or "",
                           visualPrompt=segment.get("visual_prompt") or "", duration=segment.get("total_duration"),
                           shots=segment.get("shots") or [], sourceStage="storyboard", sourceId=segment_id,
                           status=status.get("storyboard", "pending")))
        edges.append(_edge(script_id, shot_id))
        ref = refs.get(segment_id)
        if ref:
            ref_id = f"reference-{segment_id}"
            nodes.append(_node(ref_id, "image", ref.get("name") or "参考图", 1140, y,
                               description=ref.get("description") or segment.get("visual_prompt") or "",
                               mediaPath=ref.get("selected") or "", versions=ref.get("versions") or [],
                               sourceStage="reference_generation", sourceId=segment_id,
                               status=ref.get("status") or status.get("reference_generation", "pending")))
            edges.append(_edge(shot_id, ref_id))
        clip = clips.get(segment_id)
        if clip:
            clip_id = f"video-{segment_id}"
            nodes.append(_node(clip_id, "video", clip.get("name") or "视频片段", 1500, y,
                               description=clip.get("description") or "", mediaPath=clip.get("selected") or "",
                               versions=clip.get("versions") or [], duration=clip.get("duration"),
                               sourceStage="video_generation", sourceId=segment_id,
                               status=clip.get("status") or status.get("video_generation", "pending")))
            edges.append(_edge(f"reference-{segment_id}" if ref else shot_id, clip_id))

    final = artifacts.get("post_production") or {}
    videos = final.get("final_videos") or []
    if videos or final.get("final_video"):
        output_id = "stage-output"
        nodes.append(_node(output_id, "output", "最终成片", 1850, 220,
                           mediaPath=(videos[0].get("path") if videos and isinstance(videos[0], dict) else final.get("final_video")) or "",
                           sourceStage="post_production", status=status.get("post_production", "pending")))
        for clip_id in clips:
            if any(n["id"] == f"video-{clip_id}" for n in nodes):
                edges.append(_edge(f"video-{clip_id}", output_id))

    now = time.time()
    return {
        "id": session_id,
        "title": str(title)[:120],
        "kind": "workflow",
        "source_session_id": session_id,
        "revision": 0,
        "created_at": now,
        "updated_at": now,
        "trashed": False,
        "nodes": nodes,
        "edges": edges,
    }


class StudioStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or Path(settings.CODE_DIR) / "data" / "studio_projects")
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def _path(self, project_id: str) -> Path:
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("Invalid project ID")
        return self.root / f"{project_id}.json"

    def _read(self, project_id: str) -> dict | None:
        path = self._path(project_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _write(self, document: dict) -> None:
        path = self._path(document["id"])
        fd, temp_path = tempfile.mkstemp(prefix=".studio-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(document, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def list(self, workflow_engine: Any, trashed: bool = False) -> list[dict]:
        with self.lock:
            stored: dict[str, dict] = {}
            for path in self.root.glob("*.json"):
                try:
                    doc = self._read(path.stem)
                    if doc:
                        stored[doc["id"]] = doc
                except (ValueError, OSError, json.JSONDecodeError):
                    continue
            result = [self._summary(doc) for doc in stored.values() if bool(doc.get("trashed")) == trashed]
            if not trashed:
                for session in workflow_engine.list_saved_sessions():
                    if session["id"] not in stored:
                        result.append({"id": session["id"], "title": session.get("title") or session.get("idea") or "未命名作品",
                                       "kind": "workflow", "updated_at": session.get("date") or 0,
                                       "node_count": 0, "trashed": False})
            return sorted(result, key=lambda item: item["updated_at"], reverse=True)

    @staticmethod
    def _summary(doc: dict) -> dict:
        return {"id": doc["id"], "title": doc["title"], "kind": doc.get("kind", "canvas"),
                "updated_at": doc.get("updated_at", 0), "node_count": len(doc.get("nodes") or []),
                "trashed": bool(doc.get("trashed"))}

    def create(self, title: str) -> dict:
        with self.lock:
            now = time.time()
            doc = {"id": uuid.uuid4().hex, "title": title.strip()[:120] or "未命名作品", "kind": "canvas",
                   "source_session_id": None, "revision": 1, "created_at": now, "updated_at": now,
                   "trashed": False, "nodes": [], "edges": []}
            self._write(doc)
            return doc

    def clone(self, project_id: str, workflow_engine: Any) -> dict:
        with self.lock:
            source = self.get(project_id, workflow_engine)
            if not source or source.get("trashed"):
                raise KeyError(project_id)
            now = time.time()
            duplicate = copy.deepcopy(source)
            duplicate.update({"id": uuid.uuid4().hex, "title": f"{source['title']}（副本）"[:120],
                              "kind": "canvas", "source_session_id": None,
                              "revision": 1, "created_at": now, "updated_at": now, "trashed": False})
            for node in duplicate["nodes"]:
                node.get("data", {}).pop("sourceStage", None)
            self._write(duplicate)
            return duplicate

    def get(self, project_id: str, workflow_engine: Any) -> dict | None:
        with self.lock:
            doc = self._read(project_id)
            if doc:
                return doc
            snapshot = workflow_engine.get_status_snapshot(project_id)
            return project_from_session(project_id, snapshot) if snapshot else None

    def save(self, project_id: str, workflow_engine: Any, payload: dict) -> dict:
        with self.lock:
            doc = self.get(project_id, workflow_engine)
            if not doc:
                raise KeyError(project_id)
            if doc.get("trashed"):
                raise ValueError("Project is in the trash")
            if payload.get("expected_revision") != doc["revision"]:
                raise RuntimeError("Project was changed elsewhere; reload before saving")
            nodes = payload["nodes"]
            edges = payload["edges"]
            ids = {n.get("id") for n in nodes}
            if len(ids) != len(nodes) or None in ids or any(not isinstance(n.get("id"), str) for n in nodes):
                raise ValueError("Invalid or duplicate node IDs")
            if any(e.get("source") not in ids or e.get("target") not in ids for e in edges):
                raise ValueError("An edge references a missing node")
            doc.update({"title": (payload.get("title") or doc["title"]).strip()[:120], "nodes": nodes,
                        "edges": edges, "updated_at": time.time(), "revision": doc["revision"] + 1})
            self._write(doc)
            return doc

    def set_trashed(self, project_id: str, workflow_engine: Any, trashed: bool) -> dict:
        with self.lock:
            doc = self.get(project_id, workflow_engine)
            if not doc:
                raise KeyError(project_id)
            doc["trashed"] = trashed
            doc["updated_at"] = time.time()
            doc["revision"] += 1
            self._write(doc)
            return doc


studio_store = StudioStore()
