"""Explicit same-scope code approval; never extend a policy or reset a grant."""
import argparse
import copy
import hashlib
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from core.creator.store import CreatorError, digest, utc_now
from core.execution.control import (
    CODE_CLOSURE_DIRS,
    REQUIRED_CODE_FILES,
    _code_closure,
    _json,
    _read,
)
from core.execution.image_control import (
    EXTRA_CODE,
    ImageAdmission,
    LoadedImageControl,
    read_image_control,
)
from core.execution.media_ledger import records
from core.execution.policy import now
from core.execution.receipts import timestamp
from core.execution.store import MAX_STATE_BYTES, ExecutionStore
from core.harness.store import unlock_file
from core.planner.review import json_copy

QUOTE = "code_continuation_quote"
ACTIVATION = "code_continuation_activation"
KINDS = {QUOTE, ACTIVATION}
DISCLOSURE_KIND = "image_code_continuation_disclosure_v1"
MAX_CODE_BYTES = 128 * 1048576
BACKEND = Path(__file__).resolve().parents[2]
COMMON = {"schema_version", "kind", "id", "scope_id", "task_id", "created_at"}
QUOTE_FIELDS = {"disclosure", "disclosure_hash"}
ACTIVATION_FIELDS = {"quote_id", "disclosure_hash", "previous_activation_id", "activation_sequence", "consent"}
DISCLOSURE_FIELDS = {"schema_version", "kind", "control_hash", "scope_id", "grant_id", "scope_hash", "grant_hash",
    "policy_hash", "budget_anchor_hash", "parent_allocation_hash", "admission", "source_hash", "plan_hash", "manifest_hash",
    "manifest_origin_hash", "reference_profile_hash", "fee_source_hash", "old_code_manifest", "new_code_manifest", "code_diff",
    "original_valid_until", "checkpoint", "previous_activation_id", "activation_sequence", "remaining_limits",
    "new_paid_calls", "new_risk_cny"}
LIMIT_FIELDS = {"max_submissions", "used_submissions", "remaining_submissions", "max_queries_per_operation", "used_queries"}


def unavailable():
    return CreatorError("IMAGE_CODE_CONTINUATION_UNAVAILABLE", "Explicit image code continuation is unavailable", 409)


def require(value):
    if not value:
        raise unavailable()


def _hash(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _id(value):
    return type(value) is str and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def _manifest(value):
    require(type(value) is dict and 1 <= len(value) <= 1024)
    for name, sha in value.items():
        path = PurePosixPath(name)
        require(type(name) is str and str(path) == name and not path.is_absolute() and ".." not in path.parts
                and "\\" not in name and len(name) <= 500 and _hash(sha))
    require(set(REQUIRED_CODE_FILES) | EXTRA_CODE <= set(value))
    return value


def _diff(old, new):
    return {"added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)),
        "changed": sorted(name for name in set(old) & set(new) if old[name] != new[name])}


def current_code_manifest(control):
    """Collect actual approved-package candidates, never accept caller hashes."""
    require(type(control) is LoadedImageControl)
    control.verify_authority(for_dispatch=False)
    names = set(control.document.code_manifest) | set(REQUIRED_CODE_FILES) | EXTRA_CODE
    for directory in CODE_CLOSURE_DIRS:
        root = control.backend_root / directory
        require(root.is_dir())
        names.update(path.relative_to(control.backend_root).as_posix() for path in root.rglob("*.py"))
    require(len(names) <= 1024)
    manifest, size = {}, 0
    for name in sorted(names):
        raw = _read(control.backend_root / name, maximum=4 * 1048576, private=False)
        size += len(raw)
        require(size <= MAX_CODE_BYTES)
        manifest[name] = hashlib.sha256(raw).hexdigest()
    _manifest(manifest)
    _code_closure(control.backend_root, manifest)
    return manifest


def _state(store):
    return _json(_read(store.path("state.json"), maximum=MAX_STATE_BYTES))


def _checkpoint(state, payload):
    return {"head_revision": state["head_revision"], "head_hash": state["head_hash"], "payload_hash": digest(payload)}


def _active(payload, scope_id):
    found = sorted((r for r in records(payload, ACTIVATION) if r["scope_id"] == scope_id), key=lambda r: r["activation_sequence"])
    return found[-1] if found else None


def _limits(payload, scope):
    operations = [r for r in records(payload, "operation") if r["scope_id"] == scope["id"]]
    queries = [r for r in records(payload, "query") if r["scope_id"] == scope["id"]]
    return {"max_submissions": scope["policy"]["max_submissions"], "used_submissions": len(operations),
        "remaining_submissions": scope["policy"]["max_submissions"] - len(operations),
        "max_queries_per_operation": scope["policy"]["max_queries"], "used_queries": len(queries)}


def _scope(control, payload, ready, grant_id):
    found = control._matching(payload, ready)
    require(len(found) == 1)
    scope = found[0]
    grant = payload.get("media", {}).get("grant:" + grant_id)
    require(grant is not None and grant["scope_id"] == scope["id"] and grant["task_id"] == scope["task_id"])
    require(grant["revoked_at"] is None)
    require(all(record["descriptor"]["permit"]["grant_id"] == grant_id
        for record in payload.get("media", {}).values()
        if record["kind"] in {"operation", "query"} and record["scope_id"] == scope["id"]
        and record["status"] in {"reserved", "submitting"}))
    return scope, grant


def _eligible(control):
    require(type(control) is LoadedImageControl)
    context, handoff, ready, payload = control._verified(for_dispatch=False)
    require(control.document.mode == "enabled" and now() < timestamp(control.document.policy["valid_until"]))
    ledger = _json(_read(control.document.authority.parent_ledger_path))
    selected = ledger["operations"][control.document.authority.allocation_key]
    require(selected["status"] == "reserved" and selected.get("provider_task_id") is None
            and selected.get("actual_charge_fen") is None and selected["request"].get("scope_status") == "open")
    return context, handoff, ready, payload


@contextmanager
def _lock(store, task_id):
    lock = store.execution_lock(task_id)
    require(lock is not None)
    try:
        yield
    finally:
        unlock_file(lock)


def _binding(control, scope, grant):
    return {"control_hash": control.control_hash, "scope_id": scope["id"], "grant_id": grant["id"],
        "scope_hash": digest(scope), "grant_hash": digest(grant), "policy_hash": scope["policy"]["policy_hash"],
        "budget_anchor_hash": scope["anchor"]["budget_anchor_hash"], "parent_allocation_hash": scope["anchor"]["parent_allocation_hash"],
        "admission": control.document.admission.model_dump(), "source_hash": control.document.admission.source_hash,
        "plan_hash": control.document.admission.plan_hash, "manifest_hash": scope["manifest"]["manifest_hash"],
        "manifest_origin_hash": digest(scope.get("manifest_origin")),
        "reference_profile_hash": None if scope.get("reference_profile") is None else scope["reference_profile"]["profile_hash"],
        "fee_source_hash": digest(scope["fee_source"]), "old_code_manifest": copy.deepcopy(control.document.code_manifest),
        "original_valid_until": scope["policy"]["valid_until"]}


def _record(kind, record_id, scope, **fields):
    return {"schema_version": 1, "kind": kind, "id": record_id, "scope_id": scope["id"],
        "task_id": scope["task_id"], "created_at": utc_now(), **fields}


def _quote_birth(state, quote):
    key = QUOTE + ":" + quote["id"]
    found = [frame for frame in state["frames"] if key in frame["delta"].get("media", {})]
    require(len(found) == 1)
    frame, checkpoint = found[0], quote["disclosure"]["checkpoint"]
    require(frame["revision"] == checkpoint["head_revision"] + 1 and frame["previous_hash"] == checkpoint["head_hash"])
    require(digest(frame["delta"]["media"][key]) == digest(quote))
    return frame


def quote_code_continuation(control, grant_id):
    """Persist one server-built disclosure; this grants no dispatch authority."""
    try:
        require(_id(grant_id))
        store = ExecutionStore(control.roots["execution"])
        with _lock(store, control.document.admission.task_id), store.transaction() as payload:
            _, _, ready, current = _eligible(control)
            require(digest(current) == digest(payload))
            scope, grant = _scope(control, payload, ready, grant_id)
            manifest = current_code_manifest(control)
            active = _active(payload, scope["id"])
            state = _state(store)
            for quote in reversed(records(payload, QUOTE)):
                disclosure = quote["disclosure"]
                if (quote["scope_id"] == scope["id"] and disclosure["control_hash"] == control.control_hash
                        and disclosure["grant_id"] == grant_id and disclosure["new_code_manifest"] == manifest):
                    if active is not None and active["quote_id"] == quote["id"]:
                        return {"quote": copy.deepcopy(quote), "replayed": True, "automatic_followup_calls": 0}
                    birth = _quote_birth(state, quote)
                    if state["head_revision"] == birth["revision"] and state["head_hash"] == birth["hash"]:
                        return {"quote": copy.deepcopy(quote), "replayed": True, "automatic_followup_calls": 0}
            old = control.document.code_manifest
            require(manifest != old and (active is None or manifest != payload["media"][QUOTE + ":" + active["quote_id"]]["disclosure"]["new_code_manifest"]))
            disclosure = {"schema_version": 1, "kind": DISCLOSURE_KIND, **_binding(control, scope, grant),
                "new_code_manifest": manifest, "code_diff": _diff(old, manifest), "checkpoint": _checkpoint(state, payload),
                "previous_activation_id": None if active is None else active["id"],
                "activation_sequence": 1 if active is None else active["activation_sequence"] + 1,
                "remaining_limits": _limits(payload, scope), "new_paid_calls": 0, "new_risk_cny": "0.000000"}
            quote = _record(QUOTE, digest(disclosure)[:32], scope, disclosure=disclosure, disclosure_hash=digest(disclosure))
            require(QUOTE + ":" + quote["id"] not in payload["media"])
            payload["media"][QUOTE + ":" + quote["id"]] = quote
            return {"quote": copy.deepcopy(quote), "replayed": False, "automatic_followup_calls": 0}
    except Exception:
        raise unavailable() from None


def _check_binding(control, scope, grant, disclosure):
    expected = _binding(control, scope, grant)
    require(all(digest(disclosure[key]) == digest(value) for key, value in expected.items()))


def activate_code_continuation(control, quote_id, *, disclosure_hash, consent):
    """Explicit exact-hash consent, compare-and-swap against the quoted head."""
    try:
        require(_id(quote_id) and _hash(disclosure_hash) and consent is True)
        store = ExecutionStore(control.roots["execution"])
        with _lock(store, control.document.admission.task_id), store.transaction() as payload:
            _, _, ready, current = _eligible(control)
            require(digest(current) == digest(payload))
            quote = payload["media"][QUOTE + ":" + quote_id]
            require(quote["disclosure_hash"] == disclosure_hash)
            disclosure = quote["disclosure"]
            scope, grant = _scope(control, payload, ready, disclosure["grant_id"])
            _check_binding(control, scope, grant, disclosure)
            require(current_code_manifest(control) == disclosure["new_code_manifest"])
            active = _active(payload, scope["id"])
            if active is not None and active["quote_id"] == quote_id:
                return {"activation": copy.deepcopy(active), "replayed": True, "automatic_followup_calls": 0}
            state = _state(store)
            birth = _quote_birth(state, quote)
            require(state["head_revision"] == birth["revision"] and state["head_hash"] == birth["hash"])
            require(disclosure["previous_activation_id"] == (None if active is None else active["id"]))
            require(disclosure["activation_sequence"] == (1 if active is None else active["activation_sequence"] + 1))
            require(_limits(payload, scope) == disclosure["remaining_limits"])
            values = {"quote_id": quote_id, "disclosure_hash": disclosure_hash,
                "previous_activation_id": disclosure["previous_activation_id"], "activation_sequence": disclosure["activation_sequence"], "consent": True}
            activation = _record(ACTIVATION, digest(values)[:32], scope, **values)
            require(ACTIVATION + ":" + activation["id"] not in payload["media"])
            payload["media"][ACTIVATION + ":" + activation["id"]] = activation
            return {"activation": copy.deepcopy(activation), "replayed": False, "automatic_followup_calls": 0}
    except Exception:
        raise unavailable() from None


def validate_record(payload, record):
    """Replay stored contracts only; historical code/expiry never grants a call."""
    kind = record["kind"]
    require(kind in KINDS and type(record["schema_version"]) is int and record["schema_version"] == 1)
    require(set(record) == COMMON | (QUOTE_FIELDS if kind == QUOTE else ACTIVATION_FIELDS))
    require(_id(record["id"]) and _id(record["scope_id"]))
    scope = payload["media"]["scope:" + record["scope_id"]]
    require(record["task_id"] == scope["task_id"])
    if kind == QUOTE:
        d = json_copy(record["disclosure"], limit=8 * 1048576)
        require(set(d) == DISCLOSURE_FIELDS and type(d["schema_version"]) is int and d["schema_version"] == 1 and d["kind"] == DISCLOSURE_KIND)
        require(_hash(record["disclosure_hash"]) and record["disclosure_hash"] == digest(d) and record["id"] == digest(d)[:32])
        require(d["scope_id"] == scope["id"] and _hash(d["control_hash"]))
        grant = payload["media"]["grant:" + d["grant_id"]]
        require(grant["scope_id"] == scope["id"] and grant["task_id"] == scope["task_id"])
        frozen_grant = {**grant, "revoked_at": None}
        require(d["scope_hash"] == digest(scope) and d["grant_hash"] == digest(frozen_grant))
        require(d["policy_hash"] == scope["policy"]["policy_hash"] and d["budget_anchor_hash"] == scope["anchor"]["budget_anchor_hash"]
                and d["parent_allocation_hash"] == scope["anchor"]["parent_allocation_hash"])
        admission = ImageAdmission.model_validate(d["admission"]).model_dump()
        native = payload["tasks"][scope["task_id"]]
        source, handoff, origin = native["source"], scope["handoff"], scope.get("manifest_origin", {})
        expected = {"task_id": scope["task_id"], "planner_task_revision": native["planner_task_revision"],
            **{key: source[key] for key in ("snapshot_hash", "source_hash", "brief_id", "brief_revision")},
            "plan_version": handoff["plan"]["version"], "plan_hash": handoff["plan"]["plan_hash"],
            "acceptance_input_hash": handoff["acceptance"]["input_hash"], "acceptance_report_hash": handoff["acceptance"]["report_hash"],
            "handoff_hash": handoff["handoff_hash"], "provenance_hash": handoff["provenance_hash"],
            "asset_operation_id": origin["asset_operation_id"], "asset_manifest_hash": scope["manifest"]["manifest_hash"],
            "asset_proof_hash": origin["asset_proof"]["proof_hash"]}
        require(digest(admission) == digest(expected) and d["source_hash"] == source["source_hash"])
        require(d["plan_hash"] == scope["asset_input"]["binding"]["plan_hash"] and d["manifest_hash"] == scope["manifest"]["manifest_hash"])
        require(d["manifest_origin_hash"] == digest(scope.get("manifest_origin")) and d["fee_source_hash"] == digest(scope["fee_source"]))
        require(d["reference_profile_hash"] == (None if scope.get("reference_profile") is None else scope["reference_profile"]["profile_hash"]))
        old, new = _manifest(d["old_code_manifest"]), _manifest(d["new_code_manifest"])
        require(set(old) <= set(new) and old != new and d["code_diff"] == _diff(old, new))
        require(d["original_valid_until"] == scope["policy"]["valid_until"])
        require(timestamp(scope["created_at"]) <= timestamp(record["created_at"]) < timestamp(d["original_valid_until"]))
        checkpoint = d["checkpoint"]
        require(type(checkpoint) is dict and set(checkpoint) == {"head_revision", "head_hash", "payload_hash"})
        require(type(checkpoint["head_revision"]) is int and checkpoint["head_revision"] >= 1 and _hash(checkpoint["head_hash"]) and _hash(checkpoint["payload_hash"]))
        require(d["previous_activation_id"] is None or _id(d["previous_activation_id"]))
        require(type(d["activation_sequence"]) is int and d["activation_sequence"] >= 1)
        limits = d["remaining_limits"]
        require(type(limits) is dict and set(limits) == LIMIT_FIELDS and all(type(v) is int and v >= 0 for v in limits.values()))
        require(limits["max_submissions"] == scope["policy"]["max_submissions"]
                and limits["remaining_submissions"] + limits["used_submissions"] == limits["max_submissions"]
                and limits["max_queries_per_operation"] == scope["policy"]["max_queries"])
        require(type(d["new_paid_calls"]) is int and d["new_paid_calls"] == 0 and d["new_risk_cny"] == "0.000000")
    else:
        quote = payload["media"][QUOTE + ":" + record["quote_id"]]
        require(quote["scope_id"] == scope["id"] and quote["disclosure_hash"] == record["disclosure_hash"])
        d = quote["disclosure"]
        require(record["consent"] is True and type(record["activation_sequence"]) is int)
        require(record["previous_activation_id"] == d["previous_activation_id"] and record["activation_sequence"] == d["activation_sequence"])
        values = {key: record[key] for key in ACTIVATION_FIELDS}
        require(record["id"] == digest(values)[:32])
        require(timestamp(quote["created_at"]) <= timestamp(record["created_at"]) < timestamp(d["original_valid_until"]))


def validate_transition(payload, previous=None):
    for scope in records(payload, "scope"):
        chain = sorted((r for r in records(payload, ACTIVATION) if r["scope_id"] == scope["id"]), key=lambda r: r["activation_sequence"])
        require([r["activation_sequence"] for r in chain] == list(range(1, len(chain) + 1)))
        for index, activation in enumerate(chain):
            require(activation["previous_activation_id"] == (None if index == 0 else chain[index - 1]["id"]))
    if previous is None:
        return
    old = previous.get("media", {})
    new = [record for key, record in payload.get("media", {}).items() if key not in old]
    for record in new:
        if record["kind"] == QUOTE:
            scope = old["scope:" + record["scope_id"]]
            d = record["disclosure"]
            active = _active(previous, scope["id"])
            require(d["checkpoint"]["payload_hash"] == digest(previous) and d["remaining_limits"] == _limits(previous, scope))
            require(d["previous_activation_id"] == (None if active is None else active["id"]))
            require(d["activation_sequence"] == (1 if active is None else active["activation_sequence"] + 1))
            require(old["grant:" + d["grant_id"]]["revoked_at"] is None)
            require(len(new) == 1)
        elif record["kind"] == ACTIVATION:
            quote = old[QUOTE + ":" + record["quote_id"]]
            active = _active(previous, record["scope_id"])
            require(record["previous_activation_id"] == (None if active is None else active["id"]))
            require(record["activation_sequence"] == (1 if active is None else active["activation_sequence"] + 1))
            require(old["grant:" + quote["disclosure"]["grant_id"]]["revoked_at"] is None)
            require(_limits(previous, old["scope:" + record["scope_id"]]) == quote["disclosure"]["remaining_limits"])
            before_quote = {**previous, "media": {key: value for key, value in old.items() if key != QUOTE + ":" + quote["id"]}}
            require(digest(before_quote) == quote["disclosure"]["checkpoint"]["payload_hash"])
            require(len(new) == 1)
        elif record["kind"] in {"quote", "grant"}:
            require(_active(previous, record["scope_id"]) is None)
        elif record["kind"] in {"operation", "query"}:
            active = _active(previous, record["scope_id"])
            if active is not None:
                approved = old[QUOTE + ":" + active["quote_id"]]["disclosure"]["grant_id"]
                require(record["descriptor"]["permit"]["grant_id"] == approved)
        if record["kind"] in KINDS:
            without = {**payload, "media": {key: value for key, value in payload["media"].items()
                if key != record["kind"] + ":" + record["id"]}}
            require(digest(without) == digest(previous))


def validate_history(state, payload):
    """Bind saved checkpoint coordinates to actual frames without live gates."""
    births = {quote["id"]: _quote_birth(state, quote) for quote in records(payload, QUOTE)}
    for activation in records(payload, ACTIVATION):
        key = ACTIVATION + ":" + activation["id"]
        found = [frame for frame in state["frames"] if key in frame["delta"].get("media", {})]
        require(len(found) == 1)
        birth, quote_birth = found[0], births[activation["quote_id"]]
        require(digest(birth["delta"]["media"][key]) == digest(activation))
        require(birth["revision"] == quote_birth["revision"] + 1 and birth["previous_hash"] == quote_birth["hash"])


@dataclass(frozen=True)
class ContinuedImageControl:
    """Original immutable control plus one current native code-only consent."""
    base: LoadedImageControl
    activation_id: str

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _verified(self, backend_root=None, *, for_dispatch=False):
        require(type(self.base) is LoadedImageControl and _id(self.activation_id) and type(for_dispatch) is bool)
        if not for_dispatch:
            return self.base._verified(backend_root, for_dispatch=False)
        require(backend_root is None or Path(backend_root) == self.base.backend_root)
        context, handoff, ready, payload = _eligible(self.base)
        activation = payload["media"][ACTIVATION + ":" + self.activation_id]
        require(_active(payload, activation["scope_id"])["id"] == activation["id"])
        quote = payload["media"][QUOTE + ":" + activation["quote_id"]]
        _quote_birth(_state(ExecutionStore(self.roots["execution"])), quote)
        d = quote["disclosure"]
        scope, grant = _scope(self.base, payload, ready, d["grant_id"])
        _check_binding(self.base, scope, grant, d)
        require(scope["id"] == activation["scope_id"] and current_code_manifest(self.base) == d["new_code_manifest"])
        limits = _limits(payload, scope)
        require(limits["used_submissions"] >= d["remaining_limits"]["used_submissions"]
                and limits["remaining_submissions"] >= 0 and limits["used_queries"] >= d["remaining_limits"]["used_queries"])
        return context, handoff, ready, payload

    def verify_authority(self, backend_root=None, *, for_dispatch=False):
        try:
            self._verified(backend_root, for_dispatch=for_dispatch)
            return True
        except Exception:
            raise unavailable() from None

    def guard(self, scope):
        try:
            _, _, ready, payload = self._verified(for_dispatch=True)
            matches = self.base._matching(payload, ready)
            require(len(matches) == 1 and digest(scope) == digest(matches[0]))
            return True
        except Exception:
            return False


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise unavailable()


def parser():
    result = Parser(description="Explicit code-only image continuation; no automatic calls")
    result.add_argument("--control")
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("status", "quote", "activate"):
        command = sub.add_parser(name)
        command.add_argument("--control", default=argparse.SUPPRESS)
        if name == "status":
            command.add_argument("--activation")
        elif name == "quote":
            command.add_argument("--grant", required=True)
        else:
            command.add_argument("--quote", required=True)
            command.add_argument("--disclosure-hash", required=True)
            command.add_argument("--consent", action="store_true", required=True)
    return result


def _safe_quote(quote):
    d = quote["disclosure"]
    return {"id": quote["id"], "disclosure_hash": quote["disclosure_hash"], "activation_sequence": d["activation_sequence"],
        "original_valid_until": d["original_valid_until"], "remaining_limits": d["remaining_limits"],
        "changed_code_counts": {key: len(value) for key, value in d["code_diff"].items()},
        "new_paid_calls": 0, "new_risk_cny": "0.000000", "dispatch_authorized": False}


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if not args.control:
            require(args.command == "status" and args.activation is None)
            output = {"mode": "off", "code_continuation_active": False, "dispatch_available": False, "automatic_followup_calls": 0}
        else:
            control = read_image_control(Path(args.control).expanduser().absolute(), BACKEND)
            if args.command == "quote":
                result = quote_code_continuation(control, args.grant)
                output = {**result, "quote": _safe_quote(result["quote"])}
            elif args.command == "activate":
                result = activate_code_continuation(control, args.quote, disclosure_hash=args.disclosure_hash, consent=args.consent)
                output = {**result, "activation": {key: result["activation"][key] for key in ("id", "activation_sequence", "quote_id")}}
            else:
                payload = ExecutionStore(control.roots["execution"]).read_only()
                found = control._matching(payload, control.verified_asset())
                require(len(found) <= 1)
                active = _active(payload, found[0]["id"]) if found else None
                activation_id = args.activation or (active["id"] if active else None)
                valid = False
                if activation_id is not None:
                    try:
                        valid = ContinuedImageControl(control, activation_id).verify_authority(for_dispatch=True)
                    except CreatorError:
                        pass
                output = {"mode": control.document.mode, "code_continuation_active": active is not None,
                    "control_authority_valid": valid, "dispatch_available": False,
                    "dispatch_requires_original_media_gates": True, "automatic_followup_calls": 0}
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({"error": "IMAGE_CODE_CONTINUATION_UNAVAILABLE", "automatic_followup_calls": 0}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
