"""One-request-at-a-time production pilot. No automatic billed retries or agent claim."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import Config
from models.tudou_client import TudouClient

from .ledger import BACKEND, TASK, BudgetLedger, atomic_json, fingerprint

REPO = BACKEND.parents[2]
EVIDENCE = REPO / "docs/evidence" / TASK
PRIVATE = BACKEND / "harness_data" / TASK


def load_authorization():
    authorization = json.loads((EVIDENCE / "authorization.json").read_text())
    if (authorization["task_id"] != TASK or authorization["budget"]["currency"] != "CNY"
            or authorization["budget"]["hard_total"] != 100
            or authorization["status"] != "approved_bounded_live_api_trial"):
        raise ValueError("This trial requires the exact bounded coordinator authorization")
    return authorization


class Pilot:
    def __init__(self):
        self.authorization = load_authorization()
        self.ledger = BudgetLedger(PRIVATE, fingerprint(self.authorization))
        (PRIVATE / "results").mkdir(exist_ok=True)
        self.ledger.reserve("query-fee-contingency", {"method": "LOCAL_RESERVE", "purpose": "bounded-query-fee-contingency"},
                            "5.00", "coordinator-authorized-conservative-query-reserve;actual-unsettled")
        self.client = TudouClient()
        if not self.client.api_key:
            raise ValueError("Tudou credential is absent")
        if self.client.base_url != self.authorization["provider_origin"]:
            raise ValueError("Configured provider origin differs from authorization")

    def redact(self, value):
        # Raw response bodies are retained privately, with any echoed key removed.
        return str(value).replace(self.client.api_key, "[REDACTED]")

    def record_response(self, key, method, saved):
        payload = saved.get("body")
        item = (payload.get("data") or payload) if isinstance(payload, dict) else {}
        task_id = (item.get("id") or item.get("task_id")) if isinstance(item, dict) else None
        status = saved["http_status"]
        if not 200 <= status < 300:
            outcome = "failed" if 400 <= status < 500 and status != 408 else "unknown"
        elif method == "POST":
            outcome = "accepted" if task_id else "unknown"
        else:
            outcome = "responded"
        self.ledger.record(key, outcome, provider_task_id=task_id,
                           response_ref=str(PRIVATE / "calls" / fingerprint(key) / "response.json"))

    def call(self, key, method, endpoint, body, upper_cny, quote_ref, *, parent=None):
        request = {"method": method, "endpoint": endpoint, "body": body, "parent": parent}
        record = self.ledger.reserve(key, request, upper_cny, quote_ref)
        directory = PRIVATE / "calls" / fingerprint(key)
        directory.mkdir(parents=True, exist_ok=True)
        response_path = directory / "response.json"
        if response_path.is_file():
            saved = json.loads(response_path.read_text())
            if saved["request_sha256"] != record["request_sha256"]:
                raise ValueError("Saved response does not match the request")
            if record["status"] in {"submitting", "unknown"}:
                self.record_response(key, method, saved)  # Recover response-before-ledger crash, no HTTP.
            return saved
        if method not in {"POST", "GET"} or not endpoint.startswith("/v1/"):
            raise ValueError("Only the verified relay endpoint family is allowed")
        atomic_json(directory / "request.json", request)
        self.ledger.begin(key)  # Committed before the only HTTP dispatch; never retried here.
        started = time.monotonic()
        try:
            response = requests.request(
                method, self.client.base_url + endpoint, headers=self.client.headers,
                json=body if method == "POST" else None, timeout=120,
                proxies=Config.requests_proxies("tudou"),
            )
            raw = self.redact(response.text)
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
            saved = {"request_sha256": record["request_sha256"], "http_status": response.status_code,
                     "body": payload, "raw_text": raw, "elapsed_seconds": time.monotonic() - started,
                     "received_at": datetime.now(timezone.utc).isoformat()}
            atomic_json(response_path, saved)
            self.record_response(key, method, saved)
            return saved
        except Exception:
            self.ledger.record(key, "unknown")
            raise RuntimeError("Request outcome is uncertain; inspect private evidence, never resubmit") from None

    def submit(self, key, body):
        model = body.get("model")
        state = self.ledger.snapshot()
        prior = state["operations"].get(key)
        if not prior:
            if any(op["status"] in {"submitting", "unknown"} and op["request"]["method"] == "POST"
                   for op in state["operations"].values()):
                raise ValueError("An earlier submission is uncertain; reconcile before new generation")
        limits = self.authorization["operational_limits"]
        if model == "gpt-image-2-all":
            if body.get("quality") != "medium" or body.get("resolution") != "1k" or body.get("size") != "9:16":
                raise ValueError("Only the quoted medium/1k/9:16 image specification is allowed")
            endpoint, upper, maximum = "/v1/images/generations/async", "0.12", limits["image_submissions_including_repairs"]
            quote = "pricing-gpt-image-2-all.txt:medium-1k:max-displayed-group"
        elif model in {"minimax-h3", "seedance-2.5"}:
            duration = body.get("duration")
            if type(duration) is not int or not 1 <= duration <= 15 or body.get("aspect_ratio") != "9:16" or body.get("resolution") != "720p":
                raise ValueError("Only bounded 720p/9:16 video requests up to 15 seconds are allowed")
            endpoint = "/v1/videos/generations"
            upper = str(duration * (0.05 if model == "minimax-h3" else 1.3))
            maximum = limits["video_submissions_per_model_including_initial"]
            quote = f"pricing-{model}.txt:720p:max-displayed-group"
        else:
            raise ValueError("Unquoted or substituted model is forbidden in this trial")
        used = sum(op["request"]["method"] == "POST" and (op["request"].get("body") or {}).get("model") == model
                   for op in state["operations"].values())
        if not prior and used >= maximum:
            raise ValueError("Submission count exhausted for this exact model")
        return self.call(key, "POST", endpoint, body, upper, quote)

    def poll_once(self, parent_key):
        state = self.ledger.snapshot()
        parent = state["operations"][parent_key]
        task_id = parent.get("provider_task_id")
        if not task_id:
            raise ValueError("No accepted task ID; manual reconciliation required")
        result_path = PRIVATE / "results" / f"{fingerprint(parent_key)}.json"
        if result_path.is_file():
            return json.loads(result_path.read_text())
        queries = sum(op["request"].get("parent") == parent_key for op in state["operations"].values())
        if queries >= 120:
            raise ValueError("Bounded status-query count exhausted; preserve original task")
        # Query performs no new generation. The separate CNY 5 contingency remains
        # held; CNY 0 on the query row is NOT a claim of settled zero billing.
        response = self.call(f"{parent_key}/query/{queries + 1:03}", "GET", f"/v1/tasks/{task_id}",
                             None, "0", "separate-query-fee-contingency;actual-unsettled", parent=parent_key)
        payload = response.get("body") or {}
        item = payload.get("data") or payload
        status = str(item.get("status") or "unknown").lower()
        result = {"task_id": task_id, "status": status, "result": item.get("result") or {},
                  "raw_query": response}
        if status in {"completed", "succeeded"}:
            atomic_json(result_path, result)
            self.ledger.record(parent_key, "completed", provider_task_id=task_id)
        elif status in {"failed", "error", "expired", "cancelled"}:
            atomic_json(result_path, result)
            self.ledger.record(parent_key, "failed", provider_task_id=task_id)
        return result

    def download(self, parent_key, destination):
        result = self.poll_once(parent_key)
        if result["status"] not in {"completed", "succeeded"}:
            raise ValueError("Original task has not completed")
        record = self.ledger.snapshot()["operations"][parent_key]
        kind = "image" if record["request"]["body"]["model"] == "gpt-image-2-all" else "video"
        url = TudouClient._first_url(result["result"], kind)
        destination = Path(destination).resolve()
        allowed = BACKEND / "code/result" / TASK
        if not destination.is_relative_to(allowed):
            raise ValueError("Delivery must use this trial's new media directory")
        import hashlib

        delivery_path = PRIVATE / "results" / f"{fingerprint(parent_key)}-delivery.json"
        previous = json.loads(delivery_path.read_text()) if delivery_path.is_file() else None
        if previous and any(previous.get(k) != v for k, v in {
            "operation_key": parent_key, "task_id": result["task_id"], "file": str(destination),
            "model": record["request"]["body"]["model"],
        }.items()):
            raise ValueError("Existing delivery belongs to another task/model/path")
        if destination.is_file():
            if not previous or previous["sha256"] != hashlib.sha256(destination.read_bytes()).hexdigest():
                raise ValueError("Existing file has no matching delivery receipt/hash; never relabel it")
            return previous
        self.client._download(url, destination)
        file_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
        if previous and previous["sha256"] != file_sha:
            raise ValueError("Redelivered bytes differ from the original receipt")
        receipt = {"operation_key": parent_key, "task_id": result["task_id"], "model": record["request"]["body"]["model"],
                   "raw_url": url, "file": str(destination), "sha256": file_sha,
                   "bytes": destination.stat().st_size}
        atomic_json(delivery_path, receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["submit", "poll", "download", "status"])
    parser.add_argument("--key")
    parser.add_argument("--request")
    parser.add_argument("--output")
    args = parser.parse_args()
    pilot = Pilot()
    if args.action == "submit":
        result = pilot.submit(args.key, json.loads(Path(args.request).read_text()))
    elif args.action == "poll":
        result = pilot.poll_once(args.key)
    elif args.action == "download":
        result = pilot.download(args.key, args.output)
    else:
        state = pilot.ledger.snapshot()
        result = {"reserved_upper_fen": state["reserved_upper_fen"], "operation_count": len(state["operations"])}
    # Responses and signed media URLs remain private. Only useful status is printed.
    item = ((result.get("body") or {}).get("data") or result.get("body") or result)
    print(json.dumps({k: v for k, v in item.items() if k in {
        "id", "task_id", "status", "file", "sha256", "bytes", "http_status", "reserved_upper_fen", "operation_count",
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
