"""Explicit asset operator CLI; ordinary app startup never imports this factory."""
import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from core.creator.store import CreatorBriefStore, CreatorError, digest, utc_now
from core.execution.asset_control import read_asset_control, require, unavailable
from core.execution.asset_design import AssetDesignExecution
from core.execution.control import _read
from core.execution.runtime import ExecutionRuntime
from core.planner.runtime import PlannerRuntime

BACKEND = Path(__file__).resolve().parents[2]


def tudou_credential():
    """Called by a verified live start only; never by construction or inspection."""
    from config import load_config
    provider = load_config()["api_providers"]["tudou"]
    require(provider["base_url"].rstrip("/").removesuffix("/v1") == "https://api.ai-tudou.net")
    key = provider["api_key"]
    require(type(key) is str and bool(key))
    return key


@dataclass
class AssetOperatorSession:
    control: object
    planner: object
    runtime: object
    asset: object

    def close(self):
        self.runtime.close()
        self.planner.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def build_asset_session(control, *, receipt_capture=None, credential_loader=None):
    """Assemble the existing engine disabled; no key, observer or task starts."""
    control.verify_authority()
    planner = PlannerRuntime(control.roots["planner"], CreatorBriefStore(control.roots["creator"]))
    runtime = ExecutionRuntime(control.roots["execution"], planner)
    if control.document.policy["validation_only"] and receipt_capture is not None and receipt_capture.worker_factory is not None:
        runtime.api_key = "SYNTHETIC-ASSET-OPERATOR-KEY"
    asset = AssetDesignExecution(runtime, policy=control.document.policy, budget_anchor=control.document.budget_anchor,
        authority_guard=control.guard, fee_source=control.document.fee_source.model_dump(), receipt_capture=receipt_capture,
        credential_loader=credential_loader or tudou_credential)
    return AssetOperatorSession(control, planner, runtime, asset)


def _resource(record):
    result = {name: record[name] for name in ("id", "scope_id", "task_id", "status", "quote_id", "grant_id", "disclosure_hash", "revoked_at") if name in record}
    if record.get("kind") == "operation":
        result.update(fee_observed=record.get("fee_id") is not None,
            manifest_hash=record["manifest"].get("manifest_hash") if record.get("manifest") is not None else None,
            native_start_recorded=record.get("capture_started_at") is not None)
    return result


def _admit_record(control, payload, kind, record_id):
    record = payload.get("asset_design", {}).get(kind + ":" + record_id)
    require(record is not None and record["task_id"] == control.document.admission.task_id)
    scope = payload["asset_design"]["scope:" + record["scope_id"]]
    require(digest(scope["policy"]) == digest(control.document.policy) and digest(scope["anchor"]) == digest(control.document.budget_anchor))
    return record


def _fee_time(control, runtime, operation_id, raw_export):
    payload = runtime.store.read_only()
    operation = _admit_record(control, payload, "operation", operation_id)
    if operation["fee_id"] is not None:
        fee = payload["asset_design"]["fee:" + operation["fee_id"]]
        require(fee["candidate"]["source_export_hash"] == hashlib.sha256(raw_export).hexdigest())
        return fee["candidate"]["observed_at"]
    return utc_now()


def _status(control, operation_id=None):
    from core.execution.asset_design_ledger import (
        own_halted,
        own_pending_fees,
        own_unknown,
        projection,
        verified_manifest,
    )
    from core.execution.media_ledger import (
        account_halted,
        has_pending_fees,
        has_pending_unknown,
        unresolved_text_claims,
    )
    from core.execution.store import ExecutionStore
    payload = ExecutionStore(control.roots["execution"]).read_only()
    available = True
    try:
        control.verify_authority(for_dispatch=True)
    except CreatorError:
        available = False
    authority_valid = available
    reason = None if available else "ASSET_CONTROL_UNAVAILABLE"
    account_id = control.document.budget_anchor["account_scope_id"]
    scopes = {record["id"] for record in payload.get("asset_design", {}).values()
              if record["kind"] == "scope" and record["task_id"] == control.document.admission.task_id
              and digest(record["policy"]) == digest(control.document.policy)
              and digest(record["anchor"]) == digest(control.document.budget_anchor)}
    operations = [record for record in payload.get("asset_design", {}).values()
                  if record["kind"] == "operation" and record["scope_id"] in scopes]
    pending = next((record for record in operations if record["capture_started_at"] is None), None)
    exclude = pending["id"] if pending else None
    if available:
        if account_halted(payload, account_id) or own_halted(payload, account_id):
            reason = "EMPIRICAL_RISK_OVERRUN"
        elif has_pending_unknown(payload, account_id, exclude_operation_id=exclude) or own_unknown(payload, account_id, exclude):
            reason = "RESPONSE_UNCONFIRMED"
        elif has_pending_fees(payload, account_id, exclude_operation_id=exclude) or own_pending_fees(payload, account_id, exclude):
            reason = "PLATFORM_FEE_REQUIRED"
        elif unresolved_text_claims(payload):
            reason = "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED"
        elif operations and pending is None:
            reason = "SCOPE_CALL_LIMIT"
        elif pending and payload["asset_design"]["grant:" + pending["grant_id"]]["revoked_at"] is not None:
            reason = "ASSET_DESIGN_GRANT_REVOKED"
        available = reason is None
    result = {"mode": control.document.mode, "control_authority_valid": authority_valid, "dispatch_available": available,
        "blocked_reason": reason, "model": control.document.policy["model"],
        "asset_design": projection(payload, control.document.admission.task_id), "automatic_followup_calls": 0}
    if operation_id:
        _admit_record(control, payload, "operation", operation_id)
        try:
            verified = verified_manifest(payload, control.document.admission.task_id, operation_id)
            result.update(manifest_verified=True, manifest_hash=verified["manifest"]["manifest_hash"], proof=verified["proof"])
        except CreatorError:
            result["manifest_verified"] = False
    return result


def execute(control, command, args, *, session_factory=build_asset_session):
    task_id = args.task or control.document.admission.task_id
    require(task_id == control.document.admission.task_id)
    if command == "status":
        return _status(control, args.operation)
    require(control.document.mode == "enabled")
    control.verify_authority(for_dispatch=command in {"quote", "grant", "reserve", "start"})
    with session_factory(control) as session:
        asset = session.asset
        if command == "quote":
            result = asset.quote(task_id, key=args.key)
            quote = result["quote"]
            disclosure = quote["disclosure"]
            anchor = disclosure["budget_anchor"]
            return {"quote": _resource(quote), "replayed": result["replayed"], "model": disclosure["policy"]["model"],
                "max_calls": 1, "per_call_risk_cny": "1.000000", "automatic_repair_calls": 0, "image_submissions": 0,
                **{name: anchor[name] for name in ("original_budget_cny", "opening_risk_cny", "opening_paid_call_count", "scope_risk_cny")}}
        if command == "grant":
            result = asset.grant(task_id, args.quote, disclosure_hash=args.disclosure_hash, consent=args.consent, key=args.key)
            return {"grant": _resource(result["grant"]), "replayed": result["replayed"]}
        if command == "reserve":
            _admit_record(control, session.runtime.store.read_only(), "grant", args.grant)
            result = asset.reserve(task_id, args.grant, key=args.key)
        elif command in {"start", "recover"}:
            _admit_record(control, session.runtime.store.read_only(), "operation", args.operation)
            result = getattr(asset, command)(task_id, args.operation)
        elif command == "revoke":
            _admit_record(control, session.runtime.store.read_only(), "grant", args.grant)
            result = asset.revoke(task_id, args.grant)
            return {"grant": _resource(result["grant"])}
        elif command == "legacy-fees-import":
            from core.execution.media_runtime import MediaExecution
            payload = session.runtime.store.read_only()
            original = payload["operations"].get(args.operation)
            account_id = control.document.budget_anchor["account_scope_id"]
            require(original is not None and original["account_id"] == account_id)
            account = payload["accounts"][account_id]
            require(account["anchor"]["parent_authorization_hash"] == control.document.budget_anchor["parent_authorization_hash"])
            require(payload["tasks"][original["task_id"]]["account_id"] == account_id)
            blob = _read(Path(args.export).expanduser().absolute(), maximum=8 * 1048576)
            result = MediaExecution(session.runtime, fee_source=control.document.fee_source.model_dump()).register_text_claim(args.operation, blob)
            return {"claim_id": result["claim_id"], "replayed": result["replayed"]}
        elif command == "fees-import":
            blob = _read(Path(args.export).expanduser().absolute(), maximum=8 * 1048576)
            observed_at = _fee_time(control, session.runtime, args.operation, blob)
            try:
                result = asset.observe_fee(task_id, args.operation, blob, observed_at=observed_at)
            except CreatorError as error:
                if error.code != "FEE_EVIDENCE_CONFLICT":
                    raise
                # Another process may have accepted this exact export first.
                stable = _fee_time(control, session.runtime, args.operation, blob)
                require(stable != observed_at)
                result = asset.observe_fee(task_id, args.operation, blob, observed_at=stable)
        else:
            raise unavailable()
        return {"operation": _resource(result["operation"]), "replayed": result["replayed"], "automatic_followup_calls": 0}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise unavailable()


def parser():
    result = Parser(description="Explicit one-call native asset design")
    result.add_argument("--control")
    subs = result.add_subparsers(dest="command", required=True)
    for name in ("status", "quote", "grant", "reserve", "start", "recover", "fees-import", "legacy-fees-import", "revoke"):
        command = subs.add_parser(name)
        command.add_argument("--control", default=argparse.SUPPRESS)
        command.add_argument("--task")
        if name in {"quote", "grant", "reserve"}:
            command.add_argument("--key", required=True)
        if name == "grant":
            command.add_argument("--quote", required=True)
            command.add_argument("--disclosure-hash", required=True)
            command.add_argument("--consent", action="store_true", required=True)
        if name in {"reserve", "revoke"}:
            command.add_argument("--grant", required=True)
        if name in {"status", "start", "recover", "fees-import", "legacy-fees-import"}:
            command.add_argument("--operation", required=name != "status")
        if name in {"fees-import", "legacy-fees-import"}:
            command.add_argument("export")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if not args.control:
            require(args.command == "status")
            output = {"mode": "off", "dispatch_available": False, "automatic_followup_calls": 0}
        else:
            control = read_asset_control(Path(args.control).expanduser().absolute(), BACKEND)
            output = execute(control, args.command, args)
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except CreatorError as error:
        print(json.dumps({"error": error.code, "automatic_followup_calls": 0}))
        return 1
    except Exception:
        print(json.dumps({"error": "ASSET_CONTROL_UNAVAILABLE", "automatic_followup_calls": 0}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
