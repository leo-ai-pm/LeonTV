"""Original store, budget, permissions and collector; synthetic supplier only."""

import copy
import json
from dataclasses import replace

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import posts
from test_image_retained_runtime import two_unknown_case

from core.creator.store import CreatorError, digest
from core.execution import image_operator, image_runtime_continuation, image_runtime_operator
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.media_ledger import records
from core.execution.policy import money
from core.execution.scope_budget import scope_envelope
from core.execution.store import ExecutionStore, validate_state


def original_five_case(asset_control_case, monkeypatch):
    # Historical setup reuses the original native fixture's missing-supplier seam.
    # D45 never substitutes any authority, ledger, validator or fee decision.
    case = two_unknown_case(asset_control_case, monkeypatch)
    quote = image_runtime_continuation.quote_runtime_continuation(
        case.control,
        valid_until=case.runtime_valid_until,
        response_window_seconds=180,
        retain_unknown_risk=True,
    )["quote"]
    activated = image_runtime_continuation.activate_runtime_continuation(
        case.control, quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True
    )["activation"]
    case.d43_control = RuntimeContinuedImageControl(case.control, activated["id"])
    settings = replace(case.settings, worker_factory="image_comparison_fixture:make_transport")
    with image_operator.build_image_session(
        case.d43_control,
        receipt_capture=settings,
        credential_loader=lambda: pytest.fail("real key"),
    ) as session:
        target = quote["disclosure"]["retained_unknown_plan"]["funded_targets"][0]
        reserved = session.media.reserve_submit(
            case.task["id"], case.grant["id"], target["shot_id"], key="comparison-historical-fifth"
        )["operation"]
        case.raw_unknown = session.media.submit(case.task["id"], reserved["id"])["operation"]
        assert (
            case.raw_unknown["status"] == "unknown"
            and case.raw_unknown["raw"]["http_status"] == 403
        )
    return case


def test_native_comparison_bounded_chain_and_recovery(
    asset_control_case, monkeypatch, capsys, tmp_path
):
    import os
    import shutil

    case = original_five_case(asset_control_case, monkeypatch)
    case_root = case.root.parent
    backup = tmp_path / "synthetic-five-baseline"
    assert (
        case.policy["validation_only"] is True
        and case.policy["origin"] == "https://synthetic.invalid"
    )
    assert case_root.parent == tmp_path
    shutil.copytree(case_root, backup)
    outcomes = os.environ.get(
        "D45_NATIVE_OUTCOMES", "accepted,unknown,html502,invalid200,before_claim"
    ).split(",")
    for index, outcome in enumerate(outcomes):
        assert outcome in {"accepted", "unknown", "html502", "invalid200", "before_claim"}
        if index:
            shutil.rmtree(case_root)
            shutil.copytree(backup, case_root)
        branch = tmp_path / ("scenario-" + outcome)
        branch.mkdir()
        assert_native_scenario(case, monkeypatch, capsys, outcome, branch)


def assert_native_scenario(case, monkeypatch, capsys, outcome, tmp_path):
    store = case.env.runtime.store
    before = store.read_only()
    old_frames = json.loads(store.path("state.json").read_text())["frames"]
    parent = case.server_parent_path.read_bytes()
    count = len(posts(case))
    opening = scope_envelope(before, case.anchor["account_scope_id"])
    assert len(records(before, "operation")) == 5
    assert sum(op["status"] == "unknown" for op in records(before, "operation")) == 3
    # This is the existing execution seam: the new metadata entry was absent.
    monkeypatch.setattr(image_runtime_operator, "BACKEND", case.backend)
    assert (
        image_runtime_operator.main(
            ["comparison-quote", "--control", str(case.path), "--max-comparison-posts", "1"]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["dispatch_available"] is False and summary["funded_new_posts"] == 0
    from core.execution import image_comparison as comparison

    plan = store.read_only()["media"][comparison.QUOTE + ":" + summary["resource_id"]]
    assert len(plan["proposal"]["retained_unknowns"]) == 3
    assert plan["proposal"]["budget"]["proposed_additional_paid_calls"] == 1
    assert plan["proposal"]["budget"]["retained_risk_cny"] == money(opening["risk_units"])
    assert plan["proposal"]["budget"]["proposed_retained_risk_cny"] == money(
        opening["risk_units"] + 1000000
    )
    assert plan["proposal"]["case"]["planned_submission_index"] == 6
    assert comparison.verify_current(case.control, plan["id"], quote_only=True)
    code_path = case.backend / next(iter(case.control.document.code_manifest))
    code_before = code_path.read_bytes()
    code_path.write_bytes(code_before + b"# SYNTHETIC CODE DRIFT\n")
    with pytest.raises(CreatorError):
        comparison.verify_current(case.control, plan["id"], quote_only=True)
    code_path.write_bytes(code_before)
    stable = store.path("state.json").read_bytes()
    assert comparison.quote_comparison(case.control, max_posts=1)["replayed"]
    assert ExecutionStore(store.root).read_only() == store.read_only()
    assert store.path("state.json").read_bytes() == stable
    with pytest.raises(CreatorError):
        comparison.require_dispatch(case.control, plan["id"])
    assert store.path("state.json").read_bytes() == stable
    # A sealed proposal never substitutes for existing runtime authority.
    with image_operator.build_image_session(
        case.d43_control,
        receipt_capture=case.settings,
        credential_loader=lambda: pytest.fail("real key"),
    ) as session:
        with pytest.raises(CreatorError):
            session.media.reserve_submit(
                case.task["id"],
                case.grant["id"],
                case.raw_unknown["shot_id"],
                key="comparison-unfunded-denied",
            )
    assert store.path("state.json").read_bytes() == stable
    # Changing even an uncommitted raw-present collector file invalidates review.
    collector = store.root / "media-receipts" / case.raw_unknown["id"]
    path = collector / ("f" * 64 + ".record.json")
    path.write_text('{"synthetic":"partial"}')
    path.chmod(0o600)
    with pytest.raises(CreatorError):
        comparison.verify_current(case.control, plan["id"], quote_only=True)
    path.unlink()
    assert comparison.verify_current(case.control, plan["id"], quote_only=True)
    # A completely resealed identity/budget/stop-policy forgery fails native replay.
    state = json.loads(stable)
    for attack in ("account", "funding", "case_count", "deliver", "unknown_stop"):
        changed = copy.deepcopy(state)
        record = next(iter(changed["frames"][-1]["delta"]["media"].values()))
        p = record["proposal"]
        if attack == "account":
            p["account_id"] = "synthetic-other-account"
        elif attack == "funding":
            p["budget"]["funded_new_posts"] = 2
        elif attack == "case_count":
            p["max_posts"] = 3
        elif attack == "deliver":
            p["formal_delivery_authorized"] = True
        else:
            p["new_unknown_stops_batch"] = False
        p["proposal_hash"] = digest({k: v for k, v in p.items() if k != "proposal_hash"})
        record["id"] = digest(
            {"kind": comparison.QUOTE, "scope_id": record["scope_id"], "proposal": p}
        )[:32]
        frame = changed["frames"][-1]
        frame["delta"]["media"] = {comparison.QUOTE + ":" + record["id"]: record}
        frame["hash"] = digest({k: v for k, v in frame.items() if k != "hash"})
        changed["head_hash"] = frame["hash"]
        with pytest.raises(CreatorError):
            validate_state(changed)
    after = store.read_only()
    assert all(after[n] == v for n, v in before.items() if n != "media")
    assert all(after["media"][k] == v for k, v in before["media"].items())
    assert json.loads(stable)["frames"][:-1] == old_frames
    assert scope_envelope(after, case.anchor["account_scope_id"]) == opening
    assert case.server_parent_path.read_bytes() == parent and len(posts(case)) == count

    from test_image_fees import export, observed, row_for
    from test_image_receipts import image_bytes

    from core.execution.image_comparison_runtime import ComparisonExecution
    from core.execution.image_policy import FrozenImageRequest
    from core.execution.media_ledger import projection

    proposal_hash = plan["proposal"]["proposal_hash"]
    for consent in (False, 1, "true", None):
        with pytest.raises(CreatorError):
            comparison.prepare_comparison(
                case.control, plan["id"], proposal_hash=proposal_hash, consent=consent
            )
    intent = comparison.prepare_comparison(
        case.control, plan["id"], proposal_hash=proposal_hash, consent=True
    )["intent"]
    held = scope_envelope(store.read_only(), case.anchor["account_scope_id"])
    assert held == {
        "risk_units": opening["risk_units"] + 1000000,
        "max_calls": opening["max_calls"] + 1,
    }
    assert case.server_parent_path.read_bytes() == parent
    # An interrupted parent writer retains the native intent; resume creates one allocation.
    from core.execution import image_continuation_parent_io as parent_io

    with monkeypatch.context() as patch:
        patch.setattr(
            parent_io,
            "_replace_parent",
            lambda *a: (_ for _ in ()).throw(OSError("SYNTHETIC INTERRUPT")),
        )
        with pytest.raises(OSError):
            comparison.activate_comparison(
                case.control, intent["id"], proposal_hash=proposal_hash, consent=True
            )
    assert scope_envelope(store.read_only(), case.anchor["account_scope_id"]) == held
    activation = comparison.activate_comparison(
        case.control, intent["id"], proposal_hash=proposal_hash, consent=True
    )["activation"]
    funded_parent = case.server_parent_path.read_bytes()
    frozen = store.path("state.json").read_bytes()
    assert comparison.activate_comparison(
        case.control, intent["id"], proposal_hash=proposal_hash, consent=True
    )["replayed"]
    assert comparison.prepare_comparison(
        case.control, plan["id"], proposal_hash=proposal_hash, consent=True
    )["replayed"]
    assert (
        store.path("state.json").read_bytes() == frozen
        and case.server_parent_path.read_bytes() == funded_parent
    )
    settings = replace(
        case.settings,
        worker_factory="image_tool_fixture:make_transport"
        if outcome in {"accepted", "before_claim"}
        else "image_comparison_fixture:make_transport",
        worker_options={**case.settings.worker_options, "comparison_fault": outcome},
    )
    execution = ComparisonExecution(
        case.control,
        activation["id"],
        receipt_capture=settings,
        credential_loader=lambda: "SYNTHETIC-COMPARISON-KEY",
    )
    op = execution.reserve(key="diagnostic-single-A")["operation"]
    assert op["submission_index"] == 6 and op["purpose"] == comparison.PURPOSE
    assert (
        FrozenImageRequest.from_frozen(op["descriptor"]).wire
        == before["media"]["operation:" + plan["proposal"]["case"]["source_operation_id"]][
            "descriptor"
        ]["wire"]
    )
    from test_image_comparison_recovery import crash_before

    if outcome == "before_claim":
        from core.execution.image_capture import ControlledImageCapture

        crash_before(ControlledImageCapture.capture, lambda: execution.submit(op["id"]))
        result = execution.recover(op["id"])["operation"]
    else:
        result = execution.submit(op["id"])["operation"]
    assert result["status"] == ("accepted" if outcome == "accepted" else "unknown")
    receipt_root = store.root / "media-receipts" / op["id"]
    import hashlib

    def receipt_fingerprint():
        return (
            {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in receipt_root.iterdir()}
            if receipt_root.exists()
            else {}
        )

    receipt_before = receipt_fingerprint()
    if outcome in {"html502", "invalid200"}:
        assert result["raw"] is result["receipt"] is None
        assert any(name.endswith(".record.json") for name in receipt_before)
    if outcome == "before_claim":
        assert not receipt_root.exists()

    restart = ComparisonExecution(
        case.control,
        activation["id"],
        receipt_capture=settings,
        credential_loader=lambda: pytest.fail("restart key read"),
    )
    stable = store.path("state.json").read_bytes()
    assert restart.submit(op["id"])["replayed"] and restart.recover(op["id"])["replayed"]
    assert restart.reserve(key="diagnostic-single-A")["replayed"]
    with pytest.raises(CreatorError):
        restart.reserve(key="second-A-cannot-reset")
    assert store.path("state.json").read_bytes() == stable
    assert receipt_fingerprint() == receipt_before
    if outcome == "accepted":
        descriptor = FrozenImageRequest.from_frozen(result["descriptor"])
        row = row_for(descriptor, result["raw"], request_id=result["receipt"]["submit_request_id"])
        bad = {**row, "token_name": "SYNTHETIC-WRONG-TOKEN"}
        with pytest.raises(CreatorError):
            execution.observe_fee(op["id"], export(bad), observed_at=observed(result["raw"]))
        with pytest.raises(CreatorError):
            execution.query(op["id"], key="unfunded-fee-GET")
        assert store.path("state.json").read_bytes() == stable
        from test_task_image_fees import task_row, task_source_for

        account_blob = export({**row, "request_id": "SYNTHETIC-ACCOUNT-WITNESS", "quota": 7})
        task_blob = export(
            task_row(descriptor, result["raw"], task_id=result["receipt"]["accepted_task_id"])
        )
        task_source = task_source_for(descriptor, account_blob, fee_source=case.source)
        execution.observe_task_fee(
            op["id"], task_blob, account_blob, task_source, observed_at=observed(result["raw"])
        )
        fee_stable = store.path("state.json").read_bytes()
        assert execution.observe_task_fee(
            op["id"], task_blob, account_blob, task_source, observed_at=observed(result["raw"])
        )["replayed"]
        assert store.path("state.json").read_bytes() == fee_stable
        before_get = len(posts(case))
        crash_before(
            ComparisonExecution._dispatch, lambda: execution.query(op["id"], key="diagnostic-A-GET")
        )
        pending = records(store.read_only(), comparison.QUERY)[0]
        assert pending["status"] == "reserved" and len(posts(case)) == before_get
        query = execution.query(op["id"], key="diagnostic-A-GET")["query"]
        assert query["status"] == "completed"
        execution.record_artifact(op["id"], query["id"], image_bytes())
        stable = store.path("state.json").read_bytes()
        assert execution.query(op["id"], key="diagnostic-A-GET")["replayed"]
        assert execution.record_artifact(op["id"], query["id"], image_bytes())["replayed"]
        assert store.path("state.json").read_bytes() == stable
        assert len(records(store.read_only(), comparison.ARTIFACT)) == 1
        from core.creator.store import utc_now

        artifact = records(store.read_only(), comparison.ARTIFACT)[0]
        with pytest.raises(CreatorError):
            with store.transaction() as current:
                identifier = "f" * 32
                current["media"]["delivery:" + identifier] = {
                    "schema_version": 1,
                    "kind": "delivery",
                    "id": identifier,
                    "scope_id": plan["scope_id"],
                    "task_id": case.task["id"],
                    "created_at": utc_now(),
                    "operation_id": op["id"],
                    "query_id": query["id"],
                    "delivery": artifact["artifact"],
                    "bytes_base64": artifact["bytes_base64"],
                }
        assert store.path("state.json").read_bytes() == stable

    else:
        with pytest.raises(CreatorError):
            execution.query(op["id"], key="unknown-stops")
        with pytest.raises(CreatorError):
            comparison.quote_comparison(case.control, valid_until="2100-01-01T00:00:00+00:00")
        assert len(records(store.read_only(), comparison.ARTIFACT)) == 0
    after = store.read_only()
    assert all(after[n] == v for n, v in before.items() if n != "media")
    assert all(after["media"][k] == v for k, v in before["media"].items())
    assert len(records(after, "operation")) == 5 and len(records(after, "delivery")) == 2
    assert sum(op["status"] == "unknown" for op in records(after, "operation")) == 3
    assert len(records(after, comparison.OPERATION)) == 1
    formal_after, formal_before = (
        projection(after, case.task["id"]),
        projection(before, case.task["id"]),
    )
    for field in (
        "operations",
        "paid_call_count",
        "query_count",
        "platform_observed_cny",
        "quality_status",
    ):
        assert formal_after[field] == formal_before[field]
    assert formal_after["continuation"]["accounts"][0]["retained_risk_cny"] == money(
        held["risk_units"]
    )
    assert scope_envelope(after, case.anchor["account_scope_id"]) == held
    assert case.server_parent_path.read_bytes() == funded_parent
    assert len(posts(case)) == count + (
        2 if outcome == "accepted" else 0 if outcome == "before_claim" else 1
    )

    if outcome == "accepted":
        assert_native_history_guards(store, tmp_path / "native-guards")


def assert_native_history_guards(original_store, root):
    """Real isolated journal transactions, with no permission or fee mocks."""
    import base64
    from datetime import timedelta

    from test_image_fees import export, observed, row_for
    from test_image_receipts import image_bytes

    from core.creator.store import utc_now
    from core.execution import image_comparison as c
    from core.execution.image_fees import candidate_for
    from core.execution.image_policy import FrozenImageRequest
    from core.execution.image_receipts import verify_delivery
    from core.execution.media_ledger import account_halted
    from core.execution.receipts import timestamp

    state = json.loads(original_store.path("state.json").read_text())
    root.mkdir(mode=0o700)

    def clone(label, frames):
        directory = root / label
        directory.mkdir(mode=0o700)
        copied = {
            **state,
            "frames": copy.deepcopy(frames),
            "head_revision": len(frames),
            "head_hash": frames[-1]["hash"],
        }
        path = directory / "state.json"
        path.write_text(json.dumps(copied))
        path.chmod(0o600)
        store = ExecutionStore(directory)
        store.read_only()
        return store

    for kind in (c.INTENT, c.ACTIVATION, c.FEE, c.ARTIFACT):
        birth = next(
            i
            for i, frame in enumerate(state["frames"])
            if any(r["kind"] == kind for r in frame["delta"].get("media", {}).values())
        )
        store = clone(kind, state["frames"][: birth + 1])
        payload = store.read_only()
        record = records(payload, kind)[0]
        key = kind + ":" + record["id"]
        changed = copy.deepcopy(record)
        if kind in (c.INTENT, c.ACTIVATION):
            changed["created_at"] = (
                timestamp(record["created_at"]) - timedelta(microseconds=1)
            ).isoformat()
        elif kind == c.FEE:
            op = payload["media"][c.OPERATION + ":" + record["operation_id"]]
            request = FrozenImageRequest.from_frozen(op["descriptor"])
            raw = op["raw"]
            if record["candidate"]["kind"] == "image_task_fee_candidate_v1":
                from core.execution.task_image_fees import candidate_for_task

                rows = json.loads(base64.b64decode(record["export_base64"]).decode())
                assert type(rows) is dict and rows["success"] is True
                rows["data"]["items"][0]["quota"] = 40000
                blob = json.dumps(rows).encode()
                changed["candidate"] = candidate_for_task(
                    request,
                    raw,
                    blob,
                    base64.b64decode(record["account_export_base64"]),
                    payload["media"]["scope:" + record["scope_id"]]["fee_source"],
                    record["task_fee_source"],
                    observed_at=record["candidate"]["observed_at"],
                )
                changed["export_base64"] = base64.b64encode(blob).decode()
            else:
                blob = export(
                    row_for(
                        request, raw, request_id=op["receipt"]["submit_request_id"], quota=40000
                    )
                )
                changed["candidate"] = candidate_for(
                    request,
                    raw,
                    blob,
                    payload["media"]["scope:" + record["scope_id"]]["fee_source"],
                    observed_at=record["candidate"]["observed_at"],
                )
                changed["export_base64"] = base64.b64encode(blob).decode()
            assert changed["id"] == digest(changed["candidate"]["platform_log_id"])[:32]
        else:
            query = payload["media"][c.QUERY + ":" + record["query_id"]]
            image = image_bytes(format_name="JPEG")
            changed["artifact"] = verify_delivery(
                FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"], image
            )
            changed["bytes_base64"] = base64.b64encode(image).decode()
        before = store.path("state.json").read_bytes()
        with pytest.raises(CreatorError):
            with store.transaction() as current:
                current["media"][key] = changed
        assert store.path("state.json").read_bytes() == before

    intent_birth = next(
        i
        for i, frame in enumerate(state["frames"])
        if any(r["kind"] == c.INTENT for r in frame["delta"].get("media", {}).values())
    )
    invalid_birth = copy.deepcopy(state)
    invalid_birth["frames"] = invalid_birth["frames"][: intent_birth + 1]
    intent_record = next(
        r for r in invalid_birth["frames"][-1]["delta"]["media"].values() if r["kind"] == c.INTENT
    )
    quote_record = records(original_store.read_only(), c.QUOTE)[0]
    intent_record["created_at"] = (
        timestamp(quote_record["created_at"]) - timedelta(microseconds=1)
    ).isoformat()
    frame = invalid_birth["frames"][-1]
    frame["hash"] = digest({k: v for k, v in frame.items() if k != "hash"})
    invalid_birth.update(head_revision=len(invalid_birth["frames"]), head_hash=frame["hash"])
    with pytest.raises(CreatorError):
        validate_state(invalid_birth)

    revoked = clone("revoked", state["frames"])
    payload = revoked.read_only()
    activation = records(payload, c.ACTIVATION)[0]
    proposal = records(payload, c.QUOTE)[0]["proposal"]
    with revoked.transaction() as current:
        current["media"]["grant:" + proposal["grant_id"]]["revoked_at"] = utc_now()
    assert revoked.read_only()["media"]["grant:" + proposal["grant_id"]]["revoked_at"] is not None
    with pytest.raises(CreatorError):
        c.candidate_gate(revoked.read_only(), activation["id"])

    fee_birth = next(
        i
        for i, frame in enumerate(state["frames"])
        if any(r["kind"] == c.FEE for r in frame["delta"].get("media", {}).values())
    )
    overrun = clone("overrun", state["frames"][:fee_birth])
    payload = overrun.read_only()
    op = records(payload, c.OPERATION)[0]
    scope = payload["media"]["scope:" + op["scope_id"]]
    request = FrozenImageRequest.from_frozen(op["descriptor"])
    blob = export(
        row_for(request, op["raw"], request_id=op["receipt"]["submit_request_id"], quota=600000)
    )
    candidate = candidate_for(
        request, op["raw"], blob, scope["fee_source"], observed_at=observed(op["raw"])
    )
    identifier = digest(candidate["platform_log_id"])[:32]
    with overrun.transaction() as current:
        current["media"][c.FEE + ":" + identifier] = {
            "schema_version": 1,
            "kind": c.FEE,
            "id": identifier,
            "scope_id": scope["id"],
            "task_id": scope["task_id"],
            "created_at": utc_now(),
            "operation_id": op["id"],
            "candidate": candidate,
            "export_base64": base64.b64encode(blob).decode(),
        }
        current["media"][c.OPERATION + ":" + op["id"]]["fee_id"] = identifier
    assert account_halted(overrun.read_only(), scope["account_id"])
    with pytest.raises(CreatorError):
        c.candidate_gate(overrun.read_only(), activation["id"])
