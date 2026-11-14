"""Pinned panel adapter: a scoped API key must not become registry administration."""
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

SOURCE = os.environ.get('PANEL_SOURCE')
if not SOURCE:
    pytest.skip('Pinned source not configured', allow_module_level=True)
sys.path.insert(0, SOURCE)
from app import deps

spec = importlib.util.spec_from_file_location('max_users_adapter', Path(__file__).parents[1] / 'panel-extension/max_users.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

@pytest.fixture
def panel(monkeypatch):
    app = FastAPI(); app.include_router(adapter.router, prefix='/api')
    app.dependency_overrides[deps.get_session] = lambda: object()
    calls = []
    async def registry(method, path, body=None):
        calls.append((method, path, body)); return {'id': 1, 'job_id': 'job'}
    async def audit(*args): pass
    monkeypatch.setattr(adapter, 'registry', registry)
    monkeypatch.setattr(adapter.audit, 'record', audit)
    return app, TestClient(app), calls

@pytest.mark.parametrize('method,path,body', [
    ('GET', '', None), ('POST','/people',{'max_id':123,'name':'X','quota':2,'allowed_servers':[1],'password':'pw'}),
    ('PUT','/people/1',{'name':'X','quota':2,'allowed_servers':[1],'password':'pw'}),
    ('POST','/profiles/1/assign',{'person_id':1,'password':'pw'}),
    ('POST','/people/1/revoke-all',{'password':'pw'}), ('POST','/sync',{'password':'pw'}),
    ('POST','/people/1/grant',{'slots':1,'password':'pw'}), ('POST','/grants/1/cancel',{'password':'pw'}), ('POST','/payments/VPN-123456/confirm',{'password':'pw'}),
    ('POST','/profiles/1/rename',{'name':'X','password':'pw'}), ('POST','/people/1/delete',{'password':'pw'})])
def test_api_key_cannot_administer_registry(panel,method,path,body):
    app, client, calls = panel
    response = client.request(method, '/api/max-users'+path, json=body, headers={'X-API-Key':'scoped-key'})
    assert response.status_code == 401 and calls == []


def test_stepup_failure_prevents_grant(panel, monkeypatch):
    app, client, calls = panel
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(username='admin')
    async def reject(*args, **kwargs): raise HTTPException(403, 'bad password')
    monkeypatch.setattr(adapter.stepup,'verify',reject)
    r=client.post('/api/max-users/people',json={'max_id':123,'name':'X','quota':2,'allowed_servers':[1],'password':'bad'})
    assert r.status_code == 403 and calls == []


def test_verified_admin_password_not_forwarded_to_bot(panel, monkeypatch):
    app, client, calls = panel
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(username='admin')
    async def accept(*args, **kwargs): pass
    monkeypatch.setattr(adapter.stepup,'verify',accept)
    r=client.post('/api/max-users/people',json={'max_id':123,'name':'X','quota':2,'allowed_servers':[1],'password':'do-not-forward'})
    assert r.status_code == 201
    assert calls[0][0:2] == ('POST','people')
    assert 'password' not in calls[0][2]
    assert calls[0][2]['max_id'] == 123


def test_payment_confirmation_requires_stepup(panel, monkeypatch):
    app, client, calls = panel
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(username='admin')
    async def reject(*args, **kwargs): raise HTTPException(403, 'bad password')
    monkeypatch.setattr(adapter.stepup,'verify',reject)
    for path, body in [('/payments/VPN-123456/confirm', {'password':'bad'}), ('/people/1/grant', {'slots':1,'password':'bad'}), ('/grants/1/cancel', {'password':'bad'})]:
        assert client.post('/api/max-users'+path, json=body).status_code == 403
    assert calls == []


def test_key_rename_requires_stepup(panel, monkeypatch):
    app, client, calls = panel
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(username='admin')
    async def reject(*args, **kwargs): raise HTTPException(403, 'bad password')
    monkeypatch.setattr(adapter.stepup,'verify',reject)
    assert client.post('/api/max-users/profiles/1/rename', json={'name':'X','password':'bad'}).status_code == 403
    assert calls == []


def test_delete_requires_stepup_and_forwards_without_password(panel, monkeypatch):
    app, client, calls = panel
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(username='admin')
    async def reject(*args, **kwargs): raise HTTPException(403, 'bad password')
    monkeypatch.setattr(adapter.stepup,'verify',reject)
    assert client.post('/api/max-users/people/1/delete', json={'password':'bad'}).status_code == 403 and calls == []
    async def accept(*args, **kwargs): pass
    monkeypatch.setattr(adapter.stepup,'verify',accept)
    assert client.post('/api/max-users/people/1/delete', json={'password':'pw'}).status_code == 200
    assert calls == [('DELETE','people/1',None)]
