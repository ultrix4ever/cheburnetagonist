"""Billing rules and payment-source adapters. No I/O here except the explicit adapter stubs.

Model: a paid person's key limit is the sum of slots in grants active right now. A payment of
N × PRICE_RUB, once the owner confirms it, adds a grant of N slots for PERIOD from that moment;
keys are issued at once within the limit. When grants expire and the person has more keys than
slots, the newest keys are paused; a key paused this way for REVOKE_AFTER is revoked. Free people
use the static quota from the panel. Keys that existed before billing (`legacy`) are not billed.
"""
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

PRICE_RUB = 100          # one key slot for one period
PERIOD = 30 * 86400      # a payment buys slots for 30 days from the owner's confirmation
MAX_SLOTS = 20
REMIND_BEFORE = 3 * 86400
REVOKE_AFTER = 30 * 86400
# Free people who stop being free keep their quota paid for this long.
FREE_TO_PAID_GRACE = 3 * 86400
MSK = timezone(timedelta(hours=3))
CODE = re.compile(r'VPN-?(\d{6})', re.I)


def amount(slots):
    return PRICE_RUB * slots


def new_code():
    return 'VPN-' + ''.join(secrets.choice('0123456789') for _ in range(6))


def day(ts):
    return datetime.fromtimestamp(ts, MSK).strftime('%d.%m.%Y')


def keys_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return f'{n} ключ'
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f'{n} ключа'
    return f'{n} ключей'


# --- Automatic payment confirmation: prepared, not wired. ---------------------------------
# Wiring plan: a worker polls BankFeed.incoming(), runs match() against payments in
# 'awaiting'/'submitted', confirms exact matches through the same path as the owner's
# «Подтвердить» button, and sends mismatches to the owner for a manual decision.


@dataclass(frozen=True)
class Incoming:
    id: str            # bank operation id, used for idempotency
    amount_kop: int    # kopecks, never float
    comment: str       # payer's transfer message, where the VPN-XXXXXX code is expected
    at: int


def match(pending, incoming):
    """Pair bank transfers with payments by code in the comment AND exact amount.

    `pending`: dicts with `id` (the code, e.g. VPN-123456) and `amount` in rubles.
    Returns (matched [(payment_id, incoming_id)], mismatched [(payment_id, incoming_id, reason)]).
    A transfer naming several codes, or a code paid twice, is never auto-confirmed.
    """
    by_code = {p['id'].upper(): p for p in pending}
    matched, mismatched, used = [], [], set()
    for op in incoming:
        codes = {'VPN-' + c for c in CODE.findall(op.comment or '')} & by_code.keys()
        if len(codes) != 1:
            if len(codes) > 1:
                for c in sorted(codes):
                    mismatched.append((by_code[c]['id'], op.id, 'several codes in one transfer'))
            continue
        payment = by_code[codes.pop()]
        if payment['id'] in used:
            mismatched.append((payment['id'], op.id, 'code paid more than once'))
        elif op.amount_kop != payment['amount'] * 100:
            mismatched.append((payment['id'], op.id, f'amount {op.amount_kop / 100:.2f} != {payment["amount"]}'))
        else:
            matched.append((payment['id'], op.id))
            used.add(payment['id'])
    duplicated = {pid for pid, _, why in mismatched if why == 'code paid more than once'}
    return [m for m in matched if m[0] not in duplicated], mismatched


class BankFeed:
    async def incoming(self, since):
        """Incoming transfers since unix time `since`, as Incoming records."""
        raise NotImplementedError


class TBankFeed(BankFeed):
    """Stub. T-Bank offers a statement API only for business accounts (T-Business Open API,
    statements by account with an OAuth token). A personal card of a self-employed person has
    no official statement API; do not scrape the consumer app. Implement this once payments
    go to an account with API access, then enable the matcher worker."""

    def __init__(self, token, account):
        self.token, self.account = token, account

    async def incoming(self, since):
        raise NotImplementedError('T-Bank statement API is not configured')


class ReceiptSink:
    """Fiscal receipt per confirmed payment. Self-employed (НПД) receipts go to «Мой налог»."""

    async def register(self, payment):
        return None


class MoyNalogSink(ReceiptSink):
    """Stub. «Мой налог» has an official API only for partner platforms; until one is chosen,
    receipts are issued manually in the app. Must stay idempotent per payment id."""

    async def register(self, payment):
        raise NotImplementedError('Receipt registration is not configured')
