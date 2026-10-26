"""An expired reserved media permit is not a corrupt native journal."""
from datetime import timedelta

import pytest
from media_ledger_fixture import authorize, media_case, posts, reserve
from test_execution_empirical import empirical_env as empirical_env

from core.creator.store import CreatorError
from core.execution import media_runtime
from core.execution.receipts import timestamp


def test_expired_reserved_permit_stops_before_capture_without_changing_original_state(empirical_env, monkeypatch):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    path = case.media.store.path("state.json")
    before = path.read_bytes()
    text_calls = len(case.env.calls())
    after_deadline = timestamp(operation["descriptor"]["permit"]["deadline_at"]) + timedelta(seconds=1)
    monkeypatch.setattr(media_runtime, "utc_now", lambda: after_deadline.isoformat())

    def forbidden(*args, **kwargs):
        pytest.fail("Expired permit reached image capture")

    monkeypatch.setattr(case.media.capture, "capture", forbidden)
    with pytest.raises(CreatorError) as error:
        case.media.submit(case.task["id"], operation["id"])
    assert error.value.code == "MEDIA_PERMIT_EXPIRED"
    assert path.read_bytes() == before
    actual = case.media.store.read_only()["media"]["operation:" + operation["id"]]
    assert actual["status"] == "reserved" and actual["capture_started_at"] is None
    assert not posts(case) and len(case.env.calls()) == text_calls
