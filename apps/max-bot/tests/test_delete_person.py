"""Full deletion of a person: refused while any key may still work, then erases all personal data."""
import asyncio,json,time
import pytest
from fastapi.testclient import TestClient
from test_v2 import Panel,data,state_of
from vpnmax.app import create_app
from vpnmax.bot import Bot
from vpnmax.store import Denied,Store

H={'X-Registry-Admin':'a'*48}


class Max:
 def __init__(self):self.sent=[]
 async def send(self,uid,text,buttons=None,**kw):self.sent.append((uid,text))


@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path/'db');p=Panel();m=Max()
 s.save_person(dict(data(quota=3,free=False),phone='+79001112233'));s.save_person(data(uid=200,quota=2,free=True))
 b=Bot(s,p,m,{1:'awg',2:'awg',3:'awg3',4:'awg'},owner=999)
 return s,p,b,TestClient(create_app(s,b,'w'*48,'a'*48))

def rows(s,table,where='1=1',args=()):
 with s.connect() as db:return db.execute(f'SELECT count(*) FROM {table} WHERE {where}',args).fetchone()[0]

def seed_history(s,p,b):
 s.add_grant(1,2,'manual')
 async def run():
  await b.command(100,'/new 2');await b.command(100,'Телефон')
  await b.command(100,'/payslots 1');code=s.open_payment(100)['id'];await b.command(100,f'/paid {code}')
 asyncio.run(run())
 return s.profiles(100)[0]['id']


def test_refused_while_keys_live_and_after_revoke_all_erases_everything(env):
 s,p,b,c=env
 pid=seed_history(s,p,b)
 r=c.delete('/internal/admin/people/1',headers=H)
 assert r.status_code==409 and 'неотозванных' in r.json()['detail'] and s.person_any(100)
 asyncio.run(b.revoke_all(1))
 assert state_of(s,pid)=='revoked'
 assert c.delete('/internal/admin/people/1',headers=H).json()=={'deleted':True}
 assert s.person_any(100) is None and s.person_by_id(1) is None
 for table,col in [('payments','max_id'),('operations','max_id'),('confirmations','max_id'),('prompts','max_id'),('replacements','max_id')]:
  assert rows(s,table,f'{col}=?',(100,))==0,table
 for table in ('grants','admin_jobs','invites'):
  assert rows(s,table,'person_id=?',(1,))==0,table
 assert rows(s,'audit','max_id=?',(100,))==0
 with s.connect() as db:
  prof=dict(db.execute('SELECT * FROM profiles WHERE id=?',(pid,)).fetchone())
  dump=json.dumps([dict(r) for r in db.execute('SELECT * FROM audit')],ensure_ascii=False)
 assert prof['person_id'] is None and prof['state']=='revoked' and prof['name'].startswith('удалён [max-')
 assert 'Телефон' not in prof['name'] and '+79001112233' not in dump
 # Other people untouched.
 assert s.person(200)['quota']==2
 assert c.delete('/internal/admin/people/1',headers=H).status_code==404


def test_deleted_key_reappearing_on_node_is_revoked_again(env):
 s,p,b,c=env
 pid=seed_history(s,p,b)
 key=dict(p.rows[2][0]) if p.rows[2] else None
 asyncio.run(b.revoke_all(1));c.delete('/internal/admin/people/1',headers=H)
 p.rows[2].append(key);asyncio.run(b.sync_server(2))
 assert state_of(s,pid)=='revoking'
 asyncio.run(b.process_revocations(0))
 assert state_of(s,pid)=='revoked' and p.rows[2]==[]


def test_refused_with_pending_revocation_or_unknown_operation(env):
 s,p,b,c=env
 pid=seed_history(s,p,b)
 p.fail='mutate-before';asyncio.run(b.revoke_all(1))
 assert state_of(s,pid)=='revoking'
 assert c.delete('/internal/admin/people/1',headers=H).status_code==409
 p.fail=None;asyncio.run(b.process_revocations(0))
 with s.connect() as db:db.execute("INSERT INTO operations VALUES('op1',100,'issue',NULL,2,'x','unknown',0)")
 r=c.delete('/internal/admin/people/1',headers=H)
 assert r.status_code==409 and 'сверки' in r.json()['detail']
 with s.connect() as db:db.execute("UPDATE operations SET state='failed'")
 assert c.delete('/internal/admin/people/1',headers=H).status_code==200


def test_person_without_keys_is_deleted_and_can_register_again(env):
 s,p,b,c=env
 assert c.delete('/internal/admin/people/2',headers=H).status_code==200
 text,buttons=asyncio.run(b.command(200,'/start'))
 assert 'зарегистрируйтесь' in text


def test_delete_requires_registry_secret(env):
 s,p,b,c=env
 assert c.delete('/internal/admin/people/2').status_code==403 and s.person_any(200)


def test_pending_inbox_messages_of_only_that_person_are_cleared(env):
 s,p,b,c=env
 for uid in (200,2000):
  s.enqueue({'update_type':'message_created','message':{'body':{'mid':f'm{uid}','text':'секрет'},'sender':{'user_id':uid}}})
 c.delete('/internal/admin/people/2',headers=H)
 with s.connect() as db:bodies={r[0]:r[1] for r in db.execute('SELECT id,body FROM inbox')}
 assert bodies['message:m200']=='{}' and 'секрет' in bodies['message:m2000']
