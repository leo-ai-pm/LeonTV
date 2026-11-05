"""土豆中转对话/视觉客户端：消息构造、重试分类与失败路径。"""
import base64
import json

import pytest
import requests

from models.llm_tudou import TudouChatClient, TudouChatError


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_messages_use_openai_multimodal_shape(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    messages = TudouChatClient._build_messages("描述这张图", [str(image)])

    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert content[0] == {"type": "text", "text": "描述这张图"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1]) == image.read_bytes()


def test_remote_urls_and_data_uris_pass_through():
    assert TudouChatClient._as_data_uri("https://example.com/a.png") == "https://example.com/a.png"
    assert TudouChatClient._as_data_uri("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"


def test_missing_local_image_is_rejected_before_any_request():
    with pytest.raises(TudouChatError):
        TudouChatClient._build_messages("x", ["/nope/missing.png"])


def test_text_only_request_returns_content(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(200, {"choices": [{"message": {"content": "可用"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    client = TudouChatClient(api_key="test", base_url="https://relay.test")
    assert client.chat("只回答两个字", model="gemini-3.5-flash") == "可用"
    assert calls[0][0] == "https://relay.test/v1/chat/completions"
    assert calls[0][1]["json"]["model"] == "gemini-3.5-flash"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test"


def test_overload_is_retried_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    def fake_post(url, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return FakeResponse(503, {"error": {"message": "system cpu overloaded", "code": "system_cpu_overloaded"}})
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("models.llm_tudou.time.sleep", lambda *_: None)

    client = TudouChatClient(api_key="test", base_url="https://relay.test", attempts=3)
    assert client.chat("hi", model="gemini-3.5-flash") == "ok"
    assert attempts["n"] == 3


def test_auth_error_is_not_retried(monkeypatch):
    attempts = {"n": 0}

    def fake_post(url, **kwargs):
        attempts["n"] += 1
        return FakeResponse(401, {"error": {"message": "invalid key", "code": "unauthorized"}})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("models.llm_tudou.time.sleep", lambda *_: None)

    client = TudouChatClient(api_key="test", base_url="https://relay.test", attempts=5)
    with pytest.raises(TudouChatError) as excinfo:
        client.chat("hi", model="gemini-3.5-flash")
    assert attempts["n"] == 1
    assert excinfo.value.status == 401


def test_missing_key_fails_without_network(monkeypatch):
    from config import Config

    def fake_post(url, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("no request expected")

    monkeypatch.setattr(Config, "TUDOU_API_KEY", "")
    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(TudouChatError) as excinfo:
        TudouChatClient(api_key="", base_url="https://relay.test").chat("hi")
    assert excinfo.value.code == "missing_key"


def test_empty_content_is_reported(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, **kwargs: FakeResponse(200, {"choices": []}))
    with pytest.raises(TudouChatError) as excinfo:
        TudouChatClient(api_key="test", base_url="https://relay.test", attempts=1).chat("hi")
    assert excinfo.value.code == "empty_choices"


def test_provider_routing_prefers_tudou_for_its_models():
    from models.config_model import model_provider

    assert model_provider("gemini-3.5-flash") == "tudou"
    assert model_provider("qwen3.7-max") == "dashscope"
    assert model_provider("unknown-model") == ""


def test_webp_saved_as_png_is_sent_with_the_real_media_type(tmp_path):
    image = tmp_path / "frame.png"          # 供应商把 WEBP 存成了 .png
    image.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16)

    messages = TudouChatClient._build_messages("看这张图", [str(image)])
    url = messages[0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/webp;base64,"), url[:40]


def test_png_and_jpeg_are_detected_from_bytes(tmp_path):
    png = tmp_path / "a.jpg"                # 扩展名与内容不一致
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    jpg = tmp_path / "b.png"
    jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 8)

    assert TudouChatClient._build_messages("x", [str(png)])[0]["content"][1]["image_url"]["url"].startswith("data:image/png")
    assert TudouChatClient._build_messages("x", [str(jpg)])[0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg")
