"""Self-registration in the bot: phone (verified MAX contact, or typed as a fallback), then name.
A registered person is a paid account with no key slots; keys come after the first payment."""
import base64
import hashlib
import hmac
import json
import re

from . import billing
from .store import Denied

PHONE = re.compile(r'\+?[78]?[\s()-]*\d[\d\s()-]{8,16}')
NAME = re.compile(r"[\w .'-]{1,60}")
GUIDE_HINT = 'Инструкция в PDF: нажмите «Инструкция» или напишите «инструкция». '


def guide_button():
    return {'type': 'callback', 'text': 'Инструкция', 'payload': '/instruction'}


def button(text, command):
    return {'type': 'callback', 'text': text, 'payload': command}


def normalize_phone(raw):
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) == 10:
        digits = '7' + digits
    if len(digits) == 11 and digits[0] == '8':
        digits = '7' + digits[1:]
    if not 10 <= len(digits) <= 15:
        return None
    return '+' + digits


def parse_vcard(vcf):
    phone = name = None
    for line in (vcf or '').replace('\r\n', '\n').split('\n'):
        key, _, value = line.partition(':')
        if key.upper().startswith('TEL') and not phone:
            phone = normalize_phone(value)
        elif key.upper() == 'FN' and not name:
            name = value.strip()
    return phone, name


def contact_verified(payload, secret):
    """MAX signs contacts sent by the request_contact button: hash = HMAC-SHA256(bot token, vcf_info)."""
    vcf, given = payload.get('vcf_info') or '', (payload.get('hash') or '').strip().lower()
    if not secret or not vcf or not given:
        return False
    digest = hmac.new(secret.encode(), vcf.encode(), hashlib.sha256)
    candidates = {digest.hexdigest(), base64.b64encode(digest.digest()).decode().lower(),
                  base64.urlsafe_b64encode(digest.digest()).decode().rstrip('=').lower()}
    return any(hmac.compare_digest(given.rstrip('='), c.rstrip('=')) for c in candidates)


class RegistrationMixin:
    contact_secret = None

    def ask_phone(self, intro='Здравствуйте! Чтобы получать VPN-ключи, зарегистрируйтесь.'):
        return (intro + '\nНажмите «Поделиться номером» — бот получит номер телефона из вашего аккаунта MAX.'
                + '\n' + GUIDE_HINT), [
            [{'type': 'request_contact', 'text': 'Поделиться номером'}],
            [button('Ввести номер вручную', '/regphone')], [guide_button()]]

    def ask_name(self, uid, phone, verified, suggested):
        self.store.set_prompt(uid, 'reg_name', 0, ttl=3600,
                              data=json.dumps({'phone': phone, 'verified': verified, 'suggested': suggested}))
        buttons = [[button(f'Оставить «{suggested[:30]}»', '/regname')]] if suggested and NAME.fullmatch(suggested) else []
        return 'Номер получен. Как вас зовут? Напишите имя и фамилию.', buttons or None

    async def unregistered(self, uid, cmd, args):
        """Dialog for a MAX user without a registry entry."""
        prompt = self.store.prompt(uid)
        if cmd == '/regphone':
            self.store.set_prompt(uid, 'reg_phone', 0, ttl=3600)
            return 'Введите номер телефона, например +7 900 123-45-67.', None
        if cmd == '/regname' and prompt and prompt['kind'] == 'reg_name':
            data = json.loads(prompt['data'])
            return await self.finish_registration(uid, data['suggested'], data)
        if not cmd.startswith('/') and prompt and prompt['kind'] == 'reg_phone':
            phone = normalize_phone(' '.join(args))
            if not phone:
                return 'Не похоже на номер телефона. Введите ещё раз, например +7 900 123-45-67.', None
            return self.ask_name(uid, phone, False, None)
        if not cmd.startswith('/') and prompt and prompt['kind'] == 'reg_name':
            name = ' '.join(' '.join(args).split())
            if not NAME.fullmatch(name):
                return 'Имя — до 60 символов: буквы, пробел, дефис, апостроф и точка. Напишите ещё раз.', None
            return await self.finish_registration(uid, name, json.loads(prompt['data']))
        return self.ask_phone()

    async def contact(self, uid, attachment):
        payload = attachment.get('payload') or {}
        if self.store.person_any(uid):
            return 'Вы уже зарегистрированы.', self.action_buttons()
        owner = (payload.get('max_info') or {}).get('user_id')
        verified = contact_verified(payload, self.contact_secret) and owner in (None, uid)
        phone, name = parse_vcard(payload.get('vcf_info'))
        if not verified or not phone:
            return self.ask_phone('Не удалось подтвердить номер: отправьте его кнопкой «Поделиться номером», а не через скрепку.')
        return self.ask_name(uid, phone, True, name)

    async def finish_registration(self, uid, name, data):
        try:
            self.store.register(uid, name, data['phone'], data['verified'], list(self.protocols))
        except Denied:
            self.store.clear_prompt(uid)
            return 'Вы уже зарегистрированы.', self.action_buttons()
        self.store.clear_prompt(uid)
        await self.notify(f"Новый пользователь: {name}, {data['phone']}" + ('' if data['verified'] else ' (номер введён вручную, не подтверждён)')
                          + f', MAX ID {uid}. Ключей 0 — ждёт оплаты.')
        person = self.store.person(uid)
        text, buttons = self.pay_menu(uid, person)
        return [f'Готово, {name}, вы зарегистрированы.\nVPN-ключи выдаются после оплаты: '
                f'{billing.PRICE_RUB} ₽ за ключ на 30 дней. Оплаченные ключи вы сможете сразу получить на любых серверах.'
                + '\n' + GUIDE_HINT, text], buttons
