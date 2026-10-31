"""Real native authority/store/collector; no real account or external network."""
import copy
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from test_image_comparison_native import original_five_case
from test_image_fees import export, observed, row_for
from test_image_receipts import image_bytes
from test_media_persistence_io import local_http

from core.creator.store import CreatorError, digest
from core.execution import image_comparison as base
from core.execution import media_persistence as receiver
from core.execution import media_persistence_contract as contract
from core.execution import media_persistence_io as io
from core.execution.image_comparison_runtime import ComparisonExecution
from core.execution.image_control import read_image_control
from core.execution.image_policy import FrozenImageRequest
from core.execution.media_persistence_contract import (
    activate_media_persistence,
    quote_media_persistence,
)
from core.execution.media_persistence_schema import RECEIVE
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.store import ExecutionStore


def test_native_media_persistence_future_task_and_recovery(asset_control_case, monkeypatch, tmp_path):
    matching_source = os.environ.get("D47_MATCHING_AUTHORITY_SOURCE") == "1"
    if matching_source:
        # Generate fresh synthetic authorizations over the actual staged code,
        # before any source/authority/hash is frozen. No validator is mocked and
        # no historical manifest is edited. Old fixture files remain unchanged.
        import asset_operator_fixture
        import image_operator_fixture

        from core.execution.control import CODE_CLOSURE_DIRS, REQUIRED_CODE_FILES
        runtime_root = Path(base.__file__).resolve().parents[2]
        names = set(REQUIRED_CODE_FILES) | asset_operator_fixture.EXTRA_CODE | image_operator_fixture.EXTRA_CODE
        intended_manifest = json.loads(Path(os.environ["D47_AUTHORITY_MANIFEST"]).read_text())
        names.update(intended_manifest)
        for directory in CODE_CLOSURE_DIRS:
            names.update(p.relative_to(runtime_root).as_posix() for p in (runtime_root / directory).rglob("*.py"))
        monkeypatch.setattr(asset_operator_fixture, "REQUIRED_CODE_FILES", tuple(sorted(names)))
        monkeypatch.setattr(image_operator_fixture, "REQUIRED_CODE_FILES", tuple(sorted(names)))
        original_write = Path.write_text
        def actual_fixture_source(path, value, *args, **kwargs):
            if value in {"# Independent frozen synthetic public module.\n", "# Independent frozen image operator module.\n"} and "public-backend" in path.parts:
                relative = Path(*path.parts[path.parts.index("public-backend") + 1:])
                assert relative.as_posix() in names
                raw = (runtime_root / relative).read_bytes()
                path.write_bytes(raw)
                return len(raw)
            return original_write(path, value, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", actual_fixture_source)
    cached = os.environ.get("D47_COMPLETED_BASELINE") or os.environ.get("D47_SYNTHETIC_BASELINE")
    if cached:
        checkpoint = Path(cached).resolve()
        assert "synthetic-fixtures" in checkpoint.parts
        info = json.loads(checkpoint.read_text())
        control = read_image_control(info["control"], info["backend"])
        activation_id = info["comparison_activation_id"]
    else:
        case = original_five_case(asset_control_case, monkeypatch)
        control = case.control
        quote = base.quote_comparison(control, valid_until=(now() + timedelta(hours=3)).isoformat())["quote"]
        intent = base.prepare_comparison(control, quote["id"], proposal_hash=quote["proposal"]["proposal_hash"], consent=True)["intent"]
        activated = base.activate_comparison(control, intent["id"], proposal_hash=quote["proposal"]["proposal_hash"], consent=True)["activation"]
        activation_id = activated["id"]
        checkpoint = tmp_path / "prepared-synthetic.json"
        checkpoint.write_text(json.dumps({"control": str(control.path), "backend": str(control.backend_root), "comparison_activation_id": activation_id}))
    assert control.document.policy["validation_only"] is True and control.document.policy["origin"] == "https://synthetic.invalid"
    if matching_source:
        from core.execution.image_code_continuation import _collect_code_manifest
        manifest = _collect_code_manifest(control)
        assert manifest == intended_manifest
        assert all(hashlib.sha256((runtime_root / name).read_bytes()).hexdigest() == sha for name, sha in manifest.items())
        (tmp_path / "matching-authority-source.json").write_text(json.dumps({"authority_backend": str(control.backend_root), "runtime_backend": str(runtime_root), "manifest": manifest, "all_hashes_match": True}, indent=2))
    if os.environ.get("D47_PREPARE_ONLY") == "1":
        return
    store = ExecutionStore(control.roots["execution"])
    settings = ReceiptCaptureSettings(worker_factory="media_persistence_fixture:make_transport",
        worker_options={"counter": str(tmp_path / "synthetic-task-http.jsonl"), "unique_tasks": True})
    execution = ComparisonExecution(control, activation_id, receipt_capture=settings, credential_loader=lambda: "SYNTHETIC")
    if not os.environ.get("D47_COMPLETED_BASELINE"):
        q = quote_media_persistence(control, activation_id, valid_until=(now() + timedelta(hours=2)).isoformat())["quote"]
        for consent in (False, 1, "true", None):
            with pytest.raises(CreatorError):
                activate_media_persistence(control, q["id"], disclosure_hash=q["disclosure_hash"], consent=consent)
        a = activate_media_persistence(control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
        op = execution.reserve(key="D47-synthetic-future", persistence_activation_id=a["id"])["operation"]
        op = execution.submit(op["id"])["operation"]
        assert op["status"] == "accepted"
        request = FrozenImageRequest.from_frozen(op["descriptor"])
        execution.observe_fee(op["id"], export(row_for(request, op["raw"], request_id=op["receipt"]["submit_request_id"])), observed_at=observed(op["raw"]))
        # Real process interruption seam after the completed task journal commit,
        # before entering the new reception method. No validator is substituted.
        with monkeypatch.context() as fault:
            def interrupted(self, result):
                assert result["query"]["status"] == "completed"
                raise RuntimeError("SYNTHETIC AFTER COMPLETED BEFORE RECEPTION")
            fault.setattr(ComparisonExecution, "_receive_completed_result", interrupted)
            with pytest.raises(RuntimeError, match="AFTER COMPLETED"):
                execution.query(op["id"], key="D47-completed-query")
        query = base.records(store.read_only(), base.QUERY)[0]
        with pytest.raises(CreatorError):
            execution.record_artifact(op["id"], query["id"], image_bytes())
        checkpoint = tmp_path / "completed-synthetic.json"
        checkpoint.write_text(json.dumps({"control": str(control.path), "backend": str(control.backend_root),
            "comparison_activation_id": activation_id, "persistence_activation_id": a["id"], "operation_id": op["id"], "query_id": query["id"]}))
    else:
        op = store.read_only()["media"][base.OPERATION + ":" + info["operation_id"]]
        query = store.read_only()["media"][base.QUERY + ":" + info["query_id"]]
    if os.environ.get("D47_COMPLETE_ONLY") == "1":
        return
    baseline = store.path("state.json").read_bytes()
    before = store.read_only()
    permission_id = op["persistence_activation_id"]
    from core.execution.image_continuation_parent_io import server_parent_path
    parent_path = server_parent_path(control)
    parent = parent_path.read_bytes()
    directory = store.root / "media-persistence"
    assert "synthetic-fixtures" in store.root.parts and "LTV-MEDIA-PERSISTENCE-20261031" in store.root.parts
    branch_results = []

    def files_digest():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.is_file()} if directory.exists() else {}

    def reset():
        # Independent synthetic branches only; never a product retry path.
        store.path("state.json").write_bytes(baseline)
        if directory.exists():
            shutil.rmtree(directory)

    def save_branch(name, calls):
        payload = store.read_only()
        received = base.records(payload, RECEIVE)
        result = {"scenario": name, "local_requests": len(calls), "native_sha256": hashlib.sha256(store.path("state.json").read_bytes()).hexdigest(),
                  "reception": received[0] if received else None, "files": files_digest(), "parent_unchanged": parent_path.read_bytes() == parent,
                  "all_original_records_except_new_A_artifact_unchanged": all(
                      digest({k: v for k, v in old.items() if key != base.OPERATION + ":" + op["id"] or k != "artifact_id"})
                      == digest({k: v for k, v in payload["media"][key].items() if key != base.OPERATION + ":" + op["id"] or k != "artifact_id"})
                      for key, old in before["media"].items())}
        assert result["parent_unchanged"] and result["all_original_records_except_new_A_artifact_unchanged"]
        branch_results.append(result)
        (tmp_path / "native-branches.json").write_text(json.dumps(branch_results, indent=2))

    production_launch = receiver.launch_receive

    # Pure attacks use the actual native disclosure and validator, resealing
    # both disclosure and record ID; no file/authority validator is replaced.
    approved = base.records(before, contract.QUOTE)[0]
    for field, value in (("automatic_receive", 1), ("download_cap", 2), ("new_POST_cap", 1), ("max_bytes", 3 * 1048576)):
        forged = copy.deepcopy(approved)
        forged["disclosure"]["policy"][field] = value
        forged["disclosure_hash"] = digest(forged["disclosure"])
        forged["id"] = digest({"kind": forged["kind"], "scope_id": forged["scope_id"], "disclosure": forged["disclosure"], "disclosure_hash": forged["disclosure_hash"]})[:32]
        with pytest.raises(CreatorError):
            contract.validate_record(before, forged)

    # An independent synthetic fork starts immediately before the query. This
    # exercises first completion -> real subprocess -> artifact without manually
    # invoking any persistence/registration entry or changing the product code.
    state = json.loads(baseline)
    born = next(f["revision"] for f in state["frames"] if base.QUERY + ":" + query["id"] in f["delta"].get("media", {}))
    state["frames"] = state["frames"][:born - 1]
    state["head_revision"] = born - 1
    state["head_hash"] = state["frames"][-1]["hash"]
    store.path("state.json").write_text(json.dumps(state, ensure_ascii=False, allow_nan=False))
    with monkeypatch.context() as local:
        with local_http(local) as (calls, _):
            events = []
            original_transaction = ExecutionStore.transaction
            @contextmanager
            def measured_transaction(self):
                with original_transaction(self) as payload:
                    old = {r["id"]: digest(r) for r in base.records(payload, RECEIVE)}
                    yield payload
                for record in base.records(payload, RECEIVE):
                    if digest(record) != old.get(record["id"]):
                        events.append({"phase": "native_" + record["status"] + ("_artifact_pointer" if record["artifact_id"] else ""), "at": now().isoformat()})
            original_publish = io.PrivateMediaFiles.publish
            def measured_publish(self):
                original_publish(self)
                events.append({"phase": "private_publish_fsync_complete", "at": now().isoformat()})
            local.setattr(ExecutionStore, "transaction", measured_transaction)
            local.setattr(io.PrivateMediaFiles, "publish", measured_publish)
            started = now()
            first = execution.query(op["id"], key="D47-first-completion-automatic")
            finished = now()
            persistence = first["media_persistence"]
            assert persistence["received"] is True and persistence["persisted"] is True and persistence["artifact"] is True
            assert len(calls) == 1 and persistence["quality"] == "not_reviewed" and persistence["human_acceptance"] == "pending"
            payload = store.read_only()
            frames = json.loads(store.path("state.json").read_text())["frames"]
            reception = persistence["reception"]
            commit = next(f["saved_at"] for f in frames if RECEIVE + ":" + reception["id"] in f["delta"].get("media", {}))
            measurements = {"query_entry_at": started.isoformat(), "query_completed_received_at": first["query"]["raw"]["received_at"],
                            "intent_created_at": reception["created_at"], "intent_frame_saved_at": commit,
                            "worker_started_at": reception["saved"]["network_started_at"], "byte_decode_complete_at": reception["saved"]["saved_at"], "private_proof_durable_at": reception["saved"]["durable_at"],
                            "first_resource_request_received_at": calls[0]["received_at"], "durable_events": events,
                            "artifact_created_at": payload["media"][base.ARTIFACT + ":" + persistence["artifact_id"]]["created_at"],
                            "chain_return_at": finished.isoformat(), "local_requests": len(calls), "actual_subprocess": True,
                            "authority_matches_runtime": matching_source, "real_account": False}
            (tmp_path / "normal-first-completion.json").write_text(json.dumps(measurements, indent=2))
    reset()

    with pytest.raises(CreatorError):
        quote_media_persistence(control, activation_id, valid_until=(now() + timedelta(hours=1)).isoformat())
    with pytest.raises(CreatorError):
        execution.record_artifact(op["id"], query["id"], image_bytes())
    assert store.path("state.json").read_bytes() == baseline

    # HTTP404 must preserve completed/fee, consume one reception and never retry.
    with monkeypatch.context() as local:
        with local_http(local, "404") as (calls, _):
            local.setattr(receiver, "launch_receive", production_launch)
            failed = execution.recover(query["id"], query=True)["media_persistence"]
            assert failed["received"] is False and failed["artifact"] is False and failed["failure_cause"] == "unknown"
            assert failed["reception"]["status"] == "failed" and failed["reception"]["http_status"] == 404
            repeated = ComparisonExecution(control, activation_id, receipt_capture=settings).recover(query["id"], query=True)["media_persistence"]
            assert repeated["received"] is False and len(calls) == 1
            assert store.read_only()["media"][base.QUERY + ":" + query["id"]]["status"] == "completed"
            save_branch("404_and_restart_no_retry", calls)

    reset()
    # Durable intent before HTTP: a process crash consumes the sole opportunity.
    with monkeypatch.context() as local:
        with local_http(local) as (calls, _):
            def crash_before_network(*a, **k):
                raise KeyboardInterrupt("SYNTHETIC INTENT COMMITTED BEFORE HTTP")
            local.setattr(receiver, "launch_receive", crash_before_network)
            with pytest.raises(KeyboardInterrupt):
                execution.recover(query["id"], query=True)
            local.setattr(receiver, "launch_receive", production_launch)
            resumed = execution.recover(query["id"], query=True)["media_persistence"]
            assert resumed["received"] is False and resumed["needs_input"] is True and len(calls) == 0
            assert resumed["reception"]["status"] == "unknown"
            save_branch("intent_committed_before_HTTP_no_resend_or_orphan_adoption", calls)

    for phase in ("proof_before_publish", "blob_before_native_saved"):
        reset()
        with monkeypatch.context() as local:
            with local_http(local) as (calls, _):
                local.setattr(receiver, "launch_receive", production_launch)
                with monkeypatch.context() as fault:
                    if phase == "proof_before_publish":
                        def interrupted_publish(self):
                            raise KeyboardInterrupt("SYNTHETIC PROOF BEFORE PUBLISH")
                        fault.setattr(io.PrivateMediaFiles, "publish", interrupted_publish)
                    else:
                        original = receiver.MediaPersistenceExecution._save_state
                        def interrupted_saved(self, identifier, **values):
                            if values.get("status") == "saved":
                                raise KeyboardInterrupt("SYNTHETIC BLOB BEFORE NATIVE SAVED")
                            return original(self, identifier, **values)
                        fault.setattr(receiver.MediaPersistenceExecution, "_save_state", interrupted_saved)
                    with pytest.raises(KeyboardInterrupt):
                        execution.recover(query["id"], query=True)
                native_bytes, file_hashes = store.path("state.json").read_bytes(), files_digest()
                assert len(calls) == 1
                if phase == "proof_before_publish":
                    confirmation_path = next(directory.glob("*.commit.json"))
                    confirmation_bytes = confirmation_path.read_bytes()
                    for attack in ("missing", "late"):
                        if attack == "missing":
                            confirmation_path.unlink()
                        else:
                            confirmation = json.loads(confirmation_bytes)
                            ready = json.loads(next(directory.glob("*.ready.json")).read_text())
                            confirmation["confirmed_at"] = (contract.timestamp(ready["saved"]["network_started_at"]) + timedelta(seconds=46)).isoformat()
                            confirmation["confirmation_hash"] = digest({k: v for k, v in confirmation.items() if k != "confirmation_hash"})
                            confirmation_path.write_text(json.dumps(confirmation))
                        refused = receiver.MediaPersistenceExecution(control, permission_id).receive_completed(op["id"], query["id"])
                        assert refused["received"] is False and refused["artifact"] is False and refused["needs_input"] is True and len(calls) == 1
                        # Restore this independent synthetic fault branch, not a
                        # product reset/retry. No further network is permitted.
                        store.path("state.json").write_bytes(native_bytes)
                        confirmation_path.write_bytes(confirmation_bytes)
                        confirmation_path.chmod(0o600)
                # Both expiry and current-code drift must be read-only even
                # when a complete part/proof or published blob is available.
                d = contract.authority(store.read_only(), permission_id)
                with monkeypatch.context() as expired:
                    expired.setattr(contract, "now", lambda: contract.timestamp(d["valid_until"]) + timedelta(seconds=1))
                    stopped = receiver.MediaPersistenceExecution(control, permission_id).receive_completed(op["id"], query["id"])
                    assert stopped["current_authority_valid"] is False and stopped["needs_input"] is True
                assert store.path("state.json").read_bytes() == native_bytes and files_digest() == file_hashes
                code = control.backend_root / next(iter(control.document.code_manifest))
                code_bytes = code.read_bytes()
                try:
                    code.write_bytes(code_bytes + b"\n# D47 SYNTHETIC DRIFT\n")
                    stopped = receiver.MediaPersistenceExecution(control, permission_id).receive_completed(op["id"], query["id"])
                    assert stopped["current_authority_valid"] is False and stopped["needs_input"] is True
                finally:
                    code.write_bytes(code_bytes)
                assert store.path("state.json").read_bytes() == native_bytes and files_digest() == file_hashes and len(calls) == 1
                # New instance, same completed query: local publication/registration.
                completed = ComparisonExecution(control, activation_id, receipt_capture=settings).recover(query["id"], query=True)["media_persistence"]
                assert completed["received"] is True and completed["persisted"] is True and completed["artifact"] is True and len(calls) == 1
                repeated = execution.query(op["id"], key="D47-completed-query")["media_persistence"]
                assert repeated["artifact"] is True and len(calls) == 1
                save_branch(phase + "_expiry_drift_and_local_restart", calls)

    reset()
    with monkeypatch.context() as local:
        with local_http(local) as (calls, _):
            local.setattr(receiver, "launch_receive", production_launch)
            with monkeypatch.context() as fault:
                def interrupted_artifact(*a, **k):
                    raise RuntimeError("SYNTHETIC ARTIFACT REGISTRATION UNAVAILABLE")
                fault.setattr(ComparisonExecution, "record_artifact", interrupted_artifact)
                saved = execution.recover(query["id"], query=True)["media_persistence"]
                assert saved["persisted"] is True and saved["artifact"] is False and saved["needs_input"] is True
            with monkeypatch.context() as fault:
                original_saved = receiver.MediaPersistenceExecution._save_state
                def artifact_pointer_unavailable(self, identifier, **values):
                    if values.get("artifact_id") is not None:
                        raise RuntimeError("SYNTHETIC ORIGINAL ARTIFACT COMMITTED BEFORE RECEIVE POINTER")
                    return original_saved(self, identifier, **values)
                fault.setattr(receiver.MediaPersistenceExecution, "_save_state", artifact_pointer_unavailable)
                registered = execution.recover(query["id"], query=True)["media_persistence"]
                assert registered["artifact"] is True and registered["reception_pointer_pending"] is True
                assert registered["needs_input"] is True and len(calls) == 1
            completed = execution.recover(query["id"], query=True)["media_persistence"]
            assert completed["artifact"] is True and len(calls) == 1
            native_bytes = store.path("state.json").read_bytes()
            blob = next(directory.glob("*.blob"))
            blob.unlink()
            file_hashes = files_digest()
            d = contract.authority(store.read_only(), permission_id)
            with monkeypatch.context() as expired:
                expired.setattr(contract, "now", lambda: contract.timestamp(d["valid_until"]) + timedelta(seconds=1))
                unavailable = receiver.MediaPersistenceExecution(control, permission_id).receive_completed(op["id"], query["id"])
                assert unavailable["received"] is None and unavailable["persisted"] is None
                assert unavailable["artifact"] is True and unavailable["historical_saved"] is True
                assert unavailable["needs_input"] is True and unavailable["file_availability"] == "not_verified"
            assert store.path("state.json").read_bytes() == native_bytes and files_digest() == file_hashes and len(calls) == 1
            save_branch("persisted_without_artifact_local_registration_and_missing_file_after_expiry", calls)
