"""Taps are confirmed at once, impatient repeats do not multiply answers, delays are measured."""
import asyncio
import time

from fastapi.testclient import TestClient

from test_v2 import Panel, data
from vpnmax.app import create_app
from vpnmax.bot import Bot
from vpnmax.store import Store

SECRET = 's' * 40


class Max:
    def __init__(self):
        self.acked, self.sent = [], []

    async def acknowledge(self, callback_id):
        self.acked.append(callback_id)

    async def send(self, uid, text, buttons=None, forward=None, media=None):
        self.sent.append((uid, text))


def tap(payload, callback_id, uid=100, at=None):
    return {'update_type': 'message_callback', 'timestamp': at or int(time.time() * 1000),
            'callback': {'callback_id': callback_id, 'payload': payload, 'user': {'user_id': uid}},
            'message': {'recipient': {'chat_type': 'dialog'}}}


def test_repeated_tap_is_dropped_while_the_first_waits_but_accepted_later(tmp_path):
    s = Store(tmp_path / 'db')
    assert s.enqueue(tap('/start', 'c1')) is True
    assert s.enqueue(tap('/start', 'c2')) is False
    assert s.enqueue(tap('/my', 'c3')) is True and s.enqueue(tap('/start', 'c4', uid=200)) is True
    eid, _ = s.next_event()
    assert s.enqueue(tap('/start', 'c5')) is False  # still being handled
    s.event_done(eid)
    assert s.enqueue(tap('/start', 'c6')) is True


def test_timings_are_recorded_without_content(tmp_path):
    s = Store(tmp_path / 'db')
    s.enqueue(tap('/start', 'c1', at=int(time.time() * 1000) - 4000))
    eid, _ = s.next_event()
    s.event_done(eid)
    (row,) = s.timings()
    assert 3900 <= row['delivery'] <= 6000 and row['queue'] >= 0 and row['handle'] >= 0
    with s.connect() as db:
        assert db.execute('SELECT body FROM inbox').fetchone()[0] == '{}'


def test_webhook_acknowledges_every_tap_at_once_and_answers_once(tmp_path):
    s = Store(tmp_path / 'db')
    s.save_person(data())
    m = Max()
    b = Bot(s, Panel(), m, {1: 'awg', 2: 'awg', 3: 'awg3', 4: 'awg'}, owner=100)
    app = create_app(s, b, SECRET)
    client = TestClient(app)  # not entered as a context: no worker, the queue stays untouched
    for cid in ('c1', 'c2'):
        assert client.post('/webhook/max', json=tap('/start', cid), headers={'X-Max-Bot-Api-Secret': SECRET}).status_code == 200
    assert sorted(m.acked) == ['c1', 'c2']
    with s.connect() as db:
        assert db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 1
    text, _ = asyncio.run(b.command(100, '/lag'))
    assert 'Замеров пока нет' in text
