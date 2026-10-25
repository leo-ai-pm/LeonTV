"""Explicit read-only image continuation review; no execution entry point."""
import argparse
import json
import os
import stat
from pathlib import Path

from core.creator.store import CreatorError, digest
from core.execution.control import _absolute, _json, _overlap, _private_path, _read
from core.execution.image_control import read_image_control
from core.execution.receipts import ReceiptStore

BACKEND = Path(__file__).resolve().parents[2]
ERROR_CODE = "IMAGE_CONTINUATION_REVIEW_UNAVAILABLE"
MAX_PROPOSAL_BYTES = 16 * 1048576


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CreatorError(ERROR_CODE, "Read-only image continuation review is unavailable", 409)


def _require(value):
    if not value:
        raise CreatorError(ERROR_CODE, "Read-only image continuation review is unavailable", 409)


def _static(control):
    return (control.backend_root / "code", control.backend_root.parent / "frontend/public", *control.static_roots)


def _proposal_bytes(control, path):
    checked = _private_path(_absolute(path), _static(control))
    return _read(checked, maximum=MAX_PROPOSAL_BYTES)


def _export(control, parent_path, output, proposal):
    """Optional first-write document export, outside all original state roots."""
    path = _private_path(_absolute(output), _static(control))
    protected = [control.path, Path(parent_path), *control.roots.values(),
        Path(control.document.authority.authorization_path), Path(control.document.authority.parent_ledger_path),
        Path(control.document.authority.allocation_begin_path)]
    _require(not any(_overlap(path, original) for original in protected))
    parent = path.parent.stat()
    _require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
        and stat.S_IMODE(parent.st_mode) == 0o700)
    serialized = json.dumps(proposal, ensure_ascii=False, allow_nan=False).encode("utf-8")
    _require(len(serialized) <= MAX_PROPOSAL_BYTES)
    if not path.exists():
        store = ReceiptStore(path.parent)
        store.check_private(_static(control))
        store.write_new(path.name, proposal)
    _require(digest(_json(_proposal_bytes(control, path))) == digest(proposal))


def _summary(proposal):
    basis = proposal["basis"]
    return {"mode": "review_only", "proposal_hash": proposal["proposal_hash"], "basis_hash": proposal["basis_hash"],
        "original_valid_until": basis["original_valid_until"], "proposed_valid_until": basis["proposed_valid_until"],
        "limits": basis["limits"], "budget": basis["budget"],
        "remaining_job_count": len(basis["remaining_jobs"]),
        "replacement_count": sum(item["replacement_of"] is not None for item in basis["remaining_jobs"]),
        "read_only": True, "dispatch_available": False, "approval_granted": False,
        "additional_allocation_status": "proposed_only", "paid_calls": 0, "automatic_followup_calls": 0}


def main(argv=None):
    parser = Parser(description="Read-only image continuation review; never activates or dispatches")
    parser.add_argument("command", nargs="?", choices=("status", "propose", "verify"), default="status")
    parser.add_argument("--control")
    parser.add_argument("--parent-ledger",
        help="Explicit private parent observation file; provides no live authority")
    parser.add_argument("--valid-until")
    parser.add_argument("--proposal")
    parser.add_argument("--output")
    try:
        args = parser.parse_args(argv)
        if args.command == "status" and not args.control:
            _require(not any((args.parent_ledger, args.valid_until, args.proposal, args.output)))
            result = {"mode": "off", "read_only": True, "dispatch_available": False, "approval_granted": False,
                "paid_calls": 0, "automatic_followup_calls": 0}
        else:
            _require(bool(args.control))
            control = read_image_control(args.control, BACKEND)
            _require(control.document.mode == "enabled")
            if args.command == "status":
                _require(not any((args.parent_ledger, args.valid_until, args.proposal, args.output)))
                result = {"mode": "review_only", "read_only": True, "dispatch_available": False,
                    "approval_granted": False, "paid_calls": 0, "automatic_followup_calls": 0}
            else:
                from core.execution.image_continuation_parent import load_parent_evidence
                from core.execution.image_continuation_proposal import (
                    build_proposal,
                    verify_proposal,
                )

                _require(bool(args.parent_ledger))
                evidence = load_parent_evidence(control, args.parent_ledger)
                if args.command == "propose":
                    _require(bool(args.valid_until) and not args.proposal)
                    proposal = build_proposal(control, evidence, valid_until=args.valid_until)
                    if args.output:
                        _export(control, args.parent_ledger, args.output, proposal)
                else:
                    _require(bool(args.proposal) and not args.valid_until and not args.output)
                    proposal = _json(_proposal_bytes(control, args.proposal))
                    _require(verify_proposal(proposal, control, evidence) is True)
                result = _summary(proposal)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception:
        print(json.dumps({"error": ERROR_CODE, "dispatch_available": False, "approval_granted": False,
            "paid_calls": 0, "automatic_followup_calls": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
