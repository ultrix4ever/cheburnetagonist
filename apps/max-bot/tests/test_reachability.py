"""Probe results from a restricted network: storage, owner alerts and what users see."""
import asyncio
import json
import subprocess
import sys

import pytest

from test_v2 import Panel, data
from vpnmax.bot import Bot
from vpnmax.store import Denied, Store

OWNER = 999
T = 1_800_000_000


class Max:
    def __init__(self):
        self.sent, self.fail = [], False

    async def send(self, uid, text, buttons=None, forward=None, media=None):
        if self.fail:
            raise RuntimeError('MAX down')
        self.sent.append((uid, text))


def report(at, vantage='ekb', **statuses):
    """report(T, s1='ok', s3='fail') -> one probe run."""
    results = []
    for key, status in statuses.items():
        sid = int(key[1:])
        results.append(dict(id=sid, name=f'Node {sid}', status=status, checked=at, icmp=status == 'ok',
                            stage='ok' if status == 'ok' else ('setup' if status == 'error' else 'handshake'),
                            detail='', handshake_ms=90 if status == 'ok' else None,
                            rtt_ms=70 if status == 'ok' else None, exit_ip='192.0.2.1' if status == 'ok' else None))
    return dict(version=1, vantage=vantage, started=at, finished=at, results=results, broken_targets=[])


@pytest.fixture
def env(tmp_path):
    s = Store(tmp_path / 'db')
    s.save_person(data())
    s.save_person(data(OWNER))
    m = Max()
    b = Bot(s, Panel(), m, {1: 'awg', 2: 'awg', 3: 'awg3', 4: 'awg'}, owner=OWNER)
    s.update_catalog([dict(id=i, name=f'Node {i}', last_check_ok=True) for i in (1, 2, 3, 4)])
    return s, m, b


def rows(s):
    return {r['server_id']: r for r in s.reachability()}


def test_one_failed_run_is_not_yet_an_outage(env):
    s, m, b = env
    assert s.save_reachability(report(T, s1='ok', s3='fail')) == 2
    assert s.unreachable_servers(T) == set()
    s.save_reachability(report(T + 900, s1='ok', s3='fail'))
    assert s.unreachable_servers(T + 900) == {3} and rows(s)[3]['since'] == T and rows(s)[3]['fails'] == 2


def test_probe_error_and_replayed_report_do_not_change_state(env):
    s, m, b = env
    s.save_reachability(report(T, s3='ok'))
    # The probe's own breakage says nothing about the node.
    assert s.save_reachability(report(T + 900, s3='error')) == 0
    # An older or repeated report must not rewind a newer one.
    assert s.save_reachability(report(T - 60, s3='fail')) == 0 and s.save_reachability(report(T, s3='fail')) == 0
    assert rows(s)[3]['status'] == 'ok' and rows(s)[3]['checked'] == T


def test_users_see_status_in_server_list(env, monkeypatch):
    s, m, b = env
    monkeypatch.setattr('time.time', lambda: T + 1000)
    text, _ = asyncio.run(b.command(100, '/servers'))
    assert 'из России' not in text  # nothing known: say nothing
    s.save_reachability(report(T, s1='ok', s3='fail'))
    s.save_reachability(report(T + 900, s1='ok', s3='fail'))
    text, _ = asyncio.run(b.command(100, '/servers'))
    lines = text.splitlines()
    assert '✅ Node 1 · из России подключается' in lines
    assert '⛔ Node 3 · из России сейчас не подключается' in lines
    assert 'Node 2 · доступен' in lines and 'проверка раз в 15 минут' in lines[-1]
    assert m.sent == []  # nothing is pushed to the owner


def test_buttons_carry_the_mark_when_ordering_and_reissuing(env, monkeypatch):
    s, m, b = env
    monkeypatch.setattr('time.time', lambda: T + 1000)
    s.save_reachability(report(T, s1='ok', s3='fail'))
    s.save_reachability(report(T + 900, s1='ok', s3='fail'))
    _, buttons = asyncio.run(b.command(100, '/new'))
    assert [x[0]['text'] for x in buttons] == ['✅ Node 1', 'Node 2', '⛔ Node 3', 'Node 4', 'Главное меню']
    assert buttons[2][0]['payload'] == '/new 3'  # an unreachable server can still be chosen
    pid = s.issued(100, 1, 'awg', {'public_key': 'k', 'name': 'Old'})
    text, buttons = asyncio.run(b.command(100, f'/reissue {pid}'))
    assert [x[0]['text'] for x in buttons][:3] == ['✅ Node 1 (текущий)', 'Node 2', '⛔ Node 3'] and '⛔ — сейчас не подключается' in text


def test_stale_results_warn_nobody(env, monkeypatch):
    s, m, b = env
    s.save_reachability(report(T, s3='fail'))
    s.save_reachability(report(T + 900, s3='fail'))
    monkeypatch.setattr('time.time', lambda: T + 900 + 4 * 3600)
    text, _ = asyncio.run(b.command(100, '/servers'))
    assert 'из России' not in text and s.unreachable_servers() == set()


def test_owner_report_and_access(env):
    s, m, b = env
    with pytest.raises(Denied):
        asyncio.run(b.command(100, '/rf'))
    text, _ = asyncio.run(b.command(OWNER, '/rf'))
    assert 'ещё не присылала' in text
    s.save_reachability(report(T, s1='ok', s3='fail'))
    s.save_reachability(report(T + 900, s1='ok', s3='fail'))
    text = b.reachability_report(T + 1200)
    assert 'Node 1 — подключается, отклик 70 мс' in text and 'Node 3 — НЕ подключается уже 20 мин' in text
    assert 'ekb, 5 мин назад' in text and 'один провайдер' in text
    assert 'данные устарели' in b.reachability_report(T + 900 + 4 * 3600)


def test_bad_vantage_name_is_rejected(env):
    s, m, b = env
    with pytest.raises(Denied):
        s.save_reachability(report(T, vantage='ekb; drop', s1='ok'))
    assert s.reachability() == []


def test_cli_imports_report_from_stdin(tmp_path):
    db = tmp_path / 'db'
    Store(db)
    result = subprocess.run([sys.executable, '-m', 'vpnmax.cli', '--db', str(db), 'reachability'],
                            input=json.dumps(report(T, s1='ok', s2='fail')), text=True, capture_output=True)
    assert result.returncode == 0 and 'Stored 2 of 2 results from ekb' in result.stdout
    assert {r['server_id']: r['status'] for r in Store(db).reachability()} == {1: 'ok', 2: 'fail'}


def test_my_keys_show_the_mark_and_distinguishable_buttons(env, monkeypatch):
    s, m, b = env
    monkeypatch.setattr('time.time', lambda: T + 1000)
    with s.connect() as db:
        db.execute('UPDATE people SET quota=5 WHERE max_id=100')
    a = s.issued(100, 1, 'awg', {'public_key': 'a', 'name': 'Ключ'})
    c = s.issued(100, 3, 'awg3', {'public_key': 'c', 'name': 'Ключ'})
    text, buttons = asyncio.run(b.command(100, '/my'))
    assert f'#{a} Ключ · Node 1 · работает' in text.splitlines() and '✅' not in text
    assert [x[0]['text'] for x in buttons[:2]] == [f'#{a} Ключ · Node 1', f'#{c} Ключ · Node 3']
    s.save_reachability(report(T, s1='ok', s3='fail'))
    s.save_reachability(report(T + 900, s1='ok', s3='fail'))
    text, buttons = asyncio.run(b.command(100, '/my'))
    lines = text.splitlines()
    assert f'✅ #{a} Ключ · Node 1 · работает' in lines and f'⛔ #{c} Ключ · Node 3 · включён, но из России не подключается' in lines
    assert 'проверка раз в 15 минут' in lines[-1]
    assert [x[0]['text'] for x in buttons[:2]] == [f'✅ #{a} Ключ · Node 1', f'⛔ #{c} Ключ · Node 3']
    text, _ = asyncio.run(b.command(100, f'/key {c}'))
    assert 'Node 3 · включён, но из России не подключается' in text and '⛔ Сервер из России сейчас не подключается.' in text and 'перевыпустить' in text
    text, _ = asyncio.run(b.command(100, f'/key {a}'))
    assert '✅ Сервер из России подключается.' in text and 'перевыпустить' not in text
    _, buttons = asyncio.run(b.command(100, '/revoke'))
    assert [x[0]['text'] for x in buttons[:2]] == ['✅ Node 1 · 1', '⛔ Node 3 · 1']
