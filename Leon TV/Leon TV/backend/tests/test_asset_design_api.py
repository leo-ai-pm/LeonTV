"""Asset design projection is read-only, private, and does not create authority."""
import json

import pytest
from fastapi.staticfiles import StaticFiles
from test_asset_handoff import private_bytes
from test_planner_execution import env as env
from test_planner_execution import no_network as no_network


def test_asset_design_get_missing_execution_cannot_create_an_empty_namespace(env):
    current = env
    task = current.source_task()
    before = private_bytes(current.runtime)
    response = current.client.get(current.url(task) + '/asset-design')
    assert response.status_code == 404
    assert private_bytes(current.runtime) == before


def test_default_asset_design_projection_does_not_write_or_load_credentials(env, monkeypatch):
    current = env
    task = current.source_task()
    current.quote(task)
    before = private_bytes(current.runtime)

    def forbidden(*args, **kwargs):
        raise AssertionError('Read-only asset design must not recover or write')

    monkeypatch.setattr(current.runtime.store, 'transaction', forbidden)
    monkeypatch.setattr(current.runtime, 'get', forbidden)
    monkeypatch.setattr(current.runtime, 'recover', forbidden)
    for _ in range(2):
        response = current.client.get(current.url(task) + '/asset-design')
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        text = json.dumps(response.json())
        for value in ('SYNTHETIC-PRIVATE', 'PRIVATE-REQUIREMENTS', 'synthetic-secret', 'body_base64',
                      'bytes_base64', 'reasoning_content', 'source_export_hash', 'https://', str(current.runtime.store.root)):
            assert value not in text
    assert private_bytes(current.runtime) == before
    assert 'asset_design' not in current.runtime.store.read_only()


@pytest.mark.parametrize('request_kind', ['body', 'query', 'invalid_id'])
def test_asset_design_inspection_rejects_unknown_input_without_write(env, request_kind):
    current = env
    task = current.source_task()
    current.quote(task)
    before = private_bytes(current.runtime)
    url = current.url(task) + '/asset-design'
    if request_kind == 'body':
        response = current.client.request('GET', url, content=b'{"generate":true}')
    elif request_kind == 'query':
        response = current.client.get(url + '?generate=true')
    else:
        response = current.client.get(url.replace(task['id'], 'bad-id'))
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'INVALID_INPUT'
    assert private_bytes(current.runtime) == before


def test_asset_design_projection_checks_static_mount_overlap_before_reading(env):
    current = env
    task = current.source_task()
    current.quote(task)
    before = private_bytes(current.runtime)
    current.client.app.mount('/exposed', StaticFiles(directory=current.runtime.store.root))
    response = current.client.get(current.url(task) + '/asset-design')
    assert response.status_code == 503
    assert private_bytes(current.runtime) == before
