"""Async image/video adapter for the documented Tudou API relay endpoints."""

from __future__ import annotations

import base64
import hashlib
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
from core.harness.store import lock_file, unlock_file

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
        if (not isinstance(data, dict) or isinstance(data.get("code"), bool)
                or data.get("code") not in (None, 0, 200)):
            raise RuntimeError("土豆 API 提交未返回成功回执，请先核查原提交。")
        item = data.get("data") or data
        if not isinstance(item, dict):
            raise RuntimeError("土豆 API 提交未返回有效任务回执，请先核查原提交。")
        task_id = item.get("id") or item.get("task_id")
        if not task_id:
            raise RuntimeError(f"土豆 API 未返回任务 ID：{str(data)[:300]}")
        logger.info("Tudou submitted task_id=%s model=%s", task_id, payload.get("model"))
        return str(task_id)

    @staticmethod
    def _is_transient(error: Exception) -> bool:
        """网络抖动、超时、限流与 5xx 可重试；4xx 与业务错误不重试。"""
        if isinstance(error, (requests.exceptions.ConnectionError,
                              requests.exceptions.Timeout,
                              requests.exceptions.ChunkedEncodingError)):
            return True
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status in (408, 409, 429) or status >= 500
        return False

    def _get(self, path: str, *, attempts: int = 5, base_delay: float = 2.0,
             timeout: int = 60) -> dict:
        """带退避的 GET：只重试瞬时错误，绝不在这层重新提交任务。"""
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                response = requests.get(f"{self.base_url}{path}", headers=self.headers,
                                        timeout=timeout, proxies=Config.requests_proxies("tudou"))
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001 - 分类后决定是否重试
                if not self._is_transient(exc):
                    raise
                last = exc
                logger.warning("Tudou GET %s failed (attempt %s/%s): %s", path, attempt + 1, attempts, exc)
                if attempt + 1 < attempts:
                    time.sleep(min(base_delay * (2 ** attempt), 20))
        raise last if last else RuntimeError("Tudou GET failed")

    def _poll(self, task_id: str, *, interval: int = 10, timeout: int = 900) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = self._get(f"/v1/tasks/{task_id}")
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

    @staticmethod
    def _media_file_digest(path: Path) -> tuple[str, int]:
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def _refresh_completed_video_result(self, task_id: str) -> dict:
        """Read the same accepted task once; never poll, submit, or accept another task's result."""
        payload = self._get(f"/v1/tasks/{task_id}")
        if (not isinstance(payload, dict) or isinstance(payload.get("code"), bool)
                or payload.get("code") not in (None, 0, 200)):
            raise RuntimeError("土豆 API 未返回原视频任务的成功查询回执。")
        item = payload.get("data") or payload
        if not isinstance(item, dict):
            raise RuntimeError("土豆 API 未返回原视频任务的有效查询回执。")
        identifiers = [item[key] for key in ("id", "task_id") if item.get(key) is not None]
        if not identifiers or any(not isinstance(value, str) or value != task_id for value in identifiers):
            raise RuntimeError("土豆 API 查询回执与原视频任务编号不一致。")
        result = item.get("result")
        if str(item.get("status") or "").lower() not in {"completed", "succeeded"} or not isinstance(result, dict):
            raise RuntimeError("土豆 API 查询未确认原视频任务的完成结果。")
        if not urlparse(self._first_url(result, "video")).netloc:
            raise RuntimeError("土豆 API 查询未返回有效的视频链接。")
        return result

    def _complete_video(self, receipt_path: Path, receipt: dict) -> str:
        task_id = receipt["task_id"]
        completed = receipt.get("status") == "completed" and isinstance(receipt.get("result"), dict)
        cached_completed = completed
        try:
            result = receipt["result"] if completed else self._poll(task_id)
            completed = True
            receipt.update(status="completed", result=result, error=None)
            self._save_receipt(receipt_path, receipt)
            url = self._first_url(result, "video")
            path = Path(receipt["save_path"])
            delivered = False
            expected_digest, expected_size = receipt.get("delivery_sha256"), receipt.get("delivery_size_bytes")
            if (receipt.get("downloaded") and isinstance(expected_digest, str) and len(expected_digest) == 64
                    and type(expected_size) is int and expected_size > 0 and path.is_file()):
                try:
                    delivered = self._media_file_digest(path) == (expected_digest, expected_size)
                except OSError:
                    pass
            if not delivered:
                try:
                    self._download(url, path)
                except requests.HTTPError as exc:
                    response = exc.response
                    if not cached_completed or response is None or response.status_code not in {403, 404, 410}:
                        raise
                    refreshed_result = self._refresh_completed_video_result(task_id)
                    receipt.setdefault("previous_completed_result", result)
                    receipt["result"] = result = refreshed_result
                    self._save_receipt(receipt_path, receipt)
                    url = self._first_url(result, "video")
                    self._download(url, path)
                digest, size = self._media_file_digest(path)
                if not size:
                    raise RuntimeError("土豆 API 返回空视频文件。")
                receipt.update(downloaded=True, delivery_sha256=digest, delivery_size_bytes=size)
                self._save_receipt(receipt_path, receipt)
            return url
        except Exception as exc:
            if completed:
                error = TudouTaskError(str(exc), task_id, "delivery_failed", "completed")
            elif isinstance(exc, TimeoutError):
                error = TudouPollTimeout(task_id)
            elif isinstance(exc, TudouTaskError):
                error = exc
            else:
                error = TudouTaskError(str(exc), task_id, "query_failed", "unknown")
            receipt.update(status=error.status,
                           error={"code": error.code, "message": str(error)})
            try:
                self._save_receipt(receipt_path, receipt)
            except Exception:
                logger.warning("Unable to persist Tudou task state: task_id=%s", task_id)
            if error is exc:
                raise
            raise error from exc

    def _resume_video(self, path: Path, receipt: dict) -> str:
        if receipt.get("provider") != "tudou" or receipt.get("kind") == "image":
            raise ValueError("没有土豆视频任务回执，无法继续查询。")
        if receipt.get("base_url") != self.base_url:
            raise ValueError("土豆 API 地址已改变，请恢复原任务使用的地址后再查询。")
        save_path = receipt.get("save_path")
        if not isinstance(save_path, str) or not save_path or Path(f"{save_path}.tudou-task.json").resolve() != path:
            raise ValueError("视频输出路径与原任务回执不一致。")
        if receipt.get("status") in {"failed", "error", "expired", "cancelled", "rejected"}:
            error = receipt.get("error") or {}
            raise TudouTaskError(error.get("message") or "土豆视频原任务已终止，请保留原任务记录。",
                                 receipt.get("task_id"), error.get("code") or "upstream_failed", receipt["status"])
        if not receipt.get("task_id"):
            raise TudouTaskError("原提交结果尚未确认，请先核查土豆任务记录，避免重复扣费。",
                                 None, "submit_unknown", "unknown")
        return self._complete_video(path, receipt)

    def resume_video(self, receipt_path: str) -> str:
        """Query/deliver the original task under the same lock as generation; never submit."""
        path = Path(receipt_path).resolve()
        lock = lock_file(Path(f"{path}.lock"))
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            return self._resume_video(path, receipt)
        finally:
            unlock_file(lock)

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

    @staticmethod
    def _save_image_source(path: Path, source: dict) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _refresh_completed_image_result(self, task_id: str) -> dict:
        """Read the same accepted image task once; never poll or submit a replacement."""
        payload = self._get(f"/v1/tasks/{task_id}")
        if (not isinstance(payload, dict) or isinstance(payload.get("code"), bool)
                or payload.get("code") not in (None, 0, 200)):
            raise RuntimeError("土豆 API 未返回原图片任务的成功查询回执。")
        item = payload.get("data") or payload
        if not isinstance(item, dict):
            raise RuntimeError("土豆 API 未返回原图片任务的有效查询回执。")
        identifiers = [item[key] for key in ("id", "task_id") if item.get(key) is not None]
        if not identifiers or any(not isinstance(value, str) or value != task_id for value in identifiers):
            raise RuntimeError("土豆 API 查询回执与原图片任务编号不一致。")
        result = item.get("result")
        if str(item.get("status") or "").lower() not in {"completed", "succeeded"} or not isinstance(result, dict):
            raise RuntimeError("土豆 API 查询未确认原图片任务的完成结果。")
        if not urlparse(self._first_url(result, "image")).netloc:
            raise RuntimeError("土豆 API 查询未返回有效的图片链接。")
        return result

    def _complete_image(self, receipt_path: Path, receipt: dict) -> str:
        task_id = receipt["task_id"]
        completed = receipt.get("status") == "completed" and isinstance(receipt.get("result"), dict)
        cached_completed = completed
        try:
            result = receipt["result"] if completed else self._poll(task_id)
            completed = True
            receipt.update(status="completed", result=result, error=None)
            self._save_receipt(receipt_path, receipt)
            url = self._first_url(result, "image")
            path = Path(receipt["save_path"])
            delivered = False
            expected_digest, expected_size = receipt.get("delivery_sha256"), receipt.get("delivery_size_bytes")
            if (receipt.get("downloaded") and isinstance(expected_digest, str) and len(expected_digest) == 64
                    and type(expected_size) is int and expected_size > 0 and path.is_file()):
                try:
                    delivered = self._media_file_digest(path) == (expected_digest, expected_size)
                except OSError:
                    pass
            if not delivered:
                try:
                    self._download(url, path)
                except requests.HTTPError as exc:
                    response = exc.response
                    if not cached_completed or response is None or response.status_code not in {403, 404, 410}:
                        raise
                    refreshed_result = self._refresh_completed_image_result(task_id)
                    receipt.setdefault("previous_completed_result", result)
                    receipt["result"] = result = refreshed_result
                    self._save_receipt(receipt_path, receipt)
                    url = self._first_url(result, "image")
                    self._download(url, path)
                digest, size = self._media_file_digest(path)
                if not size:
                    raise RuntimeError("土豆 API 返回空图片文件。")
                receipt.update(downloaded=True, delivery_sha256=digest, delivery_size_bytes=size)
                self._save_receipt(receipt_path, receipt)
            entry = result["images"][0]
            self._save_image_source(Path(f"{path}.tudou-source.json"),
                                    {"url": url, "expires_at": entry.get("expires_at")})
            receipt["source_saved"] = True
            self._save_receipt(receipt_path, receipt)
            return str(path)
        except Exception as exc:
            if completed:
                error = TudouTaskError(str(exc), task_id, "delivery_failed", "completed")
            elif isinstance(exc, TimeoutError):
                error = TudouPollTimeout(task_id)
            elif isinstance(exc, TudouTaskError):
                error = exc
            else:
                error = TudouTaskError(str(exc), task_id,
                                       "delivery_failed" if completed else "query_failed",
                                       "completed" if completed else "unknown")
            receipt.update(status=error.status, error={"code": error.code, "message": str(error)})
            try:
                self._save_receipt(receipt_path, receipt)
            except Exception:
                logger.warning("Unable to persist Tudou image state: task_id=%s", task_id)
            if error is exc:
                raise
            raise error from exc

    def _resume_image(self, path: Path, receipt: dict) -> str:
        if receipt.get("provider") != "tudou" or receipt.get("kind") != "image":
            raise ValueError("没有土豆图片任务回执，无法继续查询。")
        if receipt.get("base_url") != self.base_url:
            raise ValueError("土豆 API 地址已改变，请恢复原任务使用的地址后再查询。")
        save_path = receipt.get("save_path")
        if not isinstance(save_path, str) or not save_path or Path(f"{save_path}.tudou-task.json").resolve() != path:
            raise ValueError("图片输出路径与原任务回执不一致。")
        if receipt.get("status") in {"failed", "error", "expired", "cancelled", "rejected"}:
            error = receipt.get("error") or {}
            raise TudouTaskError(error.get("message") or "土豆图片原任务已终止，请保留原任务记录。",
                                 receipt.get("task_id"), error.get("code") or "upstream_failed", receipt["status"])
        if not receipt.get("task_id"):
            raise TudouTaskError("原提交结果尚未确认，请先核查土豆任务记录，避免重复扣费。",
                                 None, "submit_unknown", "unknown")
        return self._complete_image(path, receipt)

    def resume_image(self, receipt_path: str) -> str:
        """Query/deliver the original image task; never submit a new generation."""
        path = Path(receipt_path).resolve()
        lock = lock_file(Path(f"{path}.lock"))
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            return self._resume_image(path, receipt)
        finally:
            unlock_file(lock)

    def generate_image(self, prompt: str, image_paths: list[str] | None, ratio: str,
                       save_dir: str | None = None, *, save_path: str | None = None) -> str:
        """Generate once per explicit output path, or resume its original task."""
        self.headers
        body = {"model": "gpt-image-2-all", "prompt": prompt, "size": ratio or "16:9",
                "resolution": "1k", "quality": "medium"}
        if image_paths:
            body["images"] = [self._reference_image(value) for value in image_paths]
        if save_path is None and save_dir is None:
            raise ValueError("请提供图片输出目录或固定输出路径。")
        path = (Path(save_path) if save_path is not None else Path(save_dir) / f"tudou_{uuid.uuid4().hex}.png").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path = Path(f"{path}.tudou-task.json")
        lock = lock_file(Path(f"{receipt_path}.lock"))
        try:
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("request") != body:
                    raise ValueError("该输出路径已有不同请求的任务回执，请保留原任务并使用新路径。")
                return self._resume_image(receipt_path, receipt)
            receipt = {"provider": "tudou", "kind": "image", "base_url": self.base_url,
                       "request": body, "save_path": str(path), "status": "submitting",
                       "submitted_at": time.time(), "task_id": None, "downloaded": False, "source_saved": False}
            self._save_receipt(receipt_path, receipt)
            try:
                task_id = self._submit("/v1/images/generations/async", body)
            except Exception as exc:
                response = getattr(exc, "response", None)
                http_status = response.status_code if response is not None else None
                rejected = http_status is not None and 400 <= http_status < 500 and http_status != 408
                status, code = ("rejected", "submit_rejected") if rejected else ("unknown", "submit_unknown")
                message = "土豆 API 拒绝图片提交。" if rejected else "土豆 API 提交结果尚未确认，请先核查提供商任务记录，避免重复扣费。"
                receipt.update(status=status, error={"code": code, "message": message})
                self._save_receipt(receipt_path, receipt)
                raise TudouTaskError(message, None, code, status) from exc
            receipt.update(task_id=task_id, status="accepted")
            try:
                self._save_receipt(receipt_path, receipt)
            except Exception as exc:
                raise TudouTaskError("土豆已接受图片任务，但回执保存异常，请先核查原任务。",
                                     task_id, "receipt_failed", "unknown") from exc
            return self._complete_image(receipt_path, receipt)
        finally:
            unlock_file(lock)

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
        path = Path(save_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path = Path(f"{path}.tudou-task.json")
        lock = lock_file(Path(f"{receipt_path}.lock"))
        try:
            if receipt_path.exists():
                previous = json.loads(receipt_path.read_text(encoding="utf-8"))
                if previous.get("request") != body:
                    raise ValueError("该输出路径已有不同请求的任务回执，请保留原任务并使用新路径。")
                return self._resume_video(receipt_path, previous)
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
        finally:
            unlock_file(lock)
