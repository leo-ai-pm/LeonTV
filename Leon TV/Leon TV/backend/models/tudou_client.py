"""Async image/video adapter for the documented Tudou API relay endpoints."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from config import Config

logger = logging.getLogger(__name__)


class TudouTaskError(RuntimeError):
    def __init__(self, message: str, task_id: str | None, code: str, status: str):
        super().__init__(f"{message}（任务 {task_id}）" if task_id else message)
        self.task_id, self.code, self.status = task_id, code, status


class TudouPollTimeout(TimeoutError):
    def __init__(self, task_id: str):
        super().__init__(f"土豆 API 查询等待超时，上游状态尚未确认；请继续查询原任务：{task_id}")
        self.task_id, self.code, self.status = task_id, "poll_timeout", "unknown"


class TudouClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 task_callback: Callable[[dict], None] | None = None):
        self.api_key = api_key or Config.TUDOU_API_KEY
        self.task_callback = task_callback
        self.base_url = (base_url or Config.TUDOU_BASE_URL or "https://api.ai-tudou.net").rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]

    @property
    def headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("请先在设置中填写土豆 API 的密钥。")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _submit(self, endpoint: str, payload: dict) -> str:
        response = requests.post(f"{self.base_url}{endpoint}", headers=self.headers, json=payload,
                                 timeout=120, proxies=Config.requests_proxies("tudou"))
        response.raise_for_status()
        data = response.json()
        item = data.get("data") or data
        task_id = item.get("id") or item.get("task_id")
        if not task_id:
            raise RuntimeError(f"土豆 API 未返回任务 ID：{str(data)[:300]}")
        logger.info("Tudou submitted task_id=%s model=%s", task_id, payload.get("model"))
        return str(task_id)

    def _poll(self, task_id: str, *, interval: int = 10, timeout: int = 900) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = requests.get(f"{self.base_url}/v1/tasks/{task_id}", headers=self.headers,
                                    timeout=60, proxies=Config.requests_proxies("tudou"))
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in (None, 0, 200):
                raise TudouTaskError(f"土豆 API 查询失败：{str(payload.get('message') or payload.get('code'))[:200]}",
                                     task_id, "query_failed", "unknown")
            item = payload.get("data") or payload
            status = str(item.get("status") or "").lower()
            if status in {"completed", "succeeded"}:
                return item.get("result") or {}
            if status in {"failed", "error", "expired", "cancelled"}:
                error = item.get("error") or {}
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise TudouTaskError(f"土豆 API 上游任务失败：{message or status}",
                                     task_id, "upstream_failed", status)
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        raise TudouPollTimeout(task_id)

    def _save_receipt(self, path: Path, receipt: dict) -> None:
        receipt["updated_at"] = time.time()
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        if self.task_callback:
            self.task_callback({"provider": "tudou", "task_id": receipt.get("task_id"),
                                "status": receipt["status"], "receipt_path": str(path),
                                "error_code": (receipt.get("error") or {}).get("code")})

    def _complete_video(self, receipt_path: Path, receipt: dict) -> str:
        task_id = receipt["task_id"]
        completed = False
        try:
            result = self._poll(task_id)
            completed = True
            receipt.update(status="completed", result=result, error=None)
            self._save_receipt(receipt_path, receipt)
            url = self._first_url(result, "video")
            self._download(url, Path(receipt["save_path"]))
            receipt["downloaded"] = True
            self._save_receipt(receipt_path, receipt)
            return url
        except Exception as exc:
            if isinstance(exc, TimeoutError) and not completed:
                error = TudouPollTimeout(task_id)
            elif isinstance(exc, TudouTaskError):
                error = exc
            else:
                error = TudouTaskError(str(exc), task_id,
                                       "delivery_failed" if completed else "query_failed",
                                       "completed" if completed else "unknown")
            receipt.update(status=error.status,
                           error={"code": error.code, "message": str(error)})
            try:
                self._save_receipt(receipt_path, receipt)
            except Exception:
                logger.warning("Unable to persist Tudou task state: task_id=%s", task_id)
            if error is exc:
                raise
            raise error from exc

    def resume_video(self, receipt_path: str) -> str:
        """Query/download an accepted task; this method never calls the submit endpoint."""
        path = Path(receipt_path)
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("provider") != "tudou" or not receipt.get("task_id"):
            raise ValueError("没有已接受的土豆任务，无法继续查询。")
        if receipt.get("base_url") != self.base_url:
            raise ValueError("土豆 API 地址已改变，请恢复原任务使用的地址后再查询。")
        return self._complete_video(path, receipt)

    @staticmethod
    def _first_url(result: dict, kind: str) -> str:
        if kind == "video" and result.get("video_url"):
            url = str(result["video_url"])
            if urlparse(url).scheme in {"http", "https"}:
                return url
        entries = result.get("images" if kind == "image" else "videos") or []
        # The live Wan relay also returns MP4 URLs inside the generic images array.
        if kind == "video" and not entries:
            entries = result.get("images") or []
        first = entries[0] if entries else {}
        urls = first.get("url") if isinstance(first, dict) else None
        url = urls[0] if isinstance(urls, list) and urls else urls
        if not url or urlparse(str(url)).scheme not in {"http", "https"}:
            raise RuntimeError(f"土豆 API 已完成任务，但未返回可用的{kind}链接。")
        if kind == "video" and not result.get("videos") and result.get("images"):
            if Path(urlparse(str(url)).path).suffix.lower() not in {".mp4", ".webm", ".mov"}:
                raise RuntimeError("土豆 API 视频任务返回了图片链接，无法作为视频下载。")
        return str(url)

    @staticmethod
    def _reference_image(value: str) -> str:
        if value.startswith(("http://", "https://", "data:image/")):
            return value
        source = Path(value)
        if not source.is_file():
            raise FileNotFoundError(f"参考图不存在：{value}")
        mime = mimetypes.guess_type(source.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(source.read_bytes()).decode('ascii')}"

    @staticmethod
    def _public_video_reference(value: str) -> str:
        if value.startswith(("http://", "https://")):
            return value
        source = Path(value)
        sidecar = Path(f"{source}.tudou-source.json")
        if source.is_file() and sidecar.is_file():
            record = json.loads(sidecar.read_text(encoding="utf-8"))
            url = str(record.get("url") or "")
            expires_at = record.get("expires_at")
            if url.startswith(("http://", "https://")) and (not expires_at or float(expires_at) > time.time()):
                return url
        raise ValueError("土豆 API 图生视频需要公网参考图。请先用土豆 API 生成参考图，或提供公网图片 URL。")

    @staticmethod
    def _download(url: str, path: Path) -> None:
        response = requests.get(url, stream=True, timeout=120,
                                proxies=Config.requests_proxies("tudou"))
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        try:
            with temp_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
            if temp_path.stat().st_size == 0:
                raise RuntimeError("土豆 API 返回空媒体文件。")
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
            response.close()

    def generate_image(self, prompt: str, image_paths: list[str] | None, ratio: str,
                       save_dir: str) -> str:
        body = {"model": "gpt-image-2-all", "prompt": prompt, "size": ratio or "16:9",
                "resolution": "1k", "quality": "medium"}
        if image_paths:
            body["images"] = [self._reference_image(value) for value in image_paths]
        task_id = self._submit("/v1/images/generations/async", body)
        result = self._poll(task_id)
        url = self._first_url(result, "image")
        entry = (result.get("images") or [{}])[0]
        path = Path(save_dir) / f"tudou_{uuid.uuid4().hex}.png"
        self._download(url, path)
        sidecar = Path(f"{path}.tudou-source.json")
        try:
            sidecar.write_text(json.dumps({"url": url, "expires_at": entry.get("expires_at")},
                                          ensure_ascii=False), encoding="utf-8")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return str(path)

    def generate_video(self, prompt: str, image_path: str, save_path: str,
                       model: str = "wan3.0", duration: int = 5,
                       ratio: str = "16:9", resolution: str = "720p") -> str:
        self.headers  # Validate credentials before recording an uncertain submission.
        body = {"model": model, "prompt": prompt, "duration": int(duration),
                "aspect_ratio": ratio, "resolution": resolution}
        if model.lower() not in {"seedance-2.0", "minimax-h3", "seedance-2.5"}:
            body["extra"] = {"aspect_ratio": ratio, "resolution": resolution}
        if image_path:
            body["images"] = [self._public_video_reference(image_path)]
        path = Path(save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path = Path(f"{path}.tudou-task.json")
        if receipt_path.exists():
            previous = json.loads(receipt_path.read_text(encoding="utf-8"))
            if previous.get("request") != body:
                raise ValueError("该输出路径已有不同请求的任务回执，请保留原任务并使用新路径。")
            if previous.get("task_id"):
                return self.resume_video(str(receipt_path))
            # A submission with an unknown outcome must not be sent again automatically.
            if previous.get("status") in {"submitting", "unknown"}:
                raise TudouTaskError("原提交结果尚未确认，请先核查土豆任务记录，避免重复扣费。",
                                     None, "submit_unknown", "unknown")
        receipt = {"provider": "tudou", "base_url": self.base_url,
                   "request": body, "save_path": str(path), "status": "submitting",
                   "submitted_at": time.time(), "task_id": None}
        self._save_receipt(receipt_path, receipt)
        try:
            task_id = self._submit("/v1/videos/generations", body)
        except Exception as exc:
            response = getattr(exc, "response", None)
            http_status = response.status_code if response is not None else None
            rejected = http_status is not None and 400 <= http_status < 500 and http_status != 408
            status, code = ("rejected", "submit_rejected") if rejected else ("unknown", "submit_unknown")
            message = f"土豆 API 拒绝提交：{exc}" if rejected else "土豆 API 提交结果尚未确认，请先核查提供商任务记录，避免重复扣费。"
            receipt.update(status=status, error={"code": code, "message": message})
            self._save_receipt(receipt_path, receipt)
            raise TudouTaskError(message, None, code, status) from exc
        receipt.update(task_id=task_id, status="accepted")
        try:
            self._save_receipt(receipt_path, receipt)
        except Exception as exc:
            raise TudouTaskError("土豆已接受任务，但回执保存异常，请先核查原任务。",
                                 task_id, "receipt_failed", "unknown") from exc
        return self._complete_video(receipt_path, receipt)
