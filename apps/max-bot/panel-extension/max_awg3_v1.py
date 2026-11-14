"""Additive scoped API for pinned Amnezia Control v0.84.6.

Copy to backend/app/api/max_awg3_v1.py, explicitly register router in main.py.
No JWT, SSH credentials, deployment, or node-settings endpoints exposed.
Upstream: 7e0598b950b86f24dfdcff392bd5d8eccdb74f6e.
"""
from types import SimpleNamespace

from fastapi import APIRouter, Query

from app.api import awg3
from app.api.v1 import _server
from app.deps import ApiClient, SessionDep
from app.schemas import AwgClientOut, ConfigTextResponse, CreateClientRequest, CreateClientResponse, PublicKeyRequest, RevokeClientRequest

router = APIRouter(prefix='/v1/servers/{server_id}/awg3/clients', tags=['integration-v1-awg3'])


def actor(client):
    return SimpleNamespace(username=f'apikey:{client.name}')


@router.get('', response_model=list[AwgClientOut])
async def clients(server_id: int, client: ApiClient, session: SessionDep):
    await _server(client, server_id, session)
    state = await awg3.get_awg3(server_id, actor(client), session)
    return state.clients


@router.post('', response_model=CreateClientResponse, status_code=201)
async def issue(server_id: int, body: CreateClientRequest, client: ApiClient, session: SessionDep):
    await _server(client, server_id, session)
    # Upstream create_client owns the same node lock as the UI.
    return await awg3.create_client(server_id, body, actor(client), session)


@router.get('/config', response_model=ConfigTextResponse)
async def config(server_id: int, client: ApiClient, session: SessionDep, public_key: str = Query(..., min_length=1)):
    await _server(client, server_id, session)
    return await awg3.get_stored_config(server_id, PublicKeyRequest(public_key=public_key), actor(client), session)


@router.post('/pause', status_code=204)
async def pause(server_id: int, client: ApiClient, session: SessionDep, public_key: str = Query(..., min_length=1)) -> None:
    await _server(client, server_id, session)
    await awg3.pause_client(server_id, PublicKeyRequest(public_key=public_key), actor(client), session)


@router.post('/resume', status_code=204)
async def resume(server_id: int, client: ApiClient, session: SessionDep, public_key: str = Query(..., min_length=1)) -> None:
    await _server(client, server_id, session)
    await awg3.resume_client(server_id, PublicKeyRequest(public_key=public_key), actor(client), session)


@router.delete('', status_code=204)
async def revoke(server_id: int, client: ApiClient, session: SessionDep, public_key: str = Query(..., min_length=1)) -> None:
    await _server(client, server_id, session)
    await awg3.revoke_client(server_id, RevokeClientRequest(public_key=public_key), actor(client), session)
