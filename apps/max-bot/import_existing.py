"""Read-only initial import via verified SSH; never imports private configs.

Runs the panel's existing read functions as its actual UID (10001), without
creating an API key or modifying deployment. Standalone staging utility only.
"""
import json
import os
import subprocess

from vpnmax.store import Store

REMOTE = '''
import asyncio,json
from sqlalchemy import select
from app.config import get_settings
from app.db import create_engine_and_factory
from app.models import Server
from app import awg
from app.api.awg import _connect,build_client_list
from app.api.awg3 import get_awg3
async def main():
    engine,factory=create_engine_and_factory(get_settings().db_url)
    try:
        async with factory() as session:
            servers=(await session.scalars(select(Server).order_by(Server.id))).all()
            result=[]
            for server in servers:
                proto='awg3' if server.id==3 else 'awg'
                if proto=='awg3':
                    state=await get_awg3(server.id,None,session)
                    clients=[c.model_dump(mode='json') for c in state.clients]
                else:
                    async with _connect(server) as conn:
                        state=await awg.read_state(conn,server.host)
                    clients=await build_client_list(session,server.id,state)
                result.append({'id':server.id,'protocol':proto,'clients':[
                    {'name':c['name'],'public_key':c['public_key'],
                     'has_config':c.get('has_config',False),'paused':c.get('paused',False)}
                    for c in clients]})
            print(json.dumps(result))
    finally:
        await engine.dispose()
asyncio.run(main())
'''


def main():
    os.umask(0o077)
    command = ['ssh', '-i', '/home/operator/.ssh/id_ed25519_vpn', '-o', 'BatchMode=yes',
               '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', 'root@203.0.113.30',
               'docker exec -i --user 10001 amnezia-control-backend-1 python -']
    result = subprocess.run(command, input=REMOTE, text=True, capture_output=True, timeout=120)
    if result.returncode:
        # A traceback can include server credentials. Never print it.
        error = result.stderr.splitlines()[-1].split(':')[0] if result.stderr else 'unknown'
        raise SystemExit('Import failed; no rows imported. Error class: ' + error)
    servers = json.loads(result.stdout)
    store = Store(os.environ.get('DATABASE_PATH', '/home/operator/.local/share/vpn-max/clients.sqlite3'))
    for server in servers:
        store.inventory(server['id'], server['protocol'], server['clients'])
        print(json.dumps({'server_id': server['id'], 'protocol': server['protocol'],
                          'profiles': len(server['clients']),
                          'saved_configs': sum(bool(c['has_config']) for c in server['clients'])}))
    print('Protected staging registry updated. No owners inferred; no remote mutations.')


if __name__ == '__main__':
    main()
