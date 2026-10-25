"""Independent code-continuation attacks, using synthetic native history only."""
import copy
import json

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from test_image_code_continuation import activate, prepared

from core.creator.store import CreatorError, digest
from core.execution import image_code_continuation as continuation
from core.execution.image_operator import build_image_session


def rewrite_checkpoint(state, *, field):
    """Rehash every affected object and every frame, preserving semantic payloads."""
    state = copy.deepcopy(state)
    quotes, activations = {}, {}
    previous = None
    for frame in state["frames"]:
        media = frame["delta"].get("media", {})
        rebuilt = {}
        for key, record in media.items():
            if record["kind"] == continuation.QUOTE:
                old_id = record["id"]
                record["disclosure"]["checkpoint"][field] = 99999 if field == "head_revision" else "0" * 64
                record["disclosure_hash"] = digest(record["disclosure"])
                record["id"] = digest(record["disclosure"])[:32]
                quotes[old_id] = (record["id"], record["disclosure_hash"])
                key = continuation.QUOTE + ":" + record["id"]
            elif record["kind"] == continuation.ACTIVATION:
                old_id = record["id"]
                record["quote_id"], record["disclosure_hash"] = quotes[record["quote_id"]]
                if record["previous_activation_id"] is not None:
                    record["previous_activation_id"] = activations[record["previous_activation_id"]]
                record["id"] = digest({name: record[name] for name in continuation.ACTIVATION_FIELDS})[:32]
                activations[old_id] = record["id"]
                key = continuation.ACTIVATION + ":" + record["id"]
            rebuilt[key] = record
        if media:
            frame["delta"]["media"] = rebuilt
        frame["previous_hash"] = previous
        frame["hash"] = digest({key: value for key, value in frame.items() if key != "hash"})
        previous = frame["hash"]
    state["head_hash"] = previous
    return state, activations


@pytest.mark.parametrize("field", ["head_revision", "head_hash"])
def test_rehashed_quote_checkpoint_coordinates_fail_complete_history_reconstruction(asset_control_case, field):
    case = prepared(asset_control_case)
    result, wrapped = activate(case)
    assert wrapped.verify_authority(for_dispatch=True)
    state = json.loads(case.state_path.read_text())
    mutated, mapped = rewrite_checkpoint(state, field=field)
    case.state_path.write_text(json.dumps(mutated))
    with pytest.raises(CreatorError) as error:
        case.env.runtime.store.read_only()
    assert error.value.code == "CORRUPT_STORAGE"
    changed_wrapper = continuation.ContinuedImageControl(case.control, mapped[result["activation"]["id"]])
    with pytest.raises(CreatorError):
        changed_wrapper.verify_authority(for_dispatch=True)


def test_preexisting_other_grant_cannot_borrow_the_exact_approved_grants_continuation(asset_control_case):
    case = prepared(asset_control_case)
    approved_bytes = case.changed_code.read_bytes()
    case.changed_code.unlink()  # Create the second grant under the original valid package.
    quote = case.env.runtime.store.read_only()["media"]["quote:" + case.grant["quote_id"]]
    other = case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
                             key="independent-existing-other-grant")["grant"]
    assert other["id"] != case.grant["id"]
    case.changed_code.write_bytes(approved_bytes)
    _, wrapped = activate(case)
    assert wrapped.verify_authority(for_dispatch=True)
    with build_image_session(wrapped, receipt_capture=case.settings) as session:
        before = case.state_path.read_bytes()
        with pytest.raises(CreatorError):
            session.media.reserve_submit(case.task["id"], other["id"], "S01", key="independent-wrong-grant-reserve")
        assert case.state_path.read_bytes() == before


def test_prior_reserved_intent_is_retained_in_continuation_used_limits(asset_control_case):
    case = prepared(asset_control_case)
    approved_bytes = case.changed_code.read_bytes()
    case.changed_code.unlink()
    reserved = case.media.reserve_submit(case.task["id"], case.grant["id"], "S01", key="independent-used-before-continuation")["operation"]
    case.changed_code.write_bytes(approved_bytes)
    before = case.env.runtime.store.read_only()
    original_descriptor = copy.deepcopy(reserved["descriptor"])
    q = continuation.quote_code_continuation(case.control, case.grant["id"])
    limits = q["quote"]["disclosure"]["remaining_limits"]
    assert limits["used_submissions"] == 1 and limits["remaining_submissions"] == case.policy["max_submissions"] - 1
    _, wrapped = activate(case, q)
    assert wrapped.verify_authority(for_dispatch=True)
    after = case.env.runtime.store.read_only()
    assert after["media"]["operation:" + reserved["id"]] == before["media"]["operation:" + reserved["id"]]
    assert after["media"]["operation:" + reserved["id"]]["descriptor"] == original_descriptor
    assert original_descriptor["permit"]["deadline_at"] == reserved["descriptor"]["permit"]["deadline_at"]
    assert all(after[name] == before[name] for name in ("accounts", "operations", "quotes", "grants"))
