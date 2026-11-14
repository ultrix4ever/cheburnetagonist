"""Reachability of the nodes from a restricted network, as reported by infra/rf-probe.

The probe is a real VPN client on a vantage host; this module only presents its results:
to every user where a server is chosen, to the owner on request, and once per outage to
the people who hold keys on the affected server.
One vantage is one provider in one city: wording must never promise more than that.
"""
import time


def button(text, command):
    return {'type': 'callback', 'text': text, 'payload': command}


def profile_name(row):
    return row.get('label') or row['name'].split(' [max-')[0]

from .store import REACH_FRESH, Denied

STAGES = {'handshake': 'сервер не отвечает на VPN-подключение',
          'traffic': 'подключение устанавливается, но трафик не идёт',
          'http': 'подключение есть, но сайты через него не открываются'}


def span(seconds):
    minutes = max(int(seconds // 60), 0)
    if minutes < 60:
        return f'{minutes} мин'
    return f'{minutes // 60} ч' if minutes < 48 * 60 else f'{minutes // 1440} дн'


def ago(seconds):
    return span(seconds) + ' назад'


def why(row):
    parts = [STAGES.get(row['stage'], 'проверка не проходит')]
    if row['stage'] == 'handshake' and row['icmp'] is not None:
        parts.append('на ping отвечает' if row['icmp'] else 'на ping тоже не отвечает')
    if row['panel_online'] == 1:
        parts.append('панель сервер видит — похоже на блокировку, а не на поломку')
    elif row['panel_online'] == 0:
        parts.append('панель его тоже не видит — возможно, сервер выключен')
    return '; '.join(parts)


class ReachabilityMixin:

    def reach_note(self, sid, now=None):
        """Short status for the server list shown to every user; empty when nothing is known."""
        now = int(now if now is not None else time.time())
        rows = [r for r in self.store.reachability() if r['server_id'] == sid and now - r['checked'] <= REACH_FRESH]
        if not rows:
            return ''
        if sid in self.store.unreachable_servers(now):
            return 'из России сейчас не подключается'
        return 'из России подключается' if all(r['status'] == 'ok' for r in rows) else ''

    PROBE_SILENCE = 3600
    probe_silent = False

    async def probe_watch(self, now):
        """Tell the owner once when probe reports stop coming, and once when they resume."""
        rows = self.store.reachability()
        if not rows or not self.owner:
            return
        age = now - max(r['checked'] for r in rows)
        silent = age > self.PROBE_SILENCE
        if silent == self.probe_silent:
            return
        text = (f'Проверка доступности из России не присылает результаты уже {span(age)}. Отметки ✅/⛔ в боте '
                'пропадут через 3 часа после последней проверки. Что проверить: сервер проверки в России '
                'и расписание rf-probe-collect.' if silent else 'Проверка доступности из России снова присылает результаты.')
        try:
            await self.max.send(self.owner, text)
        except Exception:
            return  # retried on the next pass
        self.probe_silent = silent

    async def notify_unreachable(self, now=None):
        """Ask each owner once per outage whether to revoke their keys on a server Russia cannot reach.

        Nothing is revoked here: the buttons lead to the ordinary confirmed revocation.
        When no server at all is reachable the probe itself is the likelier fault: nobody is told.
        Returns the number of people messaged."""
        now = int(now if now is not None else time.time())
        await self.probe_watch(now)
        fresh = [r for r in self.store.reachability() if now - r['checked'] <= REACH_FRESH]
        if not any(r['status'] == 'ok' for r in fresh):
            return 0
        down = self.store.unreachable_servers(now)
        names = {s['id']: s['name'] for s in self.store.catalog(self.protocols)}
        sent = 0
        for row in fresh:
            sid, name = row['server_id'], names.get(row['server_id'], row['name'])
            if sid not in self.protocols:
                continue
            if sid in down and row['notified'] != 'fail':
                for uid, keys in self.store.owned_keys_on(sid).items():
                    listing = '\n'.join(f"#{k['id']} {profile_name(k)}" for k in keys)
                    await self.tell(uid, (
                        f'Сервер «{name}» перестал подключаться из России.\n'
                        'Обычно такая блокировка снимается сама примерно за 5 дней — тогда ключ снова заработает, '
                        'ничего перенастраивать не нужно.\n\n'
                        f'Ваши ключи на этом сервере:\n{listing}\n\n'
                        'Можно подождать. Или отзовите ключ: он вернётся в ваш лимит.'),
                        [[button(f"Отозвать #{k['id']} {profile_name(k)[:30]}", f"/revoke {k['id']}")] for k in keys[:8]]
                        + [[button('Оставить и подождать', '/my')]])
                    sent += 1
                self.store.reachability_notified(row['vantage'], sid, 'fail')
            elif row['status'] == 'ok' and row['notified'] == 'fail':
                for uid, keys in self.store.owned_keys_on(sid).items():
                    await self.tell(uid, f'Сервер «{name}» снова подключается из России. Ваши ключи на нём работают: '
                                    + ', '.join(f"#{k['id']} {profile_name(k)}" for k in keys) + '.', [[button('Мои ключи', '/my')]])
                    sent += 1
                self.store.reachability_notified(row['vantage'], sid, None)
        return sent

    def reach_mark(self, sid, now=None):
        """Prefix for a server name in a list or on a button; empty when nothing fresh is known."""
        note = self.reach_note(sid, now)
        return '' if not note else ('⛔ ' if 'не подключается' in note else '✅ ')

    def key_state(self, row, now=None):
        """State of an owned key as its user experiences it, not as the node reports it."""
        from .bot import profile_state
        if row['state'] == 'active' and row['server_id'] in self.store.unreachable_servers(now):
            return 'включён, но из России не подключается'
        return profile_state(row)

    def reach_legend(self, servers, now=None):
        if not any(self.reach_note(s['id'], now) for s in servers):
            return ''
        return '\n✅ — из России подключается, ⛔ — сейчас не подключается (проверка раз в 15 минут).'

    def reachability_report(self, now=None):
        now = int(now if now is not None else time.time())
        rows = self.store.reachability()
        if not rows:
            return 'Проверка доступности из России ещё не присылала результатов.'
        lines = ['Проверка доступности из России (настоящее VPN-подключение):']
        for row in rows:
            if row['status'] == 'ok':
                speed = f", отклик {row['rtt_ms']} мс" if row['rtt_ms'] is not None else ''
                state = 'подключается' + speed
            else:
                state = f"НЕ подключается уже {span(now - row['since'])}: {why(row)}"
            stale = ' (данные устарели)' if now - row['checked'] > REACH_FRESH else ''
            lines.append(f"• {row['name']} — {state}. Точка {row['vantage']}, {ago(now - row['checked'])}{stale}.")
        past = self.store.outages(10)
        if past:
            day = lambda at: time.strftime('%d.%m', time.gmtime(at))
            lines.append('Прошлые блокировки:')
            lines += [f"• {o['name']} — {span(o['ended'] - o['started'])}, {day(o['started'])}–{day(o['ended'])}" for o in past]
            lines.append('В среднем: ' + span(sum(o['ended'] - o['started'] for o in past) / len(past)) + '.')
        lines.append('Одна точка — это один провайдер: у других операторов картина может отличаться.')
        return '\n'.join(lines)

    def owner_only(self, uid):
        if not self.owner or uid != self.owner:
            raise Denied('Команда доступна только администратору.')
