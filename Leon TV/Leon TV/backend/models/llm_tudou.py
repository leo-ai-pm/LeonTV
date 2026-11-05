# -*- coding: utf-8 -*-
"""
土豆 API 中转的文本 / 视觉客户端。

中转的 Gemini、GPT、Claude 系列模型都标注 `supported_endpoint_types: ["openai"]`，
因此统一走 OpenAI 兼容的 `/v1/chat/completions`；图片以 data URI 传入。

只在查询层重试瞬时错误（超时、429、5xx，含中转的 "system cpu overloaded"），
鉴权与参数错误立即失败。
"""
import base64
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import requests

models_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(models_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import logging

from config import Config

logger = logging.getLogger(__name__)


class TudouChatError(RuntimeError):
    """带中转错误码的失败，便于上层区分过载与配置问题。"""

    def __init__(self, message: str, *, status: Optional[int] = None, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


class TudouChatClient:
    def __init__(self, api_key: str = "", base_url: str = "", *, attempts: int = 5, base_delay: float = 2.0):
        self.api_key = api_key or Config.TUDOU_API_KEY
        self.base_url = (base_url or Config.TUDOU_BASE_URL or "").rstrip("/")
        self.attempts = max(1, int(attempts))
        self.base_delay = max(0.0, float(base_delay))

    @staticmethod
    def _is_transient(error: Exception) -> bool:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status in (408, 409, 429) or status >= 500
        return isinstance(error, (requests.exceptions.ConnectionError,
                                  requests.exceptions.Timeout,
                                  requests.exceptions.ChunkedEncodingError))

    @staticmethod
    def _sniff_mime(data: bytes, fallback: str = "image/png") -> str:
        """按文件头判断真实格式；供应商可能把 WEBP 存成 .png/.jpg，不能只看扩展名。"""
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        return fallback

    @staticmethod
    def _as_data_uri(value: str) -> str:
        """http(s)/data URI 原样传递；本地文件转 base64 data URI（按真实字节判断 MIME）。"""
        text = str(value or "")
        if text.startswith(("http://", "https://", "data:")):
            return text
        path = Path(text)
        if not path.is_file():
            raise TudouChatError(f"参考图不存在：{text}")
        raw = path.read_bytes()
        guessed = mimetypes.guess_type(path.name)[0] or "image/png"
        mime = TudouChatClient._sniff_mime(raw, guessed)
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    @staticmethod
    def _build_messages(prompt: str, images: Optional[List[str]] = None) -> list:
        content: list = [{"type": "text", "text": str(prompt or "")}]
        for image in images or []:
            content.append({"type": "image_url", "image_url": {"url": TudouChatClient._as_data_uri(image)}})
        return [{"role": "user", "content": content}]

    def _error_message(self, payload: dict, status: int) -> tuple[str, str]:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "")[:200]
            code = str(error.get("code") or "")
            return (message or f"HTTP {status}", code)
        return (str(payload)[:200] if payload else f"HTTP {status}", "")

    def chat(self, text: str, images: Optional[List[str]] = None, model: str = "gemini-3.5-flash",
             *, max_tokens: int = 2048, temperature: Optional[float] = None) -> str:
        if not self.api_key:
            raise TudouChatError("未配置土豆 API Key：请在设置页填写 api_providers.tudou.api_key 后重试。",
                                 code="missing_key")
        payload = {"model": model, "messages": self._build_messages(text, images), "max_tokens": int(max_tokens)}
        if temperature is not None:
            payload["temperature"] = float(temperature)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                response = requests.post(f"{self.base_url}/v1/chat/completions", headers=headers, json=payload,
                                         timeout=120, proxies=Config.requests_proxies("tudou"))
                if response.status_code >= 400:
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                    message, code = self._error_message(body, response.status_code)
                    raise TudouChatError(f"土豆中转对话失败（HTTP {response.status_code}）：{message}",
                                         status=response.status_code, code=code)
                data = response.json()
                choices = data.get("choices") if isinstance(data, dict) else None
                if not choices:
                    raise TudouChatError(f"土豆中转未返回可选结果：{str(data)[:200]}", code="empty_choices")
                content = (choices[0].get("message") or {}).get("content")
                if isinstance(content, list):
                    content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
                if not isinstance(content, str) or not content.strip():
                    raise TudouChatError("土豆中转返回了空内容。", code="empty_content")
                return content
            except TudouChatError as exc:
                if exc.status is None or not (exc.status in (408, 409, 429) or exc.status >= 500):
                    raise
                last = exc
            except Exception as exc:  # noqa: BLE001 - 分类后决定是否重试
                if not self._is_transient(exc):
                    raise TudouChatError(f"土豆中转请求失败：{exc}", code="request_failed") from exc
                last = exc
            if attempt + 1 < self.attempts:
                delay = min(self.base_delay * (2 ** attempt), 20)
                logger.warning("Tudou chat retry %s/%s model=%s error=%s", attempt + 1, self.attempts, model, last)
                time.sleep(delay)
        raise TudouChatError(f"土豆中转多次失败：{last}", code=getattr(last, "code", "retry_exhausted"))
