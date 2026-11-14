"""Operator-only acceptance helper. Reads JSON on stdin; no MAX messages are sent.
Run via SSH/docker exec; caller MUST capture output because config may be returned.
"""
import asyncio
import json
import os
import sys
import urllib.request
import urllib.error

from vpnmax.api import Panel
from vpnmax.bot import Bot
from vpnmax.store import Store, Denied

request=json.load(sys.stdin)
async def main():
    store=Store(os.environ['DATABASE_PATH'])
    panel=Panel(os.environ['PANEL_URL'],os.environ['PANEL_API_KEY'],{int(k):v for k,v in json.loads(os.environ['PANEL_PROTOCOLS']).items()})
    bot=Bot(store,panel,None,panel.protocols)
    try:
        kind=request['kind']
        if kind=='command':
            reply,buttons=await bot.command(request['uid'],request['command'])
            return {'reply':reply,'buttons':buttons}
        if kind=='replacement':return store.open_replacement(request['uid'])
        if kind=='config':return await panel.config(store.owned(request['uid'],request['pid']))
        if kind=='expire':
            row=store.open_replacement(request['uid'])
            with store.connect() as db:db.execute('UPDATE replacements SET expires=0 WHERE id=?',(row['id'],))
            return {'expired':True}
        if kind=='catalog':
            store.update_catalog(await panel.servers())
            return {'ok':True}
        if kind=='cleanup':
            ids=request['uids']
            for uid in ids:
                with store.connect() as db:
                    person=db.execute('SELECT * FROM people WHERE max_id=?',(uid,)).fetchone()
                    if not person:continue
                    assert person['name'].startswith('v2accept-')
                result=await bot.revoke_all(person['id'])
                if result['unconfirmed'] or result['uncertain_operations']:
                    return {'cleanup_failed':True,'result':result}
                with store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    assert not db.execute("SELECT 1 FROM profiles WHERE person_id=? AND state!='revoked'",(person['id'],)).fetchone()
                    db.execute('DELETE FROM replacements WHERE max_id=?',(uid,))
                    db.execute('DELETE FROM operations WHERE max_id=?',(uid,))
                    db.execute('DELETE FROM confirmations WHERE max_id=?',(uid,))
                    db.execute('DELETE FROM admin_jobs WHERE person_id=?',(person['id'],))
                    db.execute('DELETE FROM invites WHERE person_id=?',(person['id'],))
                    db.execute('DELETE FROM profiles WHERE person_id=?',(person['id'],))
                    db.execute('DELETE FROM people WHERE id=?',(person['id'],))
            return {'cleanup_failed':False}
        raise ValueError('Unknown RPC')
    finally:await panel.http.aclose()
try:print(json.dumps({'ok':True,'result':asyncio.run(main())},ensure_ascii=False))
except Exception as exc:
    print(json.dumps({'ok':False,'error':type(exc).__name__,'message':str(exc) if isinstance(exc,Denied) else ''},ensure_ascii=False))
