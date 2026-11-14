import asyncio
import json
import re
import time

from . import billing
from .api import RemoteError
from .billing_bot import BillingMixin, key_billing_note
from .reachability import ReachabilityMixin
from .registration import GUIDE_HINT, RegistrationMixin, guide_button
from .store import Denied


def definite(exc):
    """True when the panel provably rejected the request before changing anything."""
    return isinstance(exc, RemoteError) and exc.definite


def button(text, command):
    return {'type': 'callback', 'text': text, 'payload': command}


KEY_NAME = re.compile(r'[\w .,()-]{1,40}')
NAME_HINT = 'До 40 символов: буквы, цифры, пробел и . , - ( ).'


def profile_name(row):
    return row['name'].split(' [max-')[0]


def profile_state(row):
    return {'active': 'работает', 'paused': 'на паузе', 'missing': 'нужна сверка',
            'revoking': 'отзывается', 'revoked': 'отозван'}.get(row['state'], row['state'])


class Bot(RegistrationMixin, BillingMixin, ReachabilityMixin):
    ALERT_INTERVAL = 3600

    def __init__(self, store, panel, messenger, protocols, owner=None, payment_details=None, contact_secret=None,
                 payment_url=None, payment_phone=None, guide_token=None):
        self.store, self.panel, self.max = store, panel, messenger
        self.guide_token = guide_token
        self.payment_url, self.payment_phone = payment_url, payment_phone
        self.contact_secret = contact_secret
        self.protocols = protocols
        self.owner = owner
        self.payment_details = payment_details
        self.panel_status = {'ok': None, 'error': None, 'since': None}
        self.alerted = 0

    async def panel_seen(self, exc=None):
        """Track panel health; a rejected API key otherwise breaks every user silently."""
        if exc is None:
            if self.panel_status['ok'] is False and self.owner and self.alerted:
                await self.notify('Связь бота с панелью восстановлена.')
                self.alerted = 0
            self.panel_status = {'ok': True, 'error': None, 'since': int(time.time())}
            return
        if not (isinstance(exc, RemoteError) and exc.auth):
            return
        if self.panel_status['ok'] is not False:
            self.panel_status = {'ok': False, 'error': str(exc), 'since': int(time.time())}
        if self.owner and time.time() - self.alerted >= self.ALERT_INTERVAL:
            self.alerted = time.time()
            await self.notify(f'Панель отклоняет API-ключ бота ({exc}). Выдача, отзыв и перевыпуск ключей не работают. '
                              'Смена пароля администратора в панели отзывает все API-ключи: выпустите новый ключ '
                              'и обновите PANEL_API_KEY (deploy/rotate_panel_key.sh).')

    async def notify(self, text, buttons=None):
        try:
            await self.max.send(self.owner, text, buttons)
        except Exception:
            pass

    def limit_reached(self, person, used, limit):
        if person['free']:
            return (f'Достигнут общий лимит: {used} из {limit}. Можно отозвать старый ключ или перевыпустить его '
                    'вместо создания дополнительного.'), self.action_buttons()[1:]
        return (f'Все оплаченные ключи использованы: {used} из {limit}. Оплатите ещё ключи — их можно будет получить '
                'сразу после подтверждения. Или отзовите ненужный ключ.'), [
                    [button('Оплатить ключи', '/pay')], [button('Отозвать старый ключ', '/revoke')], [button('Главное меню', '/start')]]

    def main_menu(self, uid):
        used, limit = self.store.usage(uid)
        status = self.limit_status(self.store.person(uid))
        return (f'Ваши ключи: {used} из {limit} на всех серверах.\n' + (status + '\n' if status else '') +
                'Что хотите сделать: получить новый ключ, отозвать старый ключ или перевыпустить старый ключ?'
                + '\n' + GUIDE_HINT), self.action_buttons()

    async def deliver(self, uid, pid, text):
        """Issued key goes straight to the user: status, the bare key, then the main menu."""
        menu, buttons = self.main_menu(uid)
        try:
            row = self.store.owned(uid, pid)
            link = (await self.panel.config(row)).get('config_amnezia', '')
            if not link.startswith('vpn://'):
                raise ValueError('Invalid export')
        except Exception:
            # The key exists; a failed export must not look like a failed issue.
            return [text + '\nНе удалось сразу выгрузить ключ. Откройте «Мои ключи» → ключ → «Показать ключ».', menu], buttons
        return [text + '\nКлюч — в следующем сообщении. Скопируйте его целиком и импортируйте в AmneziaVPN.', link, menu], buttons

    @staticmethod
    def action_buttons():
        return [[button('Получить новый ключ', '/new')],
                [button('Отозвать старый ключ', '/revoke')],
                [button('Перевыпустить старый ключ', '/reissue')],
                [button('Оплатить ключи', '/pay')],
                [button('Мои ключи', '/my')],
                [guide_button()]]

    def existing_servers(self, uid, action):
        rows = [r for r in self.store.profiles(uid) if r['state'] in ('active', 'paused') and r['server_id'] in self.protocols]
        catalog = self.store.catalog(self.protocols)
        buttons = []
        for server in catalog:
            count = sum(r['server_id'] == server['id'] for r in rows)
            if count:
                buttons.append([button(f"{self.reach_mark(server['id'])}{server['name'][:48]} · {count}", f"/owned {action} {server['id']}")])
        buttons.append([button('Главное меню', '/start')])
        return ('На каком сервере выбрать старый ключ?' if rows else
                'Нет доступных для этой операции ключей. Старые профили привязывает администратор; отсутствующие на сервере требуют сверки.'), buttons

    async def sync(self):
        # Partial inventory failure must not mark another server's profiles missing.
        failures = []
        for sid, proto in self.protocols.items():
            try:
                await self.sync_server(sid)
            except Exception:
                failures.append(sid)
        return failures

    async def sync_server(self, sid):
        try:
            clients = await asyncio.wait_for(self.panel.clients(sid), timeout=15)
        except Exception as exc:
            await self.panel_seen(exc)
            raise
        await self.panel_seen()
        self.store.inventory(sid, self.protocols[sid], clients)

    async def event(self, event):
        kind = event.get('update_type')
        if kind == 'message_created':
            msg = event.get('message', {})
            if msg.get('recipient', {}).get('chat_type') != 'dialog':
                return
            sender = msg.get('sender', {})
            text = msg.get('body', {}).get('text', '') or ''
            attachments = msg.get('body', {}).get('attachments') or []
            media = [a for a in attachments if a.get('type') in ('image', 'file')]
            contact = next((a for a in attachments if a.get('type') == 'contact'), None)
        elif kind == 'bot_started':
            sender = event.get('user', {})
            text = '/start ' + (event.get('payload') or '')
        elif kind == 'message_callback':
            if event.get('message', {}).get('recipient', {}).get('chat_type') != 'dialog':
                return
            sender = event.get('callback', {}).get('user', {})
            text = event.get('callback', {}).get('payload', '')
        else:
            return
        uid = sender.get('user_id')
        if type(uid) is not int or uid <= 0 or sender.get('is_bot'):
            return
        try:
            if kind == 'message_created' and contact:
                reply, buttons = await self.contact(uid, contact)
            elif kind == 'message_created' and media:
                reply, buttons = await self.receipt(uid, event['message'].get('body', {}).get('mid'), media)
            else:
                reply, buttons = await self.command(uid, text)
        except Denied as exc:
            reply, buttons = (str(exc) or 'Нет доступа к профилю или операции.') + '\nВаш MAX ID: ' + str(uid), None
        except ValueError:
            reply, buttons = 'Неверная команда. Нажмите /start.', None
        except Exception as exc:
            await self.panel_seen(exc)
            if definite(exc):
                reply, buttons = 'Сервис ключей сейчас недоступен. Ничего не изменено — попробуйте позже.', None
            else:
                # An operation may be uncertain; don't claim rollback or retry it.
                reply, buttons = 'Не удалось завершить запрос. Проверьте «Мои ключи»; если была операция изменения — нужна сверка администратором. Не создавайте её повторно.', None
        if kind == 'message_callback' and hasattr(self.max, 'acknowledge'):
            try:
                await self.max.acknowledge(event['callback']['callback_id'])
            except Exception:
                pass
        parts = reply if isinstance(reply, list) else [reply]
        for i, part in enumerate(parts):
            if i:
                await asyncio.sleep(0.6)  # MAX allows 2 messages/second per dialog
            if isinstance(part, dict):
                await self.max.send(uid, part['text'], buttons if i == len(parts) - 1 else None, media=part['media'])
            else:
                await self.max.send(uid, part, buttons if i == len(parts) - 1 else None)

    async def command(self, uid, text):
        args = text.strip().split()
        cmd = args[0].lower() if args else '/start'
        if cmd in ('инструкция', '/instruction', '/guide'):
            if not self.guide_token:
                return 'PDF-инструкция временно недоступна. Попробуйте позже.', [[guide_button()]]
            return {'text': 'Инструкция: бот MAX, оплата, VPN-ключи, AmneziaVPN и DefaultVPN для iPhone.',
                    'media': [{'type': 'file', 'payload': {'token': self.guide_token}}]}, [[button('Главное меню', '/start')]]
        if cmd == '/id':
            return f'Ваш MAX ID: {uid}', None
        if cmd in ('/payok', '/payno') and len(args) == 2:
            if not self.owner or uid != self.owner:
                raise Denied('Команда доступна только администратору.')
            return await self.owner_command(cmd, args)
        if cmd == '/rf':
            self.owner_only(uid)
            return self.reachability_report(), [[button('Главное меню', '/start')]]
        if cmd == '/start' and len(args) == 2:
            self.store.claim(uid, args[1])
        if not self.store.person_any(uid):
            return await self.unregistered(uid, cmd, args)
        person = self.store.person(uid)
        allowed = [s for s in self.store.catalog(self.protocols) if s['id'] in json.loads(person['allowed_servers'])]
        if not cmd.startswith('/'):
            # Free text is only meaningful as the answer to a question the bot just asked.
            prompt = self.store.prompt(uid)
            if prompt and prompt['kind'] == 'slots':
                return await self.slots_answer(uid, ' '.join(args))
            if prompt and prompt['kind'] == 'key_name':
                name = ' '.join(args)
                if not KEY_NAME.fullmatch(name):
                    return f'Такое название не подойдёт. {NAME_HINT}\nВведите название ещё раз.', [
                        [button('Без названия', f"/new {prompt['server_id']} Ключ")], [button('Главное меню', '/start')]]
                self.store.clear_prompt(uid)
                return await self.command(uid, f"/new {prompt['server_id']} {name}")
        elif not (cmd == '/new' and len(args) == 2):
            # Any button press abandons a pending question, so stray text later is not misread.
            self.store.clear_prompt(uid)
        if cmd in ('/start', '/help'):
            return self.main_menu(uid)
        handled = await self.billing_command(uid, person, cmd, args)
        if handled:
            return handled
        if cmd == '/config':
            # Old messages carry «Получить ключ» buttons with this payload; users mean «new key».
            return await self.command(uid, '/new')
        if cmd in ('/revoke', '/reissue') and len(args) == 1:
            return self.existing_servers(uid, cmd[1:])
        if cmd == '/owned' and len(args) in (3, 4):
            action, sid = args[1], int(args[2])
            if action not in ('revoke', 'reissue'):
                raise Denied('Неизвестное действие.')
            page = max(0, int(args[3])) if len(args) == 4 else 0
            rows = [r for r in self.store.profiles(uid) if r['server_id'] == sid and r['state'] in ('active', 'paused')]
            if not rows or sid not in self.protocols:
                raise Denied('Ваших доступных ключей на этом сервере нет.')
            buttons = [[button(f"#{r['id']} {profile_name(r)[:50]} · {profile_state(r)}", f"/{action} {r['id']}")] for r in rows[page * 8:(page + 1) * 8]]
            if page:
                buttons.append([button('Назад', f'/owned {action} {sid} {page-1}')])
            if len(rows) > (page + 1) * 8:
                buttons.append([button('Далее', f'/owned {action} {sid} {page+1}')])
            buttons.append([button('Другой сервер', '/' + action)])
            return 'Выберите старый ключ, который нужно ' + ('отозвать.' if action == 'revoke' else 'перевыпустить.'), buttons
        if cmd in ('/servers', '/new') and len(args) == 1:
            if cmd == '/new':
                used, limit = self.store.usage(uid)
                if used >= limit:
                    return self.limit_reached(person, used, limit)
            status = lambda s: 'доступен' if s['online'] else ('не отвечает' if s['online'] == 0 else 'статус неизвестен')
            lines = [f"{self.reach_mark(s['id'])}{s['name']} · {status(s)}" + (f" · {self.reach_note(s['id'])}" if self.reach_note(s['id']) else '')
                     for s in allowed]
            return ('На каком сервере получить новый ключ?\n' + '\n'.join(lines) + self.reach_legend(allowed)
                    if allowed else 'Нет разрешённых серверов. Обратитесь к администратору.'), [
                        [button(self.reach_mark(s['id']) + s['name'][:48], f"/new {s['id']}")] for s in allowed] + [[button('Главное меню', '/start')]]
        if cmd == '/device' and len(args) == 2:
            # Old messages may still contain device-selector buttons. Never issue on stale intent.
            if int(args[1]) not in [s['id'] for s in allowed]:
                raise Denied('Этот сервер не разрешён.')
            return 'Меню обновлено. Выберите действие.', self.action_buttons()
        if cmd == '/my':
            page = max(0, int(args[1])) if len(args) == 2 else 0
            rows = self.store.profiles(uid)
            used, limit = self.store.usage(uid)
            lines, buttons = [f'Занято {used} из {limit} ключей на всех серверах.'], []
            if self.limit_status(person):
                lines.append(self.limit_status(person))
            names = {s['id']: s['name'] for s in self.store.catalog(self.protocols)}
            shown = rows[page * 8:(page + 1) * 8]
            for row in shown:
                server = names.get(row['server_id'], str(row['server_id']))
                note = key_billing_note(row, person['free'])
                mark = self.reach_mark(row['server_id'])
                lines.append(f"{mark}#{row['id']} {profile_name(row)} · {server} · {profile_state(row)}" + (f' · {note}' if note else ''))
                # Several keys may share one name: the number and server tell them apart.
                buttons.append([button(f"{mark}#{row['id']} {profile_name(row)[:24]} · {server[:28]}", f"/key {row['id']}")])
            legend = self.reach_legend([{'id': r['server_id']} for r in shown])
            if legend:
                lines.append(legend.strip())
            if not rows:
                lines.append('Привязанных ключей пока нет.')
            if page:
                buttons.append([button('Назад', f'/my {page-1}')])
            if len(rows) > (page + 1) * 8:
                buttons.append([button('Далее', f'/my {page+1}')])
            replacement = self.store.open_replacement(uid)
            if replacement:
                lines.append('Есть незавершённая замена ключа.')
                buttons.append([button('Текущая замена', '/replacement ' + replacement['id'])])
            buttons.append([button('Главное меню', '/start')])
            return '\n'.join(lines), buttons
        if cmd == '/new' and len(args) >= 2:
            sid = int(args[1])
            if sid not in self.protocols or sid not in json.loads(person['allowed_servers']):
                raise Denied('Этот сервер не разрешён.')
            if len(args) == 2:
                used, limit = self.store.usage(uid)
                if used >= limit:
                    return self.limit_reached(person, used, limit)
                self.store.set_prompt(uid, 'key_name', sid)
                return ('Введите название ключа.\nНапример: «Телефон».'), [
                    [button('Без названия', f'/new {sid} Ключ')], [button('Главное меню', '/start')]]
            name = ' '.join(args[2:])
            if not KEY_NAME.fullmatch(name):
                raise ValueError()
            # Within the paid limit keys are issued at once; start_operation re-checks it atomically.
            pid = await self.issue_key(uid, sid, name)
            return await self.deliver(uid, pid, f'Ключ «{name}» (#{pid}) создан.')
        if cmd in ('/replacement', '/switch', '/cancel') and len(args) == 2:
            if cmd == '/replacement':
                return self.replacement_menu(uid, args[1])
            return await self.finish_replacement(uid, args[1], commit=cmd == '/switch')
        if cmd in ('/reissue', '/compromised', '/move') and len(args) in (2, 3):
            row = self.store.owned(uid, int(args[1]))
            if self.store.open_replacement(uid):
                return self.replacement_menu(uid, self.store.open_replacement(uid)['id'])
            if row['state'] not in ('active', 'paused'):
                raise Denied('Состояние старого ключа требует сверки администратором.')
            if self.unpaid(person, row):
                return 'Этот ключ не оплачен. Сначала оплатите его — затем можно перевыпустить.', [[button('Оплатить ключи', '/pay')]]
            if cmd in ('/move', '/reissue') and len(args) == 2:
                targets = [s for s in allowed if cmd == '/reissue' or s['id'] != row['server_id']]
                return (f"Старый ключ: #{row['id']} {profile_name(row)}.\nНа каком сервере создать новый вместо старого?"
                        + self.reach_legend(targets) if targets else 'Нет разрешённых серверов для нового ключа. Обратитесь к администратору.'), [
                    [button(self.reach_mark(s['id']) + s['name'][:48] + (' (текущий)' if s['id'] == row['server_id'] else ''), f"{cmd} {row['id']} {s['id']}")] for s in targets] + [[button('Главное меню', '/start')]]
            sid = int(args[2]) if cmd in ('/move', '/reissue') else row['server_id']
            if sid not in [s['id'] for s in allowed]:
                raise Denied('Целевой сервер не разрешён.')
            mode = ('move' if sid != row['server_id'] else 'reissue') if cmd == '/reissue' else cmd[1:]
            token = self.store.confirm(uid, row['id'], mode + ':' + str(sid))
            text = ('Старый ключ будет отозван НЕМЕДЛЕННО, ещё до выпуска нового. При ошибке выдачи старый доступ не восстановится.'
                    if mode == 'compromised' else
                    'Будет создан новый ключ, и сразу после этого старый будет отозван. Импортируйте новый ключ в AmneziaVPN: старый перестанет работать.')
            destination = next(s['name'] for s in allowed if s['id'] == sid)
            text = f"Старый ключ: #{row['id']} {profile_name(row)}.\nНовый сервер: {destination}.\n" + text
            return text, [[button('Подтвердить', '/confirm ' + token)]]
        if cmd in ('/key', '/export', '/pause', '/resume', '/revoke') and len(args) == 2:
            row = self.store.owned(uid, int(args[1]), pending=cmd == '/key')
            pid = row['id']
            if row['state'] == 'revoking':
                return ('Этот ключ уже отзывается: сервер пока не подтвердил удаление. Отзыв завершится автоматически, '
                        'как только сервер ответит; пользоваться им нельзя.'), [[button('Мои ключи', '/my')]]
            if row['state'] == 'missing':
                return 'Профиль не найден при последней синхронизации. Нужна сверка администратором; слот пока не освобождён.', None
            if cmd == '/key':
                server = next((s['name'] for s in self.store.catalog(self.protocols) if s['id'] == row['server_id']), str(row['server_id']))
                reach = self.reach_note(row['server_id'])
                return f"#{pid} {profile_name(row)} · {server} · {profile_state(row)}" + (
                    f"\n{self.reach_mark(row['server_id'])}Сервер {reach}." if reach else '') + (
                    '\nМожно перевыпустить ключ на другом сервере.' if 'не подключается' in reach else ''), [
                    [button('Показать ключ', f'/export {pid}')],
                    [button('Пауза', f'/pause {pid}'), button('Возобновить', f'/resume {pid}')],
                    [button('Перевыпустить', f'/reissue {pid}')],
                    [button('Ключ скомпрометирован', f'/compromised {pid}')],
                    [button('Отозвать', f'/revoke {pid}')]]
            if cmd == '/export':
                if not row['has_config']:
                    return 'Исходный конфиг не сохранён в панели. Старый доступ продолжает работать; восстановить его приватный ключ с сервера нельзя.', None
                config = await self.panel.config(row)
                link = config['config_amnezia']
                if not link.startswith('vpn://'):
                    raise ValueError('Invalid export')
                # The key goes alone in its own message so it can be copied with one tap.
                return ['Ключ — в следующем сообщении. Скопируйте его целиком и импортируйте в AmneziaVPN.', link], None
            action = cmd[1:]
            if action == 'resume' and self.unpaid(person, row):
                return 'Ключ остановлен из-за неоплаты. Оплатите его — он включится сразу после подтверждения.', [[button('Оплатить ключи', '/pay')]]
            token = self.store.confirm(uid, pid, action)
            warning = 'Отзыв необратим: этот ключ перестанет работать.' if action == 'revoke' else 'Изменится доступ выбранного профиля.'
            return f"{warning}\nПрофиль #{pid}: {profile_name(row)}", [[button('Подтвердить', '/confirm ' + token)]]
        if cmd == '/confirm' and len(args) == 2:
            confirmation = self.store.consume(uid, args[1])
            row = self.store.owned(uid, confirmation['profile_id'])
            action = confirmation['action']
            if (action == 'resume' or ':' in action) and self.unpaid(person, row):
                # The key may have expired between the question and the confirmation.
                return 'Ключ не оплачен. Сначала оплатите его.', [[button('Оплатить ключи', '/pay')]]
            if ':' in action:
                mode, sid = action.split(':', 1)
                sid = int(sid)
                if sid not in self.protocols:
                    raise Denied('Сервер не настроен.')
                return await self.begin_replacement(uid, row, sid, mode)
            oid = self.store.start_operation(uid, action, profile_id=row['id'], server_id=row['server_id'])
            if action == 'revoke':
                return await self.revoke_profile(uid, oid, row)
            try:
                # Recheck ownership immediately before invoking scoped node API.
                self.store.owned(uid, row['id'])
                await self.panel.mutate(action, row)
                self.store.set_state(row['id'], {'pause': 'paused', 'resume': 'active'}[action])
                self.store.finish(oid, 'done')
            except Exception as exc:
                self.store.finish(oid, 'failed' if definite(exc) else 'unknown')
                raise
            return 'Готово.', [[button('Мои ключи', '/my')]]
        return 'Выберите действие в меню. Ваш ID можно узнать командой /id.', self.action_buttons()

    async def issue_key(self, uid, sid, name):
        oid = self.store.start_operation(uid, 'issue', server_id=sid, label=name)
        # Unique correlation label permits reconciliation after ambiguous failure.
        label = name + ' [max-' + oid[:12] + ']'
        try:
            result = await self.panel.issue(sid, label)
            pid = self.store.issued(uid, sid, self.protocols[sid], result['client'])
            self.store.finish(oid, 'done')
        except Exception as exc:
            # A provable rejection must not lock the person out pending manual reconciliation.
            self.store.finish(oid, 'failed' if definite(exc) else 'unknown')
            raise
        return pid

    async def revoke_durable(self, row, uid, reason):
        """Persist the intent before touching the node: an unreachable server must not keep a key usable.
        Returns True once the node confirmed removal, False while the revocation stays queued."""
        self.store.queue_revocation(row['id'], uid, reason)
        try:
            await asyncio.wait_for(self.panel.mutate('revoke', row), timeout=20)
        except Exception as exc:
            self.store.revocation_failed(row['id'], exc)
            return False
        self.store.confirm_revocation(row['id'])
        return True

    async def revoke_profile(self, uid, oid, row):
        self.store.owned(uid, row['id'], pending=True)
        if not await self.revoke_durable(row, uid, 'user'):
            self.store.finish(oid, 'queued')
            return ('Сервер сейчас не отвечает, поэтому ключ поставлен в очередь на отзыв: он считается недействительным '
                    'и будет удалён автоматически, как только сервер ответит. Слот уже освобождён.'), [
                        [button('Мои ключи', '/my')]]
        self.store.finish(oid, 'done')
        return 'Ключ отозван.', [[button('Мои ключи', '/my')]]

    async def process_revocations(self, retry_after=60):
        """Retry queued revocations. DELETE is idempotent and absence on the node is proof, so this
        is the one mutation safe to repeat automatically after an unreachable or ambiguous node."""
        confirmed, present, down = 0, {}, set()
        for row in self.store.due_revocations(retry_after):
            sid = row['server_id']
            if sid in down:
                # One dead node must not hold the worker lock for 15 s per queued key.
                self.store.revocation_failed(row['id'], RemoteError('Server skipped after failure in this pass'))
                continue
            try:
                if sid not in present:
                    present[sid] = {c['public_key'] for c in await asyncio.wait_for(self.panel.clients(sid), timeout=15)}
                if row['public_key'] in present[sid]:
                    await asyncio.wait_for(self.panel.mutate('revoke', row), timeout=20)
                    present[sid].discard(row['public_key'])
                self.store.confirm_revocation(row['id'])
                confirmed += 1
            except Exception as exc:
                present.pop(sid, None)
                down.add(sid)
                self.store.revocation_failed(row['id'], exc)
                await self.panel_seen(exc)
        return confirmed

    async def begin_replacement(self, uid, old, sid, mode):
        rid = self.store.start_replacement(uid, old['id'], sid, mode)
        try:
            if mode == 'compromised':
                # Explicitly chosen emergency path: do not preserve a stolen credential.
                dead = await self.revoke_durable(old, uid, 'compromised')
            label = old['name'].split(' [max-')[0][:40] + ' [max-' + rid[:12] + ']'
            result = await self.panel.issue(sid, label)
            pid = self.store.save_replacement_client(uid, rid, sid, self.protocols[sid], result['client'])
            if mode == 'compromised':
                self.store.replacement_state(rid, 'committed')
                return await self.deliver(uid, pid, f'Старый ключ отозван. Новый ключ #{pid} создан.' if dead else
                                          f'Новый ключ #{pid} создан. Старый ключ сервер пока не подтвердил: он в очереди на отзыв и будет удалён автоматически.')
            # No confirmation window: the old key is revoked as soon as the new one is verified.
            return await self.finish_replacement(uid, rid, commit=True)
        except Exception as exc:
            # finish_replacement records its own outcome; only a failure before it is decided here.
            if self.store.replacement(uid, rid)['state'] == 'creating':
                self.store.replacement_state(rid, 'failed' if definite(exc) else 'unknown')
            raise

    def replacement_menu(self, uid, rid):
        row = self.store.replacement(uid, rid)
        if row['state'] != 'ready':
            return 'Замена требует сверки администратором. Новые изменения временно запрещены.', None
        # Reached only by a replacement left half-finished by a crash; new ones switch without asking.
        return (f"Операция не завершилась: новый профиль #{row['new_id']} создан, старый #{row['old_id']} ещё не отозван.\n"
                'Получите новый ключ и нажмите «Переключился», чтобы отозвать старый, либо «Отменить», чтобы убрать новый.'), [
                    [button('Показать новый ключ', f"/export {row['new_id']}")],
                    [button('Переключился', '/switch ' + rid), button('Отменить замену', '/cancel ' + rid)]]

    def replacement_profiles(self, row):
        # Used by the expiry/admin path even if the person has been blocked.
        with self.store.connect() as db:
            person = db.execute('SELECT id FROM people WHERE max_id=?', (row['max_id'],)).fetchone()
            old = db.execute('SELECT * FROM profiles WHERE id=? AND person_id=?', (row['old_id'], person['id'] if person else -1)).fetchone()
            new = db.execute('SELECT * FROM profiles WHERE id=? AND person_id=?', (row['new_id'], person['id'] if person else -1)).fetchone()
        if not old or not new:
            raise Denied('Владелец замены изменён; нужна сверка.')
        return dict(old), dict(new)

    async def finish_replacement(self, uid, rid, commit=False, automatic=False):
        if not automatic:
            self.store.person(uid)
        row = self.store.replacement(uid, rid)
        if row['state'] != 'ready':
            raise Denied('Замена уже завершена или требует сверки.')
        if commit and row['expires'] <= time.time():
            raise Denied('Срок подтверждения истёк; новый профиль будет отозван.')
        old, new = self.replacement_profiles(row)
        if new['state'] not in ('active', 'paused') or old['state'] == 'revoked':
            raise Denied('Состояние профилей изменено; нужна сверка.')
        self.store.replacement_state(rid, 'committing' if commit else 'canceling')
        try:
            if commit:
                # Do not revoke the old access until target presence/export is verified.
                clients = await self.panel.clients(new['server_id'])
                if not any(c['public_key'] == new['public_key'] and not c.get('paused') for c in clients):
                    raise Denied('Новый профиль отсутствует или поставлен на паузу.')
                export = await self.panel.config(new)
                if not export.get('config_amnezia', '').startswith('vpn://'):
                    raise ValueError('Invalid replacement export')
            target = old if commit else new
            # The target server may be a different one; a queued revocation still guarantees removal.
            dead = await self.revoke_durable(target, row['max_id'], 'replacement')
            self.store.replacement_state(rid, 'committed' if commit else 'cancelled')
        except Exception as exc:
            # A rejected verification read changed nothing: back to 'ready' so expiry can clean up.
            self.store.replacement_state(rid, 'ready' if definite(exc) else 'unknown')
            raise
        if commit:
            text = (f"Готово. Новый ключ #{new['id']} выпущен, старый #{old['id']} отозван." if dead else
                    f"Новый ключ #{new['id']} выпущен. Сервер старого ключа не ответил: он в очереди на отзыв и будет удалён автоматически.")
            return await self.deliver(row['max_id'], new['id'], text)
        return ('Замена отменена. Новый ключ отозван, старый сохранён.' if dead else
                'Замена отменена, старый ключ сохранён. Новый в очереди на отзыв: сервер не ответил.'), [[button('Мои ключи', '/my')]]

    async def expire_replacements(self):
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM replacements WHERE state='ready' AND expires<=?", (int(time.time()),))]
        for row in rows:
            try:
                await self.finish_replacement(row['max_id'], row['id'], automatic=True)
            except Exception as exc:
                # Preserve uncertain state and charge the slot; never retry a remote mutation.
                # A provable rejection left the row 'ready', so the next pass simply tries again.
                if not definite(exc):
                    self.store.replacement_state(row['id'], 'unknown')

    async def revoke_all(self, person_id, previous=None, one_step=False):
        previous = previous or {}
        failed = list(previous.get('unconfirmed', []))
        confirmed = previous.get('revoked', 0)
        with self.store.connect() as db:
            person = db.execute('SELECT * FROM people WHERE id=?', (person_id,)).fetchone()
            if not person:
                raise Denied('Пользователь не найден.')
            db.execute('UPDATE people SET active=0 WHERE id=?', (person_id,))
            rows = [dict(r) for r in db.execute("SELECT * FROM profiles WHERE person_id=? AND state!='revoked'", (person_id,)) if r['id'] not in failed]
            self.store.log(db, None, 'revoke_all_started', person_id)
        batch = rows[:1] if one_step else rows
        for row in batch:
            # Queued first: a node that is down must not leave a blocked person's key alive.
            if await self.revoke_durable(row, person['max_id'], 'admin'):
                confirmed += 1
            else:
                failed.append(row['id'])
        with self.store.connect() as db:
            uncertain = [r[0] for r in db.execute("SELECT id FROM operations WHERE max_id=? AND state IN ('pending','unknown')", (person['max_id'],))]
            unknown_replacements = [r[0] for r in db.execute("SELECT id FROM replacements WHERE max_id=? AND state='unknown' AND new_id IS NULL", (person['max_id'],))]
            remaining = len(rows) - len(batch)
            if not remaining:
                db.execute("UPDATE replacements SET state=? WHERE max_id=? AND state IN ('creating','ready','committing','canceling','unknown')", ('unknown' if failed or uncertain or unknown_replacements else 'cancelled', person['max_id']))
                self.store.log(db, None, 'revoke_all_partial' if failed or uncertain or unknown_replacements else 'revoke_all_done', person_id)
        return {'revoked': confirmed, 'unconfirmed': failed, 'remaining': remaining,
                'uncertain_operations': uncertain + unknown_replacements, 'bot_blocked': True}
