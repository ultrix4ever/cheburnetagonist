"""Protected ownership registry; never stores VPN private keys/configs."""
import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from . import billing


class Denied(Exception):
    pass


REACH_CONFIRM = 2  # consecutive failed probe runs before a node counts as unreachable
REACH_FRESH = 3 * 3600  # older probe results are shown to nobody as current


class Store:
    # Freeing the slot before the node confirms must not let one person stack live keys during an outage.
    MAX_UNCONFIRMED = 2
    REVOCATION_BACKLOG = ('Есть отозванные ключи, которые сервер ещё не подтвердил. Новые операции станут доступны, '
                          'как только сервер ответит и отзыв завершится.')

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS people(
              id INTEGER PRIMARY KEY, name TEXT NOT NULL,
              max_id INTEGER UNIQUE, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS profiles(
              id INTEGER PRIMARY KEY, server_id INTEGER NOT NULL,
              protocol TEXT NOT NULL CHECK(protocol IN ('awg','awg3')),
              public_key TEXT NOT NULL, name TEXT NOT NULL,
              person_id INTEGER REFERENCES people(id), state TEXT NOT NULL,
              has_config INTEGER NOT NULL, seen_at INTEGER NOT NULL,
              UNIQUE(server_id,protocol,public_key));
            CREATE TABLE IF NOT EXISTS invites(
              digest TEXT PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id),
              expected_max_id INTEGER NOT NULL, expires INTEGER NOT NULL, used INTEGER);
            CREATE TABLE IF NOT EXISTS confirmations(
              digest TEXT PRIMARY KEY, max_id INTEGER NOT NULL,
              profile_id INTEGER NOT NULL, action TEXT NOT NULL,
              expires INTEGER NOT NULL, used INTEGER);
            CREATE TABLE IF NOT EXISTS operations(
              id TEXT PRIMARY KEY, max_id INTEGER NOT NULL, action TEXT NOT NULL,
              profile_id INTEGER, server_id INTEGER, label TEXT,
              state TEXT NOT NULL, created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(
              id INTEGER PRIMARY KEY, at INTEGER NOT NULL, max_id INTEGER,
              action TEXT NOT NULL, target TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox(
              id TEXT PRIMARY KEY, body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
              created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS replacements(
              id TEXT PRIMARY KEY, max_id INTEGER NOT NULL,
              old_id INTEGER NOT NULL REFERENCES profiles(id),
              new_id INTEGER REFERENCES profiles(id), server_id INTEGER NOT NULL,
              mode TEXT NOT NULL, state TEXT NOT NULL,
              created INTEGER NOT NULL, expires INTEGER NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_replacement ON replacements(max_id)
              WHERE state IN ('creating','ready','committing','canceling','unknown');
            CREATE TABLE IF NOT EXISTS server_catalog(
              id INTEGER PRIMARY KEY, name TEXT NOT NULL, online INTEGER,
              updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_jobs(
              id TEXT PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id),
              state TEXT NOT NULL, result TEXT, created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS prompts(
              max_id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
              server_id INTEGER NOT NULL, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS revocations(
              profile_id INTEGER PRIMARY KEY REFERENCES profiles(id),
              max_id INTEGER, reason TEXT NOT NULL, created INTEGER NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0, last_attempt INTEGER, last_error TEXT);
            ''')
            # The per-key table of the first billing build is replaced by slot purchases;
            # it is dropped only while still empty.
            old = {r[1] for r in db.execute('PRAGMA table_info(payments)')}
            if 'kind' in old and not db.execute('SELECT 1 FROM payments LIMIT 1').fetchone():
                db.execute('DROP TABLE payments')
            db.executescript("""
            CREATE TABLE IF NOT EXISTS payments(
              id TEXT PRIMARY KEY, max_id INTEGER NOT NULL, person_id INTEGER NOT NULL REFERENCES people(id),
              slots INTEGER NOT NULL CHECK(slots > 0), amount INTEGER NOT NULL,
              state TEXT NOT NULL, receipt_mid TEXT, grant_id INTEGER,
              created INTEGER NOT NULL, decided INTEGER, note TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_payment ON payments(max_id)
              WHERE state IN ('awaiting','submitted');
            CREATE TABLE IF NOT EXISTS grants(
              id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id),
              slots INTEGER NOT NULL CHECK(slots > 0), starts INTEGER NOT NULL, expires INTEGER NOT NULL,
              source TEXT NOT NULL, payment_id TEXT, reminded INTEGER NOT NULL DEFAULT 0,
              cancelled INTEGER);
            CREATE INDEX IF NOT EXISTS grants_person ON grants(person_id, expires);
            CREATE TABLE IF NOT EXISTS reachability(
              vantage TEXT NOT NULL, server_id INTEGER NOT NULL, name TEXT NOT NULL,
              status TEXT NOT NULL, stage TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
              handshake_ms INTEGER, rtt_ms INTEGER, exit_ip TEXT, icmp INTEGER,
              checked INTEGER NOT NULL, since INTEGER NOT NULL, fails INTEGER NOT NULL DEFAULT 0,
              notified TEXT, PRIMARY KEY(vantage, server_id));
            """)
            columns = {r[1] for r in db.execute('PRAGMA table_info(people)')}
            for name, definition in [('quota', 'INTEGER NOT NULL DEFAULT 5'),
                                     ('allowed_servers', "TEXT NOT NULL DEFAULT '[1,2,3,4]'"),
                                     ('phone', "TEXT NOT NULL DEFAULT ''"),
                                     ('free', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('phone_verified', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('registered', 'INTEGER')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE people ADD COLUMN {name} {definition}')
            columns = {r[1] for r in db.execute('PRAGMA table_info(profiles)')}
            # billing_state: 'suspended' = paused by billing over the paid limit; 'lapsed' = its owner
            # had paused it already, so it is never auto-resumed. paid_until/reminded_until are unused
            # leftovers of the first billing build.
            for name, definition in [('paid_until', 'INTEGER'), ('billing_state', 'TEXT'),
                                     ('reminded_until', 'INTEGER'), ('suspended_at', 'INTEGER')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE profiles ADD COLUMN {name} {definition}')
            if 'data' not in {r[1] for r in db.execute('PRAGMA table_info(prompts)')}:
                db.execute('ALTER TABLE prompts ADD COLUMN data TEXT')
            if 'legacy' not in columns:
                # Keys that existed before billing are left alone by decision of the owner.
                db.execute('ALTER TABLE profiles ADD COLUMN legacy INTEGER NOT NULL DEFAULT 0')
                db.execute('UPDATE profiles SET legacy=1')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def log(db, uid, action, target):
        db.execute('INSERT INTO audit(at,max_id,action,target) VALUES(?,?,?,?)',
                   (int(time.time()), uid, action, str(target)))

    def invite(self, name, uid):
        if uid <= 0:
            raise ValueError('MAX ID must be positive')
        token = secrets.token_urlsafe(24)
        with self.connect() as db:
            pid = db.execute('INSERT INTO people(name) VALUES(?)', (name,)).lastrowid
            db.execute('INSERT INTO invites VALUES(?,?,?,?,NULL)',
                       (self.digest(token), pid, uid, int(time.time()) + 86400))
            self.log(db, None, 'invite_created', pid)
        return pid, token

    def claim(self, uid, token):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM invites WHERE digest=?',
                             (self.digest(token),)).fetchone()
            if not row or row['used'] or row['expires'] <= time.time() or row['expected_max_id'] != uid:
                raise Denied('Приглашение недействительно.')
            person = db.execute('SELECT * FROM people WHERE id=?', (row['person_id'],)).fetchone()
            if not person['active']:
                raise Denied('Доступ отключён.')
            if db.execute('SELECT 1 FROM people WHERE max_id=?', (uid,)).fetchone():
                raise Denied('Учётная запись уже привязана.')
            db.execute('UPDATE people SET max_id=? WHERE id=?', (uid, person['id']))
            db.execute('UPDATE invites SET used=? WHERE digest=?', (int(time.time()), row['digest']))
            self.log(db, uid, 'invite_claimed', person['id'])

    def person(self, uid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM people WHERE max_id=? AND active=1', (uid,)).fetchone()
        if not row:
            raise Denied('Доступ ещё не выдан. Передайте администратору ваш MAX ID: ' + str(uid))
        return dict(row)

    def delete_person(self, pid):
        """Erase a person and everything tied to them. Only after all their keys are confirmed revoked:
        a registry row is the only thing that lets a live key be found and revoked later."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            person = db.execute('SELECT * FROM people WHERE id=?', (pid,)).fetchone()
            if not person:
                raise Denied('Пользователь не найден.')
            uid = person['max_id']
            live = db.execute("SELECT count(*) FROM profiles WHERE person_id=? AND state!='revoked'", (pid,)).fetchone()[0]
            if live:
                raise Denied(f'У пользователя ещё {live} неотозванных ключей. Сначала «Отозвать весь VPN-доступ» и дождитесь подтверждения.')
            if db.execute("SELECT 1 FROM admin_jobs WHERE person_id=? AND state IN ('pending','processing')", (pid,)).fetchone():
                raise Denied('Отзыв доступа ещё выполняется. Дождитесь завершения.')
            if uid is not None and db.execute("SELECT 1 FROM operations WHERE max_id=? AND state IN ('pending','unknown')", (uid,)).fetchone():
                raise Denied('Есть операция, требующая сверки. Сначала закройте её (reconcile).')
            if uid is not None and db.execute(
                    "SELECT 1 FROM replacements WHERE max_id=? AND state IN ('creating','ready','committing','canceling')", (uid,)).fetchone():
                raise Denied('Есть незавершённая замена ключа.')
            # Identifiers that also appear as audit targets.
            refs = {str(pid)}
            if uid is not None:
                for table in ('operations', 'replacements', 'payments'):
                    refs.update(r[0] for r in db.execute(f'SELECT id FROM {table} WHERE max_id=?', (uid,)))
            refs.update(r[0] for r in db.execute('SELECT id FROM admin_jobs WHERE person_id=?', (pid,)))
            refs.update(f'{pid}:{r[0]}' for r in db.execute('SELECT slots FROM grants WHERE person_id=?', (pid,)))
            refs.update(str(r[0]) for r in db.execute('SELECT id FROM profiles WHERE person_id=?', (pid,)))
            # Revoked key records stay as node inventory, detached and without the user-chosen name:
            # if such a key ever reappears on a node it is still recognised and revoked again.
            for row in db.execute('SELECT id,name FROM profiles WHERE person_id=?', (pid,)).fetchall():
                suffix = row['name'][row['name'].find(' [max-'):] if ' [max-' in row['name'] else ''
                db.execute("UPDATE profiles SET person_id=NULL,name=? WHERE id=?", ('удалён' + suffix, row['id']))
            if uid is not None:
                for table in ('confirmations', 'operations', 'replacements', 'prompts', 'payments'):
                    db.execute(f'DELETE FROM {table} WHERE max_id=?', (uid,))
                db.execute('UPDATE revocations SET max_id=NULL WHERE max_id=?', (uid,))
                db.execute('DELETE FROM audit WHERE max_id=?', (uid,))
                # Unprocessed updates may still carry the person's messages.
                db.execute("UPDATE inbox SET body='{}' WHERE body LIKE ? OR body LIKE ?",
                           (f'%"user_id": {uid},%', f'%"user_id": {uid}}}%'))
            db.execute(f"DELETE FROM audit WHERE target IN ({','.join('?' * len(refs))})", tuple(refs))
            for table in ('grants', 'admin_jobs', 'invites'):
                db.execute(f'DELETE FROM {table} WHERE person_id=?', (pid,))
            db.execute('DELETE FROM people WHERE id=?', (pid,))
            self.log(db, None, 'person_deleted', 'person')
        return True

    def person_any(self, uid):
        # Includes blocked people: they must not be offered self-registration again.
        with self.connect() as db:
            row = db.execute('SELECT * FROM people WHERE max_id=?', (uid,)).fetchone()
        return dict(row) if row else None

    def register(self, uid, name, phone, verified, servers):
        """Self-registration: a paid account with no slots, all configured servers allowed."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                pid = db.execute('''INSERT INTO people(name,max_id,quota,allowed_servers,phone,active,free,phone_verified,registered)
                  VALUES(?,?,0,?,?,1,0,?,?)''', (name, uid, json.dumps(sorted(servers)), phone, int(bool(verified)),
                                                int(time.time()))).lastrowid
            except sqlite3.IntegrityError:
                raise Denied('Учётная запись уже существует.') from None
            self.log(db, uid, 'self_registered', pid)
        return pid

    def owned(self, uid, profile_id, pending=False):
        # A profile awaiting confirmed revocation accepts no further operations.
        person = self.person(uid)
        states = ('revoked',) if pending else ('revoked', 'revoking')
        with self.connect() as db:
            row = db.execute(f"SELECT * FROM profiles WHERE id=? AND person_id=? AND state NOT IN ({','.join('?' * len(states))})",
                             (profile_id, person['id'], *states)).fetchone()
        if not row:
            raise Denied('Профиль не найден.')
        return dict(row)

    def profiles(self, uid):
        person = self.person(uid)
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM profiles WHERE person_id=? AND state!='revoked' ORDER BY id", (person['id'],))]

    def assign(self, profile_id, person_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            person = db.execute('SELECT * FROM people WHERE id=?', (person_id,)).fetchone()
            profile = db.execute('SELECT * FROM profiles WHERE id=?', (profile_id,)).fetchone()
            billed = person and not (profile and profile['legacy'] and not person['free'])
            if not person or (billed and self.used(db, person_id) >= self.limit(db, person)):
                raise Denied('Нет пользователя или достигнут его лимит. Добавьте ключи вручную или измените лимит.')
            if not profile or profile['state'] not in ('active', 'paused'):
                raise Denied('Привязать можно только существующий профиль.')
            # Reassignment is deliberately not implicit.
            cur = db.execute('UPDATE profiles SET person_id=? WHERE id=? AND person_id IS NULL',
                             (person_id, profile_id))
            if cur.rowcount != 1:
                raise Denied('Профиль отсутствует или уже имеет владельца.')
            self.log(db, None, 'profile_assigned', profile_id)

    def inventory(self, sid, proto, clients):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            keys = set()
            for client in clients:
                key = client['public_key']
                keys.add(key)
                state = 'paused' if client.get('paused') else 'active'
                # A revoked key seen again means the node never applied it: never resurrect it as usable.
                db.execute('''INSERT INTO profiles(server_id,protocol,public_key,name,state,has_config,seen_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(server_id,protocol,public_key) DO UPDATE SET
                name=excluded.name,has_config=excluded.has_config,seen_at=excluded.seen_at,
                state=CASE WHEN profiles.state IN ('revoked','revoking') THEN 'revoking' ELSE excluded.state END''',
                           (sid, proto, key, client['name'], state, bool(client.get('has_config')), int(time.time())))
            for row in db.execute('SELECT id,public_key,state FROM profiles WHERE server_id=? AND protocol=?', (sid, proto)):
                if row['public_key'] in keys:
                    if row['state'] == 'revoking':
                        self.queue_revocation_db(db, row['id'], None, 'inventory')
                elif row['state'] == 'revoking':
                    # Absence on the node is the confirmation the revocation never got.
                    self.confirm_revocation_db(db, row['id'])
                elif row['state'] != 'revoked':
                    db.execute("UPDATE profiles SET state='missing' WHERE id=?", (row['id'],))

    def confirm(self, uid, profile_id, action):
        self.owned(uid, profile_id)
        token = secrets.token_urlsafe(16)
        with self.connect() as db:
            db.execute('INSERT INTO confirmations VALUES(?,?,?,?,?,NULL)',
                       (self.digest(token), uid, profile_id, action, int(time.time()) + 300))
        return token

    def consume(self, uid, token):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM confirmations WHERE digest=? AND max_id=?',
                             (self.digest(token), uid)).fetchone()
            if not row or row['used'] or row['expires'] <= time.time():
                raise Denied('Подтверждение недействительно.')
            db.execute('UPDATE confirmations SET used=? WHERE digest=?', (int(time.time()), row['digest']))
        return dict(row)

    def start_operation(self, uid, action, profile_id=None, server_id=None, label=None):
        person = self.person(uid)
        oid = secrets.token_hex(16)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM operations WHERE state IN ('pending','unknown') AND max_id=?", (uid,)).fetchone():
                raise Denied('Предыдущая операция ожидает сверки администратором.')
            if self.open_replacement_db(db, uid):
                raise Denied('Сначала завершите или отмените текущую замену ключа.')
            if db.execute("SELECT 1 FROM admin_jobs WHERE person_id=? AND state IN ('pending','processing','unknown')", (person['id'],)).fetchone():
                raise Denied('Администратор отзывает доступ или сверяет результат операции.')
            if action != 'revoke' and self.unconfirmed_revocations(db, person['id']) >= self.MAX_UNCONFIRMED:
                raise Denied(self.REVOCATION_BACKLOG)
            if action == 'issue':
                if server_id not in json.loads(person['allowed_servers']):
                    raise Denied('Этот сервер не разрешён.')
                if self.used(db, person['id']) >= self.limit(db, person):
                    raise Denied('Достигнут лимит профилей.')
            db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)',
                       (oid, uid, action, profile_id, server_id, label, 'pending', int(time.time())))
            self.log(db, uid, action + '_started', oid)
        return oid

    def finish(self, oid, state):
        with self.connect() as db:
            db.execute('UPDATE operations SET state=? WHERE id=?', (state, oid))
            self.log(db, None, 'operation_' + state, oid)

    def set_state(self, pid, state):
        with self.connect() as db:
            db.execute('UPDATE profiles SET state=? WHERE id=?', (state, pid))

    # --- billing -----------------------------------------------------------------------------

    def open_payment(self, uid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM payments WHERE max_id=? AND state IN ('awaiting','submitted')", (uid,)).fetchone()
        return dict(row) if row else None

    def payment(self, code, uid=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM payments WHERE id=?' + (' AND max_id=?' if uid is not None else ''),
                             (code,) if uid is None else (code, uid)).fetchone()
        if not row:
            raise Denied('Платёж не найден.')
        return dict(row)

    def create_payment(self, uid, slots):
        if not 1 <= slots <= billing.MAX_SLOTS:
            raise Denied(f'Можно оплатить от 1 до {billing.MAX_SLOTS} ключей.')
        person = self.person(uid)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM payments WHERE max_id=? AND state='submitted'", (uid,)).fetchone():
                raise Denied('Ваш предыдущий платёж ещё на проверке. Дождитесь подтверждения.')
            # An unfinished payment is replaced rather than piling up.
            db.execute("UPDATE payments SET state='cancelled',decided=? WHERE max_id=? AND state='awaiting'",
                       (int(time.time()), uid))
            for _ in range(20):
                code = billing.new_code()
                if not db.execute('SELECT 1 FROM payments WHERE id=?', (code,)).fetchone():
                    break
            db.execute("INSERT INTO payments(id,max_id,person_id,slots,amount,state,created) VALUES(?,?,?,?,?,'awaiting',?)",
                       (code, uid, person['id'], slots, billing.amount(slots), int(time.time())))
            self.log(db, uid, 'payment_created', code)
        return self.payment(code)

    def submit_payment(self, uid, code, receipt_mid=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cur = db.execute("UPDATE payments SET state='submitted',receipt_mid=coalesce(?,receipt_mid) "
                             "WHERE id=? AND max_id=? AND (state='awaiting' OR (state='submitted' AND ? IS NOT NULL))",
                             (receipt_mid, code, uid, receipt_mid))
            if cur.rowcount != 1:
                raise Denied('Этот платёж уже отправлен на проверку или закрыт.')
            self.log(db, uid, 'payment_submitted', code)
        return self.payment(code)

    def cancel_payment(self, uid, code):
        with self.connect() as db:
            cur = db.execute("UPDATE payments SET state='cancelled',decided=? WHERE id=? AND max_id=? AND state='awaiting'",
                             (int(time.time()), code, uid))
            if cur.rowcount != 1:
                raise Denied('Этот платёж уже на проверке или закрыт.')

    def reject_payment(self, code):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cur = db.execute("UPDATE payments SET state='rejected',decided=? WHERE id=? AND state IN ('awaiting','submitted')",
                             (int(time.time()), code))
            if cur.rowcount != 1:
                raise Denied('Платёж уже обработан.')
            self.log(db, None, 'payment_rejected', code)
        return self.payment(code)

    def confirm_payment(self, code):
        # One transaction: a double tap on «Подтвердить» cannot grant twice.
        now = int(time.time())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM payments WHERE id=? AND state IN ('awaiting','submitted')", (code,)).fetchone()
            if not row:
                raise Denied('Платёж уже обработан.')
            gid = db.execute("INSERT INTO grants(person_id,slots,starts,expires,source,payment_id) VALUES(?,?,?,?,'payment',?)",
                             (row['person_id'], row['slots'], now, now + billing.PERIOD, code)).lastrowid
            db.execute("UPDATE payments SET state='confirmed',decided=?,grant_id=? WHERE id=?", (now, gid, code))
            self.log(db, None, 'payment_confirmed', code)
        return self.payment(code)

    def add_grant(self, person_id, slots, source):
        if not 1 <= slots <= billing.MAX_SLOTS:
            raise Denied(f'Можно добавить от 1 до {billing.MAX_SLOTS} ключей.')
        now = int(time.time())
        with self.connect() as db:
            gid = db.execute('INSERT INTO grants(person_id,slots,starts,expires,source) VALUES(?,?,?,?,?)',
                             (person_id, slots, now, now + billing.PERIOD, source)).lastrowid
            self.log(db, None, 'grant_' + source, f'{person_id}:{slots}')
        return gid

    def cancel_grant(self, gid):
        with self.connect() as db:
            row = db.execute('SELECT person_id FROM grants WHERE id=? AND cancelled IS NULL', (gid,)).fetchone()
            if not row:
                raise Denied('Пакет не найден или уже аннулирован.')
            db.execute('UPDATE grants SET cancelled=? WHERE id=?', (int(time.time()), gid))
            self.log(db, None, 'grant_cancelled', gid)
        return row['person_id']

    def grants(self, person_id, active_at=None):
        query, args = 'SELECT * FROM grants WHERE cancelled IS NULL AND person_id=?', [person_id]
        if active_at is not None:
            query += ' AND starts<=? AND expires>?'
            args += [active_at, active_at]
        with self.connect() as db:
            return [dict(r) for r in db.execute(query + ' ORDER BY expires', args)]

    def mark_reminded(self, gid):
        with self.connect() as db:
            db.execute('UPDATE grants SET reminded=1 WHERE id=?', (gid,))

    def person_limit(self, person, at=None):
        with self.connect() as db:
            return self.limit(db, person, at)

    def set_billing(self, pid, **fields):
        allowed = {'billing_state', 'suspended_at', 'state'}
        assert fields and set(fields) <= allowed
        with self.connect() as db:
            db.execute('UPDATE profiles SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', (*fields.values(), pid))

    def person_by_id(self, person_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM people WHERE id=?', (person_id,)).fetchone()
        return dict(row) if row else None

    def billed_people(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM people WHERE free=0')]

    def billed_keys(self, person_id):
        """Live keys that consume paid slots, oldest first: over the limit, the newest are stopped.
        A target of an open replacement is the same logical slot as its source and is skipped."""
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT * FROM profiles f WHERE person_id=? AND legacy=0
              AND state IN ('active','paused') AND NOT EXISTS (SELECT 1 FROM replacements r WHERE r.new_id=f.id
              AND r.state IN ('creating','ready','committing','canceling','unknown')) ORDER BY id''', (person_id,))]

    def free_suspended(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT f.*,p.max_id FROM profiles f JOIN people p ON p.id=f.person_id
              WHERE p.free=1 AND f.billing_state IS NOT NULL AND f.state IN ('active','paused')''')]

    def payments_snapshot(self, limit=100):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM payments ORDER BY created DESC LIMIT ?', (limit,))]
        for row in rows:
            row['max_id'] = str(row['max_id'])
        return rows

    def set_prompt(self, uid, kind, sid, ttl=600, data=None):
        # The bot asked a question and treats the person's next plain-text message as the answer.
        with self.connect() as db:
            db.execute('INSERT INTO prompts(max_id,kind,server_id,expires,data) VALUES(?,?,?,?,?) ON CONFLICT(max_id) DO UPDATE SET '
                       'kind=excluded.kind,server_id=excluded.server_id,expires=excluded.expires,data=excluded.data',
                       (uid, kind, sid, int(time.time()) + ttl, data))

    def prompt(self, uid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM prompts WHERE max_id=? AND expires>?', (uid, int(time.time()))).fetchone()
        return dict(row) if row else None

    def clear_prompt(self, uid):
        with self.connect() as db:
            db.execute('DELETE FROM prompts WHERE max_id=?', (uid,))

    def queue_revocation_db(self, db, pid, uid, reason):
        db.execute("UPDATE profiles SET state='revoking' WHERE id=? AND state NOT IN ('revoked','revoking')", (pid,))
        cur = db.execute('''INSERT INTO revocations(profile_id,max_id,reason,created)
          VALUES(?,?,?,?) ON CONFLICT(profile_id) DO NOTHING''', (pid, uid, reason, int(time.time())))
        if cur.rowcount:
            self.log(db, uid, 'revocation_queued', pid)

    def confirm_revocation_db(self, db, pid):
        db.execute("UPDATE profiles SET state='revoked' WHERE id=?", (pid,))
        db.execute('DELETE FROM revocations WHERE profile_id=?', (pid,))
        self.log(db, None, 'revocation_confirmed', pid)

    def queue_revocation(self, pid, uid, reason):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.queue_revocation_db(db, pid, uid, reason)

    def confirm_revocation(self, pid):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.confirm_revocation_db(db, pid)

    def revocation_failed(self, pid, error):
        # Sanitized class/message only: panel errors must never carry URLs or credentials.
        detail = (type(error).__name__ + ': ' + str(error))[:200]
        with self.connect() as db:
            db.execute('UPDATE revocations SET attempts=attempts+1,last_attempt=?,last_error=? WHERE profile_id=?',
                       (int(time.time()), detail, pid))

    def due_revocations(self, retry_after=60):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                '''SELECT p.*,r.attempts,r.last_attempt,r.reason FROM revocations r JOIN profiles p ON p.id=r.profile_id
                   WHERE r.last_attempt IS NULL OR r.last_attempt<=? ORDER BY r.created,r.profile_id''',
                (int(time.time()) - retry_after,))]

    def revocation_backlog(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT r.*,p.server_id,p.person_id,p.state
              FROM revocations r JOIN profiles p ON p.id=r.profile_id ORDER BY r.created''')]

    def issued(self, uid, sid, proto, client):
        self.inventory_one(sid, proto, client)
        with self.connect() as db:
            row = db.execute('SELECT id FROM profiles WHERE server_id=? AND protocol=? AND public_key=?',
                             (sid, proto, client['public_key'])).fetchone()
        self.assign(row['id'], self.person(uid)['id'])
        return row['id']

    def inventory_one(self, sid, proto, client):
        with self.connect() as db:
            db.execute('''INSERT INTO profiles(server_id,protocol,public_key,name,state,has_config,seen_at)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(server_id,protocol,public_key) DO NOTHING''',
                       (sid, proto, client['public_key'], client['name'], 'active', 1, int(time.time())))

    def enqueue(self, event):
        body = json.dumps(event, sort_keys=True, ensure_ascii=False)
        mid = event.get('message', {}).get('body', {}).get('mid')
        eid = ('message:' + str(mid)) if event.get('update_type') == 'message_created' and mid else self.digest(body)
        with self.connect() as db:
            # Exact repeats are coalesced even after processing.
            db.execute('INSERT OR IGNORE INTO inbox(id,body,created) VALUES(?,?,?)', (eid, body, int(time.time())))

    def next_event(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM inbox WHERE state='pending' ORDER BY created,rowid LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE inbox SET state='processing' WHERE id=?", (row['id'],))
                return row['id'], json.loads(row['body'])

    def event_done(self, eid):
        with self.connect() as db:
            db.execute("UPDATE inbox SET state='done',body='{}' WHERE id=?", (eid,))

    def recover(self):
        with self.connect() as db:
            # Never automatically replay a mutation that may have reached a node.
            db.execute("UPDATE operations SET state='unknown' WHERE state='pending'")
            db.execute("UPDATE inbox SET state='interrupted',body='{}' WHERE state='processing'")
            db.execute("UPDATE replacements SET state='unknown' WHERE state IN ('creating','committing','canceling')")
            db.execute("UPDATE admin_jobs SET state='unknown' WHERE state='processing'")

    @staticmethod
    def open_replacement_db(db, uid):
        return db.execute("SELECT * FROM replacements WHERE max_id=? AND state IN ('creating','ready','committing','canceling','unknown')", (uid,)).fetchone()

    @staticmethod
    def limit(db, person, at=None):
        """Free people: the static quota from the panel. Paid people: slots of grants active now."""
        if person['free']:
            return person['quota']
        at = int(at if at is not None else time.time())
        return db.execute('''SELECT coalesce(sum(slots),0) FROM grants WHERE person_id=? AND cancelled IS NULL
          AND starts<=? AND expires>?''', (person['id'], at, at)).fetchone()[0]

    @staticmethod
    def used(db, person_id):
        # A key queued for revocation is already unusable, so it must not keep blocking the quota.
        # Pre-billing keys of paid people are outside billing and do not take paid slots.
        free = db.execute('SELECT free FROM people WHERE id=?', (person_id,)).fetchone()
        legacy = '' if free is None or free[0] else ' AND legacy=0'
        count = db.execute("SELECT count(*) FROM profiles WHERE person_id=? AND state NOT IN ('revoked','revoking')" + legacy,
                           (person_id,)).fetchone()[0]
        # One temporary target is the SAME logical slot, not a new quota allowance.
        extra = db.execute('''SELECT count(*) FROM replacements r JOIN profiles p ON p.id=r.new_id
          JOIN profiles old ON old.id=r.old_id
          WHERE p.person_id=? AND p.state NOT IN ('revoked','revoking') AND old.state NOT IN ('revoked','revoking')
          AND r.state IN ('ready','committing','canceling','unknown')''',
                           (person_id,)).fetchone()[0]
        return max(0, count - extra)

    @staticmethod
    def unconfirmed_revocations(db, person_id):
        return db.execute('''SELECT count(*) FROM revocations r JOIN profiles p ON p.id=r.profile_id
          WHERE p.person_id=?''', (person_id,)).fetchone()[0]

    def usage(self, uid):
        person = self.person(uid)
        with self.connect() as db:
            return self.used(db, person['id']), self.limit(db, person)

    def open_replacement(self, uid):
        with self.connect() as db:
            row = self.open_replacement_db(db, uid)
            return dict(row) if row else None

    def replacement(self, uid, rid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM replacements WHERE id=? AND max_id=?', (rid, uid)).fetchone()
        if not row:
            raise Denied('Замена не найдена.')
        return dict(row)

    def start_replacement(self, uid, old_id, sid, mode):
        person = self.person(uid)
        old = self.owned(uid, old_id)
        if old['state'] not in ('active', 'paused') or sid not in json.loads(person['allowed_servers']):
            raise Denied('Профиль или целевой сервер недоступен.')
        if mode not in ('reissue', 'move', 'compromised'):
            raise ValueError('Invalid replacement mode')
        if mode == 'move' and sid == old['server_id']:
            raise Denied('Выберите другой сервер.')
        rid = secrets.token_hex(16)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.open_replacement_db(db, uid) or db.execute("SELECT 1 FROM operations WHERE max_id=? AND state IN ('pending','unknown')", (uid,)).fetchone():
                raise Denied('Есть незавершённая операция. Сначала завершите её или обратитесь к администратору.')
            if db.execute("SELECT 1 FROM admin_jobs WHERE person_id=? AND state IN ('pending','processing','unknown')", (person['id'],)).fetchone():
                raise Denied('Доступ отзывается администратором.')
            if self.unconfirmed_revocations(db, person['id']) >= self.MAX_UNCONFIRMED:
                raise Denied(self.REVOCATION_BACKLOG)
            # Switching is immediate; the deadline only reaps a row orphaned by a crash mid-operation.
            db.execute('INSERT INTO replacements VALUES(?,?,?,NULL,?,?,?,?,?)',
                       (rid, uid, old_id, sid, mode, 'creating', int(time.time()), int(time.time()) + 300))
            self.log(db, uid, 'replacement_started', rid)
        return rid

    def replacement_state(self, rid, state, new_id=None):
        with self.connect() as db:
            db.execute('UPDATE replacements SET state=?,new_id=coalesce(?,new_id) WHERE id=?', (state, new_id, rid))
            self.log(db, None, 'replacement_' + state, rid)

    def save_replacement_client(self, uid, rid, sid, proto, client):
        person = self.person(uid)
        self.inventory_one(sid, proto, client)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            replacement = db.execute("SELECT * FROM replacements WHERE id=? AND max_id=? AND state='creating'", (rid, uid)).fetchone()
            row = db.execute('SELECT * FROM profiles WHERE server_id=? AND protocol=? AND public_key=?', (sid, proto, client['public_key'])).fetchone()
            if not replacement or not row or row['person_id'] is not None:
                raise Denied('Не удалось сохранить владельца замены; нужна сверка.')
            db.execute('UPDATE profiles SET person_id=? WHERE id=?', (person['id'], row['id']))
            db.execute("UPDATE replacements SET new_id=?,state='ready' WHERE id=?", (row['id'], rid))
            self.log(db, uid, 'replacement_ready', rid)
        return row['id']

    # --- reachability from a restricted network (reported by infra/rf-probe) --------------------

    def save_reachability(self, report):
        """Store one probe report. Returns how many node results changed the stored state."""
        vantage = str(report.get('vantage', ''))
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,40}', vantage):
            raise Denied('Неверное имя точки проверки.')
        saved = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in report['results']:
                # 'error' means the probe itself broke: that is not evidence about the node.
                if item.get('status') not in ('ok', 'fail'):
                    continue
                sid, status, checked = int(item['id']), item['status'], int(item['checked'])
                old = db.execute('SELECT status,since,fails,checked FROM reachability WHERE vantage=? AND server_id=?',
                                 (vantage, sid)).fetchone()
                if old and checked <= old['checked']:
                    continue  # a replayed or out-of-order report must not rewind the state
                same = bool(old) and old['status'] == status
                fails = 0 if status == 'ok' else (old['fails'] if same else 0) + 1
                number = lambda key: int(item[key]) if isinstance(item.get(key), (int, float)) else None
                db.execute('''INSERT INTO reachability(vantage,server_id,name,status,stage,detail,handshake_ms,rtt_ms,
                exit_ip,icmp,checked,since,fails) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(vantage,server_id) DO UPDATE SET name=excluded.name,status=excluded.status,
                stage=excluded.stage,detail=excluded.detail,handshake_ms=excluded.handshake_ms,rtt_ms=excluded.rtt_ms,
                exit_ip=excluded.exit_ip,icmp=excluded.icmp,checked=excluded.checked,since=excluded.since,
                fails=excluded.fails''',
                           (vantage, sid, str(item.get('name') or f'Сервер {sid}')[:60], status,
                            str(item.get('stage', ''))[:20], str(item.get('detail', ''))[:200],
                            number('handshake_ms'), number('rtt_ms'),
                            str(item['exit_ip'])[:45] if item.get('exit_ip') else None,
                            None if item.get('icmp') is None else int(bool(item['icmp'])),
                            checked, old['since'] if same else checked, fails))
                saved += 1
        return saved

    def reachability(self):
        with self.connect() as db:
            online = {r['id']: r['online'] for r in db.execute('SELECT id,online FROM server_catalog')}
            return [dict(r, panel_online=online.get(r['server_id']))
                    for r in db.execute('SELECT * FROM reachability ORDER BY server_id,vantage')]

    def reachability_changes(self, confirm=REACH_CONFIRM):
        """Rows whose confirmed state differs from what the owner was last told."""
        return [r for r in self.reachability()
                if (r['status'] == 'fail' and r['fails'] >= confirm and r['notified'] != 'fail')
                or (r['status'] == 'ok' and r['notified'] == 'fail')]

    def reachability_notified(self, vantage, sid, status):
        with self.connect() as db:
            db.execute('UPDATE reachability SET notified=? WHERE vantage=? AND server_id=?', (status, vantage, sid))

    def unreachable_servers(self, now=None, confirm=REACH_CONFIRM, fresh=REACH_FRESH):
        """Server ids a fresh, confirmed probe could not use. Stale results warn nobody."""
        now = int(now if now is not None else time.time())
        return {r['server_id'] for r in self.reachability()
                if r['status'] == 'fail' and r['fails'] >= confirm and now - r['checked'] <= fresh}

    def catalog(self, protocols):
        with self.connect() as db:
            rows = {r['id']: dict(r) for r in db.execute('SELECT * FROM server_catalog')}
        return [dict(id=sid, name=rows.get(sid, {}).get('name', f'Сервер {sid}'),
                     online=rows.get(sid, {}).get('online'), protocol=proto) for sid, proto in protocols.items()]

    def update_catalog(self, servers):
        with self.connect() as db:
            for server in servers:
                db.execute('INSERT INTO server_catalog VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,online=excluded.online,updated=excluded.updated',
                           (server['id'], server['name'], server.get('last_check_ok'), int(time.time())))

    def admin_people(self):
        with self.connect() as db:
            result = []
            for row in db.execute('SELECT * FROM people ORDER BY id'):
                item = dict(row)
                item['max_id'] = str(item['max_id']) if item['max_id'] else None
                item['allowed_servers'] = json.loads(item['allowed_servers'])
                item['free'] = bool(item['free'])
                item['phone_verified'] = bool(item['phone_verified'])
                item['limit'] = self.limit(db, row)
                item['legacy'] = 0 if row['free'] else db.execute(
                    "SELECT count(*) FROM profiles WHERE person_id=? AND legacy=1 AND state IN ('active','paused')",
                    (row['id'],)).fetchone()[0]
                item['grants'] = [dict(g) for g in db.execute('''SELECT id,slots,starts,expires,source FROM grants
                  WHERE person_id=? AND cancelled IS NULL AND expires>? ORDER BY expires''', (row['id'], int(time.time())))]
                item['used'] = self.used(db, row['id'])
                result.append(item)
            return result

    def save_person(self, data, pid=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if pid is None:
                try:
                    pid = db.execute('INSERT INTO people(name,max_id,quota,allowed_servers,phone,active) VALUES(?,?,?,?,?,?)',
                                     (data['name'], data['max_id'], data['quota'], json.dumps(data['allowed_servers']), data['phone'], data['active'])).lastrowid
                except sqlite3.IntegrityError:
                    raise Denied('Этот MAX ID уже добавлен.') from None
            else:
                before = db.execute('SELECT free FROM people WHERE id=?', (pid,)).fetchone()
                if not before:
                    raise Denied('Пользователь не найден.')
                db.execute('UPDATE people SET name=?,quota=?,allowed_servers=?,phone=?,active=? WHERE id=?',
                           (data['name'], data['quota'], json.dumps(data['allowed_servers']), data['phone'], data['active'], pid))
                if before['free'] and data.get('free') is False and data['quota'] > 0:
                    # Losing the free flag must not stop keys at once: the current quota stays paid briefly.
                    now = int(time.time())
                    db.execute("INSERT INTO grants(person_id,slots,starts,expires,source) VALUES(?,?,?,?,'free_to_paid')",
                               (pid, data['quota'], now, now + billing.FREE_TO_PAID_GRACE))
            if data.get('free') is not None:
                # None keeps the flag: older panel builds do not send it.
                db.execute('UPDATE people SET free=? WHERE id=?', (int(bool(data['free'])), pid))
            self.log(db, None, 'person_updated' if data.get('id') else 'person_saved', pid)
        return pid
