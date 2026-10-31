"""One bounded native compound, executed by the public offline runner."""
import copy
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import images_case
from media_ledger_fixture import image_fee
from test_image_receipts import image_bytes

from core.creator.store import CreatorError, digest
from core.execution import production_image_persistence_contract as contract
from core.execution.control import CODE_CLOSURE_DIRS, REQUIRED_CODE_FILES
from core.execution.image_control import read_image_control
from core.execution.image_operator import build_image_session
from core.execution.policy import now
from core.execution.production_image_persistence import ProductionImagePersistenceExecution
from core.execution.production_image_persistence_schema import OPERATION_FIELD, RECEIVE
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.store import validate_state


@contextmanager
def server(monkeypatch, code=200):
    calls, content = [], image_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append({"path": self.path, "headers": dict(self.headers)})
            self.send_response(code)
            self.send_header("Content-Length", str(len(content) if code == 200 else 0))
            self.end_headers()
            if code == 200:
                self.wfile.write(content)

        def log_message(self, *args):
            pass

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    with monkeypatch.context() as changes:
        changes.setenv("D48_LOOPBACK_PORT", str(instance.server_port))
        try:
            yield calls, content
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join()


def actual_source(monkeypatch):
    import asset_operator_fixture
    import image_operator_fixture

    root = Path(contract.__file__).resolve().parents[2]
    names = set(REQUIRED_CODE_FILES) | asset_operator_fixture.EXTRA_CODE | image_operator_fixture.EXTRA_CODE
    for directory in CODE_CLOSURE_DIRS:
        names.update(p.relative_to(root).as_posix() for p in (root / directory).rglob("*.py"))
    monkeypatch.setattr(asset_operator_fixture, "REQUIRED_CODE_FILES", tuple(sorted(names)))
    monkeypatch.setattr(image_operator_fixture, "REQUIRED_CODE_FILES", tuple(sorted(names)))
    original = Path.write_text

    def freeze(path, value, *args, **kwargs):
        if value in {"# Independent frozen synthetic public module.\n", "# Independent frozen image operator module.\n"} and "public-backend" in path.parts:
            relative = Path(*path.parts[path.parts.index("public-backend") + 1:])
            raw = (root / relative).read_bytes()
            path.write_bytes(raw)
            return len(raw)
        return original(path, value, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", freeze)
    return root


def fingerprint(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_formal_native_compound(asset_control_case, monkeypatch, tmp_path):
    assert os.environ.get("D48_ISOLATED_RUNNER") == "1"
    evidence = Path(os.environ["D48_EVIDENCE"])
    runtime_root = actual_source(monkeypatch)
    case = images_case(asset_control_case)
    control = read_image_control(case.path, case.backend)
    assert control.document.policy["validation_only"] is True and control.document.policy["origin"] == "https://synthetic.invalid"
    assert case.path.is_relative_to(tmp_path)
    matching = contract._collect_code_manifest(control)
    assert all(hashlib.sha256((runtime_root / name).read_bytes()).hexdigest() == sha for name, sha in matching.items())
    (evidence / "matching-authority-source.json").write_text(json.dumps({"manifest": matching, "all_hashes_match": True}, indent=2))
    parent_before = {p: p.read_bytes() for p in (case.root / "parent-ledger.json", case.asset_case.root / "parent-ledger.json")}
    settings = ReceiptCaptureSettings(worker_factory="media_persistence_fixture:make_transport",
                                      worker_options={"counter": str(case.counter), "unique_tasks": True})
    journal = control.roots["execution"] / "state.json"
    samples, timings, scenarios = {}, {}, []

    def response(name):
        before = fingerprint(control.roots["execution"])
        started = time.monotonic()
        r = case.env.client.get(f"/api/planner/tasks/{case.task['id']}/execution/media")
        timings[name] = time.monotonic() - started
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        assert fingerprint(control.roots["execution"]) == before
        safe = r.json()
        text = json.dumps(safe)
        assert all(s not in text for s in (str(control.roots["execution"]), "https://", "bytes_base64", "body_base64", "synthetic-secret"))
        samples[name] = {"http_status": r.status_code, "body": safe}
        return safe["production_images"]["shots"][0]

    with build_image_session(control, receipt_capture=settings, credential_loader=lambda: pytest.fail("real Key loader")) as session:
        media, task_id = session.media, case.task["id"]
        q = media.quote(task_id, key="formal-quote-001")["quote"]
        grant = media.grant(task_id, q["id"], disclosure_hash=q["disclosure_hash"], consent=True, key="formal-grant-001")["grant"]
        old = session.runtime.store.read_only()
        old_records = copy.deepcopy(old)
        pquote = contract.quote_production_image_persistence(control, grant["id"], "S01", valid_until=(now() + timedelta(minutes=40)).isoformat())["quote"]
        with pytest.raises(CreatorError):
            contract.activate_production_image_persistence(control, pquote["id"], disclosure_hash=pquote["disclosure_hash"], consent=1)
        activation = contract.activate_production_image_persistence(control, pquote["id"], disclosure_hash=pquote["disclosure_hash"], consent=True)["activation"]
        born = media.reserve_submit(task_id, grant["id"], "S01", key="formal-reserve-001", persistence_activation_id=activation["id"])["operation"]
        assert born[OPERATION_FIELD] == activation["id"]
        op = media.submit(task_id, born["id"])["operation"]
        assert op["status"] == "accepted"
        image_fee(type("FeeCase", (), {"media": media, "task": case.task})(), op)
        with server(monkeypatch) as (calls, content):
            result = media.query(task_id, op["id"], key="formal-query-001")
            assert result["query"]["status"] == "completed"
            assert result["image_persistence"]["delivery_registered"] is True, result["image_persistence"]
            query = result["query"]
            assert len(calls) == 1
            assert "authorization" not in {key.lower() for key in calls[0]["headers"]}
            complete_state = json.loads(journal.read_bytes())
            payload = session.runtime.store.read_only()
            assert all(digest(payload[space][key]) == digest(value) for space in old_records for key, value in old_records[space].items())
            delivery_id = payload["media"]["operation:" + op["id"]]["delivery_id"]
            assert payload["media"]["delivery:" + delivery_id]["delivery"]["delivery_sha256"] == hashlib.sha256(content).hexdigest()
            normal = response("managed_success")
            assert normal["reception"] == "saved" and normal["file_availability"] == "available" and not normal["needs_input"]
            assert normal["delivery_registered"] and normal["historical_saved"]
            media.query(task_id, op["id"], key="formal-query-001")
            media.recover(task_id, query["id"])
            assert len(calls) == 1
            receiver = ProductionImagePersistenceExecution(media, activation["id"])
            saved_record = next(r for r in payload["media"].values() if r["kind"] == RECEIVE)
            folder = control.roots["execution"] / "media-persistence"
            file_state = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
            receive_birth = next(f["revision"] for f in complete_state["frames"] if RECEIVE + ":" + saved_record["id"] in f["delta"].get("media", {}))

            def restore(revision):
                # Only this freshly generated, clearly synthetic temporary
                # account is branched. No original/private fixture is loaded.
                assert case.path.is_relative_to(tmp_path) and control.document.policy["validation_only"] is True
                state = copy.deepcopy(complete_state)
                state["frames"] = state["frames"][:revision]
                state["head_revision"] = revision
                state["head_hash"] = state["frames"][-1]["hash"]
                validate_state(state)
                journal.write_text(json.dumps(state))
                journal.chmod(0o600)
                for p in folder.iterdir():
                    if p.is_file():
                        p.unlink()
                for name, raw in file_state.items():
                    p = folder / name
                    p.write_bytes(raw)
                    p.chmod(0o600)

            restore(receive_birth)
            partial = response("published_before_native_saved")
            assert partial["received"] is partial["persisted"] is True
            assert partial["reception"] == "receiving" and not partial["historical_saved"] and not partial["delivery_registered"]
            assert partial["error_code"] == "DELIVERY_PENDING"
            media.recover(task_id, query["id"])
            assert len(calls) == 1
            assert response("recovered_published")["delivery_registered"] is True
            scenarios += ["first completed auto delivery", "same key and recover no resend", "published/native-saved gap and local recovery"]

            restore(receive_birth)
            blob = folder / (saved_record["id"] + ".blob")
            blob.rename(blob.with_suffix(".part"))
            partial = response("part_not_published")
            assert partial["received"] is True and partial["persisted"] is False and partial["file_availability"] == "not_published"
            media.recover(task_id, query["id"])
            assert len(calls) == 1 and response("recovered_part")["file_availability"] == "available"
            scenarios += ["part proof readonly and local-only publish recovery"]

            restore(len(complete_state["frames"]))
            blob.write_bytes(b"corrupt")
            damaged = response("corrupt_file")
            assert damaged["file_availability"] == "invalid" and damaged["delivery_registered"] and damaged["historical_saved"]
            media.recover(task_id, query["id"])
            assert len(calls) == 1
            blob.unlink()
            missing = response("missing_file")
            assert missing["file_availability"] == "missing" and missing["received"] is False
            restore(len(complete_state["frames"]))
            from core.execution import production_image_status
            with monkeypatch.context() as fault:
                fault.setattr(production_image_status, "PrivateMediaFiles", lambda *a, **k: (_ for _ in ()).throw(BlockingIOError()))
                busy = response("storage_busy")
                assert busy["file_availability"] == "not_verified" and busy["received"] is None
            with monkeypatch.context() as clock:
                future = now() + timedelta(hours=2)
                clock.setattr(production_image_status, "now", lambda: future)
                expired = response("expired_available")
                assert expired["authority"] == "expired" and expired["file_availability"] == "available" and not expired["needs_input"]
            scenarios += ["corrupt/missing preserve history and never resend", "busy storage stays unverified", "expired readonly file availability"]

            changed = case.backend / "core/execution/media_runtime.py"
            raw = changed.read_bytes()
            changed.write_bytes(raw + b"\n# isolated source drift\n")
            try:
                with pytest.raises(CreatorError):
                    receiver._current()
                assert response("source_drift")["authority"] == "unavailable"
            finally:
                changed.write_bytes(raw)
            assert len(calls) == 1
        # Replay a completed pre-receive prefix with one failing HTTP resource.
        restore(receive_birth - 1)
        for p in folder.iterdir():
            if p.name != ".capacity.lock":
                p.unlink()
        with server(monkeypatch, 404) as (failed_calls, _):
            failed = media.query(task_id, op["id"], key="formal-query-001")
            assert failed["query"]["status"] == "completed" and len(failed_calls) == 1
            row = response("resource_404")
            assert row["completion"] == "completed" and row["reception"] == "failed" and row["http_status"] == 404
            assert row["error_code"] == "HTTP_NOT_200" and not row["delivery_registered"]
            media.recover(task_id, query["id"])
            assert len(failed_calls) == 1
        restore(len(complete_state["frames"]))
        scenarios += ["source drift blocks execution but GET stays readonly", "completed replay404 and no retry"]

        # Actual read-only ASGI fixtures share exact task/source/brief binding.
        for key, path in {"task": f"/api/planner/tasks/{task_id}", "source": f"/api/planner/tasks/{task_id}/source",
                          "events": f"/api/planner/tasks/{task_id}/events",
                          "brief": f"/api/creator/briefs/{control.document.admission.brief_id}"}.items():
            r = case.env.client.get(path)
            assert r.status_code == 200, (key, r.text)
            samples[key] = {"http_status": r.status_code, "body": r.json()}
        assert all(p.read_bytes() == raw for p, raw in parent_before.items())
        assert session.runtime.store.read_only()["accounts"] == old["accounts"]
        assert matching == contract._collect_code_manifest(control)
    (evidence / "asgi-fixtures.json").write_text(json.dumps({"task_id": task_id, "brief_id": control.document.admission.brief_id,
                                                               "samples": samples}, ensure_ascii=False, indent=2))
    (evidence / "native-result.json").write_text(json.dumps({"scenarios": scenarios, "get_seconds": timings,
                                                            "normal_downloads": 1, "failure_branch_downloads": 1,
                                                            "parent_bytes_unchanged": True, "account_unchanged": True}, indent=2))
