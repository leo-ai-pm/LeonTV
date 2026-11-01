"""One formal native retained-unknown persistence chain; synthetic only."""
import copy
import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import posts
from legacy_reserve_trace import ReserveTrace
from media_ledger_fixture import image_fee
from production_image_persistence_scenario import actual_source, fingerprint, server
from test_image_retained_runtime import two_unknown_case

from core.creator.store import CreatorError
from core.execution import image_runtime_continuation as runtime_contract
from core.execution import production_image_persistence_contract as persistence
from core.execution.image_operator import build_image_session
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.media_ledger import has_pending_fees, has_pending_unknown, records
from core.execution.policy import now
from core.execution.production_image_persistence_schema import OPERATION_FIELD, RECEIVE
from core.execution.scope_budget import scope_envelope


def test_retained_native_formal_persistence(asset_control_case, monkeypatch, tmp_path):
    assert os.environ.get("D50_ISOLATED_RUNNER") == "1"
    evidence = Path(os.environ["D50_EVIDENCE"])
    started = time.monotonic()

    def stage(name):
        (evidence / "live-stage.json").write_text(json.dumps({"stage": name, "elapsed_seconds": time.monotonic() - started}))

    stage("fresh synthetic native setup; two preserved unknowns")
    source = actual_source(monkeypatch)
    # Reuse original public fixture: real native role/asset/image journals and
    # real continuation/funding checks; historical missing-supplier seams only.
    case = two_unknown_case(asset_control_case, monkeypatch)
    assert case.root.is_relative_to(tmp_path)
    assert case.control.document.policy["validation_only"] is True
    matching = persistence._collect_code_manifest(case.control)
    assert all(hashlib.sha256((source / n).read_bytes()).hexdigest() == h for n, h in matching.items())
    (evidence / "matching-authority-source.json").write_text(json.dumps(matching, indent=2))
    store = case.env.runtime.store
    baseline = store.read_only()
    unknowns = [op for op in records(baseline, "operation") if op["status"] == "unknown"]
    assert len(unknowns) == 2 and len(records(baseline, "operation")) == 4
    assert len(records(baseline, "delivery")) == 2
    account = case.control.document.budget_anchor["account_scope_id"]
    envelope = scope_envelope(baseline, account)
    old_frames = json.loads(store.path("state.json").read_bytes())["frames"]
    parent_bytes = case.server_parent_path.read_bytes()
    old_unknowns = {op["id"]: copy.deepcopy(op) for op in unknowns}
    stage("existing retained-unknown quote and activation")
    quote = runtime_contract.quote_runtime_continuation(case.control, valid_until=case.runtime_valid_until,
                response_window_seconds=180, retain_unknown_risk=True)["quote"]
    activation = runtime_contract.activate_runtime_continuation(case.control, quote["id"],
                disclosure_hash=quote["disclosure_hash"], consent=True)["activation"]
    authority = runtime_contract.authority_for(store.read_only(), activation["id"])
    assert authority["schema_version"] == 4
    target = authority["retained_unknown_plan"]["funded_targets"][0]
    deferred = authority["retained_unknown_plan"]["deferred_targets"][0]
    assert target["planned_submission_index"] == 5 and target["replacement_of"] == case.new_unknown["id"]
    assert authority["additional_paid_calls"] == 0 and authority["additional_risk_cny"] == "0.000000"
    control = RuntimeContinuedImageControl(case.control, activation["id"])
    settings = replace(case.settings, worker_factory="media_persistence_fixture:make_transport", observation_wait_seconds=None)
    def fail_key():
        pytest.fail("A synthetic session requested a real credential")
    stable = store.path("state.json").read_bytes()
    with build_image_session(case.control, receipt_capture=settings, credential_loader=fail_key) as old_session:
        with pytest.raises(CreatorError):
            old_session.media.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"], key="old-permission-cannot-pass")
    assert store.path("state.json").read_bytes() == stable
    with build_image_session(control, receipt_capture=settings, credential_loader=fail_key) as session:
        media = session.media
        with pytest.raises(CreatorError):
            media.reserve_submit(case.task["id"], case.grant["id"], deferred["shot_id"], key="unfunded-fourth-denied")
        assert store.path("state.json").read_bytes() == stable
        stage("prospective formal persistence quote/activation and birth")
        pquote = persistence.quote_production_image_persistence(control, case.grant["id"], target["shot_id"],
                    valid_until=(now() + timedelta(minutes=45)).isoformat())["quote"]
        pactivation = persistence.activate_production_image_persistence(control, pquote["id"],
                    disclosure_hash=pquote["disclosure_hash"], consent=True)["activation"]
        http_before = len(posts(case))
        with ReserveTrace(evidence / "future-reserve-trace"):
            operation = media.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"],
                        key="retained-persistence-future", persistence_activation_id=pactivation["id"])["operation"]
        assert operation[OPERATION_FIELD] == pactivation["id"] and operation["submission_index"] == 5
        # Exact prior original delivery is the only predecessor reference.
        references = operation["descriptor"]["reference_bindings"]
        for binding in references:
            prior = baseline["media"]["operation:" + binding["source_operation_id"]]
            delivery = baseline["media"]["delivery:" + prior["delivery_id"]]
            assert prior["status"] == "accepted" and prior["id"] not in old_unknowns
            assert binding["source_delivery_id"] == delivery["id"]
        stage("one future submit and exact fee observation")
        submitted = media.submit(case.task["id"], operation["id"])["operation"]
        assert submitted["status"] == "accepted"
        before_fee = store.path("state.json").read_bytes()
        with pytest.raises(CreatorError):
            persistence.verified_current(media, pactivation["id"])
        assert store.path("state.json").read_bytes() == before_fee
        image_fee(type("FeeCase", (), {"media": media, "task": case.task})(), submitted)
        stage("completed query auto-receives through production worker")
        with server(monkeypatch) as (downloads, content):
            result = media.query(case.task["id"], operation["id"], key="retained-persistence-query")
            assert result["query"]["status"] == "completed"
            assert result["image_persistence"]["delivery_registered"] is True, result["image_persistence"]
            assert len(downloads) == 1
            query = result["query"]
            after = store.read_only()
            new_op = after["media"]["operation:" + operation["id"]]
            delivery = after["media"]["delivery:" + new_op["delivery_id"]]
            assert delivery["query_id"] == query["id"] and delivery["delivery"]["delivery_sha256"] == hashlib.sha256(content).hexdigest()
            assert len(records(after, RECEIVE)) == 1 and len(records(after, "delivery")) == 3
            stable_files = fingerprint(store.root)
            media.query(case.task["id"], operation["id"], key="retained-persistence-query")
            media.recover(case.task["id"], query["id"])
            assert media.submit(case.task["id"], operation["id"])["replayed"]
            assert len(downloads) == 1 and fingerprint(store.root) == stable_files
            assert len(posts(case)) == http_before + 2
            stage("actual readonly ASGI exports and retained-cap assertions")
            samples, get_seconds = {}, []
            for index in range(2):
                before = fingerprint(store.root)
                since = time.monotonic()
                response = case.env.client.get(f"/api/planner/tasks/{case.task['id']}/execution/media")
                get_seconds.append(time.monotonic() - since)
                assert response.status_code == 200, response.text
                assert response.headers["cache-control"] == "no-store"
                assert fingerprint(store.root) == before
                body = response.json()
                row = next(s for s in body["production_images"]["shots"] if s["operation_id"] == operation["id"])
                assert row["delivery_registered"] and row["historical_saved"] and row["file_availability"] == "available"
                assert row["received"] is row["persisted"] is True and not row["needs_input"]
                assert row["quality"] == "not_reviewed" and row["human_acceptance"] == "pending"
                text = json.dumps(body)
                assert all(secret not in text for secret in (str(store.root), "https://", "bytes_base64", "body_base64", "synthetic-secret"))
                samples["media_" + str(index)] = {"http_status": response.status_code, "body": body}
            for key, path in {"task": f"/api/planner/tasks/{case.task['id']}", "source": f"/api/planner/tasks/{case.task['id']}/source",
                              "events": f"/api/planner/tasks/{case.task['id']}/events",
                              "brief": f"/api/creator/briefs/{case.control.document.admission.brief_id}"}.items():
                response = case.env.client.get(path)
                assert response.status_code == 200
                samples[key] = {"http_status": response.status_code, "body": response.json()}
            (evidence / "asgi-fixtures.json").write_text(json.dumps({"task_id": case.task["id"],
                "brief_id": case.control.document.admission.brief_id, "samples": samples}, ensure_ascii=False, indent=2))
            assert fingerprint(store.root) == stable_files and len(downloads) == 1
        final = store.read_only()
        stable = store.path("state.json").read_bytes()
        with pytest.raises(CreatorError):
            media.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"], key="sixth-cannot-be-added")
        assert store.path("state.json").read_bytes() == stable
    assert all(final["media"]["operation:" + key] == value for key, value in old_unknowns.items())
    assert all(final[n] == value for n, value in baseline.items() if n != "media")
    assert all(final["media"][key] == value for key, value in baseline["media"].items())
    assert scope_envelope(final, account) == envelope and case.server_parent_path.read_bytes() == parent_bytes
    assert has_pending_unknown(final, account) and has_pending_fees(final, account)
    assert json.loads(store.path("state.json").read_bytes())["frames"][:len(old_frames)] == old_frames
    summary = {"synthetic_unknown_count": 2, "original_real_unknown_count_replayed": 0,
               "synthetic_operations_before": 4, "synthetic_operations_after": 5, "target_submit_mock_calls": 1,
               "target_query_mock_calls": 1, "local_receive_calls": 1, "all_model_http": len(posts(case)),
               "formal_deliveries_before": 2, "formal_deliveries_after": 3, "source_references": references,
               "retained_envelope": envelope, "old_unknowns_unchanged": True, "parent_bytes_unchanged": True,
               "old_records_and_frame_prefix_unchanged": True, "private_files_unchanged_after_replays_and_GET": True,
               "private_file_fingerprints": stable_files, "GET_seconds": get_seconds,
               "current_code_authority_matched": True, "authority_manifest_files": len(matching),
               "elapsed_seconds": time.monotonic() - started, "quality": "not_reviewed", "human_acceptance": "pending"}
    (evidence / "native-result.json").write_text(json.dumps(summary, indent=2))
    stage("passed")
