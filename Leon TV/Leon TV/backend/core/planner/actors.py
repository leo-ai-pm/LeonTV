"""Synthetic responses and an explicit, single-request text transport adapter."""

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

from core.creator.store import digest


class ModelUncertain(Exception):
    """Acceptance/response unconfirmed; must not automatically resubmit."""


class OfflineActor:
    """Only model responses are synthetic; local tools/control flow are real."""

    def respond(self, messages, operation, task):
        scenario = task["scenario"]
        if scenario == "response_unknown":
            raise ModelUncertain("Synthetic response not confirmed")
        if scenario == "invalid_tool":
            return json.dumps({"tool": "open_url", "arguments": {"url": "https://invalid.example/"}})
        results = [message["content"] for message in messages if message["role"] == "tool"]
        sources = [result["source"] for result in results if result.get("ok") and "source" in result]
        if not sources or scenario == "step_limit":
            return json.dumps({"tool": "read_frozen_brief", "arguments": {}})
        source = sources[-1]
        corrected = any(result.get("code") == "DURATION_TOTAL" for result in results)
        duration = 7500 if scenario != "repair" or corrected else 6500
        plan = {"source_snapshot_hash": source["snapshot_hash"], "duration_ms": 15000, "ratio": "9:16", "shots": [
            {"id": "S01", "duration_ms": 7500, "visual": "Offline fixture: " + source["script"][:160], "camera": "Static", "audio": "Not generated", "depends_on": []},
            {"id": "S02", "duration_ms": duration, "visual": "Offline fixture ending", "camera": "Slow push", "audio": "Not generated", "depends_on": ["S01"]},
        ]}
        return json.dumps({"tool": "submit_shot_plan", "arguments": {"plan": plan}}, ensure_ascii=False)


@dataclass(frozen=True)
class TextCallPermit:
    """Internal server-issued per-operation permit, never accepted by public API."""

    task_id: str
    operation_id: str
    snapshot_hash: str
    context_hash: str
    provider: str
    model: str
    endpoint: str
    expires_at: datetime
    allow_text_export: bool
    max_calls: int
    max_input_bytes: int
    max_output_bytes: int
    max_output_tokens: int
    per_call_upper_cny: str
    total_ceiling_cny: str


class SingleRequestTextAdapter:
    """One explicit OpenAI-compatible HTTP protocol; service capability unverified.

    No config import, model/provider fallback, prompt logging, retries or search.
    The future server policy must persist authorization/reservations before
    issuing this operation-bound permit. No D08 route issues such a permit.
    """

    def __init__(self, *, endpoint, model, api_key, permit_provider, transport=None):
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.endswith("/chat/completions"):
            raise ValueError("Explicit compatible text endpoint required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", model) or not api_key:
            raise ValueError("Explicit model and credentials required")
        self.endpoint, self.model, self.api_key = endpoint, model, api_key
        self.permit_provider, self.transport = permit_provider, transport
        self.used_operations, self.lock = set(), threading.RLock()

    def respond(self, messages, operation, task):
        context_hash = digest(messages)
        permit = self.permit_provider(task, operation, context_hash)
        valid = isinstance(permit, TextCallPermit) and permit.provider == "openai_compatible" and permit.task_id == task["id"] and permit.operation_id == operation["id"] and permit.snapshot_hash == task["snapshot_hash"] and permit.context_hash == context_hash and permit.endpoint == self.endpoint and permit.model == self.model and permit.allow_text_export is True and type(permit.max_calls) is int and permit.max_calls == 1
        if not valid:
            raise PermissionError("A server-issued operation-bound text permit is required")
        if not isinstance(permit.expires_at, datetime) or permit.expires_at.tzinfo is None or permit.expires_at <= datetime.now(timezone.utc):
            raise PermissionError("Text permit expired")
        bounds = (permit.max_input_bytes, permit.max_output_bytes, permit.max_output_tokens)
        if any(type(value) is not int or value <= 0 for value in bounds) or permit.max_input_bytes > 1048576 or permit.max_output_bytes > 65536 or permit.max_output_tokens > 8192:
            raise PermissionError("Text bounds are invalid")
        try:
            upper, ceiling = Decimal(permit.per_call_upper_cny), Decimal(permit.total_ceiling_cny)
            money_ok = upper.is_finite() and ceiling.is_finite() and 0 < upper <= ceiling
        except (ValueError, ArithmeticError, TypeError):
            money_ok = False
        if not money_ok:
            raise PermissionError("An explicit paid call ceiling is required")
        payload = {"model": self.model, "max_tokens": permit.max_output_tokens, "messages": [
            {"role": "user" if message["role"] == "tool" else message["role"], "content": json.dumps(message["content"], ensure_ascii=False)} for message in messages
        ]}
        wire = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(wire) > permit.max_input_bytes:
            raise PermissionError("Text context exceeds the authorized bound")
        with self.lock:
            if operation["id"] in self.used_operations:
                raise ModelUncertain("Operation already attempted; reconcile its receipt")
            self.used_operations.add(operation["id"])
        import httpx

        client = httpx.Client(transport=self.transport, timeout=30, follow_redirects=False, trust_env=False)
        try:
            with client.stream("POST", self.endpoint, content=wire, headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key, "X-Client-Operation-Id": operation["id"]}) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise ModelUncertain("Text receipt exceeds the bounded envelope")
                value = json.loads(data)
                content = value["choices"][0]["message"]["content"]
                if not isinstance(content, str) or len(content.encode("utf-8")) > permit.max_output_bytes:
                    raise ModelUncertain("Text response exceeds the authorized bound")
                return content
        except Exception:
            raise ModelUncertain("Text response unconfirmed; automatic retry is forbidden") from None
        finally:
            client.close()
