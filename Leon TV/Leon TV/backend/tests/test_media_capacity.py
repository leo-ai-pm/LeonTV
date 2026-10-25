"""A full append journal must never replace a readable state with an unreadable one."""
import pytest
from test_planner_execution import env as env
from test_planner_execution import no_network as no_network

from core.creator.store import CreatorError
from core.execution import store


def test_capacity_failure_keeps_original_journal_readable_and_bytes_unchanged(env, monkeypatch):
    current = env
    task = current.source_task()
    first = current.quote(task)
    path = current.runtime.store.path('state.json')
    before = path.read_bytes()
    monkeypatch.setattr(store, 'MAX_STATE_BYTES', len(before) + 1)
    with pytest.raises(CreatorError) as caught:
        current.runtime.quote(task['id'], {'client_request_key': 'capacity-second-quote',
            'expected_task_revision': task['revision'],
            'expected_execution_revision': first['execution']['revision']})
    assert caught.value.code == 'EXECUTION_STORAGE_LIMIT'
    assert path.read_bytes() == before
    assert current.runtime.store.read_only()['quotes'][first['quote']['id']]['id'] == first['quote']['id']
    assert not list(current.runtime.store.root.glob('.execution-*'))
