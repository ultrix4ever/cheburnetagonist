"""Contract verification against optional pinned upstream checkout.

PANEL_SOURCE=/path/to/backend python -m pytest tests/test_panel_contract.py
Requires upstream dependencies in the test environment, never a live panel.
"""
import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = os.environ.get('PANEL_SOURCE')
if not SOURCE:
    pytest.skip('Pinned upstream source not configured', allow_module_level=True)
sys.path.insert(0, SOURCE)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import deps
from app.api import v1

spec = importlib.util.spec_from_file_location('max_awg3_v1', Path(__file__).parents[1] / 'panel-extension/max_awg3_v1.py')
extension = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extension)


@pytest.fixture
def api(monkeypatch):
    app = FastAPI()
    app.include_router(extension.router, prefix='/api')
    key = SimpleNamespace(name='max-test', allows=lambda sid: sid == 3)
    app.dependency_overrides[deps.get_api_client] = lambda: key
    app.dependency_overrides[deps.get_session] = lambda: object()
    async def get_server(sid, session):
        return SimpleNamespace(id=sid)
    monkeypatch.setattr(v1, '_get_or_404', get_server)
    calls = []
    async def mutate(sid, body, actor, session):
        calls.append((sid, body.public_key, actor.username))
    for name in ('pause_client', 'resume_client', 'revoke_client'):
        monkeypatch.setattr(extension.awg3, name, mutate)
    return TestClient(app), calls


@pytest.mark.parametrize('method,path', [('GET',''),('POST',''),('GET','/config'),('POST','/pause'),('POST','/resume'),('DELETE','')])
def test_scope_on_every_route(api, method, path):
    client, calls = api
    kwargs = {'params': {'public_key': 'a/b+c='}}
    if method == 'POST' and not path:
        kwargs['json'] = {'name': 'device'}
    response = client.request(method, '/api/v1/servers/4/awg3/clients' + path, **kwargs)
    assert response.status_code == 404
    assert calls == []


@pytest.mark.parametrize('method,path', [('POST','/pause'),('POST','/resume'),('DELETE','')])
def test_mutation_calls_real_signature(api, method, path):
    client, calls = api
    response = client.request(method, '/api/v1/servers/3/awg3/clients' + path, params={'public_key': 'a/b+c='})
    assert response.status_code == 204
    assert calls == [(3, 'a/b+c=', 'apikey:max-test')]


def test_list_models(api, monkeypatch):
    client, _ = api
    async def state(sid, actor, session):
        return SimpleNamespace(clients=[])
    monkeypatch.setattr(extension.awg3, 'get_awg3', state)
    assert client.get('/api/v1/servers/3/awg3/clients').json() == []


def test_only_client_routes_exposed(api):
    client, _ = api
    assert client.post('/api/v1/servers/3/awg3/deploy').status_code == 404
    assert client.get('/api/v1/servers/3/awg3/full-access').status_code == 404
