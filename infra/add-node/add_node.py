#!/usr/bin/env python3
"""Add a spare VPN node end to end: panel, bot, reachability probe. Safe to re-run.

    add_node.py --manager <ssh-alias> --host <new server address> --name "Amnezia — City" \
                --vantage <name>=<ssh-alias> [--port 47300] [--ssh-user root] [--ssh-port 22] [--dry-run]

Runs on the operator host. Before the first run the new server must accept the panel's SSH key
(the script prints it when the panel cannot log in) and have Docker available to that user.

Steps, each skipped when already done:
  1. panel: server record, SSH check, AmneziaWG 3 deployment on the chosen UDP port;
  2. bot: server added to PANEL_PROTOCOLS, the bot's panel API key scope and every person's
     allowed servers; the bot container alone is recreated;
  3. probe: a service profile "rf-probe <vantage>" and its target file on each vantage host;
  4. verification: a real probe run for the new node and the bot's own view of it.

Panel calls use an administrator token minted inside the backend container for five minutes and
kept in memory only; password, 2FA and token version are not touched. This needs root on the
manager, which already implies full control of the panel. Nothing here touches existing nodes.
"""
import argparse
import json
import re
import subprocess
import sys
import time

BACKEND = 'amnezia-control-backend-1'
BOT = 'amnezia-control-vpn-max-1'
DATABASE = 'amnezia-control-db-1'
BOT_ENV = '/opt/vpn-max/.env'
COMPOSE_DIR = '/opt/amnezia-control'
API_KEY_NAME = 'vpn-max-self-service'

PANEL_CALL = r'''
import asyncio,json,urllib.request,urllib.error
from sqlalchemy import select
from app.models import User
from app.db import create_engine_and_factory
from app.config import get_settings
from app.security import create_access_token
async def token():
    s=get_settings(); e,f=create_engine_and_factory(s.db_url)
    async with f() as db:
        u=await db.scalar(select(User).where(User.username==s.admin_user))
        t=create_access_token(u.username,s.jwt_secret,5,u.token_version)
    await e.dispose()
    return t
method,path,body=REQUEST
req=urllib.request.Request('http://127.0.0.1:8000/api'+path,data=None if body is None else json.dumps(body).encode(),method=method,
    headers={'Authorization':'Bearer '+asyncio.run(token()),'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req,timeout=120) as r:
        data=r.read(); print(json.dumps({'status':r.status,'body':json.loads(data) if data else None}))
except urllib.error.HTTPError as e:
    print(json.dumps({'status':e.code,'body':None}))
'''


def ssh(host, command, data=None, check=True, timeout=200):
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', host, command], input=data, text=True, capture_output=True, timeout=timeout)
    if check and p.returncode:
        raise SystemExit(f'FAILED on {host}: {command[:60]}...\n{p.stderr[-600:]}')
    return p.stdout


class Panel:
    def __init__(self, manager):
        self.manager = manager

    def call(self, method, path, body=None):
        code = PANEL_CALL.replace('REQUEST', repr([method, path, body]))
        return json.loads(ssh(self.manager, f'docker exec -i --user 10001 {BACKEND} python -', code))


def say(step, text):
    print(f'[{step}] {text}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--manager', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--port', type=int, default=47300)
    parser.add_argument('--ssh-user', default='root')
    parser.add_argument('--ssh-port', type=int, default=22)
    parser.add_argument('--vantage', action='append', default=[], help='name=ssh-alias, repeatable')
    parser.add_argument('--dry-run', action='store_true', help='report what would change, change nothing')
    args = parser.parse_args()
    vantages = dict(v.split('=', 1) for v in args.vantage)
    if not all(re.fullmatch(r'[A-Za-z0-9._-]{1,40}', v) for v in vantages):
        raise SystemExit('bad vantage name')
    panel, dry = Panel(args.manager), args.dry_run

    # 1. panel -------------------------------------------------------------------------------
    servers = panel.call('GET', '/servers')
    if servers['status'] != 200:
        raise SystemExit(f"panel refused the request: HTTP {servers['status']}")
    server = next((s for s in servers['body'] if s['host'] == args.host), None)
    if server:
        say('panel', f"server record exists: id {server['id']} «{server['name']}»")
    elif dry:
        say('panel', 'WOULD create the server record, check SSH and deploy AmneziaWG 3')
        say('dry-run', 'stopping here: the remaining steps need the server id')
        return
    else:
        created = panel.call('POST', '/servers', dict(name=args.name, host=args.host, ssh_port=args.ssh_port,
                                                      ssh_user=args.ssh_user, note='Added by add_node.py', group_name=''))
        if created['status'] != 201:
            raise SystemExit(f"panel did not create the server: HTTP {created['status']}")
        server = created['body']
        say('panel', f"server record created: id {server['id']}")
    sid = server['id']
    if not dry:
        check = panel.call('POST', f'/servers/{sid}/check')
        if check['status'] != 200 or not check['body'].get('last_check_ok'):
            key = ssh(args.manager, f'cat {COMPOSE_DIR}/data/ssh/id_ed25519.pub', check=False).strip()
            raise SystemExit('The panel cannot log in to the new server over SSH. Add this key to '
                             f'authorized_keys of {args.ssh_user} there and run again:\n{key}')
        say('panel', 'SSH check passed')
    state = panel.call('GET', f'/servers/{sid}/awg3')
    deployed = state['status'] == 200 and state['body'].get('listen_port')
    if deployed:
        say('panel', f"AmneziaWG 3 already deployed on UDP {state['body']['listen_port']}, clients: {len(state['body']['clients'])}")
    elif dry:
        say('panel', f'WOULD deploy AmneziaWG 3 on UDP {args.port}')
    else:
        started = panel.call('POST', f'/servers/{sid}/awg3/deploy', {'port': args.port})
        if started['status'] != 202:
            raise SystemExit(f"deployment was not accepted: HTTP {started['status']}")
        for _ in range(120):
            time.sleep(10)
            status = panel.call('GET', f'/servers/{sid}/awg3/deploy/status')['body'] or {}
            if status.get('state') in ('done', 'error'):
                break
        if status.get('state') != 'done':
            raise SystemExit(f"deployment did not finish: {status.get('state')}. Check the panel before running again.")
        state = panel.call('GET', f'/servers/{sid}/awg3')
        say('panel', f"AmneziaWG 3 deployed on UDP {state['body']['listen_port']}")

    # 2. bot ---------------------------------------------------------------------------------
    line = ssh(args.manager, f"grep '^PANEL_PROTOCOLS=' {BOT_ENV}").strip()
    protocols = json.loads(line.split('=', 1)[1])
    if str(sid) in protocols:
        say('bot', 'server already in PANEL_PROTOCOLS')
    elif dry:
        say('bot', 'WOULD add the server to PANEL_PROTOCOLS and recreate the bot container')
    else:
        protocols[str(sid)] = 'awg3'
        new = 'PANEL_PROTOCOLS=' + json.dumps(protocols, separators=(',', ':'))
        ssh(args.manager, f"umask 077; cp -p {BOT_ENV} {BOT_ENV}.before-node{sid} && python3 -c \"import sys;p='{BOT_ENV}';"
            f"L=open(p).read().splitlines();i=[n for n,l in enumerate(L) if l.startswith('PANEL_PROTOCOLS=')];assert len(i)==1;"
            f"L[i[0]]=sys.stdin.read().strip();open(p,'w').write(chr(10).join(L)+chr(10))\"", new)
        say('bot', 'PANEL_PROTOCOLS updated')
    scope = ssh(args.manager, f"docker exec {DATABASE} psql -U acontrol -d acontrol -Atc \"select id,servers from api_keys "
                f"where name='{API_KEY_NAME}' and not revoked order by id desc limit 1\"").strip()
    key_id, _, key_servers = scope.partition('|')
    wanted = ','.join(sorted(set(key_servers.split(',')) | {str(sid)}, key=int)) if key_servers else ''
    if not key_id:
        raise SystemExit('the bot API key was not found in the panel')
    if not key_servers or str(sid) in key_servers.split(','):
        say('bot', 'API key scope already covers the server')
    elif dry:
        say('bot', f'WOULD extend the API key scope to {wanted}')
    else:
        ssh(args.manager, f'docker exec -i {DATABASE} psql -v ON_ERROR_STOP=1 -q -U acontrol -d acontrol',
            f"BEGIN; UPDATE api_keys SET servers='{wanted}' WHERE id={int(key_id)} AND NOT revoked;"
            f"INSERT INTO audit_log(username,action,target,detail) VALUES ('admin','apikey_update','{API_KEY_NAME}',"
            f"'scope extended to server {sid} by add_node.py'); COMMIT;")
        say('bot', f'API key scope extended to {wanted}')
    if not dry and str(sid) in protocols and line != 'PANEL_PROTOCOLS=' + json.dumps(protocols, separators=(',', ':')):
        files = ssh(args.manager, f"docker inspect {BOT} --format '{{{{index .Config.Labels \"com.docker.compose.project.config_files\"}}}}'").strip()
        flags = ' '.join('-f ' + f for f in files.split(','))
        ssh(args.manager, f'cd {COMPOSE_DIR} && docker compose -p amnezia-control {flags} up -d --no-deps --force-recreate vpn-max')
        for _ in range(45):
            if ssh(args.manager, f"docker inspect {BOT} --format '{{{{.State.Health.Status}}}}'", check=False).strip() == 'healthy':
                break
            time.sleep(2)
        else:
            raise SystemExit(f'bot is not healthy after recreation; previous settings are in {BOT_ENV}.before-node{sid}')
        say('bot', 'container recreated and healthy')
    people = ('import json,os,sqlite3\ndb=sqlite3.connect(os.environ["DATABASE_PATH"],timeout=20)\nn=0\n'
              'for pid,a in db.execute("select id,allowed_servers from people").fetchall():\n'
              f'    v=json.loads(a)\n    if {sid} not in v:\n        n+=1\n'
              + ('' if dry else f'        db.execute("update people set allowed_servers=? where id=?",(json.dumps(sorted(v+[{sid}])),pid))\n')
              + 'db.commit(); print(n)\n')
    missing = ssh(args.manager, f'docker exec -i {BOT} python -', people).strip()
    say('bot', f"people {'who WOULD get' if dry else 'given'} the server: {missing}")

    # 3. probe -------------------------------------------------------------------------------
    clients = {c['name']: c for c in (panel.call('GET', f'/servers/{sid}/awg3')['body'] or {}).get('clients', [])}
    for vantage, alias in vantages.items():
        profile = f'rf-probe {vantage}'
        present = ssh(alias, f'sudo -n test -f /etc/rf-probe/targets/{sid}.conf && echo yes || echo no').strip() == 'yes'
        if profile in clients and present:
            say('probe', f'{vantage}: profile and target file exist')
            continue
        if profile in clients and not present:
            raise SystemExit(f'{vantage}: profile «{profile}» exists in the panel but its target file is missing on the vantage '
                             'host. Its private key cannot be read back: revoke that profile in the panel and run again.')
        if dry:
            say('probe', f'{vantage}: WOULD issue «{profile}» and install the target file')
            continue
        issued = panel.call('POST', f'/servers/{sid}/awg3/clients', {'name': profile, 'note': 'Service: reachability probe. Do not revoke.'})
        if issued['status'] != 201:
            raise SystemExit(f"{vantage}: profile was not issued: HTTP {issued['status']}")
        head = '# rf-probe ' + json.dumps({'id': sid, 'name': re.sub(r'^Amnezia\s*—\s*', '', args.name), 'engine': 'awg3'}, ensure_ascii=False)
        ssh(alias, f'sudo -n sh -c "umask 077; cat > /etc/rf-probe/targets/{sid}.conf"', head + '\n' + issued['body']['config'].strip() + '\n')
        say('probe', f'{vantage}: profile issued, target file installed')

    # 4. verification ------------------------------------------------------------------------
    if dry:
        say('dry-run', 'nothing was changed')
        return
    ok = True
    for vantage, alias in vantages.items():
        report = json.loads(ssh(alias, f'sudo -n /opt/rf-probe/probe.py --vantage {vantage} --only {sid}'))
        result = report['results'][0] if report['results'] else {'status': 'error', 'stage': 'no target', 'detail': ''}
        say('verify', f"{vantage}: {result['status']} ({result['stage']}) {result.get('detail', '')}")
        ok = ok and result['status'] == 'ok'
    seen = ssh(args.manager, f'docker exec -i {BOT} python -',
               'import asyncio,json,os\nfrom vpnmax.api import Panel\n'
               'p={int(k):v for k,v in json.loads(os.environ["PANEL_PROTOCOLS"]).items()}\n'
               f'async def f():\n    x=Panel(os.environ["PANEL_URL"],os.environ["PANEL_API_KEY"],p)\n    print(len(await x.clients({sid})))\n'
               'asyncio.run(f())\n', check=False).strip()
    say('verify', f"bot reads the node through its own key: {'yes, clients ' + seen if seen.isdigit() else 'NO'}")
    say('done' if ok and seen.isdigit() else 'ATTENTION', f'server id {sid}. The mark in the bot appears after the next scheduled probe run.')
    return 0 if ok and seen.isdigit() else 1


if __name__ == '__main__':
    sys.exit(main())
