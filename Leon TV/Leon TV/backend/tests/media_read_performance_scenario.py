"""Build one synthetic read fixture; no supplier socket or download worker.

Setup uses existing text/asset MockTransports and synchronous synthetic image
raw responses at the capture boundary. A PNG proof writer replaces the download
worker ONLY during assembly. Native history/control/proof/receipt/registration
validators stay real. This does not test capture, downloading or model output.
"""
import cProfile
import hashlib
import json
import os
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from fastapi.testclient import TestClient
from image_operator_fixture import authorize, images_case, make_media, posts
from media_ledger_fixture import image_fee
from media_read_fixture import pre_d51_get, readonly_app
from production_image_persistence_scenario import actual_source, fingerprint
from test_image_receipts import image_bytes, raw_for

from core.creator.store import CreatorError, digest, utc_now
from core.execution import media_persistence, production_image_status
from core.execution import production_image_persistence_contract as contract
from core.execution import store as store_module
from core.execution.image_control import read_image_control
from core.execution.image_operator import build_image_session
from core.execution.image_policy import FrozenImageRequest, ImageToolPolicy
from core.execution.image_receipts import verify_delivery
from core.execution.media_persistence import intent_hash
from core.execution.policy import now
from core.execution.production_image_persistence_schema import RECEIVE


def offline_proof(query, record, output_fd, proof_fd, confirmation_fd, *, started_at):
    image = image_bytes()
    artifact = verify_delivery(FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"], image)
    os.write(output_fd, image)
    os.fsync(output_fd)
    saved = {"sha256": hashlib.sha256(image).hexdigest(), "byte_length": len(image), "artifact": artifact,
             "saved_at": utc_now(), "network_started_at": started_at}
    proof = {"intent_hash": intent_hash(record), "query_hash": record["query_hash"], "receipt_hash": record["receipt_hash"], "saved": saved}
    raw = json.dumps(proof, sort_keys=True).encode()
    os.write(proof_fd, raw)
    os.fsync(proof_fd)
    confirmation = {"intent_hash": intent_hash(record), "proof_sha256": hashlib.sha256(raw).hexdigest(), "confirmed_at": utc_now()}
    confirmation["confirmation_hash"] = digest(confirmation)
    os.write(confirmation_fd, json.dumps(confirmation).encode())
    os.fsync(confirmation_fd)
    return {"ok": True}


def test_build_and_read(asset_control_case, monkeypatch, tmp_path):
    assert os.environ.get("D51_ISOLATED_RUNNER") == "1"
    evidence = Path(os.environ["D51_EVIDENCE"])
    since = time.perf_counter()

    def stage(value):
        (evidence / "live-stage.json").write_text(json.dumps({"stage": value, "elapsed_seconds": time.perf_counter() - since}))

    stage("one synthetic setup; existing MockTransports, no real credentials")
    source = actual_source(monkeypatch)
    case = images_case(asset_control_case)
    # New synthetic context, before any image scope/quote/operation exists.
    # Bound the initial lease to four hours for the coordinator's HTTP handoff;
    # this never edits or renews an older fixture's authority or changes caps.
    values = {k: v for k, v in case.policy.items() if k != "policy_hash"}
    values["valid_until"] = (now() + timedelta(hours=4)).isoformat()
    case.policy = ImageToolPolicy.model_validate(values).frozen()
    case.data["policy"] = case.policy
    case.request["policy_hash"] = case.policy["policy_hash"]
    case.save()
    case.media = make_media(case)
    control = read_image_control(case.path, case.backend)
    assert control.document.policy["validation_only"] is True and case.root.is_relative_to(tmp_path)
    matching = contract._collect_code_manifest(control)
    assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == sha for name, sha in matching.items())
    (evidence / "matching-authority-source.json").write_text(json.dumps(matching, indent=2))
    _, grant = authorize(case)
    with build_image_session(control, receipt_capture=case.settings,
                             credential_loader=lambda: pytest.fail("Real credential loader")) as session:
        media, task_id = session.media, case.task["id"]
        synthetic_image_responses = []

        def synchronous_response(request, _unused_key):
            frozen = request.frozen()
            identifier = frozen["permit"]["operation_id"]
            synthetic_image_responses.append(request.method)
            if request.method == "POST":
                body = {"code": 0, "data": {"id": "synthetic-read-task-" + identifier}}
            else:
                body = {"code": 0, "data": {"id": frozen["accepted_task_id"], "status": "completed",
                        "result": {"images": [{"url": "https://synthetic.invalid/fixture.png"}]}}}
            raw = raw_for(request, body, headers={"x-request-id": "synthetic-read-" + identifier})
            raw["received_at"] = utc_now()
            return raw

        # This explicit fixture boundary never invokes a real capture adapter,
        # credential loader, detached image worker, HTTP or download.
        monkeypatch.setattr(media.capture, "capture", synchronous_response)
        stage("native managed/legacy fixture assembly; downloader replaced by local proof writer")
        q = contract.quote_production_image_persistence(control, grant["id"], "S01", valid_until=(now() + timedelta(hours=3, minutes=50)).isoformat())["quote"]
        activation = contract.activate_production_image_persistence(control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
        op = media.reserve_submit(task_id, grant["id"], "S01", key="read-managed-001", persistence_activation_id=activation["id"])["operation"]
        op = media.submit(task_id, op["id"])["operation"]
        assert op["status"] == "accepted", op["status"]
        image_fee(SimpleNamespace(media=media, task=case.task), op)
        with monkeypatch.context() as setup_only:
            setup_only.setattr(media_persistence, "launch_receive", offline_proof)
            completed = media.query(task_id, op["id"], key="read-managed-query-001")
        assert completed["image_persistence"]["delivery_registered"] is True, completed
        # Exact validated committed prefix with the second (and last) funded
        # position still unused. Only this new owned fixture is later branched.
        unknown_branch_bytes = media.store.path("state.json").read_bytes()
        legacy = media.reserve_submit(task_id, grant["id"], "S02", key="read-legacy-001")["operation"]
        legacy = media.submit(task_id, legacy["id"])["operation"]
        assert legacy["status"] == "accepted", legacy["status"]
        image_fee(SimpleNamespace(media=media, task=case.task), legacy)
        query = media.query(task_id, legacy["id"], key="read-legacy-query-001")["query"]
        media.record_delivery(task_id, legacy["id"], query["id"], image_bytes())
        store = media.store
        while True:
            count = len(json.loads(store.path("state.json").read_bytes())["frames"])
            if count >= 69:
                assert count == 69
                break
            media.quote(task_id, key="read-history-quote-" + str(count))
        state_bytes = store.path("state.json").read_bytes()
        payload = store.read_only()
        reception = next(r for r in payload["media"].values() if r["kind"] == RECEIVE)
        metadata = {"root": str(case.root.parent), "task_id": task_id, "brief_id": control.document.admission.brief_id,
                    "operation_id": op["id"], "grant_id": grant["id"], "reception_id": reception["id"],
                    "frames": 69, "fixture_native_sha256": hashlib.sha256(state_bytes).hexdigest(),
                    "authority_manifest_files": len(matching), "source_matches_final_runtime": True,
                    "initial_policy_valid_until": case.policy["valid_until"], "persistence_valid_until": q["disclosure"]["valid_until"],
                    "history_padding": "valid native media quote calls to 69 frames; not identical to D50 runtime-heavy history",
                    "setup_image_MockTransport_calls": len(posts(case)), "download_worker_calls": 0, "real_provider_calls": 0}
        metadata["setup_synchronous_image_responses"] = list(synthetic_image_responses)
        metadata["capture_boundary_tested"] = False
        (evidence / "fixture.json").write_text(json.dumps(metadata, indent=2))
        stage("readonly ASGI 3 baseline + 3 final; no provider or fixture writes")
        app = readonly_app(case.root.parent)
        runtime = app.state.read_runtime
        active_media = runtime.media
        samples, measurements = {}, {}
        counts, spans, observed_heads = {}, {}, []
        originals = {"full_native_validation": store_module.validate_state,
                     "file_proof": production_image_status._file,
                     "code_manifest": contract._collect_code_manifest}

        def measured(name):
            def call(*args, **kwargs):
                counts[name] = counts.get(name, 0) + 1
                if name == "full_native_validation":
                    observed_heads.append(args[0].get("head_revision"))
                started = time.perf_counter()
                try:
                    return originals[name](*args, **kwargs)
                finally:
                    spans.setdefault(name, []).append(time.perf_counter() - started)
            return call

        monkeypatch.setattr(store_module, "validate_state", measured("full_native_validation"))
        monkeypatch.setattr(production_image_status, "_file", measured("file_proof"))
        monkeypatch.setattr(contract, "_collect_code_manifest", measured("code_manifest"))
        url = f"/api/planner/tasks/{task_id}/execution/media"

        def request(client, name, expected=200):
            before = fingerprint(case.root.parent)
            counts.clear()
            spans.clear()
            observed_heads.clear()
            started = time.perf_counter()
            response = client.get(url)
            elapsed = time.perf_counter() - started
            body = response.json()
            samples[name] = {"http_status": response.status_code, "body": body}
            measurements[name] = {"seconds": elapsed, "counts": dict(counts), "native_heads": list(observed_heads),
                                  "spans_seconds": {k: list(v) for k, v in spans.items()}}
            (evidence / "asgi-partial.json").write_text(json.dumps({"task_id": task_id, "brief_id": metadata["brief_id"], "samples": samples}, indent=2))
            (evidence / "measurements-partial.json").write_text(json.dumps(measurements, indent=2))
            assert response.status_code == expected, response.text
            assert response.headers["cache-control"] == "no-store"
            assert fingerprint(case.root.parent) == before
            safe = json.dumps(body)
            assert all(secret not in safe for secret in (str(case.root.parent), "https://", "bytes_base64", "synthetic-secret"))
            return body

        def row(body, shot="S01"):
            return next(r for r in body["production_images"]["shots"] if r["shot_id"] == shot)

        with TestClient(app) as client:
            for mode in ("pre_d51_algorithm", "final"):
                runtime.media = SimpleNamespace(get=lambda task: pre_d51_get(runtime.store, task)) if mode != "final" else active_media
                for index in range(3):
                    body = request(client, mode + "_" + str(index))
                    assert row(body)["authority"] == "valid" and row(body)["file_availability"] == "available"
                    assert row(body)["delivery_registered"] and row(body)["historical_saved"] and not row(body)["needs_input"]
                    assert row(body, "S02")["delivery_origin"] == "legacy" and row(body, "S02")["received"] is None
                    assert measurements[mode + "_" + str(index)]["counts"]["full_native_validation"] == (3 if mode != "final" else 1)
                    assert body == samples["pre_d51_algorithm_0"]["body"]
            for mode in ("pre_d51_algorithm", "final"):
                runtime.media = SimpleNamespace(get=lambda task: pre_d51_get(runtime.store, task)) if mode != "final" else active_media
                profile = cProfile.Profile()
                profile.enable()
                request(client, mode + "_profile")
                profile.disable()
                profile.dump_stats(str(evidence / (mode + ".pstats")))
            runtime.media = active_media
            stage("finite freshness branches on this owned synthetic fixture")
            blob = store.root / "media-persistence" / (reception["id"] + ".blob")
            saved_bytes = blob.read_bytes()
            blob.unlink()
            missing = request(client, "managed_file_missing")
            assert row(missing)["historical_saved"] and row(missing)["delivery_registered"] and row(missing)["file_availability"] == "missing"
            blob.write_bytes(saved_bytes)
            blob.chmod(0o600)
            blob.write_bytes(saved_bytes[:-1] + bytes([saved_bytes[-1] ^ 1]))
            assert row(request(client, "managed_bytes_corrupt"))["file_availability"] == "invalid"
            blob.write_bytes(saved_bytes)
            proof = blob.with_suffix(".ready.json")
            proof_bytes = proof.read_bytes()
            proof.write_bytes(b"{}")
            assert row(request(client, "managed_proof_corrupt"))["file_availability"] == "invalid"
            proof.write_bytes(proof_bytes)
            code = case.backend / "core/execution/media_runtime.py"
            code_bytes = code.read_bytes()
            code.write_bytes(code_bytes + b"\n# synthetic drift\n")
            assert row(request(client, "source_changed"))["authority"] == "unavailable"
            code.write_bytes(code_bytes)
            native = json.loads(state_bytes)
            native["frames"][-1]["hash"] = "0" * 64
            store.path("state.json").write_text(json.dumps(native))
            broken = request(client, "native_corrupt", 503)
            assert broken["detail"]["code"] == "CORRUPT_STORAGE"
            store.path("state.json").write_bytes(state_bytes)
            media.quote(task_id, key="read-fresh-native-quote")
            changed = request(client, "native_valid_change")
            assert changed == samples["final_0"]["body"]
            assert measurements["native_valid_change"]["native_heads"] == [70]
            assert row(changed)["authority"] == "valid"
            store.path("state.json").write_bytes(state_bytes)
            media.revoke(task_id, grant["id"])
            assert row(request(client, "grant_revoked"))["authority"] == "revoked"
            store.path("state.json").write_bytes(state_bytes)
            store.path("state.json").write_bytes(unknown_branch_bytes)
            unknown = media.reserve_submit(task_id, grant["id"], "S02", key="read-unknown-001")["operation"]

            def missing_supplier(*args, **kwargs):
                raise CreatorError("MEDIA_RESPONSE_UNCONFIRMED", "Synthetic absent response", 409)

            with monkeypatch.context() as setup_only:
                setup_only.setattr(media.capture, "capture", missing_supplier)
                unknown = media.submit(task_id, unknown["id"])["operation"]
            assert unknown["status"] == "unknown"
            unknown_body = request(client, "unknown_retained")
            assert row(unknown_body, "S02")["generation"] == "unknown" and row(unknown_body, "S02")["needs_input"]
            assert row(unknown_body)["authority"] == "unavailable"
            store.path("state.json").write_bytes(state_bytes)
            assert request(client, "restored_fresh") == samples["final_0"]["body"]
            for key, path in {"task": f"/api/planner/tasks/{task_id}", "source": f"/api/planner/tasks/{task_id}/source",
                              "brief": f"/api/creator/briefs/{metadata['brief_id']}"}.items():
                before = fingerprint(case.root.parent)
                response = client.get(path)
                assert response.status_code == 200, response.text
                samples[key] = {"http_status": 200, "body": response.json()}
                assert fingerprint(case.root.parent) == before
        app.state.close_fixture()
        assert store.path("state.json").read_bytes() == state_bytes
        assert len(posts(case)) == metadata["setup_image_MockTransport_calls"]
        assert synthetic_image_responses == ["POST", "GET", "POST", "GET"]
        (evidence / "asgi-fixtures.json").write_text(json.dumps({"task_id": task_id, "brief_id": metadata["brief_id"], "samples": samples}, indent=2))
        (evidence / "measurements.json").write_text(json.dumps(measurements, indent=2))
        stage("passed; fixture reusable with readonly_app")
