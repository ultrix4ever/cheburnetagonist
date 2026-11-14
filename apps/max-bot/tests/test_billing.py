"""Paid key slots: a confirmed payment of N×100 ₽ raises the limit by N for 30 days; keys are issued
at once within the limit; when slots expire, the newest keys pause and are revoked 30 days later."""
import asyncio
import time
import pytest
from test_v2 import Panel,data,state_of
from vpnmax import billing
from vpnmax.api import RemoteError
from vpnmax.bot import Bot
from vpnmax.store import Denied,Store

OWNER=999
DAY=86400


class Max:
 def __init__(self):self.sent=[];self.fail_forward=False
 async def send(self,uid,text,buttons=None,forward=None,media=None):
  if forward and self.fail_forward:raise RemoteError('MAX HTTP 400',400)
  self.sent.append(dict(uid=uid,text=text,buttons=buttons,forward=forward,media=media))
 def to(self,uid):return [m for m in self.sent if m['uid']==uid]


@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path/'db');s.save_person(data(quota=5,free=False));s.save_person(data(uid=OWNER,quota=5,free=True))
 p=Panel();m=Max();b=Bot(s,p,m,{1:'awg',2:'awg',3:'awg3',4:'awg'},owner=OWNER,payment_details='Перевод по номеру +7 900 000-00-00 в Т-Банк.')
 return s,p,m,b

def payloads(buttons):return [x['payload'] for row in buttons or [] for x in row if 'payload' in x]

async def pay(b,slots,receipt=False):
 text,buttons=await b.command(100,'/pay')
 cmd=f'/payslots {slots}'
 if cmd not in payloads(buttons):
  await b.command(100,'/payslots other');text,buttons=await b.command(100,str(slots))
 else:
  text,buttons=await b.command(100,cmd)
 code=b.store.open_payment(100)['id']
 assert code in text and f'{billing.amount(slots)} ₽' in text
 await b.command(100,f'/paid {code}')
 return code

async def new_key(b,sid=2,name='Телефон'):
 await b.command(100,f'/new {sid}')
 return await b.command(100,name)

def key_ids(s,uid=100):return [r['id'] for r in s.profiles(uid)]


def test_no_paid_slots_means_no_keys(env):
 s,p,m,b=env
 text,buttons=asyncio.run(b.command(100,'/new 2'))
 assert 'Все оплаченные ключи использованы: 0 из 0' in text and payloads(buttons)[0]=='/pay'
 with pytest.raises(Denied):asyncio.run(b.command(100,'/new 2 Телефон'))
 assert p.calls==[]


def test_confirmed_payment_raises_limit_and_keys_issue_without_waiting(env):
 s,p,m,b=env
 async def run():
  code=await pay(b,3)
  assert s.usage(100)==(0,0) and p.calls==[]
  card=m.to(OWNER)[-1]
  assert code in card['text'] and '3 ключа на 30 дней — 300 ₽' in card['text'] and payloads(card['buttons'])==[f'/payok {code}',f'/payno {code}']
  reply,_=await b.command(OWNER,f'/payok {code}')
  assert '+3 ключа' in reply and s.usage(100)==(0,3)
  assert 'Доступно ключей: 3 из 3' in m.to(100)[-1]['text']
  for sid,name in [(1,'Телефон'),(3,'Ноутбук'),(4,'Планшет')]:
   parts,_=await new_key(b,sid,name)
   assert parts[1]=='vpn://SECRET-EXPORT'
  assert s.usage(100)==(3,3) and len(m.to(OWNER))==1
  text,_=await b.command(100,'/new 2')
  assert 'использованы: 3 из 3' in text
  with pytest.raises(Denied):await b.command(OWNER,f'/payok {code}')
 asyncio.run(run())


def test_grant_lasts_30_days_from_confirmation(env):
 s,p,m,b=env
 code=asyncio.run(pay(b,2));asyncio.run(b.command(OWNER,f'/payok {code}'))
 g=s.grants(1)[0];person=s.person_by_id(1)
 assert g['expires']-g['starts']==30*DAY and abs(g['starts']-time.time())<5
 assert s.person_limit(person,g['expires']-1)==2 and s.person_limit(person,g['expires'])==0


def test_two_payments_stack_with_separate_expiry(env):
 s,p,m,b=env
 async def run():
  first=await pay(b,1);await b.command(OWNER,f'/payok {first}')
  second=await pay(b,2);await b.command(OWNER,f'/payok {second}')
 asyncio.run(run())
 assert s.usage(100)==(0,3) and [g['slots'] for g in s.grants(1)]==[1,2]


def test_custom_slot_count_and_bounds(env):
 s,p,m,b=env
 async def run():
  await b.command(100,'/payslots other')
  text,_=await b.command(100,'25')
  assert 'от 1 до 20' in text and s.open_payment(100) is None
  text,_=await b.command(100,'12')
  assert '1200 ₽' in text and s.open_payment(100)['slots']==12
 asyncio.run(run())


def test_only_owner_confirms_and_reject_notifies_user(env):
 s,p,m,b=env
 async def run():
  code=await pay(b,1)
  with pytest.raises(Denied):await b.command(100,f'/payok {code}')
  await b.command(OWNER,f'/payno {code}')
  assert s.payment(code)['state']=='rejected' and s.usage(100)==(0,0) and 'не подтверждена' in m.to(100)[-1]['text']
 asyncio.run(run())


def test_receipt_is_forwarded_to_owner(env):
 s,p,m,b=env
 async def run():
  await b.command(100,'/payslots 2');code=s.open_payment(100)['id']
  await b.event({'update_type':'message_created','message':{'recipient':{'chat_type':'dialog'},'sender':{'user_id':100},
                 'body':{'mid':'mid.42','text':None,'attachments':[{'type':'file','payload':{'token':'tok','url':'https://x'}}]}}})
  assert s.payment(code)['state']=='submitted' and s.payment(code)['receipt_mid']=='mid.42'
  owner=m.to(OWNER)
  assert owner[0]['forward']=='mid.42' and code in owner[-1]['text']
 asyncio.run(run())


@pytest.mark.parametrize('kind', ['file', 'image'])
def test_receipt_after_paid_button_is_forwarded_without_granting_slots(env, kind):
 s,p,m,b=env
 async def run():
  code=await pay(b,1)
  assert s.payment(code)['receipt_mid'] is None
  m.sent.clear()
  await b.event({'update_type':'message_created','message':{'recipient':{'chat_type':'dialog'},'sender':{'user_id':100},
                 'body':{'mid':'mid.late','attachments':[{'type':kind,'payload':{'token':'receipt-token'}}]}}})
  assert s.payment(code)['state']=='submitted' and s.payment(code)['receipt_mid']=='mid.late'
  assert m.to(OWNER)[0]['forward']=='mid.late' and code in m.to(OWNER)[-1]['text']
  assert 'получена' in m.to(100)[-1]['text'] and s.usage(100)==(0,0)
  with pytest.raises(Denied):await b.command(100,f'/paid {code}')
 asyncio.run(run())


def test_receipt_cannot_change_closed_or_other_users_payment(env):
 s,p,m,b=env
 async def run():
  code=await pay(b,1)
  with pytest.raises(Denied):s.submit_payment(OWNER,code,receipt_mid='wrong-owner')
  await b.command(OWNER,f'/payok {code}')
  with pytest.raises(Denied):s.submit_payment(100,code,receipt_mid='too-late')
  assert s.payment(code)['receipt_mid'] is None and s.usage(100)==(0,1)
 asyncio.run(run())


def test_receipt_falls_back_to_resending_media(env):
 s,p,m,b=env;m.fail_forward=True
 async def run():
  await b.command(100,'/payslots 1')
  await b.event({'update_type':'message_created','message':{'recipient':{'chat_type':'dialog'},'sender':{'user_id':100},
                 'body':{'mid':'mid.7','attachments':[{'type':'image','payload':{'token':'img-tok','url':'u'}}]}}})
  assert m.to(OWNER)[0]['media']==[{'type':'image','payload':{'token':'img-tok'}}]
 asyncio.run(run())


def test_receipt_without_open_payment_is_explained(env):
 s,p,m,b=env
 asyncio.run(b.event({'update_type':'message_created','message':{'recipient':{'chat_type':'dialog'},'sender':{'user_id':100},
                      'body':{'mid':'m','attachments':[{'type':'image','payload':{'token':'t'}}]}}}))
 assert '«Оплатить ключи»' in m.to(100)[-1]['text'] and not m.to(OWNER)


def test_one_open_payment_and_submitted_blocks_new(env):
 s,p,m,b=env
 async def run():
  await b.command(100,'/payslots 1');first=s.open_payment(100)['id']
  await b.command(100,'/payslots 2');second=s.open_payment(100)['id']
  assert s.payment(first)['state']=='cancelled'
  await b.command(100,f'/paid {second}')
  with pytest.raises(Denied):await b.command(100,'/payslots 3')
 asyncio.run(run())


def test_expiry_pauses_newest_then_payment_resumes(env):
 s,p,m,b=env
 now=int(time.time())
 async def run():
  code=await pay(b,3);await b.command(OWNER,f'/payok {code}')
  for name in ('Первый','Второй','Третий'):await new_key(b,2,name)
 asyncio.run(run())
 first,second,third=key_ids(s)
 s.add_grant(1,1,'manual')  # 4 slots for now; the 3-slot payment will lapse first
 now=int(time.time())  # The manual grant must already have started at the tick being tested.
 with s.connect() as db:db.execute("UPDATE grants SET expires=? WHERE source='payment'",(now-1,))
 p.calls.clear()
 asyncio.run(b.billing_tick(now))
 # Only the 1 manual slot remains: the two newest keys stop.
 assert [state_of(s,x) for x in (first,second,third)]==['active','paused','paused'] and p.calls==[('pause',2),('pause',2)]
 assert 'остановлен' in m.to(100)[-1]['text']
 _,buttons=asyncio.run(b.command(100,f'/resume {third}'))
 assert payloads(buttons)==['/pay']
 _,buttons=asyncio.run(b.command(100,f'/reissue {third} 2'))
 assert payloads(buttons)==['/pay']
 async def renew():
  code=await pay(b,1);await b.command(OWNER,f'/payok {code}')
 asyncio.run(renew())
 # Manual 1 + new 1 = 2 slots: the second key comes back, the newest stays stopped.
 assert state_of(s,second)=='active' and state_of(s,third)=='paused'


def test_revoking_an_old_key_moves_the_pause_to_the_newest(env):
 s,p,m,b=env
 s.add_grant(1,2,'manual')
 async def run():
  for name in ('Первый','Второй'):await new_key(b,2,name)
 asyncio.run(run())
 first,second=key_ids(s)
 with s.connect() as db:db.execute("UPDATE grants SET slots=1")
 asyncio.run(b.billing_tick())
 assert state_of(s,second)=='paused'
 async def revoke():
  _,buttons=await b.command(100,f'/revoke {first}')
  await b.command(100,buttons[0][0]['payload'])
 asyncio.run(revoke())
 asyncio.run(b.billing_tick())
 assert state_of(s,first)=='revoked' and state_of(s,second)=='active'


def test_suspended_key_is_revoked_after_30_days(env):
 s,p,m,b=env
 now=int(time.time())
 gid=s.add_grant(1,1,'manual')
 asyncio.run(new_key(b))
 pid=key_ids(s)[0]
 s.cancel_grant(gid)
 asyncio.run(b.billing_tick(now))
 assert state_of(s,pid)=='paused'
 asyncio.run(b.billing_tick(now+29*DAY))
 assert state_of(s,pid)=='paused'
 asyncio.run(b.billing_tick(now+30*DAY))
 assert state_of(s,pid)=='revoked' and p.rows[2]==[] and 'удалён' in m.to(100)[-1]['text']


def test_users_own_pause_is_not_undone_by_payment(env):
 s,p,m,b=env
 gid=s.add_grant(1,1,'manual')
 asyncio.run(new_key(b));pid=key_ids(s)[0]
 async def pause():
  _,buttons=await b.command(100,f'/pause {pid}')
  await b.command(100,buttons[0][0]['payload'])
 asyncio.run(pause())
 s.cancel_grant(gid);p.calls.clear()
 asyncio.run(b.billing_tick())
 assert p.calls==[] and s.profiles(100)[0]['billing_state']=='lapsed'
 s.add_grant(1,1,'manual');asyncio.run(b.billing_tick())
 assert p.calls==[] and state_of(s,pid)=='paused' and s.profiles(100)[0]['billing_state'] is None


def test_failed_pause_is_retried_next_tick(env):
 s,p,m,b=env
 gid=s.add_grant(1,1,'manual');asyncio.run(new_key(b));pid=key_ids(s)[0]
 s.cancel_grant(gid)
 p.fail='mutate-before';asyncio.run(b.billing_tick())
 assert state_of(s,pid)=='active'
 p.fail=None;asyncio.run(b.billing_tick())
 assert state_of(s,pid)=='paused'


def test_reminder_only_when_expiry_would_stop_keys(env):
 s,p,m,b=env
 now=int(time.time())
 gid=s.add_grant(1,1,'manual')
 asyncio.run(b.billing_tick(now+28*DAY))
 assert not [x for x in m.to(100) if 'заканчивается' in x['text']]
 gid=s.add_grant(1,1,'manual');asyncio.run(new_key(b))
 asyncio.run(b.billing_tick(now+28*DAY));asyncio.run(b.billing_tick(now+28*DAY))
 assert len([x for x in m.to(100) if 'заканчивается оплата' in x['text']])==1


def test_legacy_keys_are_never_billed_and_take_no_slots(env,tmp_path):
 s,p,m,b=env
 c=asyncio.run(p.issue(1,'Старый'))['client'];s.inventory_one(1,'awg',c)
 with s.connect() as db:
  pid=db.execute('SELECT id FROM profiles WHERE public_key=?',(c['public_key'],)).fetchone()[0]
  db.execute('UPDATE profiles SET legacy=1,person_id=1 WHERE id=?',(pid,))
 p.calls.clear()
 asyncio.run(b.billing_tick())
 assert state_of(s,pid)=='active' and p.calls==[] and s.usage(100)==(0,0)


def test_migration_marks_existing_profiles_legacy(tmp_path):
 import sqlite3
 db=sqlite3.connect(tmp_path/'old')
 db.executescript('''CREATE TABLE people(id INTEGER PRIMARY KEY,name TEXT NOT NULL,max_id INTEGER UNIQUE,active INTEGER NOT NULL DEFAULT 1);
 CREATE TABLE profiles(id INTEGER PRIMARY KEY,server_id INTEGER NOT NULL,protocol TEXT NOT NULL,public_key TEXT NOT NULL,name TEXT NOT NULL,
  person_id INTEGER,state TEXT NOT NULL,has_config INTEGER NOT NULL,seen_at INTEGER NOT NULL,UNIQUE(server_id,protocol,public_key));
 INSERT INTO people(name,max_id) VALUES('A',100);
 INSERT INTO profiles VALUES(1,1,'awg','k','Old',1,'active',1,0);''');db.commit();db.close()
 s=Store(tmp_path/'old')
 assert s.profiles(100)[0]['legacy']==1
 s.inventory_one(1,'awg',{'public_key':'k2','name':'New'})
 with s.connect() as c:assert c.execute("SELECT legacy FROM profiles WHERE public_key='k2'").fetchone()[0]==0
 assert Store(tmp_path/'old').profiles(100)[0]['legacy']==1


def test_old_empty_payments_table_is_replaced(tmp_path):
 import sqlite3
 db=sqlite3.connect(tmp_path/'v1')
 db.execute("CREATE TABLE payments(id TEXT PRIMARY KEY,max_id INTEGER NOT NULL,person_id INTEGER NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,created INTEGER NOT NULL)")
 db.commit();db.close()
 s=Store(tmp_path/'v1')
 with s.connect() as c:assert 'slots' in {r[1] for r in c.execute('PRAGMA table_info(payments)')}


def test_free_people_use_quota_and_never_pay(env):
 s,p,m,b=env
 text,_=asyncio.run(b.command(OWNER,'/pay'))
 assert 'бесплатный' in text and s.usage(OWNER)==(0,5)
 parts,_=asyncio.run(b.command(OWNER,'/new 2 Роутер'))
 assert 'создан' in parts[0]


def test_free_to_paid_gives_grace_and_paid_to_free_resumes(env):
 s,p,m,b=env
 asyncio.run(b.command(OWNER,'/new 2 Роутер'))
 s.save_person(dict(data(uid=OWNER,quota=5),free=False),2)
 g=s.grants(2)[0]
 assert g['slots']==5 and abs(g['expires']-time.time()-billing.FREE_TO_PAID_GRACE)<5 and s.usage(OWNER)==(1,5)
 gid=s.add_grant(1,1,'manual');asyncio.run(new_key(b));pid=key_ids(s)[0]
 s.cancel_grant(gid);asyncio.run(b.billing_tick())
 assert state_of(s,pid)=='paused'
 s.save_person(dict(data(),quota=5,free=True),1)
 asyncio.run(b.billing_tick())
 assert state_of(s,pid)=='active'


def test_reissue_keeps_slot_and_is_not_double_counted(env):
 s,p,m,b=env
 s.add_grant(1,1,'manual');asyncio.run(new_key(b));pid=key_ids(s)[0]
 async def run():
  _,buttons=await b.command(100,f'/reissue {pid} 3')
  await b.command(100,buttons[0][0]['payload'])
 asyncio.run(run())
 p.calls.clear();asyncio.run(b.billing_tick())
 new=key_ids(s)[0]
 assert new!=pid and state_of(s,new)=='active' and state_of(s,pid)=='revoked' and p.calls==[] and s.usage(100)==(1,1)


def test_my_keys_and_menu_show_paid_slots(env):
 s,p,m,b=env
 s.add_grant(1,2,'manual')
 text,_=asyncio.run(b.command(100,'/start'))
 assert 'Ваши ключи: 0 из 2' in text and 'Оплачено: 2 ключа до' in text
 text,_=asyncio.run(b.command(100,'/my'))
 assert 'Оплачено: 2 ключа до' in text


def test_admin_api_grant_cancel_and_payment_decision(env):
 from fastapi.testclient import TestClient
 from vpnmax.app import create_app
 s,p,m,b=env
 c=TestClient(create_app(s,b,'w'*48,'a'*48));h={'X-Registry-Admin':'a'*48}
 body=dict(name='Person',quota=5,allowed_servers=[1,2,3,4],phone='',active=True)
 # Older panel builds omit `free`: the flag must survive their saves.
 assert c.put('/internal/admin/people/2',json=body,headers=h).status_code==200 and s.person_by_id(2)['free']==1
 r=c.post('/internal/admin/people/1/grant',json={'slots':2},headers=h)
 assert r.status_code==200 and s.usage(100)==(0,2)
 assert c.post('/internal/admin/people/2/grant',json={'slots':2},headers=h).status_code==409
 assert c.post('/internal/admin/people/1/grant',json={'slots':0},headers=h).status_code==422
 state=c.get('/internal/admin/state',headers=h).json()
 person=[x for x in state['people'] if x['id']==1][0]
 assert person['limit']==2 and person['grants'][0]['slots']==2 and state['billing']['price']==100
 assert c.post(f"/internal/admin/grants/{person['grants'][0]['id']}/cancel",headers=h).status_code==200 and s.usage(100)==(0,0)
 code=asyncio.run(pay(b,3))
 r=c.post(f'/internal/admin/payments/{code}/confirm',headers=h)
 assert r.status_code==200 and s.usage(100)==(0,3)
 assert c.post(f'/internal/admin/payments/{code}/confirm',headers=h).status_code==409
 assert c.post(f'/internal/admin/payments/{code}/confirm').status_code==403


def test_bank_matcher_requires_code_and_exact_amount():
 pending=[{'id':'VPN-111111','amount':300},{'id':'VPN-222222','amount':100}]
 ops=[billing.Incoming('a',30000,'оплата vpn-111111',0),
      billing.Incoming('b',5000,'VPN-222222',0),
      billing.Incoming('c',10000,'без кода',0),
      billing.Incoming('d',40000,'VPN-111111 и VPN-222222',0)]
 matched,mismatched=billing.match(pending,ops)
 assert matched==[('VPN-111111','a')]
 assert ('VPN-222222','b') in [(x[0],x[1]) for x in mismatched]
 assert any(x[1]=='d' for x in mismatched)


def test_bank_matcher_never_confirms_duplicate_code():
 pending=[{'id':'VPN-111111','amount':100}]
 ops=[billing.Incoming('a',10000,'VPN-111111',0),billing.Incoming('b',10000,'VPN111111',0)]
 matched,mismatched=billing.match(pending,ops)
 assert matched==[] and mismatched[0][2]=='code paid more than once'


def test_payment_message_has_pay_link_and_copy_buttons(env):
 s,p,m,b=env
 b.payment_url='https://www.tbank-online.com/rm/example/';b.payment_phone='+79536037833'
 text,buttons=asyncio.run(b.command(100,'/payslots 3'))
 code=s.open_payment(100)['id']
 flat=[x for row in buttons for x in row]
 assert {'type':'link','text':'Оплатить в Т-Банке','url':b.payment_url} in flat
 assert {'type':'clipboard','text':f'📋 Код {code}','payload':code} in flat
 assert {'type':'clipboard','text':'📋 300 ₽','payload':'300'} in flat
 assert {'type':'clipboard','text':'📋 Номер телефона','payload':'+79536037833'} in flat
 assert f'Код платежа: {code}' in text and 'Или: перевод по номеру +7 900' in text
 assert [x['payload'] for row in buttons[-2:] for x in row]==[f'/paid {code}',f'/paycancel {code}']
 # MAX limits: link row ≤3 buttons, clipboard captions short enough for 2 per row.
 assert all(len(r)<=3 for r in buttons) and all(len(x['text'])<=20 for x in flat if x['type']=='clipboard')


def test_payment_message_without_link_keeps_details(env):
 s,p,m,b=env
 text,buttons=asyncio.run(b.command(100,'/payslots 1'))
 assert 'Т-Банк' in text and not any(x['type']=='link' for row in buttons for x in row)


def test_payment_url_must_be_https():
 from vpnmax.app import payment_url
 assert payment_url(None) is None and payment_url('https://x/y')=='https://x/y'
 with pytest.raises(ValueError):payment_url('http://x')
