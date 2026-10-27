"""Opt-in GET-only observation approval metadata; no implicit execution."""
import argparse
import json
from pathlib import Path

from core.creator.store import CreatorError
from core.execution.image_control import read_image_control
from core.execution.image_observation_continuation import (
    ACTIVATION,
    QUOTE,
    activate_observation,
    authority_for,
    latest_activation_id,
    quote_observation,
    verify_current,
)
from core.execution.store import ExecutionStore

BACKEND = Path(__file__).resolve().parents[2]
ERROR = "IMAGE_OBSERVATION_REVIEW_UNAVAILABLE"


def require(value):
    if not value:
        raise CreatorError(ERROR, "Explicit accepted-task observation review is unavailable", 409)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        require(False)


def _flags():
    return {"paid_calls": 0, "automatic_followup_calls": 0, "additional_risk_cny": "0.000000",
        "additional_paid_calls": 0, "dispatch_available": False, "allowed_method": "GET"}


def _status(control, operation_id=None, result=None, resource=None):
    payload = ExecutionStore(control.roots["execution"]).read_only()
    own = [r for r in payload.get("media", {}).values() if r["task_id"] == control.document.admission.task_id and r["kind"] == ACTIVATION]
    if result is not None:
        record = result[resource]
        q = record if resource == "quote" else payload["media"][QUOTE + ":" + record["quote_id"]]
        operation_id = q["disclosure"]["source_operation_id"]
    value = {"mode": "accepted_task_observation_review", "activation_count": len(own),
        "current_authority_valid": False, "journal_stage": "not_activated", **_flags()}
    if operation_id is not None:
        identifier = latest_activation_id(payload, operation_id)
        if identifier is not None:
            authority = authority_for(payload, identifier)
            try:
                verify_current(control, payload, identifier)
                valid = True
            except Exception:
                valid = False
            value.update(current_authority_valid=valid, journal_stage="observation_revision_recorded",
                revision_sequence=authority["revision_sequence"], max_queries=authority["effective_policy"]["max_queries"],
                used_queries=sum(r["kind"] == "query" and r["operation_id"] == operation_id for r in payload.get("media", {}).values()))
    if result is not None:
        value.update(resource_kind=record["kind"], resource_id=record["id"], replayed=result["replayed"],
            disclosure_hash=q["disclosure_hash"], code_manifest_hash=q["disclosure"]["code_manifest_hash"],
            valid_until=q["disclosure"]["effective_policy"]["valid_until"])
    return value


def main(argv=None):
    parser = Parser(description="Explicit accepted-task GET observation review; no media dispatch")
    parser.add_argument("command", nargs="?", choices=("status", "quote", "activate"), default="status")
    parser.add_argument("--control")
    parser.add_argument("--operation")
    parser.add_argument("--valid-until")
    parser.add_argument("--quote")
    parser.add_argument("--disclosure-hash")
    parser.add_argument("--consent", action="store_true")
    try:
        args = parser.parse_args(argv)
        if not args.control:
            require(args.command == "status" and not any((args.operation, args.valid_until, args.quote, args.disclosure_hash, args.consent)))
            value = {"mode": "off", **_flags()}
        else:
            control = read_image_control(args.control, BACKEND)
            require(control.document.mode == "enabled")
            if args.command == "status":
                require(not any((args.valid_until, args.quote, args.disclosure_hash, args.consent)))
                value = _status(control, args.operation)
            elif args.command == "quote":
                require(bool(args.operation) and bool(args.valid_until) and not any((args.quote, args.disclosure_hash, args.consent)))
                result = quote_observation(control, args.operation, valid_until=args.valid_until)
                value = _status(control, result=result, resource="quote")
            else:
                require(bool(args.quote) and bool(args.disclosure_hash) and args.consent is True and not any((args.operation, args.valid_until)))
                result = activate_observation(control, args.quote, disclosure_hash=args.disclosure_hash, consent=args.consent)
                value = _status(control, result=result, resource="activation")
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception:
        print(json.dumps({"error": ERROR, **_flags()}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
