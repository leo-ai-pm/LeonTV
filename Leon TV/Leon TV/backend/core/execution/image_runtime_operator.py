"""Explicit exact-code runtime approvals; no implicit enablement or media call."""
import argparse
import json
import sys
from pathlib import Path

from core.creator.store import CreatorError
from core.execution.image_control import read_image_control
from core.execution.image_runtime_continuation import (
    activate_runtime_continuation,
    authority_for,
    latest_activation_id,
    quote_runtime_continuation,
    verify_current,
)
from core.execution.store import ExecutionStore

BACKEND = Path(__file__).resolve().parents[2]
ERROR = "IMAGE_RUNTIME_REVIEW_UNAVAILABLE"


def require(value):
    if not value:
        raise CreatorError(ERROR, "Explicit runtime revision review is unavailable", 409)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        require(False)


def _flags():
    return {"paid_calls": 0, "automatic_followup_calls": 0,
        "additional_risk_cny": "0.000000", "additional_paid_calls": 0, "dispatch_available": False}


def _retained_summary(plan):
    return {"retained_unknown_count": len(plan["retained_unknowns"]),
        "funded_jobs": [{key: target[key] for key in ("shot_id", "attempt_version", "planned_submission_index")}
            for target in plan["funded_targets"]],
        "deferred_jobs": [{key: target[key] for key in ("shot_id", "reason", "required_paid_calls", "required_risk_cny")}
            for target in plan["deferred_targets"]],
        "retained_risk_released_cny": plan["retained_risk_released_cny"], "call_slots_released": plan["call_slots_released"]}


def _status(control, result=None, resource=None):
    payload = ExecutionStore(control.roots["execution"]).read_only()
    own = [r for r in payload.get("media", {}).values() if r["task_id"] == control.document.admission.task_id]
    activations = [r for r in own if r["kind"] == "runtime_continuation_activation"]
    value = {"mode": "runtime_revision_review", "activation_count": len(activations), **_flags()}
    if activations:
        identifier = latest_activation_id(payload, activations[0]["scope_id"])
        authority = authority_for(payload, identifier)
        try:
            verify_current(control, payload, identifier)
            valid = True
        except Exception:
            valid = False
        value.update(journal_stage="runtime_revision_recorded", current_authority_valid=valid,
            revision_sequence=authority["revision_sequence"], cumulative_max_submissions=authority["max_cumulative_submissions"])
        if "response_policy" in authority:
            value["response_policy"] = authority["response_policy"]
        if "retained_unknown_plan" in authority:
            value["retained_unknown_plan"] = _retained_summary(authority["retained_unknown_plan"])
    else:
        value.update(journal_stage="not_activated", current_authority_valid=False)
    if result is not None:
        record = result[resource]
        quote = record if resource == "quote" else payload["media"]["runtime_continuation_quote:" + record["quote_id"]]
        value.update(resource_kind=record["kind"], resource_id=record["id"], replayed=result["replayed"],
            disclosure_hash=quote["disclosure_hash"], code_manifest_hash=quote["disclosure"]["code_manifest_hash"],
            valid_until=quote["disclosure"]["effective_policy"]["valid_until"])
        # The quote response shows the concrete contract before activation.
        if "response_policy" in quote["disclosure"]:
            value["response_policy"] = quote["disclosure"]["response_policy"]
        else:
            value.pop("response_policy", None)
        if "retained_unknown_plan" in quote["disclosure"]:
            value["retained_unknown_plan"] = _retained_summary(quote["disclosure"]["retained_unknown_plan"])
        else:
            value.pop("retained_unknown_plan", None)
    return value


def main(argv=None):
    parser = Parser(description="Review and record an exact image runtime revision; no media dispatch")
    parser.add_argument("command", nargs="?", choices=("status", "quote", "activate", "comparison-quote", "comparison-prepare", "comparison-activate", "comparison-status"), default="status")
    parser.add_argument("--control")
    parser.add_argument("--valid-until")
    parser.add_argument("--response-window-seconds", type=int)
    parser.add_argument("--retain-unknown-risk", action="store_true")
    parser.add_argument("--max-comparison-posts", type=int)
    parser.add_argument("--intent")
    parser.add_argument("--proposal-hash")
    parser.add_argument("--quote")
    parser.add_argument("--disclosure-hash")
    parser.add_argument("--consent", action="store_true")
    comparison_command = any(v.startswith("comparison-") for v in (argv if argv is not None else sys.argv[1:]))
    try:
        args = parser.parse_args(argv)
        extras = (args.max_comparison_posts is not None, args.intent, args.proposal_hash, args.valid_until, args.quote, args.disclosure_hash, args.consent, args.response_window_seconds is not None, args.retain_unknown_risk)
        if comparison_command:
            from core.execution.image_comparison import operator_review
            value = operator_review(args, BACKEND)
        elif not args.control:
            require(args.command == "status" and not any(extras))
            value = {"mode": "off", **_flags()}
        else:
            require(args.max_comparison_posts is None and args.intent is None and args.proposal_hash is None)
            control = read_image_control(args.control, BACKEND)
            require(control.document.mode == "enabled")
            control.verify_authority(for_dispatch=False)
            if args.command == "status":
                require(not any(extras))
                value = _status(control)
            elif args.command == "quote":
                require(bool(args.valid_until) and not any((args.quote, args.disclosure_hash, args.consent)))
                result = quote_runtime_continuation(control, valid_until=args.valid_until, response_window_seconds=args.response_window_seconds, retain_unknown_risk=args.retain_unknown_risk)
                value = _status(control, result, "quote")
            else:
                require(bool(args.quote) and bool(args.disclosure_hash) and args.consent is True and not args.valid_until and args.response_window_seconds is None and not args.retain_unknown_risk)
                result = activate_runtime_continuation(control, args.quote,
                    disclosure_hash=args.disclosure_hash, consent=args.consent)
                value = _status(control, result, "activation")
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception:
        print(json.dumps({"error": "IMAGE_COMPARISON_UNAVAILABLE" if comparison_command else ERROR, **_flags()}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
