"""Small durable reservation ledger. Unknown charges retain their full upper bound."""

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

TASK = "LTV-TUDOU-AB-20261016"
BACKEND = Path(__file__).resolve().parents[2]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def fen(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid CNY amount")
    return int((amount * 100).to_integral_value(rounding=ROUND_CEILING))


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class BudgetLedger:
    def __init__(self, directory, authorization_hash, limit_cny="100.00"):
        self.root = Path(directory).resolve()
        if self.root.is_relative_to(BACKEND / "code"):
            raise ValueError("Ledger must be outside public /code")
        if not authorization_hash or not 0 < fen(limit_cny) <= 10000:
            raise ValueError("Authorization and a maximum of CNY 100 are required")
        self.authorization_hash, self.limit = authorization_hash, fen(limit_cny)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "budget-ledger.json"
        with self.locked() as state:
            if state["authorization_hash"] != authorization_hash or state["limit_fen"] != self.limit:
                raise ValueError("Authorization/limit changed; preserve the original ledger")

    @contextmanager
    def locked(self):
        with (self.root / ".budget.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {
                "schema_version": 1, "task_id": TASK, "currency": "CNY",
                "authorization_hash": self.authorization_hash, "limit_fen": self.limit, "operations": {},
            }
            try:
                yield state
            except BaseException:
                raise
            else:
                atomic_json(self.path, state)

    def reserve(self, operation_key, request, upper_cny, quote_ref):
        if not operation_key or not quote_ref:
            raise ValueError("Stable operation key and verified quote reference are required")
        upper = fen(upper_cny)
        with self.locked() as state:
            operations = state["operations"]
            if operation_key in operations:
                previous = operations[operation_key]
                if previous["request_sha256"] != fingerprint(request) or previous["upper_fen"] != upper:
                    raise ValueError("Operation key already belongs to another request/quote")
                return previous
            held = sum(op["upper_fen"] for op in operations.values())
            if held + upper > self.limit:
                raise ValueError("CNY 100 budget exceeded; no dispatch permitted")
            record = {"operation_key": operation_key, "request_sha256": fingerprint(request),
                      "request": request, "upper_fen": upper, "quote_ref": quote_ref,
                      "status": "reserved", "provider_task_id": None, "actual_charge_fen": None}
            operations[operation_key] = record
            return record

    def begin(self, operation_key):
        with self.locked() as state:
            record = state["operations"][operation_key]
            if record["status"] != "reserved":
                raise ValueError("Request already dispatched or uncertain; query/reconcile, never resubmit")
            record["status"] = "submitting"

    def record(self, operation_key, status, *, provider_task_id=None, response_ref=None):
        if status not in {"responded", "accepted", "unknown", "completed", "failed"}:
            raise ValueError("Unsupported outcome")
        with self.locked() as state:
            record = state["operations"][operation_key]
            if record["status"] == "reserved":
                raise ValueError("Dispatch intent must be persisted first")
            record["status"] = status
            if provider_task_id:
                if record["provider_task_id"] and record["provider_task_id"] != provider_task_id:
                    raise ValueError("Provider task ID changed")
                record["provider_task_id"] = provider_task_id
            if response_ref:
                record["response_ref"] = response_ref

    def snapshot(self):
        with self.locked() as state:
            state["reserved_upper_fen"] = sum(op["upper_fen"] for op in state["operations"].values())
            return state
