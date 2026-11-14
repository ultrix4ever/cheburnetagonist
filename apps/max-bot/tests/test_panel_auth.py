"""A rejected panel key must not silently lock users out, and the owner must hear about it."""
import asyncio
import pytest
from test_v2 import Panel,data,state_of
from vpnmax.api import RemoteError
from vpnmax.bot import Bot
from vpnmax.store import Store

OWNER=999


class Max:
 def __init__(self):self.sent=[]
 async def send(self,uid,text,buttons=None):self.sent.append((uid,text))


class RejectingPanel(Panel):
 status=None
 attempts=0
 def gate(self):
  self.attempts+=1
  if self.status:raise RemoteError(f'Panel HTTP {self.status}',self.status)
 async def issue(self,sid,name):self.gate();return await super().issue(sid,name)
 async def clients(self,sid):self.gate();return await super().clients(sid)
 async def mutate(self,action,row):self.gate();return await super().mutate(action,row)


@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path/'db');s.save_person(data(quota=3));p=RejectingPanel();m=Max()
 c=asyncio.run(p.issue(1,'Original'))['client'];pid=s.issued(100,1,'awg',c);p.calls.clear()
 return s,p,m,Bot(s,p,m,{1:'awg',2:'awg',3:'awg3',4:'awg'},owner=OWNER),pid

def callback(text):
 return {'update_type':'message_callback','message':{'recipient':{'chat_type':'dialog'}},
         'callback':{'user':{'user_id':100},'payload':text,'callback_id':'x'}}

def operations(s):
 with s.connect() as db:return [r[0] for r in db.execute('SELECT state FROM operations ORDER BY created,rowid')]


def test_rejected_issue_does_not_block_and_alerts_owner_once(env):
 s,p,m,b,pid=env;p.status=401
 async def run():
  await b.event(callback('/new 2 Ключ'));await b.event(callback('/new 2 Ключ'))
  assert operations(s)==['failed','failed']
  user=[t for u,t in m.sent if u==100]
  assert all('Ничего не изменено' in t for t in user)
  owner=[t for u,t in m.sent if u==OWNER]
  assert len(owner)==1 and 'API-ключ' in owner[0] and b.panel_status['ok'] is False
  p.status=None
  await b.event(callback('/new 2 Ключ'))
  assert operations(s)[-1]=='done' and s.usage(100)==(2,3)
  await b.sync_server(1)
  assert b.panel_status['ok'] is True and 'восстановлена' in m.sent[-1][1]
 asyncio.run(run())


def test_ambiguous_failure_still_requires_reconciliation(env):
 s,p,m,b,pid=env;p.status=502
 asyncio.run(b.event(callback('/new 2 Ключ')))
 assert operations(s)==['unknown'] and not [u for u,_ in m.sent if u==OWNER]


def test_rejected_replacement_issue_leaves_old_key_and_no_open_replacement(env):
 s,p,m,b,pid=env
 async def run():
  _,buttons=await b.command(100,f'/reissue {pid} 2')
  p.status=401
  await b.event(callback(buttons[0][0]['payload']))
  assert s.open_replacement(100) is None and state_of(s,pid)=='active' and len(p.rows[1])==1
  p.status=None
  _,buttons=await b.command(100,f'/reissue {pid} 2')
  await b.command(100,buttons[0][0]['payload'])
  assert state_of(s,pid)=='revoked' and len(p.rows[2])==1
 asyncio.run(run())


def test_dead_server_is_skipped_for_rest_of_revocation_pass(env):
 s,p,m,b,pid=env
 others=[s.issued(100,1,'awg',asyncio.run(p.issue(1,f'K{i}'))['client']) for i in range(2)]
 for x in [pid]+others:s.queue_revocation(x,100,'test')
 p.status=401;p.attempts=0
 assert asyncio.run(b.process_revocations(0))==0
 assert p.attempts==1
 assert s.due_revocations()==[] and all(r["attempts"]==1 for r in s.revocation_backlog())
