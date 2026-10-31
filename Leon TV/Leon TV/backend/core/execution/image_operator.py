"""Explicit one-request image CLI; never imported by the ordinary bootstrap."""
import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from core.creator.store import CreatorBriefStore, CreatorError, digest, utc_now
from core.execution.asset_media_handoff import media_from_asset_design
from core.execution.control import _private_path, _read
from core.execution.image_control import read_image_control, require, unavailable
from core.execution.media_failure_phase import capture_stage, failure_projection
from core.execution.media_ledger import (
    account_halted,
    has_pending_fees,
    has_pending_unknown,
    projection,
    records,
    unresolved_text_claims,
)
from core.execution.media_runtime import MediaExecution
from core.execution.policy import now
from core.execution.receipts import timestamp
from core.execution.runtime import ExecutionRuntime
from core.execution.store import ExecutionStore
from core.planner.runtime import PlannerRuntime

BACKEND = Path(__file__).resolve().parents[2]
NETWORK_COMMANDS = {"quote", "grant", "reserve", "start", "query"}


def tudou_credential():
    """Load only after the durable image intent passes its current guard."""
    from config import load_config
    provider = load_config()["api_providers"]["tudou"]
    require(provider["base_url"].rstrip("/").removesuffix("/v1") == "https://api.ai-tudou.net")
    key = provider["api_key"]
    require(type(key) is str and bool(key))
    return key


class _BoundImageCapture:
    """Supply a fresh key locally, never from ExecutionRuntime.api_key."""
    def __init__(self, control, media, capture, loader):
        self.control, self.media, self.capture_adapter, self.loader = control, media, capture, loader
        self.settings, self.receipts = capture.settings, capture.receipts

    def capture(self, request, _runtime_key):
        capture_stage("control_before_key")
        combined = (getattr(self.control, "observation_activation_id", None) is not None
            or getattr(self.control, "runtime_activation_id", None) is not None)
        if combined:
            require(self.control.guard_capture(self.media, request.frozen(), native_stage="native_before_key") is True)
        else:
            self.control.verify_authority(for_dispatch=True)
            capture_stage("native_before_key")
            require(self.media._capture_guard(request.frozen()) is True)
        capture_stage("deadline_before_key")
        require(now() < timestamp(request.deadline_at))
        require(request.validation_only is self.control.document.policy["validation_only"])
        capture_stage("credential_load")
        if request.validation_only:
            require(self.settings.worker_factory is not None)
            key = "SYNTHETIC-IMAGE-OPERATOR-KEY"
        else:
            require(self.settings.worker_factory is None and not self.settings.worker_options)
            require(callable(self.loader))
            key = self.loader()
        capture_stage("credential_check")
        require(type(key) is str and bool(key) and len(key.encode("utf-8")) <= 16384)
        # Loading a credential is not authority. Recheck the actual persisted
        # reservation and current control after it, before the original capture.
        capture_stage("control_after_key")
        if combined:
            require(self.control.guard_capture(self.media, request.frozen(), native_stage="native_after_key") is True)
        else:
            self.control.verify_authority(for_dispatch=True)
            capture_stage("native_after_key")
            require(self.media._capture_guard(request.frozen()) is True)
        capture_stage("deadline_after_key")
        require(now() < timestamp(request.deadline_at))
        return self.capture_adapter.capture(request, key)

    def ready_raw(self, request):
        return self.capture_adapter.ready_raw(request)


@dataclass
class ImageOperatorSession:
    control: object
    planner: object
    runtime: object
    media: object

    def close(self):
        self.runtime.close()
        self.planner.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def build_image_session(control, *, receipt_capture=None, credential_loader=None):
    """Keep text disabled and reconstruct the original paid asset manifest."""
    control.verify_authority()
    planner = PlannerRuntime(control.roots["planner"], CreatorBriefStore(control.roots["creator"]))
    runtime = ExecutionRuntime(control.roots["execution"], planner)
    try:
        continuation = {}
        if getattr(control, "runtime_activation_id", None) is not None:
            continuation = {"continuation_activation_id": control.runtime_activation_id,
                "continuation_guard": control.guard_runtime, "continuation_control": control}
        factory = media_from_asset_design
        if getattr(control, "observation_activation_id", None) is not None:
            from core.execution.image_observation_runtime import observation_from_asset_design

            factory = observation_from_asset_design
            continuation = {"observation_control": control}
        media = factory(runtime, control.document.admission.task_id,
            control.document.admission.asset_operation_id, policy=control.document.policy,
            budget_anchor=control.document.budget_anchor, authority_guard=control.guard,
            fee_source=control.document.fee_source.model_dump(), reference_profile=control.document.reference_profile,
            receipt_capture=receipt_capture, image_control=control, **continuation)
        expected = control.verified_asset()
        require(digest(media.manifest) == digest(expected["manifest"]) and digest(media.manifest_origin) == digest(expected["origin"]))
        media.capture = _BoundImageCapture(control, media, media.capture, credential_loader or tudou_credential)
        return ImageOperatorSession(control, planner, runtime, media)
    except Exception:
        runtime.close()
        planner.close()
        raise


def _matching_scope(control, scope):
    doc = control.document
    return (scope["task_id"] == doc.admission.task_id
        and digest(scope["policy"]) == digest(doc.policy) and digest(scope["anchor"]) == digest(doc.budget_anchor)
        and scope["manifest"]["manifest_hash"] == doc.admission.asset_manifest_hash
        and scope.get("manifest_origin", {}).get("asset_operation_id") == doc.admission.asset_operation_id
        and scope.get("manifest_origin", {}).get("asset_proof", {}).get("proof_hash") == doc.admission.asset_proof_hash
        and digest(scope.get("reference_profile")) == digest(doc.reference_profile)
        and digest(scope["fee_source"]) == digest(doc.fee_source.model_dump()))


def _admit_record(control, payload, kind, record_id):
    record = payload.get("media", {}).get(kind + ":" + record_id)
    require(record is not None and record["task_id"] == control.document.admission.task_id)
    require(_matching_scope(control, payload["media"]["scope:" + record["scope_id"]]))
    if getattr(control, "observation_activation_id", None) is not None:
        from core.execution.image_observation_continuation import authority_for

        authority = authority_for(payload, control.observation_activation_id)
        if kind == "operation":
            require(record_id == authority["source_operation_id"])
        elif kind == "query":
            require(record["operation_id"] == authority["source_operation_id"]
                and record["descriptor"]["kind"] == "controlled_image_observation_http_v1"
                and record["descriptor"]["permit"]["observation_activation_id"] == authority["activation_id"])
        else:
            raise unavailable()
    return record


def _resource(record):
    """Project verified native facts without deriving new execution authority."""
    result = {name: record[name] for name in ("id", "scope_id", "task_id", "status", "shot_id", "query_index",
        "quote_id", "grant_id", "disclosure_hash", "revoked_at") if name in record}
    if record["kind"] == "operation":
        result.update(fee_observed=record["fee_id"] is not None, delivered=record["delivery_id"] is not None)
    if record["kind"] in {"operation", "query"}:
        result["native_start_recorded"] = record["capture_started_at"] is not None
        receipt = record["receipt"]
        result["response_diagnostic"] = {
            "raw_captured": record["raw"] is not None,
            "http_status": None if receipt is None else receipt["http_status"],
            "receipt_classification": None if receipt is None else receipt["classification"],
            "accepted_task_present": receipt is not None and receipt["accepted_task_id"] is not None,
            "retry_authorized": False,
        }
    return result


def _status(control, operation_id=None):
    if getattr(control, "observation_activation_id", None) is not None:
        from core.execution.image_observation_control import observation_status

        return observation_status(control, operation_id)
    if getattr(control, "runtime_activation_id", None) is not None:
        from core.execution.image_runtime_control import runtime_status
        return runtime_status(control, operation_id)
    payload = ExecutionStore(control.roots["execution"]).read_only()
    try:
        control.verify_authority(for_dispatch=True)
        valid = True
    except CreatorError:
        valid = False
    scopes = [scope for scope in records(payload, "scope") if _matching_scope(control, scope)]
    require(len(scopes) <= 1)
    scope_ids = {scope["id"] for scope in scopes}
    operations = [op for op in records(payload, "operation") if op["scope_id"] in scope_ids]
    grants = [grant for grant in records(payload, "grant") if grant["scope_id"] in scope_ids]
    pending = [op for op in operations if op["capture_started_at"] is None]
    require(len(pending) <= 1)
    account_id = control.document.budget_anchor["account_scope_id"]
    reason = None if valid else "IMAGE_CONTROL_UNAVAILABLE"
    if valid:
        exclude = pending[0]["id"] if pending else None
        if account_halted(payload, account_id):
            reason = "EMPIRICAL_RISK_OVERRUN"
        elif has_pending_unknown(payload, account_id, exclude_operation_id=exclude):
            reason = "RESPONSE_UNCONFIRMED"
        elif has_pending_fees(payload, account_id, exclude_operation_id=exclude):
            reason = "PLATFORM_FEE_REQUIRED"
        elif unresolved_text_claims(payload):
            reason = "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED"
        elif any(op["account_id"] == account_id and op["status"] != "settled" for op in payload["operations"].values()):
            reason = "MEDIA_TEXT_RESPONSE_PENDING"
        elif pending and payload["media"]["grant:" + pending[0]["grant_id"]]["revoked_at"] is not None:
            reason = "MEDIA_GRANT_REVOKED"
        elif pending and now() >= timestamp(pending[0]["descriptor"]["permit"]["deadline_at"]):
            reason = "MEDIA_POLICY_EXPIRED"
        elif operations and all(op["delivery_id"] is not None for op in operations) and len(operations) == len(control.verified_asset()["manifest"]["frames"]):
            reason = "SCOPE_FINISHED"
        elif grants and all(grant["revoked_at"] is not None for grant in grants):
            reason = "MEDIA_GRANT_REVOKED"
        elif not pending and len(operations) >= control.document.policy["max_submissions"]:
            reason = "SCOPE_CALL_LIMIT"
    result = {"mode": control.document.mode, "control_authority_valid": valid, "dispatch_available": reason is None,
        "blocked_reason": reason, "model": control.document.policy["model"],
        "media": projection(payload, control.document.admission.task_id), "automatic_followup_calls": 0,
        "query_available": False}
    if operation_id:
        operation = _admit_record(control, payload, "operation", operation_id)
        queries = [query for query in records(payload, "query") if query["operation_id"] == operation_id]
        grant = payload["media"]["grant:" + operation["grant_id"]]
        query_reason = None if valid else "IMAGE_CONTROL_UNAVAILABLE"
        if valid:
            if grant["revoked_at"] is not None:
                query_reason = "MEDIA_GRANT_REVOKED"
            elif operation["status"] != "accepted":
                query_reason = "MEDIA_ACCEPTED_TASK_REQUIRED"
            elif len(queries) >= control.document.policy["max_queries"]:
                query_reason = "MEDIA_QUERY_LIMIT"
        result.update(operation=_resource(operation), query_count=len(queries), query_available=query_reason is None,
            query_blocked_reason=query_reason, queries=[_resource(query) for query in queries])
        result["operation"]["failure_diagnostic"] = failure_projection(control.roots["execution"], operation)
        for projected, query in zip(result["queries"], queries):
            projected["failure_diagnostic"] = failure_projection(control.roots["execution"], query)
    return result


def _import_bytes(control, name, maximum):
    path = Path(name).expanduser().absolute()
    static = (control.backend_root / "code", control.backend_root.parent / "frontend/public", *control.static_roots)
    _private_path(path, static)
    return _read(path, maximum=maximum)


def _fee_time(control, runtime, operation_id, blob):
    payload = runtime.store.read_only()
    operation = _admit_record(control, payload, "operation", operation_id)
    if operation["fee_id"] is not None:
        candidate = payload["media"]["fee:" + operation["fee_id"]]["candidate"]
        require(candidate["source_export_hash"] == hashlib.sha256(blob).hexdigest())
        return candidate["observed_at"]
    return utc_now()


def execute(control, command, args, *, session_factory=build_image_session):
    task_id = args.task or control.document.admission.task_id
    require(task_id == control.document.admission.task_id)
    if command == "status":
        return _status(control, args.operation)
    require(control.document.mode == "enabled")
    control.verify_authority(for_dispatch=command in NETWORK_COMMANDS)
    if command == "task-fees-import":
        from core.execution.control import _json
        from core.execution.media_fee_import import import_task_fee
        from core.execution.task_image_fees import checked_task_source
        source = checked_task_source(_json(_import_bytes(control, args.source, 16384)))
        task_blob = _import_bytes(control, args.export, 8 * 1048576)
        account_blob = _import_bytes(control, args.account_export, 8 * 1048576)
        store = ExecutionStore(control.roots["execution"])
        payload = store.read_only()
        operation = _admit_record(control, payload, "operation", args.operation)
        observed_at = utc_now()
        if operation["fee_id"] is not None:
            candidate = payload["media"]["fee:" + operation["fee_id"]]["candidate"]
            require(candidate["kind"] == "image_task_fee_candidate_v1")
            observed_at = candidate["observed_at"]
        result = import_task_fee(store, task_id, args.operation, task_blob, account_blob, source, observed_at=observed_at)
        current = store.read_only()
        fee = current["media"]["fee:" + result["operation"]["fee_id"]]["candidate"]
        return {"operation": _resource(result["operation"]), "replayed": result["replayed"],
            "observation_kind": fee["observation_kind"], "platform_observed_cny": fee["platform_fee_cny"],
            "provider_bill_confirmed": False, "risk_released": False, "automatic_followup_calls": 0}
    with session_factory(control) as session:
        media = session.media
        payload = session.runtime.store.read_only()
        if command == "quote":
            result = media.quote(task_id, key=args.key)
            quote, disclosure = result["quote"], result["quote"]["disclosure"]
            anchor = disclosure["budget_anchor"]
            return {"quote": _resource(quote), "replayed": result["replayed"], "model": disclosure["policy"]["model"],
                "max_submissions": disclosure["policy"]["max_submissions"], "max_queries": disclosure["policy"]["max_queries"],
                "manifest_hash": disclosure["manifest_hash"], "reference_profile_hash": (disclosure.get("reference_profile") or {}).get("profile_hash"),
                "allow_lossy_jpeg": (disclosure.get("reference_profile") or {}).get("allow_lossy_jpeg", False),
                **{name: anchor[name] for name in ("original_budget_cny", "opening_risk_cny", "opening_paid_call_count", "scope_risk_cny")},
                "automatic_followup_calls": 0}
        if command == "grant":
            _admit_record(control, payload, "quote", args.quote)
            result = media.grant(task_id, args.quote, disclosure_hash=args.disclosure_hash, consent=args.consent, key=args.key)
            return {"grant": _resource(result["grant"]), "replayed": result["replayed"], "automatic_followup_calls": 0}
        if command == "reserve":
            _admit_record(control, payload, "grant", args.grant)
            result = media.reserve_submit(task_id, args.grant, args.shot, key=args.key)
        elif command in {"start", "query", "fees-import", "delivery-import"}:
            _admit_record(control, payload, "operation", args.operation)
            if command == "start":
                operation = payload["media"]["operation:" + args.operation]
                if operation["capture_started_at"] is None:
                    require(now() < timestamp(operation["descriptor"]["permit"]["deadline_at"]))
                result = media.submit(task_id, args.operation)
            elif command == "query":
                result = media.query(task_id, args.operation, key=args.key)
            elif command == "delivery-import":
                query = _admit_record(control, payload, "query", args.query)
                require(query["operation_id"] == args.operation)
                blob = _import_bytes(control, args.image, 2 * 1048576)
                return {**media.record_delivery(task_id, args.operation, args.query, blob), "visual_quality": "not_reviewed", "automatic_followup_calls": 0}
            else:
                blob = _import_bytes(control, args.export, 8 * 1048576)
                observed_at = _fee_time(control, session.runtime, args.operation, blob)
                try:
                    result = media.observe_fee(task_id, args.operation, blob, observed_at=observed_at)
                except CreatorError as error:
                    if error.code != "FEE_EVIDENCE_CONFLICT":
                        raise
                    stable = _fee_time(control, session.runtime, args.operation, blob)
                    require(stable != observed_at)
                    result = media.observe_fee(task_id, args.operation, blob, observed_at=stable)
        elif command == "recover":
            kind = "operation" if "operation:" + args.operation in payload.get("media", {}) else "query"
            _admit_record(control, payload, kind, args.operation)
            result = media.recover(task_id, args.operation)
        elif command == "revoke":
            _admit_record(control, payload, "grant", args.grant)
            result = media.revoke(task_id, args.grant)
            return {"grant": _resource(result["grant"]), "automatic_followup_calls": 0}
        elif command == "legacy-fees-import":
            original = payload["operations"].get(args.operation)
            account_id = control.document.budget_anchor["account_scope_id"]
            require(original is not None and original["account_id"] == account_id)
            require(payload["accounts"][account_id]["anchor"]["parent_authorization_hash"] == control.document.budget_anchor["parent_authorization_hash"])
            require(payload["tasks"][original["task_id"]]["account_id"] == account_id)
            blob = _import_bytes(control, args.export, 8 * 1048576)
            result = MediaExecution(session.runtime, fee_source=control.document.fee_source.model_dump()).register_text_claim(args.operation, blob)
            return {"claim_id": result["claim_id"], "replayed": result["replayed"], "automatic_followup_calls": 0}
        else:
            raise unavailable()
        kind = "query" if "query" in result else "operation"
        return {kind: _resource(result[kind]), "replayed": result["replayed"], "automatic_followup_calls": 0}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise unavailable()


def parser():
    result = Parser(description="Explicit native image operation; no automatic follow-ups")
    result.add_argument("--control")
    result.add_argument("--continuation")
    result.add_argument("--runtime-continuation")
    result.add_argument("--observation")
    subs = result.add_subparsers(dest="command", required=True)
    for name in ("status", "quote", "grant", "reserve", "start", "query", "recover", "fees-import", "task-fees-import", "legacy-fees-import", "delivery-import", "revoke"):
        command = subs.add_parser(name)
        command.add_argument("--control", default=argparse.SUPPRESS)
        command.add_argument("--continuation", default=argparse.SUPPRESS)
        command.add_argument("--runtime-continuation", default=argparse.SUPPRESS)
        command.add_argument("--observation", default=argparse.SUPPRESS)
        command.add_argument("--task")
        if name in {"quote", "grant", "reserve", "query"}:
            command.add_argument("--key", required=True)
        if name == "grant":
            command.add_argument("--quote", required=True)
            command.add_argument("--disclosure-hash", required=True)
            command.add_argument("--consent", action="store_true", required=True)
        if name in {"reserve", "revoke"}:
            command.add_argument("--grant", required=True)
        if name == "reserve":
            command.add_argument("--shot", required=True)
        if name in {"status", "start", "query", "recover", "fees-import", "task-fees-import", "legacy-fees-import", "delivery-import"}:
            command.add_argument("--operation", required=name != "status")
        if name in {"fees-import", "legacy-fees-import"}:
            command.add_argument("export")
        if name == "task-fees-import":
            command.add_argument("export")
            command.add_argument("--source", required=True)
            command.add_argument("--account-export", required=True)
        if name == "delivery-import":
            command.add_argument("--query", required=True)
            command.add_argument("image")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if not args.control:
            require(args.command == "status" and args.continuation is None and args.runtime_continuation is None and args.observation is None)
            output = {"mode": "off", "dispatch_available": False, "automatic_followup_calls": 0}
        else:
            control = read_image_control(Path(args.control).expanduser().absolute(), BACKEND)
            require(sum(value is not None for value in (args.continuation, args.runtime_continuation, args.observation)) <= 1)
            if args.continuation is not None:
                from core.execution.image_code_continuation import ContinuedImageControl
                control = ContinuedImageControl(control, args.continuation)
            if args.runtime_continuation is not None:
                require(args.command not in {"quote", "grant", "legacy-fees-import"})
                from core.execution.image_runtime_control import RuntimeContinuedImageControl
                control = RuntimeContinuedImageControl(control, args.runtime_continuation)
            if args.observation is not None:
                require(args.command in {"status", "query", "recover", "task-fees-import", "delivery-import"})
                from core.execution.image_observation_control import ImageObservationControl

                control = ImageObservationControl(control, args.observation)
            output = execute(control, args.command, args)
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except CreatorError as error:
        print(json.dumps({"error": error.code, "automatic_followup_calls": 0}))
        return 1
    except Exception:
        print(json.dumps({"error": "IMAGE_CONTROL_UNAVAILABLE", "automatic_followup_calls": 0}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
