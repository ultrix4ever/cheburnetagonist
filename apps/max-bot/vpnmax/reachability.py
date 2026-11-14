"""Reachability of the nodes from a restricted network, as reported by infra/rf-probe.

The probe is a real VPN client on a vantage host; this module only presents its results:
to every user where a server is chosen, and to the owner on request. Nothing is pushed.
One vantage is one provider in one city: wording must never promise more than that.
"""
import time

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
    # Nothing is pushed to anyone: the status is shown where a server is being chosen, and in /rf.

    def reach_note(self, sid, now=None):
        """Short status for the server list shown to every user; empty when nothing is known."""
        now = int(now if now is not None else time.time())
        rows = [r for r in self.store.reachability() if r['server_id'] == sid and now - r['checked'] <= REACH_FRESH]
        if not rows:
            return ''
        if sid in self.store.unreachable_servers(now):
            return 'из России сейчас не подключается'
        return 'из России подключается' if all(r['status'] == 'ok' for r in rows) else ''

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
        lines.append('Одна точка — это один провайдер: у других операторов картина может отличаться.')
        return '\n'.join(lines)

    def owner_only(self, uid):
        if not self.owner or uid != self.owner:
            raise Denied('Команда доступна только администратору.')
