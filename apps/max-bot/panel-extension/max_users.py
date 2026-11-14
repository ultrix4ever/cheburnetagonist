"""JWT + step-up protected panel adapter to the single MAX ownership registry."""
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import audit, stepup
from app.deps import CurrentUser, SessionDep

router = APIRouter(prefix='/max-users', tags=['max-users'])


class Edit(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    quota: int = Field(ge=0, le=100)
    allowed_servers: list[int] = Field(max_length=100)
    phone: str = Field(default='', max_length=40)
    active: bool = True
    free: bool | None = None
    password: str = Field(min_length=1, max_length=128)


class Create(Edit):
    max_id: int = Field(gt=0, le=9223372036854775807)


class Confirm(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class Assign(Confirm):
    person_id: int = Field(gt=0)


class KeyName(Confirm):
    name: str = Field(min_length=1, max_length=40)


class Grant(Confirm):
    slots: int = Field(ge=1, le=20)


async def registry(method, path, body=None):
    secret = os.environ.get('REGISTRY_ADMIN_SECRET', '')
    if len(secret) < 32:
        raise HTTPException(503, 'Реестр MAX не настроен')
    try:
        async with httpx.AsyncClient(base_url='http://vpn-max:8088/internal/admin/',
                                     headers={'X-Registry-Admin': secret}, timeout=100,
                                     follow_redirects=False, trust_env=False) as client:
            response = await client.request(method, path, json=body)
    except httpx.HTTPError:
        raise HTTPException(503, 'Реестр MAX временно недоступен. Не повторяйте изменение до проверки состояния.') from None
    if not response.is_success:
        detail = 'Ошибка реестра MAX'
        try:
            candidate = response.json().get('detail')
            if isinstance(candidate, str):
                detail = candidate[:800]
        except (ValueError, AttributeError):
            pass
        raise HTTPException(response.status_code if response.status_code in (404, 409, 422) else 502, detail)
    return response.json()


@router.get('')
async def state(user: CurrentUser):
    return await registry('GET', 'state')


@router.post('/people', status_code=201)
async def create(body: Create, request: Request, user: CurrentUser, session: SessionDep):
    await stepup.verify(user, body.password, request)
    result = await registry('POST', 'people', body.model_dump(exclude={'password'}))
    await audit.record(session, user.username, 'max_user_create', str(result['id']))
    return result


@router.put('/people/{pid}')
async def update(pid: int, body: Edit, request: Request, user: CurrentUser, session: SessionDep):
    await stepup.verify(user, body.password, request)
    result = await registry('PUT', f'people/{pid}', body.model_dump(exclude={'password'}))
    await audit.record(session, user.username, 'max_user_update', str(pid))
    return result


@router.post('/profiles/{profile_id}/assign')
async def assign(profile_id: int, body: Assign, request: Request, user: CurrentUser, session: SessionDep):
    await stepup.verify(user, body.password, request)
    result = await registry('POST', f'profiles/{profile_id}/assign', {'person_id': body.person_id})
    await audit.record(session, user.username, 'max_profile_assign', str(profile_id), f'person={body.person_id}')
    return result


@router.post('/people/{pid}/revoke-all', status_code=202)
async def revoke_all(pid: int, body: Confirm, request: Request, user: CurrentUser, session: SessionDep):
    await stepup.verify(user, body.password, request)
    result = await registry('POST', f'people/{pid}/revoke-all')
    await audit.record(session, user.username, 'max_revoke_all_queued', str(pid), result['job_id'])
    return result


@router.post('/profiles/{profile_id}/rename')
async def rename_key(profile_id: int, body: KeyName, request: Request, user: CurrentUser, session: SessionDep):
    """Renames on the node through the panel's own rename endpoint (same JWT), then in the registry."""
    await stepup.verify(user, body.password, request)
    target = await registry('GET', f'profiles/{profile_id}/target')
    name = ' '.join(body.name.split())
    suffix = target['name'][target['name'].find(' [max-'):] if ' [max-' in target['name'] else ''
    try:
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000/api/', timeout=60, trust_env=False,
                                     headers={'Authorization': request.headers.get('Authorization', '')}) as client:
            response = await client.post(f"servers/{target['server_id']}/protocols/{target['protocol']}/rename",
                                         json={'client_id': target['public_key'], 'name': name + suffix})
    except httpx.HTTPError:
        raise HTTPException(502, 'Панель не ответила при переименовании') from None
    if response.status_code != 204:
        try:
            detail = response.json().get('detail')
        except ValueError:
            detail = None
        raise HTTPException(response.status_code if response.status_code in (404, 409) else 502,
                            detail if isinstance(detail, str) else 'Не удалось переименовать ключ на сервере')
    result = await registry('POST', f'profiles/{profile_id}/name', {'name': name})
    await audit.record(session, user.username, 'max_key_rename', str(profile_id), name)
    return result


@router.post('/people/{pid}/grant')
async def grant(pid: int, body: Grant, request: Request, user: CurrentUser, session: SessionDep):
    await stepup.verify(user, body.password, request)
    result = await registry('POST', f'people/{pid}/grant', {'slots': body.slots})
    await audit.record(session, user.username, 'max_manual_grant', str(pid), f'slots={body.slots}')
    return result


@router.post('/grants/{gid}/cancel')
async def cancel_grant(gid: int, body: Confirm, request: Request, user: CurrentUser, session: SessionDep):
    await stepup.verify(user, body.password, request)
    result = await registry('POST', f'grants/{gid}/cancel')
    await audit.record(session, user.username, 'max_grant_cancel', str(gid))
    return result


@router.post('/payments/{code}/{decision}')
async def decide(code: str, decision: str, body: Confirm, request: Request, user: CurrentUser, session: SessionDep):
    if decision not in ('confirm', 'reject'):
        raise HTTPException(404, 'Неизвестное действие')
    await stepup.verify(user, body.password, request)
    result = await registry('POST', f'payments/{code}/{decision}')
    await audit.record(session, user.username, 'max_payment_' + decision, code)
    return result


@router.post('/people/{pid}/delete')
async def delete_person(pid: int, body: Confirm, request: Request, user: CurrentUser, session: SessionDep):
    await stepup.verify(user, body.password, request)
    result = await registry('DELETE', f'people/{pid}')
    await audit.record(session, user.username, 'max_user_delete', str(pid))
    return result


@router.post('/sync')
async def sync(body: Confirm, request: Request, user: CurrentUser, session: SessionDep):
    await stepup.verify(user, body.password, request)
    return await registry('POST', 'sync')
