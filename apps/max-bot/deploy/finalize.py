"""One-time post-acceptance inventory cleanup; run inside bot container."""
import asyncio,json,os
from vpnmax.api import Panel
from vpnmax.store import Store

async def main():
 protocols={int(k):v for k,v in json.loads(os.environ['PANEL_PROTOCOLS']).items()}
 panel=Panel(os.environ['PANEL_URL'],os.environ['PANEL_API_KEY'],protocols)
 store=Store(os.environ['DATABASE_PATH'])
 try:
  for sid,proto in protocols.items():
   rows=await panel.clients(sid)
   assert not any(c['name'].startswith('max-accept-') for c in rows), 'Live acceptance profile still exists'
   store.inventory(sid,proto,rows)
   print('Server',sid,'current profiles',len(rows))
  with store.connect() as db:
   cur=db.execute("DELETE FROM profiles WHERE name LIKE 'max-accept-%' AND state='missing' AND person_id IS NULL")
   print('Temporary registry entries cleaned:',cur.rowcount)
   print('Registry states:',[(r[0],r[1]) for r in db.execute('SELECT state,count(*) FROM profiles GROUP BY state')])
   print('Owner:',[(r[0],r[1]) for r in db.execute('SELECT max_id,active FROM people')])
   print('Webhook events:',[(r[0],r[1]) for r in db.execute('SELECT state,count(*) FROM inbox GROUP BY state')])
  assert store.person(100000001)['active']
 finally:await panel.http.aclose()

try:asyncio.run(main())
except Exception as e:
 print('Final inventory failed:',type(e).__name__)
 raise SystemExit(1)
