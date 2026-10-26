"""Explicit image continuation authorization; this CLI cannot dispatch media."""
import argparse
import json
from pathlib import Path

from core.creator.store import CreatorError
from core.execution.image_control import read_image_control
from core.execution.policy import money
from core.execution.scope_budget import scope_envelope
from core.execution.store import ExecutionStore

BACKEND = Path(__file__).resolve().parents[2]
ERROR_CODE = "IMAGE_VALIDITY_REVIEW_UNAVAILABLE"


def require(value):
    if not value:
        raise CreatorError(ERROR_CODE, "Image continuation authorization is unavailable", 409)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CreatorError(ERROR_CODE, "Image continuation authorization is unavailable", 409)


def _flags():
    return {"dispatch_ready": False, "runtime_contract_pending": True,
        "paid_calls": 0, "automatic_followup_calls": 0}


def _status(control, result=None, resource=None):
    payload = ExecutionStore(control.roots["execution"]).read_only()
    own = [record for record in payload.get("media", {}).values()
           if record["task_id"] == control.document.admission.task_id]
    quotes = [r for r in own if r["kind"] == "validity_continuation_quote"]
    intents = [r for r in own if r["kind"] == "validity_continuation_intent"]
    activations = [r for r in own if r["kind"] == "validity_continuation_activation"]
    stage = ("activation_recorded_runtime_pending" if activations else "reservation_pending" if intents
             else "quote_ready" if quotes else "not_started")
    value = {"mode": "authorization_only", "journal_stage": stage,
        "quote_count": len(quotes), "reservation_intent_count": len(intents), "activation_count": len(activations), **_flags()}
    account_id = control.document.budget_anchor["account_scope_id"]
    envelope = scope_envelope(payload, account_id)
    value.update(retained_risk_cny=money(envelope["risk_units"]), retained_max_calls=envelope["max_calls"])
    if result is not None:
        record = result[resource]
        quote = record if resource == "quote" else payload["media"]["validity_continuation_quote:" + record["quote_id"]]
        proposal = quote["proposal"]
        value.update(resource_kind=record["kind"], resource_id=record["id"], replayed=result["replayed"],
            proposal_hash=proposal["proposal_hash"], proposed_valid_until=proposal["basis"]["proposed_valid_until"])
    return value


def main(argv=None):
    parser = Parser(description="Persist bounded continuation authorization; never dispatch media")
    parser.add_argument("command", nargs="?", choices=("status", "quote", "prepare", "activate"), default="status")
    parser.add_argument("--control")
    parser.add_argument("--valid-until")
    parser.add_argument("--quote")
    parser.add_argument("--intent")
    parser.add_argument("--proposal-hash")
    parser.add_argument("--consent", action="store_true")
    try:
        args = parser.parse_args(argv)
        extras = (args.valid_until, args.quote, args.intent, args.proposal_hash, args.consent)
        if not args.control:
            require(args.command == "status" and not any(extras))
            value = {"mode": "off", **_flags()}
        else:
            control = read_image_control(args.control, BACKEND)
            require(control.document.mode == "enabled")
            control.verify_authority(for_dispatch=False)
            if args.command == "status":
                require(not any(extras))
                value = _status(control)
            else:
                from core.execution.image_validity_continuation import (
                    activate_continuation,
                    prepare_continuation,
                    quote_continuation,
                )

                if args.command == "quote":
                    require(bool(args.valid_until) and not any((args.quote, args.intent, args.proposal_hash, args.consent)))
                    result = quote_continuation(control, valid_until=args.valid_until)
                    resource = "quote"
                elif args.command == "prepare":
                    require(bool(args.quote) and bool(args.proposal_hash) and args.consent is True
                            and not args.valid_until and not args.intent)
                    result = prepare_continuation(control, args.quote, proposal_hash=args.proposal_hash, consent=args.consent)
                    resource = "intent"
                else:
                    require(bool(args.intent) and bool(args.proposal_hash) and args.consent is True
                            and not args.valid_until and not args.quote)
                    result = activate_continuation(control, args.intent, proposal_hash=args.proposal_hash, consent=args.consent)
                    resource = "activation"
                value = _status(control, result, resource)
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception:
        print(json.dumps({"error": ERROR_CODE, **_flags()}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
