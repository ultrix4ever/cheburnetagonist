"""Internal registry API. Only the panel backend may call it, never a bot user."""
import hmac
import json
import secrets
import time

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field

from . import billing
from .bot import KEY_NAME
from .store import Denied


class PersonEdit(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    quota: int = Field(ge=0, le=100)
    allowed_servers: list[int] = Field(max_length=100)
    phone: str = Field(default='', max_length=40)
    active: bool = True
    free: bool | None = None  # None keeps the current flag


class PersonCreate(PersonEdit):
    max_id: int = Field(gt=0, le=9223372036854775807)


class Assign(BaseModel):
    person_id: int = Field(gt=0)


class KeyName(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class Grant(BaseModel):
    slots: int = Field(ge=1, le=billing.MAX_SLOTS)


def add_admin_routes(app, store, bot, secret, lock):
    if len(secret) < 32:
        raise ValueError('Registry admin secret too short')

    def auth(x_registry_admin: str = Header(default='')):
        if not hmac.compare_digest(x_registry_admin.encode(), secret.encode()):
            raise HTTPException(403, 'Forbidden')

    def checked(body):
        data = body.model_dump()
        data['name'] = data['name'].strip()
        if not data['name'] or not set(data['allowed_servers']).issubset(bot.protocols):
            raise HTTPException(422, 'Неверное имя или список серверов')
        data['allowed_servers'] = sorted(set(data['allowed_servers']))
        return data

    def denied(exc):
        return HTTPException(409, str(exc) or 'Конфликт состояния')

    @app.get('/internal/admin/state', dependencies=[Depends(auth)])
    async def state():
        # Snapshot has no client public/private keys, configs, invitations or inbox bodies.
        with store.connect() as db:
            profiles = [dict(r) for r in db.execute('SELECT id,server_id,protocol,name,person_id,state,has_config,billing_state,suspended_at,legacy FROM profiles ORDER BY id')]
            replacements = [dict(r) for r in db.execute('SELECT * FROM replacements ORDER BY created DESC LIMIT 100')]
            operations = [dict(r) for r in db.execute("SELECT * FROM operations WHERE state IN ('pending','unknown') ORDER BY created DESC LIMIT 100")]
            audit = [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]
            jobs = [dict(r) for r in db.execute('SELECT * FROM admin_jobs ORDER BY created DESC LIMIT 100')]
            for job in jobs:
                job['result'] = json.loads(job['result']) if job['result'] else None
        return dict(people=store.admin_people(), profiles=profiles, servers=store.catalog(bot.protocols),
                    replacements=replacements, operations=operations, audit=audit, jobs=jobs,
                    revocations=store.revocation_backlog(), panel=bot.panel_status,
                    payments=store.payments_snapshot(),
                    reachability=store.reachability(),
                    billing={'price': billing.PRICE_RUB, 'period_days': billing.PERIOD // 86400, 'max_slots': billing.MAX_SLOTS})

    @app.post('/internal/admin/people', dependencies=[Depends(auth)], status_code=201)
    async def create(body: PersonCreate):
        async with lock:
            try:
                return {'id': store.save_person(checked(body))}
            except Denied as exc:
                raise denied(exc) from None

    @app.put('/internal/admin/people/{pid}', dependencies=[Depends(auth)])
    async def update(pid: int, body: PersonEdit):
        async with lock:
            try:
                return {'id': store.save_person(checked(body), pid)}
            except Denied as exc:
                raise denied(exc) from None

    @app.post('/internal/admin/profiles/{profile_id}/assign', dependencies=[Depends(auth)])
    async def assign(profile_id: int, body: Assign):
        async with lock:
            try:
                store.assign(profile_id, body.person_id)
            except Denied as exc:
                raise denied(exc) from None
            return {'assigned': True}

    @app.post('/internal/admin/people/{pid}/revoke-all', dependencies=[Depends(auth)], status_code=202)
    async def revoke_all(pid: int):
        async with lock:
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT 1 FROM people WHERE id=?', (pid,)).fetchone():
                    raise HTTPException(404, 'Пользователь не найден')
                existing = db.execute("SELECT id FROM admin_jobs WHERE person_id=? AND state IN ('pending','processing')", (pid,)).fetchone()
                if existing:
                    return {'job_id': existing['id']}
                jid = secrets.token_hex(16)
                db.execute('UPDATE people SET active=0 WHERE id=?', (pid,))
                db.execute("INSERT INTO admin_jobs VALUES(?,?,'pending',NULL,?)", (jid, pid, int(time.time())))
                store.log(db, None, 'revoke_all_queued', pid)
            return {'job_id': jid}

    @app.get('/internal/admin/profiles/{profile_id}/target', dependencies=[Depends(auth)])
    async def target(profile_id: int):
        # For the panel's own node rename; the panel already holds these keys.
        with store.connect() as db:
            row = db.execute("SELECT server_id,protocol,public_key,name FROM profiles WHERE id=? AND state IN ('active','paused')",
                             (profile_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Ключ не найден или отозван')
        return dict(row)

    @app.post('/internal/admin/profiles/{profile_id}/name', dependencies=[Depends(auth)])
    async def rename(profile_id: int, body: KeyName):
        name = ' '.join(body.name.split())
        if not KEY_NAME.fullmatch(name):
            raise HTTPException(422, 'Название: до 40 символов, буквы, цифры, пробел и . , - ( )')
        async with lock:
            with store.connect() as db:
                row = db.execute('SELECT name FROM profiles WHERE id=?', (profile_id,)).fetchone()
                if not row:
                    raise HTTPException(404, 'Ключ не найден')
                # Keep the [max-...] correlation label used for reconciliation.
                suffix = row['name'][row['name'].find(' [max-'):] if ' [max-' in row['name'] else ''
                label = name + suffix
                db.execute('UPDATE profiles SET name=? WHERE id=?', (label, profile_id))
                store.log(db, None, 'profile_renamed', profile_id)
            return {'name': label}

    @app.post('/internal/admin/people/{pid}/grant', dependencies=[Depends(auth)])
    async def grant(pid: int, body: Grant):
        # Payment received outside the bot (cash, a transfer without a code): slots for 30 days.
        async with lock:
            person = store.person_by_id(pid)
            if not person:
                raise HTTPException(404, 'Пользователь не найден')
            if person['free']:
                raise HTTPException(409, 'Аккаунт бесплатный: его лимит задаётся полем лимита')
            try:
                gid = store.add_grant(pid, body.slots, 'manual')
            except Denied as exc:
                raise denied(exc) from None
            await bot.enforce(person)
            return {'grant_id': gid}

    @app.post('/internal/admin/grants/{gid}/cancel', dependencies=[Depends(auth)])
    async def cancel_grant(gid: int):
        async with lock:
            try:
                pid = store.cancel_grant(gid)
            except Denied as exc:
                raise denied(exc) from None
            await bot.enforce(store.person_by_id(pid))
            return {'cancelled': True}

    @app.post('/internal/admin/payments/{code}/{decision}', dependencies=[Depends(auth)])
    async def decide(code: str, decision: str):
        if decision not in ('confirm', 'reject'):
            raise HTTPException(404, 'Неизвестное действие')
        command = {'confirm': '/payok', 'reject': '/payno'}[decision]
        async with lock:
            try:
                text, _ = await bot.owner_command(command, [command, code])
            except Denied as exc:
                raise denied(exc) from None
            return {'result': text}

    @app.delete('/internal/admin/people/{pid}', dependencies=[Depends(auth)])
    async def delete_person(pid: int):
        async with lock:
            try:
                store.delete_person(pid)
            except Denied as exc:
                raise (HTTPException(404, str(exc)) if 'не найден' in str(exc) else denied(exc)) from None
            return {'deleted': True}

    @app.post('/internal/admin/sync', dependencies=[Depends(auth)])
    async def sync():
        async with lock:
            failures = await bot.sync()
            try:
                store.update_catalog(await bot.panel.servers())
            except Exception:
                pass
            return {'failed_servers': failures}
