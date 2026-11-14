import asyncio,json,sqlite3
import pytest
from fastapi.testclient import TestClient
from vpnmax.api import RemoteError
from vpnmax.app import create_app
from vpnmax.bot import Bot
from vpnmax.store import Store,Denied

class Panel:
 def __init__(self):self.rows={i:[] for i in [1,2,3,4]};self.calls=[];self.fail=None;self.serial=0;self.vanish=False
 async def issue(self,sid,name):
  self.calls.append(('issue',sid));self.serial+=1
  c=dict(public_key='secret-public-'+str(self.serial),name=name,has_config=True,paused=False)
  if self.fail=='issue-before':raise RemoteError('failure')
  if not self.vanish:self.rows[sid].append(c)
  if self.fail=='issue-after':raise RemoteError('ambiguous issue')
  return {'client':c}
 async def clients(self,sid):
  self.calls.append(('clients',sid))
  if self.fail=='read':raise RemoteError('unreachable')
  return [dict(c) for c in self.rows[sid]]
 async def servers(self):return [dict(id=s,name=f'Server {s}',last_check_ok=True) for s in self.rows]
 async def config(self,row):self.calls.append(('config',row['server_id']));return {'config_amnezia':'vpn://SECRET-EXPORT'}
 async def mutate(self,action,row):
  sid=row['server_id'];self.calls.append((action,sid))
  if self.fail=='mutate-before':raise RemoteError('ambiguous')
  for c in list(self.rows[sid]):
   if c['public_key']==row['public_key']:
    if action=='revoke':self.rows[sid].remove(c)
    else:c['paused']=action=='pause'
  if self.fail=='mutate-after':raise RemoteError('ambiguous applied')
class Max:
 async def send(self,*args):pass

# Issuance/replacement mechanics are tested on free people; billing has its own tests.
def data(uid=100,quota=1,active=True,servers=None,free=True):return dict(max_id=uid,name='Person',quota=quota,phone='',active=active,free=free,allowed_servers=[1,2,3,4] if servers is None else servers)
@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path/'db');s.save_person(data());s.save_person(data(200));p=Panel()
 c=asyncio.run(p.issue(1,'Original'))['client'];pid=s.issued(100,1,'awg',c);p.calls.clear()
 return s,p,Bot(s,p,Max(),{1:'awg',2:'awg',3:'awg3',4:'awg'}),pid
async def start(b,pid,cmd='/reissue',target=''):
 _,buttons=await b.command(100,f'{cmd} {pid}'+target)
 if cmd=='/reissue' and not target:
  sid=b.store.owned(100,pid)['server_id']
  _,buttons=await b.command(100,f'/reissue {pid} {sid}')
 return await b.command(100,buttons[0][0]['payload'])

async def stranded(b,pid,sid=2):
 """Replacement left in 'ready' by a crash between issuing the target and revoking the source."""
 rid=b.store.start_replacement(100,pid,sid,'move')
 client=(await b.panel.issue(sid,'Stranded'))['client']
 b.store.save_replacement_client(100,rid,sid,b.protocols[sid],client)
 return rid

def state_of(s,pid):
 with s.connect() as db:return db.execute('SELECT state FROM profiles WHERE id=?',(pid,)).fetchone()[0]

def test_full_quota_move_revokes_old_immediately(env):
 s,p,b,pid=env
 async def run():
  text,_=await start(b,pid,'/move',' 3')
  # No confirmation window: one command issues the target and drops the source.
  assert s.open_replacement(100) is None and state_of(s,pid)=='revoked'
  assert p.rows[1]==[] and len(p.rows[3])==1
  assert s.usage(100)==(1,1) and len(s.profiles(100))==1 and 'отозван' in text[0] and text[1]=='vpn://SECRET-EXPORT'
 asyncio.run(run())

def latest_replacement(s):
 with s.connect() as db:return dict(db.execute('SELECT * FROM replacements ORDER BY created DESC,rowid DESC').fetchone())

def test_stale_switch_and_cancel_buttons_are_inert(env):
 s,p,b,pid=env
 async def run():
  await start(b,pid,'/move',' 3')
  rid=latest_replacement(s)['id'];calls=len(p.calls)
  for cmd in ('/switch ','/cancel '):
   with pytest.raises(Denied):await b.command(100,cmd+rid)
  with pytest.raises(Denied):await b.command(200,'/switch '+rid)
  assert len(p.calls)==calls and state_of(s,pid)=='revoked'
 asyncio.run(run())

def test_crashed_replacement_can_still_be_cancelled(env):
 s,p,b,pid=env
 async def run():
  rid=await stranded(b,pid)
  assert s.open_replacement(100)['state']=='ready'
  await b.command(100,'/cancel '+rid)
  assert len(p.rows[1])==1 and p.rows[2]==[] and s.usage(100)==(1,1)
 asyncio.run(run())

@pytest.mark.parametrize('disabled',[False,True])
def test_expiry_cancels_target_even_when_owner_blocked(env,disabled):
 s,p,b,pid=env
 async def run():
  rid=await stranded(b,pid)
  with s.connect() as db:
   db.execute('UPDATE replacements SET expires=0 WHERE id=?',(rid,))
   if disabled:db.execute('UPDATE people SET active=0 WHERE max_id=100')
  with pytest.raises(Denied):await b.command(100,'/switch '+rid)
  await b.expire_replacements()
  assert len(p.rows[1])==1 and p.rows[2]==[] and s.replacement(100,rid)['state']=='cancelled'
 asyncio.run(run())

def test_missing_target_never_revokes_original(env):
 s,p,b,pid=env;p.vanish=True
 async def run():
  with pytest.raises(Denied):await start(b,pid,'/move',' 2')
  assert len(p.rows[1])==1 and state_of(s,pid)=='active'
  assert s.open_replacement(100)['state']=='unknown'
 asyncio.run(run())

@pytest.mark.parametrize('mode',['issue-before','issue-after'])
def test_issue_failure_blocks_retry_preserves_old(env,mode):
 s,p,b,pid=env;p.fail=mode
 async def run():
  with pytest.raises(RemoteError):await start(b,pid,'/move',' 2')
  assert len(p.rows[1])==1 and s.open_replacement(100)['state']=='unknown'
  calls=len(p.calls)
  with pytest.raises(Denied):await b.command(100,'/new 1 retry')
  assert len(p.calls)==calls
 asyncio.run(run())

@pytest.mark.parametrize('mode',['mutate-before','mutate-after'])
def test_unconfirmed_old_key_is_queued_not_lost(env,mode):
 s,p,b,pid=env;p.fail=mode
 async def run():
  text,_=await start(b,pid,'/move',' 2')
  # The new key is usable, the old one is never silently left active.
  assert len(p.rows[2])==1 and state_of(s,pid)=='revoking' and 'очереди на отзыв' in text[0]
  assert s.usage(100)==(1,1) and [r['id'] for r in s.due_revocations(0)]==[pid]
  p.fail=None
  assert await b.process_revocations(0)==1
  assert state_of(s,pid)=='revoked' and p.rows[1]==[] and s.due_revocations(0)==[]
 asyncio.run(run())

def test_compromise_revokes_before_issue_and_counts_new(env):
 s,p,b,pid=env;asyncio.run(start(b,pid,'/compromised'))
 assert p.calls[:2]==[('revoke',1),('issue',1)] and s.usage(100)==(1,1) and s.open_replacement(100) is None

def test_compromise_failure_never_restores_stolen_key(env):
 s,p,b,pid=env;p.fail='issue-before'
 with pytest.raises(RemoteError):asyncio.run(start(b,pid,'/compromised'))
 assert p.rows[1]==[] and s.open_replacement(100)['state']=='unknown'
 with pytest.raises(Denied):asyncio.run(b.command(100,'/new 1 retry'))

def test_permissions_reject_without_panel_calls(env):
 s,p,b,pid=env;s.save_person(data(servers=[1]),1)
 for cmd in ['/new 2 phone',f'/move {pid} 2','/key 999','/device 4']:
  with pytest.raises(Denied):asyncio.run(b.command(100,cmd))
 with pytest.raises(Denied):asyncio.run(b.command(200,f'/reissue {pid}'))
 assert p.calls==[]

def test_paused_missing_shared_quota_and_lowering_limit(env):
 s,p,b,pid=env
 for state in ['paused','missing']:
  s.set_state(pid,state);assert s.usage(100)==(1,1)
 s.save_person(data(quota=0),1)
 with pytest.raises(Denied):asyncio.run(b.command(100,'/new 3 extra'))
 assert len(p.rows[1])==1 and p.calls==[]

def test_creating_crash_becomes_unknown_without_replay(env):
 s,p,b,pid=env;rid=s.start_replacement(100,pid,2,'move');s.recover()
 assert s.replacement(100,rid)['state']=='unknown' and p.calls==[]

def test_ready_restart_survives_and_cancels(env):
 s,p,b,pid=env;rid=asyncio.run(stranded(b,pid));s.recover()
 assert s.replacement(100,rid)['state']=='ready'
 asyncio.run(b.command(100,'/cancel '+rid));assert s.usage(100)==(1,1)

def test_partial_revoke_does_not_claim_success(env):
 s,p,b,pid=env;p.fail='mutate-after';r=asyncio.run(b.revoke_all(1))
 assert r['unconfirmed']==[pid]
 with pytest.raises(Denied):s.person(100)
 with s.connect() as db:assert db.execute('SELECT state FROM profiles WHERE id=?',(pid,)).fetchone()[0]!='revoked'

def test_revoke_all_reports_unknown_orphan(env):
 s,p,b,pid=env;p.fail='issue-after'
 with pytest.raises(RemoteError):asyncio.run(start(b,pid,'/move',' 2'))
 p.fail=None;r=asyncio.run(b.revoke_all(1))
 assert r['uncertain_operations'] and s.open_replacement(100)['state']=='unknown'

def test_admin_auth_crud_duplicate_assignment_and_redaction(env):
 s,p,b,pid=env;c=TestClient(create_app(s,b,'w'*48,'a'*48));h={'X-Registry-Admin':'a'*48}
 assert c.get('/internal/admin/state').status_code==403
 assert c.post('/internal/admin/people',json=data(300)).status_code==403
 assert c.post('/internal/admin/people',headers=h,json=data(300,quota=2)).status_code==201
 assert s.person(300)['quota']==2
 assert c.post('/internal/admin/people',headers=h,json=data(300)).status_code==409
 snap=c.get('/internal/admin/state',headers=h);assert snap.status_code==200
 assert 'secret-public' not in snap.text and 'SECRET-EXPORT' not in snap.text
 assert snap.json()['people'][0]['max_id']=='100'
 assert c.put('/internal/admin/people/1',headers=h,json=data(servers=[999])).status_code==422
 assert c.post(f'/internal/admin/profiles/{pid}/assign',headers=h,json={'person_id':3}).status_code==409

def test_block_bot_does_not_revoke_vpn(env):
 s,p,b,pid=env;s.save_person(data(active=False),1)
 with pytest.raises(Denied):asyncio.run(b.command(100,'/my'))
 assert len(p.rows[1])==1 and p.calls==[]

def test_bulk_job_queued_once_blocks_user_immediately(env):
 s,p,b,pid=env;c=TestClient(create_app(s,b,'w'*48,'a'*48));h={'X-Registry-Admin':'a'*48}
 one=c.post('/internal/admin/people/1/revoke-all',headers=h);two=c.post('/internal/admin/people/1/revoke-all',headers=h)
 assert one.status_code==two.status_code==202 and one.json()==two.json()
 with pytest.raises(Denied):s.person(100)
 assert p.calls==[]

def test_legacy_migration_preserves_identity(tmp_path):
 path=tmp_path/'old.db'
 with sqlite3.connect(path) as db:
  db.execute('CREATE TABLE people(id INTEGER PRIMARY KEY,name TEXT NOT NULL,max_id INTEGER UNIQUE,active INTEGER NOT NULL DEFAULT 1)')
  db.execute("INSERT INTO people VALUES(1,'Legacy',123,1)")
 s=Store(path);assert s.person(123)['quota']==5 and json.loads(s.person(123)['allowed_servers'])==[1,2,3,4]

def test_bulk_steps_do_not_retry_failures_or_starve_other_work(env):
 s,p,b,pid=env
 s.save_person(data(quota=3),1)
 second=asyncio.run(p.issue(2,'Second'))['client'];s.issued(100,2,'awg',second)
 p.calls.clear();p.fail='mutate-before'
 first=asyncio.run(b.revoke_all(1,one_step=True))
 assert first['remaining']==1 and first['unconfirmed']==[pid]
 assert len(p.calls)==1
 p.fail=None
 final=asyncio.run(b.revoke_all(1,previous=first,one_step=True))
 assert final['remaining']==0 and final['revoked']==1 and final['unconfirmed']==[pid]
 assert p.calls==[('revoke',1),('revoke',2)]
