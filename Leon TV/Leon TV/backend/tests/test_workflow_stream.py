import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.services.project_helpers import make_progress_channel, stream_workflow_task


@pytest.mark.asyncio
async def test_stream_reports_fast_completion_without_waiting_for_heartbeat():
    engine = SimpleNamespace(execute_stage=AsyncMock(return_value={"stage_completed": True}),
                             persist_session_snapshot=Mock(return_value={"script_generation": "completed"}),
                             track_background_task=Mock())
    queue, trigger, callback = make_progress_channel()
    stream = stream_workflow_task(request=SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
        workflow_engine=engine, state=SimpleNamespace(session_id="mock-session"), stage="script_generation",
        input_data={}, cancellation_check=lambda: False, progress_callback=callback,
        progress_events=queue, event_trigger=trigger)
    try:
        result = await asyncio.wait_for(anext(stream), timeout=0.1)
        assert json.loads(result)["type"] == "stage_complete"
    finally:
        await stream.aclose()
