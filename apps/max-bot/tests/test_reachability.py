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
    assert s.unreachable_servers(T) == set() and s.reachability_changes() == []
    asyncio.run(b.reachability_tick(T))
    assert m.sent == []
    s.save_reachability(report(T + 900, s1='ok', s3='fail'))
    assert s.unreachable_servers(T + 900) == {3} and rows(s)[3]['since'] == T and rows(s)[3]['fails'] == 2


def test_owner_hears_once_about_outage_and_once_about_recovery(env):
    s, m, b = env
    for step in range(3):
        s.save_reachability(report(T + step * 900, s1='ok', s3='fail'))
        asyncio.run(b.reachability_tick(T + step * 900))
    assert len(m.sent) == 1 and m.sent[0][0] == OWNER
    text = m.sent[0][1]
    assert 'Node 3' in text and 'не подключается из России' in text and 'ekb' in text and 'Node 1' not in text
    assert 'на ping тоже не отвечает' in text and 'похоже на блокировку' in text
    s.save_reachability(report(T + 3600, s1='ok', s3='ok'))
    asyncio.run(b.reachability_tick(T + 3600))
    asyncio.run(b.reachability_tick(T + 3601))
    assert len(m.sent) == 2 and 'снова подключается' in m.sent[1][1]
    assert s.unreachable_servers(T + 3600) == set()


def test_a_single_good_run_resets_the_failure_count(env):
    s, m, b = env
    for step, status in enumerate(['fail', 'ok', 'fail']):
        s.save_reachability(report(T + step * 900, s3=status))
        asyncio.run(b.reachability_tick(T + step * 900))
    assert rows(s)[3]['fails'] == 1 and m.sent == []


def test_failed_alert_is_retried_not_lost(env):
    s, m, b = env
    s.save_reachability(report(T, s3='fail'))
    s.save_reachability(report(T + 900, s3='fail'))
    m.fail = True
    asyncio.run(b.reachability_tick(T + 900))
    assert rows(s)[3]['notified'] is None
    m.fail = False
    asyncio.run(b.reachability_tick(T + 930))
    assert len(m.sent) == 1 and rows(s)[3]['notified'] == 'fail'


def test_probe_error_and_replayed_report_do_not_change_state(env):
    s, m, b = env
    s.save_reachability(report(T, s3='ok'))
    # The probe's own breakage says nothing about the node.
    assert s.save_reachability(report(T + 900, s3='error')) == 0
    # An older or repeated report must not rewind a newer one.
    assert s.save_reachability(report(T - 60, s3='fail')) == 0 and s.save_reachability(report(T, s3='fail')) == 0
    assert rows(s)[3]['status'] == 'ok' and rows(s)[3]['checked'] == T


def test_panel_seeing_the_node_changes_the_explanation(env):
    s, m, b = env
    s.update_catalog([dict(id=3, name='Node 3', last_check_ok=False)])
    s.save_reachability(report(T, s3='fail'))
    s.save_reachability(report(T + 900, s3='fail'))
    asyncio.run(b.reachability_tick(T + 900))
    assert 'возможно, сервер выключен' in m.sent[0][1] and 'похоже на блокировку' not in m.sent[0][1]


def test_owner_is_told_when_the_probe_goes_silent_and_when_it_returns(env):
    s, m, b = env
    s.save_reachability(report(T, s1='ok'))
    asyncio.run(b.reachability_tick(T + 600))
    assert m.sent == []
    asyncio.run(b.reachability_tick(T + 3700))
    asyncio.run(b.reachability_tick(T + 3800))
    assert len(m.sent) == 1 and 'не обновлялась' in m.sent[0][1]
    s.save_reachability(report(T + 4000, s1='ok'))
    asyncio.run(b.reachability_tick(T + 4010))
    assert len(m.sent) == 2 and 'снова обновляется' in m.sent[1][1]


def test_no_owner_no_messages(tmp_path):
    s = Store(tmp_path / 'db')
    m = Max()
    b = Bot(s, Panel(), m, {1: 'awg'})
    s.save_reachability(report(T, s1='fail'))
    s.save_reachability(report(T + 900, s1='fail'))
    asyncio.run(b.reachability_tick(T + 900))
    assert m.sent == []


def test_users_see_status_in_server_list(env, monkeypatch):
    s, m, b = env
    monkeypatch.setattr('time.time', lambda: T + 1000)
    text, _ = asyncio.run(b.command(100, '/servers'))
    assert 'из России' not in text  # nothing known: say nothing
    s.save_reachability(report(T, s1='ok', s3='fail'))
    s.save_reachability(report(T + 900, s1='ok', s3='fail'))
    text, _ = asyncio.run(b.command(100, '/servers'))
    lines = {line.split(' · ')[0]: line for line in text.splitlines()[1:]}
    assert lines['Node 1'].endswith('доступен · из России подключается')
    assert lines['Node 3'].endswith('доступен · из России сейчас не подключается')
    assert lines['Node 2'].endswith('доступен')


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
