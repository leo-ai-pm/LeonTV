"""Server-only media methods over ExecutionRuntime's original store and lock."""
import base64
import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta

from core.creator.store import KEY_RE, CreatorError, digest, utc_now
from core.execution.fees import FrozenFeeSource
from core.execution.image_capture import ControlledImageCapture
from core.execution.image_fees import candidate_for
from core.execution.image_policy import (
    ImageToolPolicy,
    MediaBudgetAnchor,
    build_image_query,
    build_image_submit,
)
from core.execution.image_receipts import decode_query, decode_submit, verify_delivery
from core.execution.media_failure_phase import CaptureTrace, record_failure_phase
from core.execution.media_ledger import (
    account_halted,
    candidate_claimed,
    disclosure_for,
    fee_claimed,
    has_pending_fees,
    has_pending_unknown,
    internal_references,
    native_context,
    projection,
    records,
    reference_sources,
    require,
    text_claim_key,
    unresolved_text_claims,
)
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings, timestamp
from core.execution.reference_derivation import (
    check_descriptor_capacity,
    checked_profile,
    derive_references,
)
from core.harness.store import unlock_file
from core.planner.product_assets import image_jobs
from core.planner.review import json_copy


class MediaExecution:
    def __init__(self, runtime, *, policy=None, budget_anchor=None, manifest=None, authority_guard=None, fee_source=None, receipt_capture=None, reference_profile=None, manifest_origin=None, continuation_activation_id=None, continuation_guard=None, continuation_control=None, image_control=None):
        self.runtime, self.store = runtime, runtime.store
        self.policy = policy.frozen() if isinstance(policy, ImageToolPolicy) else copy.deepcopy(policy)
        self.anchor = budget_anchor.frozen() if isinstance(budget_anchor, MediaBudgetAnchor) else copy.deepcopy(budget_anchor)
        self.manifest = copy.deepcopy(manifest)
        self.manifest_origin = copy.deepcopy(manifest_origin)
        self.reference_profile = checked_profile(reference_profile) if reference_profile is not None else None
        self.authority_guard = authority_guard
        self.continuation_activation_id = continuation_activation_id
        self.continuation_guard = continuation_guard
        self.continuation_control = continuation_control
        self.image_control = image_control
        if continuation_control is not None:
            self._bound_continuation_control()
        self.fee_source = FrozenFeeSource.model_validate(fee_source).model_dump() if fee_source is not None else None
        self.capture = ControlledImageCapture(self.store.root / "media-receipts", authority_verifier=self._capture_guard,
            settings=receipt_capture or ReceiptCaptureSettings())

    @contextmanager
    def _lock(self, task_id):
        lock = self.store.execution_lock(task_id)
        if lock is None:
            raise CreatorError("ACTION_NOT_ALLOWED", "The original execution task is active", 409)
        try:
            yield
        finally:
            unlock_file(lock)

    def get(self, task_id):
        with self.store.inspection_snapshot() as payload:
            if task_id not in payload["tasks"]:
                raise CreatorError("NOT_FOUND", "Execution record not found", 404)
            from core.execution.production_image_status import production_images

            return {"media": projection(payload, task_id), "production_images": production_images(self.store, payload, task_id)}

    @staticmethod
    def _record(kind, record_id, task_id, scope_id, **fields):
        return {"schema_version": 1, "kind": kind, "id": record_id, "scope_id": scope_id, "task_id": task_id,
            "created_at": utc_now(), **fields}

    @staticmethod
    def _put(payload, record):
        key = record["kind"] + ":" + record["id"]
        require(key not in payload.setdefault("media", {}), "MEDIA_CONFLICT")
        payload["media"][key] = record
        return record

    def _bound_continuation_control(self):
        from types import MethodType

        from core.execution.image_runtime_control import RuntimeContinuedImageControl

        control, guard = self.continuation_control, self.continuation_guard
        require(type(control) is RuntimeContinuedImageControl
            and control.runtime_activation_id == self.continuation_activation_id
            and control.roots["execution"] == self.store.root
            and type(guard) is MethodType and guard.__self__ is control
            and guard.__func__ is RuntimeContinuedImageControl.guard_runtime, "MEDIA_AUTHORITY_REJECTED")
        return control

    def _configured_authority(self, scope):
        require(self.policy is not None and self.anchor is not None and self.manifest is not None and self.fee_source is not None, "MEDIA_DISABLED")
        require(digest(scope["policy"]) == digest(self.policy) and digest(scope["anchor"]) == digest(self.anchor)
                and digest(scope["manifest"]) == digest(self.manifest) and digest(scope["fee_source"]) == digest(self.fee_source), "MEDIA_POLICY_STALE")
        require(digest(scope.get("reference_profile")) == digest(self.reference_profile), "MEDIA_POLICY_STALE")
        require(digest(scope.get("manifest_origin")) == digest(self.manifest_origin), "MEDIA_POLICY_STALE")

    def _continuation_authority_verified(self, scope, payload, authority):
        """Pure checks on the fresh snapshot supplied by the bound control."""
        from core.execution.image_runtime_continuation import authority_for, latest_activation_id

        self._configured_authority(scope)
        self._bound_continuation_control()
        require(digest(scope) == digest(payload["media"]["scope:" + scope["id"]]), "MEDIA_POLICY_STALE")
        require(digest(authority) == digest(authority_for(payload, self.continuation_activation_id)), "MEDIA_POLICY_STALE")
        require(authority["scope_id"] == scope["id"] and authority["scope_hash"] == digest(scope), "MEDIA_POLICY_STALE")
        require(latest_activation_id(payload, scope["id"]) == authority["activation_id"], "MEDIA_POLICY_STALE")
        require(now() < timestamp(authority["effective_policy"]["valid_until"]), "MEDIA_POLICY_EXPIRED")
        return authority

    def _authority(self, scope, *, payload=None):
        self._configured_authority(scope)
        if self.continuation_control is not None:
            current, authority = self._bound_continuation_control().verified_current()
            require(payload is None or digest(payload) == digest(current), "MEDIA_AUTHORITY_REJECTED")
            return self._continuation_authority_verified(scope, current, authority)
        if getattr(self, "continuation_activation_id", None) is not None:
            from core.execution.image_runtime_continuation import (
                authority_for,
                latest_activation_id,
            )
            current = self.store.read_only() if payload is None else payload
            authority = authority_for(current, self.continuation_activation_id)
            require(authority["scope_id"] == scope["id"] and authority["scope_hash"] == digest(scope), "MEDIA_POLICY_STALE")
            require(latest_activation_id(current, scope["id"]) == authority["activation_id"], "MEDIA_POLICY_STALE")
            require(now() < timestamp(authority["effective_policy"]["valid_until"]), "MEDIA_POLICY_EXPIRED")
            require(callable(self.continuation_guard), "MEDIA_DISABLED")
            candidate, frozen = copy.deepcopy(scope), copy.deepcopy(authority)
            permitted = self.continuation_guard(candidate, frozen)
            json_copy(candidate, limit=16 * 1048576)
            json_copy(frozen, limit=16 * 1048576)
            require(permitted is True and digest(candidate) == digest(scope) and digest(frozen) == digest(authority), "MEDIA_AUTHORITY_REJECTED")
            return authority
        require(now() < timestamp(scope["policy"]["valid_until"]), "MEDIA_POLICY_EXPIRED")
        require(callable(self.authority_guard), "MEDIA_DISABLED")
        candidate = json_copy(scope, limit=16 * 1048576)
        original = digest(candidate)
        permitted = self.authority_guard(candidate)
        json_copy(candidate, limit=16 * 1048576)
        require(permitted is True and digest(candidate) == original, "MEDIA_AUTHORITY_REJECTED")

    def _scope(self, payload, task_id):
        require(self.policy is not None and self.anchor is not None and self.manifest is not None, "MEDIA_DISABLED")
        policy = ImageToolPolicy.model_validate({k: v for k, v in self.policy.items() if k != "policy_hash"}).frozen()
        anchor = MediaBudgetAnchor.model_validate({k: v for k, v in self.anchor.items() if k != "budget_anchor_hash"}).frozen()
        require(digest(policy) == digest(self.policy) and digest(anchor) == digest(self.anchor), "MEDIA_POLICY_STALE")
        frozen = [scope for scope in records(payload, "scope") if scope["task_id"] == task_id
                  and digest(scope["policy"]) == digest(policy) and digest(scope["anchor"]) == digest(anchor)
                  and digest(scope["manifest"]) == digest(self.manifest)
                  and digest(scope.get("reference_profile")) == digest(self.reference_profile)
                  and digest(scope.get("manifest_origin")) == digest(self.manifest_origin)]
        require(len(frozen) <= 1, "MEDIA_CONFLICT")
        if frozen:
            from core.execution.media_ledger import _stable_handoff
            context, handoff = native_context(payload, task_id, historical=True)
            existing = frozen[0]
            require(digest(existing["asset_input"]) == digest(context)
                    and digest(_stable_handoff(existing["handoff"])) == digest(_stable_handoff(handoff)), "MEDIA_POLICY_STALE")
            self._authority(existing)
            return existing
        context, handoff = native_context(payload, task_id)
        require(policy["validation_only"] is (handoff["evidence_kind"] == "synthetic"), "MEDIA_ENVIRONMENT_MISMATCH")
        account = payload["accounts"][payload["tasks"][task_id]["account_id"]]
        identity = {"task_id": task_id, "policy_hash": policy["policy_hash"], "budget_anchor_hash": anchor["budget_anchor_hash"],
            "manifest_hash": self.manifest["manifest_hash"], "handoff_hash": handoff["handoff_hash"]}
        if self.reference_profile is not None:
            identity["reference_profile_hash"] = self.reference_profile["profile_hash"]
        if self.manifest_origin is not None:
            identity["manifest_origin_hash"] = digest(self.manifest_origin)
        scope_id = digest(identity)[:32]
        existing = payload.get("media", {}).get("scope:" + scope_id)
        if existing is not None:
            self._authority(existing)
            return existing
        image_jobs(context, self.manifest, expected_manifest_hash=self.manifest["manifest_hash"])
        scope = self._record("scope", scope_id, task_id, scope_id, account_id=account["id"], account_hash=digest(account),
            ordinal=len(records(payload, "scope")) + 1, policy=policy, anchor=anchor, asset_input=context,
            manifest=copy.deepcopy(self.manifest), handoff=handoff, fee_source=copy.deepcopy(self.fee_source))
        if self.reference_profile is not None:
            scope["reference_profile"] = copy.deepcopy(self.reference_profile)
        if self.manifest_origin is not None:
            scope["manifest_origin"] = copy.deepcopy(self.manifest_origin)
        self._authority(scope)
        return self._put(payload, scope)

    @staticmethod
    def _replay(payload, verb, key, request):
        json_copy(request)
        require(type(key) is str and KEY_RE.fullmatch(key) is not None, "INVALID_INPUT")
        record = payload.get("media", {}).get("key:" + verb + ":" + key)
        if record is not None:
            require(record["request_hash"] == digest(request), "IDEMPOTENCY_CONFLICT")
            return payload["media"][record["resource_key"]]
        return None

    def _remember(self, payload, verb, key, request, resource):
        return self._put(payload, self._record("key", verb + ":" + key, resource["task_id"], resource["scope_id"],
            verb=verb, request=json_copy(request), request_hash=digest(request), resource_key=resource["kind"] + ":" + resource["id"]))

    def quote(self, task_id, *, key):
        request = {"task_id": task_id}
        with self._lock(task_id), self.store.transaction() as payload:
            old = self._replay(payload, "quote", key, request)
            if old:
                return {"quote": copy.deepcopy(old), "replayed": True}
            scope = self._scope(payload, task_id)
            quote = self._record("quote", uuid.uuid4().hex, task_id, scope["id"], disclosure=disclosure_for(scope))
            quote["disclosure_hash"] = digest(quote)
            self._put(payload, quote)
            self._remember(payload, "quote", key, request, quote)
            return {"quote": copy.deepcopy(quote), "replayed": False}

    def grant(self, task_id, quote_id, *, disclosure_hash, consent, key):
        request = {"task_id": task_id, "quote_id": quote_id, "disclosure_hash": disclosure_hash, "consent": consent}
        require(consent is True, "CONSENT_REQUIRED")
        with self._lock(task_id), self.store.transaction() as payload:
            old = self._replay(payload, "grant", key, request)
            if old:
                return {"grant": copy.deepcopy(old), "replayed": True}
            quote = payload.get("media", {}).get("quote:" + quote_id)
            require(quote is not None and quote["task_id"] == task_id and quote["disclosure_hash"] == disclosure_hash, "MEDIA_DISCLOSURE_REQUIRED")
            scope = payload["media"]["scope:" + quote["scope_id"]]
            self._authority(scope)
            require(not unresolved_text_claims(payload), "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED")
            require(not account_halted(payload, scope["account_id"]), "EMPIRICAL_RISK_OVERRUN")
            grant = self._put(payload, self._record("grant", uuid.uuid4().hex, task_id, scope["id"],
                quote_id=quote_id, disclosure_hash=disclosure_hash, revoked_at=None))
            self._remember(payload, "grant", key, request, grant)
            return {"grant": copy.deepcopy(grant), "replayed": False}

    def revoke(self, task_id, grant_id):
        with self._lock(task_id), self.store.transaction() as payload:
            grant = payload.get("media", {}).get("grant:" + grant_id)
            require(grant is not None and grant["task_id"] == task_id, "NOT_FOUND")
            grant["revoked_at"] = grant["revoked_at"] or utc_now()
            return {"grant": copy.deepcopy(grant)}

    @staticmethod
    def _references(payload, scope, shot_id):
        return internal_references(payload, scope, shot_id)

    @staticmethod
    def _permit(scope, grant, job, *, operation_id, index, action="submit", query_index=0):
        issued = now()
        deadline = min(issued + timedelta(seconds=scope["policy"]["request_deadline_seconds"]), timestamp(scope["policy"]["valid_until"]))
        anchor, binding = scope["anchor"], scope["asset_input"]["binding"]
        return {"operation_id": operation_id, "capture_id": operation_id, "task_id": scope["task_id"], "nonce": uuid.uuid4().hex,
            "action": action, "policy_hash": scope["policy"]["policy_hash"], "budget_anchor_hash": anchor["budget_anchor_hash"],
            **{key: anchor[key] for key in ("parent_authorization_hash", "parent_ledger_hash", "parent_allocation_hash", "parent_allocation_begin_hash")},
            "grant_id": grant["id"], "disclosure_hash": grant["disclosure_hash"], "handoff_hash": scope["handoff"]["handoff_hash"],
            "provenance_hash": scope["handoff"]["provenance_hash"], "asset_input_hash": digest(scope["asset_input"]),
            "manifest_hash": scope["manifest"]["manifest_hash"], "job_hash": digest(job), "shot_id": job["shot_id"], "first_frame_version": 1,
            **{key: binding[key] for key in ("source_snapshot_hash", "plan_version", "plan_hash", "acceptance_input_hash", "acceptance_report_hash")},
            "scope_submission_index": index, "query_index": query_index, "paid_calls": 1 if action == "submit" else 0,
            "risk_cny": scope["policy"]["per_submission_risk_cny"] if action == "submit" else "0.000000",
            "issued_at": issued.isoformat(), "deadline_at": deadline.isoformat()}

    def reserve_submit(self, task_id, grant_id, shot_id, *, references=(), key, persistence_activation_id=None):
        request = {"task_id": task_id, "grant_id": grant_id, "shot_id": shot_id, "references": [] if references == () else references}
        if self.continuation_activation_id is not None:
            request["runtime_activation_id"] = self.continuation_activation_id
        if persistence_activation_id is not None:
            request["production_persistence_activation_id"] = persistence_activation_id
        with self._lock(task_id), self.store.transaction() as payload:
            old = self._replay(payload, "submit", key, request)
            if old:
                return {"operation": copy.deepcopy(old), "replayed": True}
            if persistence_activation_id is not None:
                from core.execution.production_image_persistence_contract import verified_current

                actual, persistence = verified_current(self, persistence_activation_id)
                require(digest(actual) == digest(payload) and persistence["grant_id"] == grant_id
                        and persistence["shot_id"] == shot_id, "MEDIA_AUTHORITY_REJECTED")
            grant = payload.get("media", {}).get("grant:" + grant_id)
            require(grant is not None and grant["task_id"] == task_id and grant["revoked_at"] is None, "MEDIA_GRANT_REQUIRED")
            scope = payload["media"]["scope:" + grant["scope_id"]]
            authority = self._authority(scope, payload=payload)
            require(not unresolved_text_claims(payload), "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED")
            require(not account_halted(payload, scope["account_id"]), "EMPIRICAL_RISK_OVERRUN")
            if authority is None:
                require(not has_pending_unknown(payload, scope["account_id"]), "MEDIA_RESPONSE_UNCONFIRMED")
                require(not has_pending_fees(payload, scope["account_id"]), "MEDIA_PLATFORM_FEE_REQUIRED")
            require(not any(op["account_id"] == scope["account_id"] and op["status"] != "settled" for op in payload["operations"].values()), "MEDIA_TEXT_RESPONSE_PENDING")
            prior = [op for op in records(payload, "operation") if op["scope_id"] == scope["id"]]
            if authority is None:
                require(all(op["fee_id"] is not None for op in prior), "MEDIA_PLATFORM_FEE_REQUIRED")
                require(len(prior) < scope["policy"]["max_submissions"] and all(op["shot_id"] != shot_id for op in prior), "MEDIA_CALL_LIMIT")
            else:
                require(len(prior) < authority["max_cumulative_submissions"], "MEDIA_CALL_LIMIT")
            job = next(job for job in image_jobs(scope["asset_input"], scope["manifest"]) if job["shot_id"] == shot_id)
            operation_id = uuid.uuid4().hex
            proofs = []
            if "reference_profile" in scope:
                require(type(references) in {tuple, list} and not references, "MEDIA_REFERENCE_REQUIRED")
                actual_references, proofs = derive_references(profile=scope["reference_profile"],
                    sources=reference_sources(payload, scope, shot_id), target_job=job, policy=scope["policy"],
                    scope_id=scope["id"], task_id=task_id, operation_id=operation_id)
            else:
                actual_references = self._references(payload, scope, shot_id)
                require(references == () or digest(json_copy(references)) == digest(actual_references), "MEDIA_REFERENCE_REQUIRED")
            if authority is None:
                permit = self._permit(scope, grant, job, operation_id=operation_id, index=len(prior) + 1)
                descriptor = build_image_submit(policy=scope["policy"], budget_anchor=scope["anchor"], permit=permit,
                    asset_input=scope["asset_input"], manifest=scope["manifest"], shot_id=shot_id, references=actual_references)
            else:
                from core.execution.image_continuation_policy import (
                    bind_reference_sources,
                    build_image_submit_v2,
                    make_continuation_permit,
                )
                bindings = bind_reference_sources(reference_sources(payload, scope, shot_id), actual_references)
                permit = make_continuation_permit(scope, grant, job, authority, operation_id=operation_id,
                    index=len(prior) + 1, reference_bindings=bindings)
                descriptor = build_image_submit_v2(policy=authority["effective_policy"], budget_anchor=scope["anchor"],
                    permit=permit, continuation_authority=authority, asset_input=scope["asset_input"],
                    manifest=scope["manifest"], shot_id=shot_id, references=actual_references, reference_bindings=bindings)
            if "reference_profile" in scope:
                check_descriptor_capacity(descriptor, worker_factory=self.capture.settings.worker_factory,
                    worker_options=self.capture.settings.worker_options)
                for proof in proofs:
                    self._put(payload, self._record("reference_derivation", proof["derivation_id"], task_id, scope["id"],
                        operation_id=operation_id, proof=proof))
            operation = self._record("operation", operation_id, task_id, scope["id"], descriptor=descriptor.frozen(),
                grant_id=grant_id, quote_id=grant["quote_id"], shot_id=shot_id, submission_index=len(prior) + 1,
                status="reserved", capture_started_at=None, raw=None, receipt=None, fee_id=None, delivery_id=None)
            if persistence_activation_id is not None:
                operation["production_persistence_activation_id"] = persistence_activation_id
            if "reference_profile" in scope:
                operation.update(reference_profile_hash=scope["reference_profile"]["profile_hash"],
                    reference_derivations=[{"id": proof["derivation_id"], "derivation_hash": proof["derivation_hash"]} for proof in proofs])
            if authority is not None:
                from core.execution.media_continuation_gate import candidate_gate
                candidate_gate(payload, operation)
            self._put(payload, operation)
            self._remember(payload, "submit", key, request, operation)
            return {"operation": copy.deepcopy(operation), "replayed": False}

    def _record_authority(self, payload, scope, record):
        from core.execution.media_continuation_gate import is_continuation
        authority = self._authority(scope, payload=payload)
        if authority is None:
            require(not is_continuation(record), "MEDIA_AUTHORITY_REJECTED")
        else:
            require(is_continuation(record)
                    and digest(record["descriptor"]["continuation_authority"]) == digest(authority), "MEDIA_AUTHORITY_REJECTED")
        return authority

    def _dispatch_checks(self, payload, scope, record):
        from core.execution.media_continuation_gate import candidate_gate, is_continuation
        require(not account_halted(payload, scope["account_id"]), "EMPIRICAL_RISK_OVERRUN")
        if is_continuation(record):
            candidate_gate(payload, record, exclude_candidate=True)
        else:
            require(not has_pending_unknown(payload, scope["account_id"], exclude_operation_id=record["id"]), "MEDIA_RESPONSE_UNCONFIRMED")
            require(not has_pending_fees(payload, scope["account_id"], exclude_operation_id=record["id"]), "MEDIA_PLATFORM_FEE_REQUIRED")

    def _capture_guard(self, frozen):
        try:
            if self.continuation_control is not None:
                return self._bound_continuation_control().guard_capture(self, frozen) is True
            payload = self.store.read_only()
            permit = frozen["permit"]
            kind = "operation" if frozen["method"] == "POST" else "query"
            record = payload["media"][kind + ":" + permit["operation_id"]]
            require(record["status"] == "submitting" and digest(record["descriptor"]) == digest(frozen))
            scope = payload["media"]["scope:" + record["scope_id"]]
            self._record_authority(payload, scope, record)
            grant = payload["media"]["grant:" + permit["grant_id"]]
            require(grant["revoked_at"] is None)
            if kind == "operation":
                self._dispatch_checks(payload, scope, record)
                require(not unresolved_text_claims(payload))
            return True
        except Exception:
            return False

    def _capture_guard_verified(self, payload, authority, frozen):
        """Native candidate checks within one fresh v2 capture decision."""
        from core.execution.media_continuation_gate import is_continuation

        try:
            permit = frozen["permit"]
            kind = "operation" if frozen["method"] == "POST" else "query"
            record = payload["media"][kind + ":" + permit["operation_id"]]
            require(record["status"] == "submitting" and digest(record["descriptor"]) == digest(frozen))
            scope = payload["media"]["scope:" + record["scope_id"]]
            self._continuation_authority_verified(scope, payload, authority)
            require(is_continuation(record)
                and digest(record["descriptor"]["continuation_authority"]) == digest(authority), "MEDIA_AUTHORITY_REJECTED")
            grant = payload["media"]["grant:" + permit["grant_id"]]
            require(grant["revoked_at"] is None)
            if kind == "operation":
                self._dispatch_checks(payload, scope, record)
                require(not unresolved_text_claims(payload))
            return True
        except Exception:
            return False

    @staticmethod
    def _adopt(record, raw):
        from core.execution.image_policy import FrozenImageRequest
        require(record["raw"] is None or digest(record["raw"]) == digest(raw), "MEDIA_RECEIPT_CONFLICT")
        descriptor = FrozenImageRequest.from_frozen(record["descriptor"])
        try:
            receipt = decode_submit(descriptor, raw) if record["kind"] == "operation" else decode_query(descriptor, raw)
        except (CreatorError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            # The original committed HTTP envelope remains in ReceiptStore.
            # Invalid content never becomes a normalized native receipt.
            record["status"] = "unknown"
            return
        record.update(raw=raw, receipt=receipt, status="unknown" if receipt["classification"] == "unconfirmed" else receipt["classification"])

    def _run(self, task_id, record_key):
        from core.execution.image_policy import FrozenImageRequest
        with self.store.transaction() as payload:
            record = payload["media"][record_key]
            require(record["task_id"] == task_id, "NOT_FOUND")
            if record["capture_started_at"] is not None:
                return {record["kind"]: copy.deepcopy(record), "replayed": True}
            scope = payload["media"]["scope:" + record["scope_id"]]
            self._record_authority(payload, scope, record)
            grant = payload["media"]["grant:" + record["descriptor"]["permit"]["grant_id"]]
            require(grant["revoked_at"] is None, "MEDIA_GRANT_REVOKED")
            if record["kind"] == "operation":
                self._dispatch_checks(payload, scope, record)
            started = utc_now()
            require(timestamp(record["created_at"]) <= timestamp(started) < timestamp(record["descriptor"]["permit"]["deadline_at"]),
                    "MEDIA_PERMIT_EXPIRED")
            record.update(status="submitting", capture_started_at=started)
            descriptor = FrozenImageRequest.from_frozen(record["descriptor"])
        with CaptureTrace().active() as trace:
            try:
                raw = self.capture.capture(descriptor, self.runtime.api_key)
            except Exception:
                try:
                    record_failure_phase(self.store.root, record, trace.stage)
                except Exception:
                    # Diagnostic availability never changes the original conservative
                    # outcome or substitutes for a dispatch/HTTP/billing receipt.
                    pass
                with self.store.transaction() as payload:
                    payload["media"][record_key]["status"] = "unknown"
                    return {payload["media"][record_key]["kind"]: copy.deepcopy(payload["media"][record_key]), "replayed": False}
        with self.store.transaction() as payload:
            record = payload["media"][record_key]
            self._adopt(record, raw)
            return {record["kind"]: copy.deepcopy(record), "replayed": False}

    def submit(self, task_id, operation_id):
        with self._lock(task_id):
            return self._run(task_id, "operation:" + operation_id)

    def query(self, task_id, operation_id, *, key):
        from core.execution.image_policy import FrozenImageRequest
        request = {"task_id": task_id, "operation_id": operation_id}
        if self.continuation_activation_id is not None:
            request["runtime_activation_id"] = self.continuation_activation_id
        with self._lock(task_id):
            with self.store.transaction() as payload:
                old = self._replay(payload, "query", key, request)
                if old:
                    query_id = old["id"]
                else:
                    operation = payload["media"]["operation:" + operation_id]
                    require(operation["task_id"] == task_id and operation["status"] == "accepted", "MEDIA_ACCEPTED_TASK_REQUIRED")
                    scope = payload["media"]["scope:" + operation["scope_id"]]
                    authority = self._record_authority(payload, scope, operation)
                    count = sum(q["operation_id"] == operation_id for q in records(payload, "query"))
                    require(count < scope["policy"]["max_queries"], "MEDIA_QUERY_LIMIT")
                    original = FrozenImageRequest.from_frozen(operation["descriptor"])
                    grant = payload["media"]["grant:" + operation["grant_id"]]
                    require(grant["revoked_at"] is None, "MEDIA_GRANT_REVOKED")
                    query_id = uuid.uuid4().hex
                    if authority is None:
                        permit = self._permit(scope, grant, original.frozen()["job"], operation_id=query_id,
                            index=operation["submission_index"], action="query", query_index=count + 1)
                    else:
                        from core.execution.image_continuation_policy import (
                            make_continuation_permit,
                        )
                        permit = make_continuation_permit(scope, grant, original.frozen()["job"], authority,
                            operation_id=query_id, index=operation["submission_index"], action="query",
                            query_index=count + 1, reference_bindings=original.frozen()["reference_bindings"])
                    descriptor = build_image_query(submit=original, submit_raw=operation["raw"], permit=permit, query_index=count + 1)
                    query = self._put(payload, self._record("query", query_id, task_id, scope["id"], operation_id=operation_id,
                        descriptor=descriptor.frozen(), query_index=count + 1, status="reserved", capture_started_at=None, raw=None, receipt=None))
                    self._remember(payload, "query", key, request, query)
            result = self._run(task_id, "query:" + query_id)
        return self._receive_completed_result(result)

    def recover(self, task_id, operation_id):
        from core.execution.image_policy import FrozenImageRequest
        with self._lock(task_id):
            payload = self.store.read_only()
            selected = next((kind + ":" + operation_id for kind in ("operation", "query") if kind + ":" + operation_id in payload.get("media", {})), None)
            require(selected is not None and payload["media"][selected]["task_id"] == task_id, "NOT_FOUND")
            record = payload["media"][selected]
            if record["raw"] is not None or record["capture_started_at"] is None:
                result = {record["kind"]: copy.deepcopy(record), "replayed": True}
            else:
                raw, _ = self.capture.ready_raw(FrozenImageRequest.from_frozen(record["descriptor"]))
                with self.store.transaction() as current:
                    record = current["media"][selected]
                    before = digest(record)
                    if raw is None:
                        record["status"] = "unknown"
                    else:
                        self._adopt(record, raw)
                    result = {record["kind"]: copy.deepcopy(record), "replayed": digest(record) == before}
        return self._receive_completed_result(result) if "query" in result else result

    def _receive_completed_result(self, result):
        query = result.get("query")
        if query is None or query["status"] != "completed":
            return result
        payload = self.store.read_only()
        operation = payload["media"]["operation:" + query["operation_id"]]
        activation_id = operation.get("production_persistence_activation_id")
        if activation_id is None:
            return result
        from core.execution.production_image_persistence import ProductionImagePersistenceExecution

        receiver = ProductionImagePersistenceExecution(self, activation_id)
        return {**result, "image_persistence": receiver.receive_completed(operation["id"], query["id"])}

    def observe_fee(self, task_id, operation_id, export_bytes, *, observed_at):
        from core.execution.image_policy import FrozenImageRequest
        with self._lock(task_id), self.store.transaction() as payload:
            operation = payload["media"]["operation:" + operation_id]
            require(operation["task_id"] == task_id and operation["raw"] is not None, "MEDIA_RECEIPT_REQUIRED")
            scope = payload["media"]["scope:" + operation["scope_id"]]
            candidate = candidate_for(FrozenImageRequest.from_frozen(operation["descriptor"]), operation["raw"], export_bytes,
                scope["fee_source"], observed_at=observed_at)
            fee_id = candidate["platform_log_id"]
            if operation["fee_id"] is not None:
                old = payload["media"]["fee:" + operation["fee_id"]]
                require(digest(old["candidate"]) == digest(candidate), "FEE_EVIDENCE_CONFLICT")
                return {"operation": copy.deepcopy(operation), "replayed": True}
            require(not candidate_claimed(payload, candidate, operation_id), "FEE_EVIDENCE_CONFLICT")
            require(not unresolved_text_claims(payload), "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED")
            export_base64 = base64.b64encode(export_bytes).decode()
            self._put(payload, self._record("fee", fee_id, task_id, scope["id"], operation_id=operation_id, candidate=candidate, export_base64=export_base64))
            self._put(payload, self._record("claim", fee_id, task_id, scope["id"], owner="media", operation_id=operation_id,
                evidence_hash=digest(candidate), export_base64=export_base64, fee_source=scope["fee_source"]))
            operation["fee_id"] = fee_id
            return {"operation": copy.deepcopy(operation), "replayed": False}

    def observe_task_fee(self, task_id, operation_id, task_export_bytes, account_export_bytes, task_source, *, observed_at):
        """Import an exact native-task quota fact without loading a credential."""
        from core.execution.media_fee_import import import_task_fee
        return import_task_fee(self.store, task_id, operation_id, task_export_bytes, account_export_bytes,
            task_source, observed_at=observed_at)

    def register_text_claim(self, operation_id, export_bytes):
        payload = self.store.read_only()
        operation = payload["operations"][operation_id]
        task_id = operation["task_id"]
        require(self.fee_source is not None, "MEDIA_DISABLED")
        with self._lock(task_id), self.store.transaction() as payload:
            operation = payload["operations"][operation_id]
            key = text_claim_key(payload, operation_id, export_bytes, self.fee_source)
            old = payload.get("media", {}).get("claim:" + key)
            if old is not None:
                require(old["owner"] == "text" and old["operation_id"] == operation_id, "FEE_EVIDENCE_CONFLICT")
                return {"claim_id": key, "replayed": True}
            require(not fee_claimed(payload, key, operation_id), "FEE_EVIDENCE_CONFLICT")
            self._put(payload, self._record("claim", key, task_id, None, owner="text", operation_id=operation_id,
                evidence_hash=digest(operation["empirical"]["fee_evidence"]), export_base64=base64.b64encode(export_bytes).decode(), fee_source=self.fee_source))
            return {"claim_id": key, "replayed": False}

    def record_delivery(self, task_id, operation_id, query_id, image_bytes):
        from core.execution.image_policy import FrozenImageRequest
        require(type(image_bytes) is bytes and len(image_bytes) <= 2 * 1048576, "MEDIA_DELIVERY_TOO_LARGE")
        with self._lock(task_id), self.store.transaction() as payload:
            operation = payload["media"]["operation:" + operation_id]
            query = payload["media"]["query:" + query_id]
            require(operation["task_id"] == task_id and query["task_id"] == task_id and query["operation_id"] == operation_id, "NOT_FOUND")
            delivery = verify_delivery(FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"], image_bytes)
            if "production_persistence_activation_id" in operation:
                from core.execution.production_image_persistence import (
                    ProductionImagePersistenceExecution,
                )

                receiver = ProductionImagePersistenceExecution(self, operation["production_persistence_activation_id"])
                require(receiver.verified_saved_bytes(payload, operation, query, delivery) == image_bytes, "MEDIA_DELIVERY_CONFLICT")
            if operation["delivery_id"] is not None:
                require(digest(payload["media"]["delivery:" + operation["delivery_id"]]["delivery"]) == digest(delivery), "MEDIA_DELIVERY_CONFLICT")
                return {"delivery_hash": delivery["delivery_hash"], "replayed": True}
            record_id = uuid.uuid4().hex
            self._put(payload, self._record("delivery", record_id, task_id, operation["scope_id"], operation_id=operation_id,
                query_id=query_id, delivery=delivery, bytes_base64=base64.b64encode(image_bytes).decode()))
            operation["delivery_id"] = record_id
            return {"delivery_hash": delivery["delivery_hash"], "replayed": False}
