"""Standalone read-only image fee diagnosis; never authority or reconciliation."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from core.creator.store import CreatorError, utc_now
from core.execution.fees import FrozenFeeSource, parse_export
from core.execution.image_fees import candidate_for
from core.execution.image_policy import FrozenImageRequest
from core.execution.image_receipts import decode_submit
from core.execution.receipts import raw_body
from core.planner.protocol import parse_json

BACKEND = Path(__file__).resolve().parents[2]
ERROR_CODE = "IMAGE_FEE_INSPECTION_UNAVAILABLE"
MESSAGE = "Explicit image fee inspection is unavailable"
EXPORT_ERRORS = {"FEE_EXPORT_INVALID", "FEE_EVIDENCE_CONFLICT"}


def unavailable():
    return CreatorError(ERROR_CODE, MESSAGE, 503)


def _require(value):
    if not value:
        raise unavailable()


def _readonly():
    return {"read_only": True, "automatic_followup_calls": 0,
            "import_authorized": False, "new_dispatch_authorized": False}


def _inputs(request, source):
    try:
        _require(type(request) is FrozenImageRequest)
        frozen = request.frozen()
        _require(frozen["method"] == "POST")
        source = FrozenFeeSource.model_validate(source.model_dump() if isinstance(source, FrozenFeeSource) else source)
        _require(source.account_namespace == frozen["budget_anchor"]["account_scope_id"])
        return frozen, source
    except Exception:
        raise unavailable() from None


def _export(report, blob, source, model):
    if blob is None:
        return
    try:
        rows = parse_export(blob)
        report.update(export_status="valid", export_sha256=hashlib.sha256(blob).hexdigest(),
            same_account_model_row_count=sum(row["type"] == 2 and row["token_name"] == source.token_name
                and row["group"] == source.group and row["model_name"] == model for row in rows.values()))
    except Exception as error:
        code = error.code if isinstance(error, CreatorError) and error.code in EXPORT_ERRORS else "FEE_EXPORT_INVALID"
        report.update(export_status="invalid", export_error_code=code)


def inspect_receipt(request, raw, source, *, export_bytes=None, observed_at):
    """Report exact direct-ID matching only; never return private identities."""
    frozen, source = _inputs(request, source)
    report = {**_readonly(), "schema_version": 1, "kind": "image_fee_inspection_v1",
        "operation_id": frozen["permit"]["operation_id"], "descriptor_hash": frozen["descriptor_hash"],
        "model": frozen["policy"]["model"], "raw_hash": None, "raw_body_sha256": None,
        "receipt_classification": "unconfirmed", "request_id_status": "missing",
        "captured_standard_header_present": type(raw) is dict and type(raw.get("headers")) is dict and "x-request-id" in raw["headers"],
        "body_request_id_present": False, "accepted_task_id_present": False,
        # The existing collector did not retain other header names.
        "upstream_other_request_id_presence": "unknown", "fee_match_status": "receipt_unavailable",
        "candidate_matches": False, "candidate_platform_fee_cny": None, "candidate_overrun": None,
        "candidate_hash": None, "same_account_model_row_count": None,
        "export_status": "not_provided", "export_error_code": None, "export_sha256": None}
    _export(report, export_bytes, source, frozen["policy"]["model"])
    if raw is None:
        return report
    try:
        receipt = decode_submit(request, raw)
        body = parse_json(raw_body(raw).decode("utf-8"))
    except Exception:
        report["fee_match_status"] = "receipt_invalid"
        return report
    report.update(receipt_classification=receipt["classification"], request_id_status=receipt["request_id_status"],
        accepted_task_id_present=receipt["accepted_task_id"] is not None,
        body_request_id_present=type(body) is dict and "request_id" in body,
        raw_hash=receipt["raw_hash"], raw_body_sha256=receipt["raw_body_sha256"])
    identity_status = {"missing": "missing_submit_request_id", "conflicting": "conflicting_submit_request_id",
                       "invalid": "invalid_submit_request_id"}.get(receipt["request_id_status"])
    if identity_status:
        report["fee_match_status"] = identity_status
        return report
    if export_bytes is None:
        report["fee_match_status"] = "export_required"
        return report
    if report["export_status"] == "invalid":
        report["fee_match_status"] = "evidence_conflict" if report["export_error_code"] == "FEE_EVIDENCE_CONFLICT" else "export_invalid"
        return report
    try:
        candidate = candidate_for(request, raw, export_bytes, source, observed_at=observed_at)
    except CreatorError as error:
        report["fee_match_status"] = {"FEE_EXPORT_PENDING": "direct_match_missing",
            "FEE_EXPORT_INVALID": "export_invalid"}.get(error.code, "evidence_conflict")
        return report
    except Exception:
        report["fee_match_status"] = "evidence_conflict"
        return report
    report.update(fee_match_status="direct_match", candidate_matches=True, candidate_hash=candidate["candidate_hash"],
        candidate_platform_fee_cny=candidate["platform_fee_cny"], candidate_overrun=candidate["overrun"])
    return report


def _next_action(report):
    if report.get("native_fee_candidate_conflict"):
        return "await_exact_fee_evidence"
    if report.get("fee_observed") and report["export_status"] == "not_provided":
        return "await_separate_dispatch_authority" if report["delivery_present"] else "inspect_existing_task"
    status = report["fee_match_status"]
    if status in {"receipt_unavailable", "receipt_invalid", "conflicting_submit_request_id", "invalid_submit_request_id"}:
        return "inspect_original_receipt"
    if status == "export_required":
        return "provide_original_export"
    if status == "direct_match":
        if report.get("fee_observed"):
            return "await_separate_dispatch_authority" if report["delivery_present"] else "inspect_existing_task"
        return "review_direct_fee_candidate"
    return "await_exact_fee_evidence"


def inspect_native(control, operation_id, *, export_bytes=None, observed_at=None):
    """Read existing journal evidence without building any execution runtime."""
    from core.execution.image_operator import _admit_record
    from core.execution.store import ExecutionStore

    try:
        _require(control.verify_authority(for_dispatch=False) is True)
        payload = ExecutionStore(control.roots["execution"]).read_only()
        operation = _admit_record(control, payload, "operation", operation_id)
        scope = payload["media"]["scope:" + operation["scope_id"]]
        fee = payload["media"]["fee:" + operation["fee_id"]] if operation["fee_id"] is not None else None
        original = fee["candidate"] if fee else None
        observed = observed_at if observed_at is not None else original["observed_at"] if original else utc_now()
        report = inspect_receipt(FrozenImageRequest.from_frozen(operation["descriptor"]), operation["raw"],
            scope["fee_source"], export_bytes=export_bytes, observed_at=observed)
        queries = [record for record in payload.get("media", {}).values()
                   if record["kind"] == "query" and record["operation_id"] == operation_id]
        counts = {}
        for query in queries:
            counts[query["status"]] = counts.get(query["status"], 0) + 1
        used = sum(record["kind"] == "operation" and record["scope_id"] == scope["id"]
                   for record in payload.get("media", {}).values())
        conflict = original is not None and report["candidate_hash"] != original["candidate_hash"] if report["candidate_matches"] else None
        report.update(native_status=operation["status"], fee_observed=fee is not None,
            native_platform_observed_cny=original["platform_fee_cny"] if original else None,
            native_fee_candidate_conflict=conflict, query_count=len(queries), query_status_counts=dict(sorted(counts.items())),
            delivery_present=operation["delivery_id"] is not None, quality_status="not_reviewed",
            scope_risk_cny=scope["anchor"]["scope_risk_cny"],
            remaining_call_count=max(0, scope["policy"]["max_submissions"] - used))
        if conflict:
            report["fee_match_status"] = "evidence_conflict"
        report["next_action"] = _next_action(report)
        return report
    except CreatorError as error:
        if error.code in {"IMAGE_CONTROL_UNAVAILABLE", ERROR_CODE}:
            raise
        raise unavailable() from None
    except Exception:
        raise unavailable() from None


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise unavailable()


def parser():
    result = Parser(description="Read-only native image fee inspection")
    result.add_argument("command", nargs="?", choices=["status"], default="status")
    result.add_argument("--control")
    result.add_argument("--operation")
    result.add_argument("--export")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if not args.control:
            _require(args.operation is None and args.export is None)
            output = {**_readonly(), "mode": "off"}
        else:
            from core.execution.control import _private_path, _read
            from core.execution.image_control import read_image_control

            _require(args.operation is not None)
            control = read_image_control(Path(args.control).expanduser().absolute(), BACKEND)
            blob = None
            if args.export is not None:
                path = Path(args.export).expanduser().absolute()
                static = (control.backend_root / "code", control.backend_root.parent / "frontend/public", *control.static_roots)
                _private_path(path, static)
                blob = _read(path, maximum=8 * 1048576)
            output = inspect_native(control, args.operation, export_bytes=blob)
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except Exception as error:
        code = error.code if isinstance(error, CreatorError) and error.code == "IMAGE_CONTROL_UNAVAILABLE" else ERROR_CODE
        print(json.dumps({**_readonly(), "error": code}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
