import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from vpnmax.api import Panel, RemoteError
from vpnmax.app import create_app
from vpnmax.bot import Bot
from vpnmax.store import Denied, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'clients.sqlite3')


def register(store, uid):
    pid, token = store.invite('Person ' + str(uid), uid)
    store.claim(uid, token)
    with store.connect() as db:
        # Issuance mechanics are tested on free people; billing has its own tests.
        db.execute('UPDATE people SET free=1 WHERE id=?', (pid,))
    return pid


def profile(store, uid, key='public1', sid=1, proto='awg'):
    return store.issued(uid, sid, proto, {'name': 'Device', 'public_key': key})


class PanelFake:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def issue(self, sid, name):
        self.calls.append(('issue', sid, name))
        if self.fail:
            raise RemoteError('timeout')
        return {'client': {'public_key': 'issued-public', 'name': name}}

    async def config(self, row):
        self.calls.append(('config', row['id']))
        return {'config_amnezia': 'vpn://EXAMPLE'}

    async def mutate(self, action, row):
        self.calls.append((action, row['id']))


class Messenger:
    def __init__(self):
        self.sent = []

    async def send(self, uid, text, buttons=None):
        self.sent.append((uid, text, buttons))


@pytest.fixture
def bot(store):
    return Bot(store, PanelFake(), Messenger(), {1: 'awg', 3: 'awg3'})


def test_invite_wrong_user_cannot_consume(store):
    pid, token = store.invite('Alice', 100)
    with pytest.raises(Denied):
        store.claim(200, token)
    store.claim(100, token)
    assert store.person(100)['id'] == pid
    with pytest.raises(Denied):
        store.claim(100, token)


def test_invite_digest_not_token(store):
    _, token = store.invite('Alice', 100)
    assert token.encode() not in store.path.read_bytes()


def test_invite_expiry(store):
    _, token = store.invite('Alice', 100)
    with store.connect() as db:
        db.execute('UPDATE invites SET expires=0')
    with pytest.raises(Denied):
        store.claim(100, token)


@pytest.mark.parametrize('command', ['/key', '/export', '/pause', '/resume', '/revoke'])
def test_cross_owner_access_never_calls_panel(store, bot, command):
    register(store, 100)
    register(store, 200)
    pid = profile(store, 100)
    with pytest.raises(Denied):
        asyncio.run(bot.command(200, f'{command} {pid}'))
    assert bot.panel.calls == []


def test_unregistered_cannot_issue(store, bot):
    text, buttons = asyncio.run(bot.command(999, '/new 1 phone'))
    # Unknown users are sent to self-registration, never to issuance.
    assert 'зарегистрируйтесь' in text and buttons[0][0]['type'] == 'request_contact'
    assert bot.panel.calls == []


def test_confirmation_wrong_owner_and_replay(store, bot):
    register(store, 100)
    register(store, 200)
    pid = profile(store, 100)
    token = store.confirm(100, pid, 'revoke')
    with pytest.raises(Denied):
        asyncio.run(bot.command(200, '/confirm ' + token))
    asyncio.run(bot.command(100, '/confirm ' + token))
    assert bot.panel.calls == [('revoke', pid)]
    with pytest.raises(Denied):
        asyncio.run(bot.command(100, '/confirm ' + token))


def test_confirmation_expiry(store):
    register(store, 100)
    pid = profile(store, 100)
    token = store.confirm(100, pid, 'pause')
    with store.connect() as db:
        db.execute('UPDATE confirmations SET expires=0')
    with pytest.raises(Denied):
        store.consume(100, token)


def test_unknown_issuance_no_blind_retry(store, bot):
    register(store, 100)
    bot.panel.fail = True
    with pytest.raises(RemoteError):
        asyncio.run(bot.command(100, '/new 1 phone'))
    with pytest.raises(Denied):
        asyncio.run(bot.command(100, '/new 1 phone'))
    assert len(bot.panel.calls) == 1
    with store.connect() as db:
        assert db.execute('SELECT state FROM operations').fetchone()[0] == 'unknown'


def test_crash_recovery_blocks_pending(store):
    register(store, 100)
    store.start_operation(100, 'issue', server_id=1)
    store.recover()
    with pytest.raises(Denied):
        store.start_operation(100, 'issue', server_id=1)


def test_quota(store, bot):
    register(store, 100)
    with store.connect() as db:
        db.execute('UPDATE people SET quota=1 WHERE max_id=100')
    profile(store, 100)
    with pytest.raises(Denied):
        asyncio.run(bot.command(100, '/new 1 other'))
    assert bot.panel.calls == []


def test_concurrent_operations_only_one(store):
    register(store, 100)
    def attempt(_):
        try:
            store.start_operation(100, 'issue', server_id=1)
            return True
        except Denied:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1


def test_inventory_names_do_not_claim_ownership(store):
    register(store, 100)
    store.inventory(1, 'awg', [{'name': 'Alice', 'public_key': 'old'}])
    assert store.profiles(100) == []


def test_inventory_preserves_binding_and_protocol_namespace(store):
    owner = register(store, 100)
    pid = profile(store, 100, key='same', proto='awg')
    store.inventory(1, 'awg3', [{'name': 'Other', 'public_key': 'same'}])
    store.inventory(1, 'awg', [{'name': 'Renamed', 'public_key': 'same', 'paused': True}])
    row = store.owned(100, pid)
    assert row['person_id'] == owner and row['state'] == 'paused'
    assert len(store.profiles(100)) == 1


def test_no_implicit_reassign(store):
    register(store, 100)
    other = register(store, 200)
    pid = profile(store, 100)
    with pytest.raises(Denied):
        store.assign(pid, other)


def test_group_message_ignored(store, bot):
    event = {'update_type': 'message_created', 'message': {
        'sender': {'user_id': 100}, 'recipient': {'chat_type': 'chat'},
        'body': {'text': '/new 1 phone', 'mid': 'group1'}}}
    asyncio.run(bot.event(event))
    assert bot.max.sent == [] and bot.panel.calls == []


def test_duplicate_inbox_event_only_once(store):
    event = {'update_type': 'message_created', 'message': {'body': {'mid': 'm1', 'text': '/new 1 phone'}}}
    store.enqueue(event)
    store.enqueue(event)
    eid, _ = store.next_event()
    store.event_done(eid)
    store.enqueue(event)
    assert store.next_event() is None


def test_webhook_requires_secret_and_limits_body(store, bot):
    secret = 's' * 48
    app = create_app(store, bot, secret)
    # Deliberately do not start background worker for admission tests.
    client = TestClient(app)
    assert client.post('/webhook/max', json={}).status_code == 403
    headers = {'X-Max-Bot-Api-Secret': secret}
    assert client.post('/webhook/max', headers=headers, content='x' * 65537).status_code == 413
    assert client.post('/webhook/max', headers=headers, json=[]).status_code == 400
    assert client.post('/webhook/max', headers=headers, json={'update_type': 'bot_started', 'user': {'user_id': 100}}).status_code == 200
    assert store.next_event() is not None


def test_api_routes_and_encoded_query():
    requests = []
    def responder(request):
        requests.append(request)
        return httpx.Response(200, json=[])
    async def run():
        panel = Panel('http://backend:8000/', 'secret', {1: 'awg', 3: 'awg3'}, transport=httpx.MockTransport(responder))
        await panel.clients(1)
        await panel.clients(3)
        await panel.config({'server_id': 3, 'public_key': 'a/b+c='})
        await panel.http.aclose()
    asyncio.run(run())
    assert requests[0].url.path == '/api/v1/servers/1/clients'
    assert requests[1].url.path == '/api/v1/servers/3/awg3/clients'
    assert requests[2].url.params['public_key'] == 'a/b+c='
    assert requests[0].headers['X-API-Key'] == 'secret'


def test_disabled_person_cannot_act(store, bot):
    person = register(store, 100)
    pid = profile(store, 100)
    with store.connect() as db:
        db.execute('UPDATE people SET active=0 WHERE id=?', (person,))
    with pytest.raises(Denied):
        asyncio.run(bot.command(100, f'/config {pid}'))


def test_old_config_not_fabricated(store, bot):
    person = register(store, 100)
    store.inventory(1, 'awg', [{'name': 'old', 'public_key': 'old'}])
    with store.connect() as db:
        pid = db.execute('SELECT id FROM profiles').fetchone()[0]
    store.assign(pid, person)
    text, _ = asyncio.run(bot.command(100, f'/export {pid}'))
    assert 'не сохранён' in text
    assert bot.panel.calls == []


def test_failed_reconcile_preserves_last_inventory(store):
    class ReadPanel:
        async def clients(self, sid):
            if sid == 1:
                raise RemoteError('unreachable')
            return [{'name': 'Found', 'public_key': 'found'}]
    store.inventory(1, 'awg', [{'name': 'Preserved', 'public_key': 'kept'}])
    bot = Bot(store, ReadPanel(), Messenger(), {1: 'awg', 2: 'awg'})
    assert asyncio.run(bot.sync()) == [1]
    with store.connect() as db:
        rows = [tuple(r) for r in db.execute('SELECT server_id,name,state FROM profiles ORDER BY server_id')]
    assert rows == [(1, 'Preserved', 'active'), (2, 'Found', 'active')]
