import asyncio
import pytest
from test_v2 import env


@pytest.mark.parametrize('uid', [100, 999999])
@pytest.mark.parametrize('query', ['инструкция', ' Инструкция ', 'ИНСТРУКЦИЯ', '/instruction', '/guide'])
def test_instruction_does_not_require_registration_or_touch_vpn(env, uid, query):
    store, panel, bot, _ = env
    bot.guide_token = 'test-file-token'
    store.set_prompt(uid, 'reg_name', 0, ttl=3600, data='{}')
    reply, buttons = asyncio.run(bot.command(uid, query))
    assert reply['media'] == [{'type': 'file', 'payload': {'token': 'test-file-token'}}]
    assert panel.calls == []
    assert store.prompt(uid)['kind'] == 'reg_name'
    assert 'https://vpn.example.net/guide' not in reply['text']


def test_instruction_missing_token_is_clear_and_non_mutating(env):
    _, panel, bot, _ = env
    reply, _ = asyncio.run(bot.command(100, 'инструкция'))
    assert 'временно недоступна' in reply and panel.calls == []


@pytest.mark.parametrize('kind', ['message_created', 'message_callback'])
def test_instruction_event_sends_pdf_attachment(env, kind):
    _, panel, bot, _ = env
    bot.guide_token = 'test-file-token'
    sent = []
    class Messenger:
        async def send(self, uid, text, buttons=None, **kw):
            sent.append((uid, text, buttons, kw))
    bot.max = Messenger()
    event = {'update_type': kind, 'message': {'recipient': {'chat_type': 'dialog'},
             'sender': {'user_id': 100}, 'body': {'text': 'инструкция'}}}
    if kind == 'message_callback':
        event['callback'] = {'user': {'user_id': 100}, 'payload': '/instruction'}
    asyncio.run(bot.event(event))
    assert len(sent) == 1 and sent[0][0] == 100
    assert sent[0][3]['media'] == [{'type': 'file', 'payload': {'token': 'test-file-token'}}]
    assert panel.calls == []
