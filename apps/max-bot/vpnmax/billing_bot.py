"""Paid key slots: purchase dialog, owner confirmation and limit enforcement. Mixed into Bot."""
import asyncio
import time

from . import billing
from .store import Denied


def button(text, command):
    return {'type': 'callback', 'text': text, 'payload': command}


def key_billing_note(row, free=False):
    if free:
        return ''
    if row.get('billing_state'):
        return 'остановлен: не оплачен'
    if row.get('legacy'):
        return 'без оплаты'
    return ''


class BillingMixin:
    payment_details = None
    payment_url = None
    payment_phone = None

    # --- user side ---------------------------------------------------------------------------

    def unpaid(self, person, row):
        return not person['free'] and bool(row.get('billing_state'))

    def pay_buttons(self):
        return [[button('Оплатить ключи', '/pay')]]

    def limit_status(self, person):
        """One line about what is paid; empty for free people."""
        if person['free']:
            return ''
        grants = self.store.grants(person['id'], active_at=int(time.time()))
        if not grants:
            return 'Оплаченных ключей нет.'
        return 'Оплачено: ' + ', '.join(f"{billing.keys_word(g['slots'])} до {billing.day(g['expires'])}" for g in grants) + '.'

    def pay_menu(self, uid, person):
        if person['free']:
            return 'Ваш аккаунт бесплатный, оплачивать ключи не нужно.', self.action_buttons()
        used, limit = self.store.usage(uid)
        text = (f'{self.limit_status(person)}\nИспользовано {used} из {limit}.\n\n'
                f'Сколько ключей оплатить? {billing.PRICE_RUB} ₽ за ключ на 30 дней со дня подтверждения оплаты. '
                'Оплаченные ключи можно получать сразу на любых серверах.')
        counts = [1, 2, 3, 4, 5, 6]
        buttons = [[button(f'{n} — {billing.amount(n)} ₽', f'/payslots {n}') for n in counts[i:i + 3]] for i in (0, 3)]
        buttons += [[button('Другое количество', '/payslots other')], [button('Главное меню', '/start')]]
        return text, buttons

    def payment_instructions(self, payment):
        code, amount = payment['id'], payment['amount']
        lines = [f"К оплате: {amount} ₽ — {billing.keys_word(payment['slots'])} на 30 дней.",
                 f'Код платежа: {code}', '']
        buttons = []
        if self.payment_url:
            lines.append('1. Нажмите «Оплатить в Т-Банке». Введите сумму, а в поле «Сообщение» вставьте код — '
                         'кнопка «📋 Код» ниже его скопирует.')
            buttons.append([{'type': 'link', 'text': 'Оплатить в Т-Банке', 'url': self.payment_url}])
            if self.payment_details:
                lines.append(f'Или: {self.payment_details[0].lower() + self.payment_details[1:]} Код укажите в комментарии к переводу.')
        else:
            lines.append(f"1. {self.payment_details or 'Реквизиты для перевода уточните у администратора.'} "
                         'Код обязательно укажите в комментарии к переводу.')
        lines.append('2. Пришлите сюда скриншот или PDF платёжки — или нажмите «Я оплатил».')
        # MAX copies the payload to the clipboard: no fiddly selection of the code inside the text.
        copy = [{'type': 'clipboard', 'text': f'📋 Код {code}', 'payload': code},
                {'type': 'clipboard', 'text': f'📋 {amount} ₽', 'payload': str(amount)}]
        buttons.append(copy)
        if self.payment_phone:
            buttons.append([{'type': 'clipboard', 'text': '📋 Номер телефона', 'payload': self.payment_phone}])
        buttons += [[button('Я оплатил', f'/paid {code}')], [button('Отменить', f'/paycancel {code}')]]
        return '\n'.join(lines), buttons

    async def billing_command(self, uid, person, cmd, args):
        """Reply for billing commands, or None when the command is not about billing."""
        if cmd == '/pay' and len(args) == 1:
            return self.pay_menu(uid, person)
        if cmd == '/payslots' and len(args) == 2:
            if person['free']:
                return 'Ваш аккаунт бесплатный, оплачивать ключи не нужно.', self.action_buttons()
            if args[1] == 'other':
                self.store.set_prompt(uid, 'slots', 0)
                return f'Введите количество ключей числом, от 1 до {billing.MAX_SLOTS}.', [[button('Главное меню', '/start')]]
            if not args[1].isdigit():
                raise ValueError()
            return self.payment_instructions(self.store.create_payment(uid, int(args[1])))
        if cmd == '/paid' and len(args) == 2:
            payment = self.store.submit_payment(uid, args[1])
            await self.payment_to_owner(payment)
            return 'Спасибо! Платёж отправлен на проверку. Как только администратор подтвердит оплату, бот пришлёт сообщение.', None
        if cmd == '/paycancel' and len(args) == 2:
            self.store.cancel_payment(uid, args[1])
            return self.main_menu(uid)
        return None

    async def slots_answer(self, uid, text):
        if not text.isdigit() or not 1 <= int(text) <= billing.MAX_SLOTS:
            return f'Нужно число от 1 до {billing.MAX_SLOTS}. Введите ещё раз.', [[button('Главное меню', '/start')]]
        self.store.clear_prompt(uid)
        return await self.command(uid, f'/payslots {int(text)}')

    async def receipt(self, uid, mid, attachments):
        """A photo or file from the user: the receipt for their open payment."""
        if not self.store.person_any(uid):
            return self.ask_phone()
        self.store.person(uid)
        payment = self.store.open_payment(uid)
        if not payment:
            return 'Платёжку присылайте после выбора оплаты: «Оплатить ключи» в главном меню.', self.action_buttons()
        payment = self.store.submit_payment(uid, payment['id'], receipt_mid=mid)
        await self.payment_to_owner(payment, attachments)
        return 'Платёжка получена и отправлена на проверку. Как только администратор подтвердит оплату, бот пришлёт сообщение.', None

    # --- owner side --------------------------------------------------------------------------

    def describe(self, payment):
        person = self.store.person_by_id(payment['person_id'])
        who = f"{person['name'] if person else '?'} (MAX {payment['max_id']})"
        return f"{who}: {billing.keys_word(payment['slots'])} на 30 дней — {payment['amount']} ₽"

    async def payment_to_owner(self, payment, attachments=None):
        if not self.owner:
            return
        text = (f"Платёж {payment['id']} на проверку.\n{self.describe(payment)}.\n"
                + ('Платёжка — в предыдущем сообщении.' if payment['receipt_mid'] else 'Платёжку не прислали — проверьте поступление по коду.'))
        buttons = [[button('Подтвердить', f"/payok {payment['id']}"), button('Отклонить', f"/payno {payment['id']}")]]
        if payment['receipt_mid']:
            await self.forward_receipt(payment, attachments)
            await asyncio.sleep(0.6)
        await self.notify(text, buttons)

    async def forward_receipt(self, payment, attachments):
        try:
            await self.max.send(self.owner, f"Платёжка {payment['id']}:", forward=payment['receipt_mid'])
            return
        except Exception:
            pass
        media = [a for a in attachments or [] if a.get('type') in ('image', 'file') and a.get('payload', {}).get('token')]
        try:
            if media:
                await self.max.send(self.owner, f"Платёжка {payment['id']}:",
                                    media=[{'type': a['type'], 'payload': {'token': a['payload']['token']}} for a in media])
                return
        except Exception:
            pass
        await self.notify(f"Платёжку {payment['id']} переслать не удалось — сверьте поступление по коду в банке.")

    async def owner_command(self, cmd, args):
        code = args[1]
        if cmd == '/payno':
            payment = self.store.reject_payment(code)
            await self.tell(payment['max_id'], f'Оплата {code} не подтверждена. Если это ошибка, свяжитесь с администратором.',
                            self.action_buttons())
            return f'Платёж {code} отклонён, пользователь уведомлён.', None
        payment = self.store.confirm_payment(code)
        person = self.store.person_by_id(payment['person_id'])
        resumed = await self.enforce(person)
        used, limit = self.store.usage(payment['max_id']) if person['active'] else (0, 0)
        expires = self.store.grants(person['id'])[-1]['expires']
        text = (f"Оплата подтверждена: +{billing.keys_word(payment['slots'])} до {billing.day(expires)}.\n"
                f"Доступно ключей: {max(limit - used, 0)} из {limit}." + (f' Включено остановленных: {resumed}.' if resumed else ''))
        await self.tell(payment['max_id'], text, self.action_buttons())
        return f"Платёж {code} подтверждён: {person['name']} +{billing.keys_word(payment['slots'])} до {billing.day(expires)}.", None

    async def tell(self, uid, text, buttons=None):
        parts = text if isinstance(text, list) else [text]
        for i, part in enumerate(parts):
            if i:
                await asyncio.sleep(0.6)  # MAX allows 2 messages/second per dialog
            try:
                await self.max.send(uid, part, buttons if i == len(parts) - 1 else None)
            except Exception:
                return

    # --- limit enforcement -------------------------------------------------------------------

    async def enforce(self, person, now=None, down=None):
        """Make exactly the newest keys beyond the paid limit stopped. Returns how many were resumed."""
        now = int(now if now is not None else time.time())
        down = down if down is not None else set()
        keys = self.store.billed_keys(person['id'])
        limit = self.store.person_limit(person, now)
        tell = person['active'] and person['max_id']
        resumed = 0
        for row in keys[:limit]:
            if not row['billing_state']:
                continue
            if row['billing_state'] == 'lapsed':
                # Its owner paused it before billing did; it stays paused.
                self.store.set_billing(row['id'], billing_state=None, suspended_at=None)
                continue
            if row['server_id'] in down:
                continue
            try:
                if row['state'] == 'paused':
                    await asyncio.wait_for(self.panel.mutate('resume', row), timeout=20)
                self.store.set_billing(row['id'], state='active', billing_state=None, suspended_at=None)
                resumed += 1
            except Exception as exc:
                down.add(row['server_id'])
                await self.panel_seen(exc)
        for row in keys[limit:]:
            name = row['name'].split(' [max-')[0]
            if row['billing_state']:
                if now >= row['suspended_at'] + billing.REVOKE_AFTER:
                    await self.revoke_durable(row, person['max_id'], 'unpaid')  # queued if the node is down
                    if tell:
                        await self.tell(person['max_id'], f'Ключ «{name}» удалён: он не был оплачен {billing.REVOKE_AFTER // 86400} дней.',
                                        self.action_buttons())
                continue
            if row['state'] == 'paused':
                self.store.set_billing(row['id'], billing_state='lapsed', suspended_at=now)
            elif row['server_id'] in down:
                continue
            else:
                try:
                    await asyncio.wait_for(self.panel.mutate('pause', row), timeout=20)
                except Exception as exc:
                    down.add(row['server_id'])
                    await self.panel_seen(exc)
                    continue  # retried on the next tick
                self.store.set_billing(row['id'], state='paused', billing_state='suspended', suspended_at=now)
            if tell:
                await self.tell(person['max_id'], f'Ключ «{name}» остановлен: оплаченных ключей меньше, чем выдано. '
                                                  'Оплатите ключи — он заработает сразу после подтверждения. Можно также '
                                                  'отозвать ненужный ключ, тогда этот включится. '
                                                  f'Без оплаты через {billing.REVOKE_AFTER // 86400} дней ключ будет удалён.',
                                self.pay_buttons())
        return resumed

    async def remind(self, person, now):
        keys = len(self.store.billed_keys(person['id']))
        for grant in self.store.grants(person['id'], active_at=now):
            if grant['reminded'] or grant['expires'] - now > billing.REMIND_BEFORE:
                continue
            self.store.mark_reminded(grant['id'])
            # Only worth a message if the expiry would actually stop something.
            if keys > self.store.person_limit(person, grant['expires']) and person['active'] and person['max_id']:
                await self.tell(person['max_id'], f"{billing.day(grant['expires'])} заканчивается оплата "
                                                  f"{billing.keys_word(grant['slots'])}. Продлите заранее, чтобы ключи не остановились.",
                                self.pay_buttons())

    async def billing_tick(self, now=None):
        now = int(now if now is not None else time.time())
        down = set()
        for person in self.store.billed_people():
            await self.remind(person, now)
            await self.enforce(person, now, down)
        for row in self.store.free_suspended():
            # Marked free after being stopped for non-payment: start it again.
            try:
                if row['billing_state'] == 'suspended' and row['state'] == 'paused':
                    await asyncio.wait_for(self.panel.mutate('resume', row), timeout=20)
                    self.store.set_billing(row['id'], state='active')
                self.store.set_billing(row['id'], billing_state=None, suspended_at=None)
            except Exception as exc:
                await self.panel_seen(exc)
