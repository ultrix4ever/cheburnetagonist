"""A user names their own keys in the bot; the node name is not touched."""
import asyncio

import pytest

from test_v2 import env  # noqa: F401
from vpnmax.store import Denied


def texts(buttons):
    return [b['text'] for row in buttons for b in row]


def test_rename_from_key_card_survives_inventory_sync(env):
    s, p, b, pid = env
    _, buttons = asyncio.run(b.command(100, f'/key {pid}'))
    assert 'Переименовать' in texts(buttons)
    text, _ = asyncio.run(b.command(100, f'/rename {pid}'))
    assert '«Original»' in text and p.calls == []
    text, _ = asyncio.run(b.command(100, 'Ноутбук'))
    assert text.startswith(f'#{pid} Ноутбук ·')
    asyncio.run(b.sync())  # the node still reports the old name
    assert 'Ноутбук' in asyncio.run(b.command(100, '/my'))[0]
    assert not any(c[0] in ('issue', 'pause', 'resume', 'revoke') for c in p.calls)


def test_bad_name_is_asked_again_and_button_press_cancels(env):
    s, p, b, pid = env
    asyncio.run(b.command(100, f'/rename {pid}'))
    text, _ = asyncio.run(b.command(100, 'bad/name'))
    assert 'не подойдёт' in text
    asyncio.run(b.command(100, '/my'))  # any button abandons the question
    asyncio.run(b.command(100, 'Ноутбук'))
    assert 'Original' in asyncio.run(b.command(100, '/my'))[0]


def test_cannot_rename_a_foreign_key(env):
    s, p, b, pid = env
    with pytest.raises(Denied):
        asyncio.run(b.command(200, f'/rename {pid}'))
    with pytest.raises(Denied):
        s.set_label(200, pid, 'Mine')
