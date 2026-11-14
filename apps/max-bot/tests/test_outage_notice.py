"""A server Russia cannot reach: its key owners are asked once, nothing is revoked for them."""
import asyncio

import pytest

from test_reachability import OWNER, T, Max, report
from test_v2 import Panel, data
from vpnmax.bot import Bot
from vpnmax.store import Store


@pytest.fixture
def env(tmp_path):
    s = Store(tmp_path / 'db')
    s.save_person(data(quota=5))
    s.save_person(data(200, quota=5))
    p, m = Panel(), Max()
    b = Bot(s, p, m, {1: 'awg', 2: 'awg', 3: 'awg3', 4: 'awg'}, owner=OWNER)
    s.update_catalog([dict(id=i, name=f'Node {i}', last_check_ok=True) for i in (1, 2, 3, 4)])

    def add(sid, name, uid):
        client = asyncio.run(p.issue(sid, name))['client']
        if uid:
            return s.issued(uid, sid, b.protocols[sid], client)
        s.inventory_one(sid, b.protocols[sid], client)
    add(1, 'Good', 100)
    mine = add(3, 'Mine', 100)
    add(3, 'Legacy', None)
    p.calls.clear()
    return s, p, m, b, mine


def outage(s, at=T):
    s.save_reachability(report(at, s1='ok', s3='fail'))
    s.save_reachability(report(at + 900, s1='ok', s3='fail'))


def test_owner_of_a_key_is_asked_once_and_nothing_is_revoked(env):
    s, p, m, b, mine = env
    s.save_reachability(report(T, s1='ok', s3='fail'))
    assert asyncio.run(b.notify_unreachable(T)) == 0  # one failed run is not an outage yet
    s.save_reachability(report(T + 900, s1='ok', s3='fail'))
    assert asyncio.run(b.notify_unreachable(T + 900)) == 1
    assert asyncio.run(b.notify_unreachable(T + 960)) == 0
    (uid, text), = m.sent
    assert uid == 100 and 'Node 3' in text and 'за 5 дней' in text and f'#{mine} Mine' in text and 'вернётся в ваш лимит' in text
    assert p.calls == [] and s.usage(100) == (2, 5)


def test_button_leads_to_ordinary_confirmed_revocation_that_frees_the_slot(env):
    s, p, m, b, mine = env
    outage(s)
    asyncio.run(b.notify_unreachable(T + 900))
    text, buttons = asyncio.run(b.command(100, f'/revoke {mine}'))
    assert 'необратим' in text and p.calls == []
    asyncio.run(b.command(100, buttons[0][0]['payload']))
    assert ('revoke', 3) in p.calls and s.usage(100) == (1, 5)


def test_recovery_is_announced_to_those_who_kept_their_keys(env):
    s, p, m, b, mine = env
    outage(s)
    asyncio.run(b.notify_unreachable(T + 900))
    s.save_reachability(report(T + 5 * 86400, s1='ok', s3='ok'))
    assert asyncio.run(b.notify_unreachable(T + 5 * 86400)) == 1
    assert 'снова подключается' in m.sent[-1][1] and f'#{mine} Mine' in m.sent[-1][1]
    assert asyncio.run(b.notify_unreachable(T + 5 * 86400 + 60)) == 0
    outage(s, T + 6 * 86400)  # a new outage is a new question
    assert asyncio.run(b.notify_unreachable(T + 6 * 86400 + 900)) == 1


def test_no_reachable_server_at_all_tells_nobody(env):
    s, p, m, b, mine = env
    for at in (T, T + 900):
        s.save_reachability(report(at, s1='fail', s3='fail'))
    assert asyncio.run(b.notify_unreachable(T + 900)) == 0 and m.sent == []


def test_people_without_keys_there_and_stale_data_get_nothing(env):
    s, p, m, b, mine = env
    outage(s)
    assert asyncio.run(b.notify_unreachable(T + 900 + 4 * 3600)) == 0 and m.sent == []
    asyncio.run(b.notify_unreachable(T + 900))
    assert [uid for uid, _ in m.sent] == [100]
