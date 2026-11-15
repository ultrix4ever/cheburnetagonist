import asyncio
import hmac
import json
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from .api import MaxAPI, Panel
from .bot import Bot
from .store import Store


def create_app(store, bot, secret, admin_secret=None, poll=False):
    if len(secret) < 32:
        raise ValueError('Webhook secret must contain at least 32 characters')

    work_lock = asyncio.Lock()
    acks = set()
    poll_state = {'failures': 0, 'last_ok': None}

    async def acknowledge(callback):
        try:
            await bot.max.acknowledge(callback)
        except Exception:
            pass

    def accept(event):
        if not isinstance(event, dict) or event.get('update_type') not in ('message_created', 'message_callback', 'bot_started'):
            return
        store.enqueue(event)
        callback = event.get('callback', {}).get('callback_id') if event.get('update_type') == 'message_callback' else None
        if callback and hasattr(bot.max, 'acknowledge'):
            # Confirm the tap at once, not after the queue: otherwise people press again.
            task = asyncio.create_task(acknowledge(callback))
            acks.add(task)
            task.add_done_callback(acks.discard)

    async def poller():
        """Pull updates ourselves instead of waiting for MAX to reach this host."""
        marker = store.setting('updates_marker')
        marker = int(marker) if marker and marker.lstrip('-').isdigit() else None
        while True:
            try:
                updates, following = await bot.max.updates(marker)
            except Exception:
                poll_state['failures'] += 1
                await asyncio.sleep(min(2 * poll_state['failures'], 20))
                continue
            poll_state.update(failures=0, last_ok=int(time.time()))
            for event in updates:
                accept(event)
            if following is not None and following != marker:
                marker = following
                # Saved after the updates are queued: a crash repeats them, the queue drops exact repeats.
                store.set_setting('updates_marker', marker)

    async def worker():
        servers = iter(bot.protocols)
        next_sync = asyncio.get_running_loop().time() + 75
        next_expiry = 0
        next_revocations = 0
        next_billing = asyncio.get_running_loop().time() + 30
        next_unreachable = asyncio.get_running_loop().time() + 45
        while True:
            if asyncio.get_running_loop().time() >= next_expiry:
                async with work_lock:
                    await bot.expire_replacements()
                next_expiry = asyncio.get_running_loop().time() + 5
            if asyncio.get_running_loop().time() >= next_revocations:
                async with work_lock:
                    try:
                        # Revocation is the one mutation retried automatically: DELETE is idempotent.
                        await bot.process_revocations()
                    except Exception:
                        with store.connect() as db:
                            store.log(db, None, 'revocations_failed', 0)
                next_revocations = asyncio.get_running_loop().time() + 30
            if asyncio.get_running_loop().time() >= next_billing:
                async with work_lock:
                    try:
                        await bot.billing_tick()
                    except Exception:
                        with store.connect() as db:
                            store.log(db, None, 'billing_tick_failed', 0)
                next_billing = asyncio.get_running_loop().time() + 60
            if asyncio.get_running_loop().time() >= next_unreachable:
                async with work_lock:
                    try:
                        await bot.notify_unreachable()
                    except Exception:
                        with store.connect() as db:
                            store.log(db, None, 'notify_unreachable_failed', 0)
                next_unreachable = asyncio.get_running_loop().time() + 60
            async with work_lock:
                with store.connect() as db:
                    job = db.execute("SELECT * FROM admin_jobs WHERE state='pending' ORDER BY created,rowid LIMIT 1").fetchone()
                    if job:
                        db.execute("UPDATE admin_jobs SET state='processing' WHERE id=?", (job['id'],))
                if job:
                    try:
                        previous = json.loads(job['result']) if job['result'] else None
                        result = await bot.revoke_all(job['person_id'], previous=previous, one_step=True)
                        state = ('pending' if result['remaining'] else
                                 ('partial' if result['unconfirmed'] or result['uncertain_operations'] else 'done'))
                    except Exception:
                        state, result = 'unknown', {'error': 'Требуется сверка администратором'}
                    with store.connect() as db:
                        db.execute('UPDATE admin_jobs SET state=?,result=? WHERE id=?', (state, json.dumps(result), job['id']))
            item = store.next_event()
            if item:
                eid, event = item
                try:
                    async with work_lock:
                        await bot.event(event)
                except Exception:
                    # Do not log event/config/headers, or reissue a mutation.
                    with store.connect() as db:
                        db.execute("UPDATE inbox SET state='interrupted',body='{}' WHERE id=?", (eid,))
                else:
                    store.event_done(eid)
                # MAX limits each dialogue to 2 messages/second.
                await asyncio.sleep(0.6)
            else:
                if asyncio.get_running_loop().time() >= next_sync:
                    try:
                        sid = next(servers)
                    except StopIteration:
                        servers = iter(bot.protocols)
                        sid = next(servers)
                    try:
                        # Same worker as mutations: no stale inventory write racing a revoke.
                        async with work_lock:
                            await bot.sync_server(sid)
                            if hasattr(bot.panel, 'servers'):
                                store.update_catalog(await asyncio.wait_for(bot.panel.servers(), timeout=15))
                    except Exception:
                        # Unreachable is NOT evidence of removal; preserve prior inventory.
                        with store.connect() as db:
                            store.log(db, None, 'inventory_failed', sid)
                    next_sync = asyncio.get_running_loop().time() + 75
                await asyncio.sleep(0.2)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        tasks = [asyncio.create_task(worker())] + ([asyncio.create_task(poller())] if poll else [])
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if admin_secret:
        from .admin import add_admin_routes
        add_admin_routes(app, store, bot, admin_secret, work_lock)

    @app.get('/health')
    async def health():
        # In polling mode a silent poller means a deaf bot: fail the health check so it is noticed.
        if poll and poll_state['failures'] >= 10:
            raise HTTPException(503)
        return {'ok': True}

    @app.post('/webhook/max')
    async def webhook(request: Request):
        value = request.headers.get('X-Max-Bot-Api-Secret', '')
        if not hmac.compare_digest(value.encode(), secret.encode()):
            raise HTTPException(403)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 65536:
                raise HTTPException(413)
            chunks.append(chunk)
        try:
            event = json.loads(b''.join(chunks))
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400) from None
        if not isinstance(event, dict):
            raise HTTPException(400)
        accept(event)
        return {'ok': True}

    return app


def payment_url(value):
    # A link button with a non-https URL would hand users an unsafe or broken payment page.
    if not value:
        return None
    if not value.startswith('https://') or len(value) > 2048:
        raise ValueError('PAYMENT_URL must be an https URL')
    return value


def from_env():
    os.umask(0o077)
    protocols = {int(k): v for k, v in json.loads(os.environ['PANEL_PROTOCOLS']).items()}
    if not protocols or any(v not in ('awg', 'awg3') for v in protocols.values()):
        raise ValueError('Invalid protocol mapping')
    store = Store(os.environ.get('DATABASE_PATH', '/data/clients.sqlite3'))
    panel = Panel(os.environ['PANEL_URL'], os.environ['PANEL_API_KEY'], protocols)
    messenger = MaxAPI(os.environ['MAX_BOT_TOKEN'], ca_file=os.environ.get('MAX_CA_FILE'),
                       api_socket=os.environ.get('MAX_API_SOCKET'))
    owner = int(os.environ.get('MAX_OWNER_ID') or 0) or None
    bot = Bot(store, panel, messenger, protocols, owner=owner,
              payment_details=os.environ.get('PAYMENT_DETAILS') or None,
              payment_url=payment_url(os.environ.get('PAYMENT_URL')),
              payment_phone=os.environ.get('PAYMENT_PHONE') or None,
              contact_secret=os.environ['MAX_BOT_TOKEN'],
              guide_token=os.environ.get('GUIDE_FILE_TOKEN'))
    return create_app(store, bot, os.environ['MAX_WEBHOOK_SECRET'], os.environ.get('REGISTRY_ADMIN_SECRET'),
                      poll=os.environ.get('MAX_UPDATES', 'webhook') == 'poll')
