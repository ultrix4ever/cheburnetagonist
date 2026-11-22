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
    assert asyncio.run(b.notify_unreachable(T + 900 + 4 * 3600)) == 0
    assert [uid for uid, _ in m.sent] == [OWNER]  # only the owner hears that the probe went silent
    b.probe_silent = False
    asyncio.run(b.notify_unreachable(T + 900))
    assert [uid for uid, _ in m.sent] == [OWNER, 100]


def test_outage_length_is_recorded_and_reported(env):
    s, p, m, b, mine = env
    outage(s)
    s.save_reachability(report(T + 3 * 86400, s1='ok', s3='ok'))
    (o,) = s.outages()
    assert (o['server_id'], o['started'], o['ended']) == (3, T, T + 3 * 86400)
    text = b.reachability_report(T + 3 * 86400 + 60)
    assert 'Прошлые блокировки:' in text and 'Node 3 — 3 дн' in text and 'В среднем: 3 дн.' in text


def test_single_failed_run_is_not_recorded_as_an_outage(env):
    s, p, m, b, mine = env
    s.save_reachability(report(T, s1='ok', s3='fail'))
    s.save_reachability(report(T + 900, s1='ok', s3='ok'))
    assert s.outages() == []


def test_owner_is_told_once_when_the_probe_goes_silent_and_once_when_it_returns(env):
    s, p, m, b, mine = env
    s.save_reachability(report(T, s1='ok', s3='ok'))
    asyncio.run(b.notify_unreachable(T + 600))
    assert m.sent == []
    asyncio.run(b.notify_unreachable(T + 3700))
    asyncio.run(b.notify_unreachable(T + 3800))
    assert [u for u, _ in m.sent] == [OWNER] and 'не присылает результаты' in m.sent[0][1]
    s.save_reachability(report(T + 4000, s1='ok', s3='ok'))
    asyncio.run(b.notify_unreachable(T + 4010))
    asyncio.run(b.notify_unreachable(T + 4070))
    assert len(m.sent) == 2 and 'снова присылает' in m.sent[1][1]


def test_hidden_server_state_sends_and_shows_nothing(env):
    s, p, m, b, mine = env
    s.set_setting('server_state', 'off')
    outage(s)
    assert asyncio.run(b.notify_unreachable(T + 900)) == 0 and m.sent == []
    for cmd in ('/start', '/new', '/my', f'/key {mine}'):
        text = asyncio.run(b.command(100, cmd))[0]
        text = text if isinstance(text, str) else '\n'.join(text)
        assert 'Серверы:' not in text and 'России' not in text and '⛔' not in text and '✅' not in text, cmd
        # «работает» in /my and /key is the state of the person's own key, which stays.
        assert 'доступен' not in text and (cmd not in ('/start', '/new') or 'работает' not in text), cmd
    assert 'Node 3' in asyncio.run(b.command(OWNER, '/rf'))[0]
    s.set_setting('server_state', 'on')
    assert asyncio.run(b.notify_unreachable(T + 960)) == 1
