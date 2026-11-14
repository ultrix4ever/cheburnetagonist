"""Revocation must survive an unreachable node: intent is durable and retried until the node confirms."""
import asyncio
import pytest
from test_v2 import Panel,data,env,state_of
from vpnmax.bot import Bot
from vpnmax.store import Denied,Store


def queued(s):return [r['id'] for r in s.due_revocations(0)]

async def revoke(b,pid):
 _,buttons=await b.command(100,f'/revoke {pid}')
 return await b.command(100,buttons[0][0]['payload'])


def test_unreachable_server_queues_revocation_instead_of_losing_it(env):
 s,p,b,pid=env;p.fail='mutate-before'
 async def run():
  text,_=await revoke(b,pid)
  # Key is unusable from the registry's point of view even though the node never answered.
  assert state_of(s,pid)=='revoking' and queued(s)==[pid] and len(p.rows[1])==1
  assert 'очередь на отзыв' in text
  with pytest.raises(Denied):await b.command(100,f'/revoke {pid}')
  with pytest.raises(Denied):await b.command(100,f'/reissue {pid} 1')
  p.fail=None
  assert await b.process_revocations(0)==1
  assert state_of(s,pid)=='revoked' and p.rows[1]==[] and queued(s)==[] and s.usage(100)==(0,1)
 asyncio.run(run())


def test_queued_revocation_survives_restart(tmp_path):
 s=Store(tmp_path/'db');s.save_person(data());p=Panel()
 client=asyncio.run(p.issue(1,'Original'))['client'];pid=s.issued(100,1,'awg',client)
 protocols={1:'awg',2:'awg',3:'awg3',4:'awg'}
 p.fail='mutate-before'
 asyncio.run(revoke(Bot(s,p,None,protocols),pid))
 assert queued(s)==[pid]
 restarted=Store(tmp_path/'db');restarted.recover();p.fail=None
 assert asyncio.run(Bot(restarted,p,None,protocols).process_revocations(0))==1
 assert state_of(restarted,pid)=='revoked' and p.rows[1]==[]


def test_ambiguous_failure_is_confirmed_by_absence_without_second_delete(env):
 s,p,b,pid=env;p.fail='mutate-after'
 async def run():
  await revoke(b,pid)
  assert state_of(s,pid)=='revoking' and p.rows[1]==[]
  p.fail=None;p.calls.clear()
  assert await b.process_revocations(0)==1
  assert state_of(s,pid)=='revoked' and ('revoke',1) not in p.calls
 asyncio.run(run())


def test_revoked_key_reappearing_on_node_is_killed_again(env):
 s,p,b,pid=env
 async def run():
  await revoke(b,pid)
  assert state_of(s,pid)=='revoked'
  # Node restored from a backup that still contains the key.
  p.rows[1].append(dict(public_key='secret-public-1',name='Original',has_config=True,paused=False))
  await b.sync_server(1)
  assert state_of(s,pid)=='revoking' and queued(s)==[pid]
  assert await b.process_revocations(0)==1
  assert p.rows[1]==[] and state_of(s,pid)=='revoked'
 asyncio.run(run())


def test_inventory_absence_confirms_queued_revocation(env):
 s,p,b,pid=env;p.fail='mutate-before'
 async def run():
  await revoke(b,pid)
  p.rows[1].clear();p.fail=None
  await b.sync_server(1)
  # Gone from the node is the confirmation the failed call never returned.
  assert state_of(s,pid)=='revoked' and queued(s)==[] and s.usage(100)==(0,1)
 asyncio.run(run())


def test_revoke_all_queues_profiles_of_unreachable_server(env):
 s,p,b,pid=env;p.fail='mutate-before'
 result=asyncio.run(b.revoke_all(1))
 assert result['unconfirmed']==[pid] and queued(s)==[pid] and state_of(s,pid)=='revoking'
 p.fail=None
 assert asyncio.run(b.process_revocations(0))==1
 assert p.rows[1]==[] and state_of(s,pid)=='revoked'


def test_queued_key_is_not_offered_for_operations(env):
 s,p,b,pid=env;p.fail='mutate-before'
 async def run():
  await revoke(b,pid)
  for cmd in ('/revoke','/reissue'):
   text,buttons=await b.command(100,cmd)
   assert [x['payload'] for row in buttons for x in row]==['/start']
  text,_=await b.command(100,f'/key {pid}')
  assert 'отзывается' in text
 asyncio.run(run())


def test_backlog_caps_new_keys_but_never_blocks_further_revocation(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=4 WHERE max_id=100')
 second=s.issued(100,1,'awg',{'public_key':'second','name':'Second'})
 third=s.issued(100,1,'awg',{'public_key':'third','name':'Third'})
 p.fail='mutate-before'
 async def run():
  await revoke(b,pid);await revoke(b,second)
  # Freed slots must not become a way to stack live keys while the node is unreachable.
  with pytest.raises(Denied):await b.command(100,'/new 1 extra')
  await revoke(b,third)
  assert sorted(queued(s))==sorted([pid,second,third])
  p.fail=None
  assert await b.process_revocations(0)==3
  text,_=await b.command(100,'/new 1 extra')
  assert 'создан' in text[0] and s.usage(100)==(1,4)
 asyncio.run(run())


def test_retry_backoff_does_not_hammer_an_unreachable_node(env):
 s,p,b,pid=env;p.fail='mutate-before'
 async def run():
  await revoke(b,pid);p.calls.clear()
  assert await b.process_revocations()==0 and p.calls==[]
  assert await b.process_revocations(0)==0 and p.calls
 asyncio.run(run())
