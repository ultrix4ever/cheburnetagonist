"""Self-registration: verified MAX contact (or typed phone), then name; account starts with 0 keys."""
import asyncio,hashlib,hmac,json
import pytest
from test_v2 import Panel
from vpnmax.bot import Bot
from vpnmax.registration import contact_verified,normalize_phone,parse_vcard
from vpnmax.store import Store,Denied

TOKEN='bot-token-secret';OWNER=999
VCF='BEGIN:VCARD\r\nVERSION:3.0\r\nPRODID:ez-vcard 0.10.3\r\nTEL;TYPE=cell:79531234567\r\nFN:Ivan Ivanov\r\nEND:VCARD\r\n'


class Max:
 def __init__(self):self.sent=[]
 async def send(self,uid,text,buttons=None,**kw):self.sent.append((uid,text,buttons))


@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path/'db');p=Panel();m=Max()
 s.save_person(dict(max_id=OWNER,name='Owner',quota=5,phone='',active=True,free=True,allowed_servers=[1,2,3,4]))
 return s,p,m,Bot(s,p,m,{1:'awg',2:'awg',3:'awg3',4:'awg'},owner=OWNER,payment_details='Т-Банк',contact_secret=TOKEN)

def signed(vcf=VCF,uid=100,token=TOKEN):
 return {'type':'contact','payload':{'vcf_info':vcf,'max_info':{'user_id':uid},'hash':hmac.new(token.encode(),vcf.encode(),hashlib.sha256).hexdigest()}}

def message(uid,text=None,attachments=None):
 return {'update_type':'message_created','message':{'recipient':{'chat_type':'dialog'},'sender':{'user_id':uid},
         'body':{'mid':'m','text':text,'attachments':attachments}}}

def payloads(buttons):return [x.get('payload') for row in buttons or [] for x in row]


def test_instruction_is_available_before_registration(env):
 s,p,m,b=env
 text,buttons=asyncio.run(b.command(100,'/start'))
 assert 'Инструкция в PDF' in text and 'https://vpn.example.net/guide' not in text
 assert buttons[-1]==[{'type':'callback','text':'Инструкция','payload':'/instruction'}]
 assert p.calls==[]


def test_verified_contact_then_suggested_name_registers_with_zero_keys(env):
 s,p,m,b=env
 async def run():
  await b.event(message(100,'привет'))
  assert m.sent[-1][2][0][0]=={'type':'request_contact','text':'Поделиться номером'}
  await b.event(message(100,attachments=[signed()]))
  assert 'Как вас зовут' in m.sent[-1][1] and payloads(m.sent[-1][2])==['/regname']
  text,_=await b.command(100,'/regname')
 asyncio.run(run())
 person=s.person(100)
 assert (person['name'],person['phone'],person['phone_verified'],person['free'],person['active'])==('Ivan Ivanov','+79531234567',1,0,1)
 assert s.usage(100)==(0,0) and json.loads(person['allowed_servers'])==[1,2,3,4]
 owner=[t for u,t,_ in m.sent if u==OWNER]
 assert owner and 'Ivan Ivanov' in owner[-1] and '+79531234567' in owner[-1] and '100' in owner[-1]
 assert p.calls==[]


def test_registration_reply_offers_payment(env):
 s,p,m,b=env
 async def run():
  await b.event(message(100,attachments=[signed()]))
  parts,buttons=await b.command(100,'Иван Петров')
  assert 'зарегистрированы' in parts[0] and 'Сколько ключей оплатить' in parts[1] and '/payslots 1' in payloads(buttons)
  assert 'Инструкция в PDF' in parts[0]
 asyncio.run(run())
 assert s.person(100)['name']=='Иван Петров'


def test_forged_or_foreign_contact_is_rejected(env):
 s,p,m,b=env
 async def run():
  bad=signed();bad['payload']['hash']='00'*32
  text,buttons=await b.contact(100,bad)
  assert 'Не удалось подтвердить' in text and buttons[0][0]['type']=='request_contact'
  text,_=await b.contact(100,signed(uid=555))
  assert 'Не удалось подтвердить' in text
  text,_=await b.contact(100,{'type':'contact','payload':{'vcf_info':VCF}})
  assert 'Не удалось подтвердить' in text
 asyncio.run(run())
 assert s.person_any(100) is None


def test_typed_phone_fallback_is_marked_unverified(env):
 s,p,m,b=env
 async def run():
  await b.command(100,'/regphone')
  text,_=await b.command(100,'не номер')
  assert 'Не похоже' in text
  text,buttons=await b.command(100,'8 (953) 123-45-67')
  assert 'Как вас зовут' in text and buttons is None
  text,_=await b.command(100,'<script>')
  assert 'Имя' in text and s.person_any(100) is None
  await b.command(100,'Мария')
 asyncio.run(run())
 person=s.person(100)
 assert person['phone']=='+79531234567' and person['phone_verified']==0
 assert 'не подтверждён' in [t for u,t,_ in m.sent if u==OWNER][-1]


def test_registered_and_blocked_people_are_not_re_registered(env):
 s,p,m,b=env
 s.register(100,'A','+79000000000',True,[1])
 with pytest.raises(Denied):s.register(100,'B','+79000000001',True,[1])
 text,_=asyncio.run(b.contact(100,signed()))
 assert 'уже зарегистрированы' in text
 with s.connect() as db:db.execute('UPDATE people SET active=0 WHERE max_id=100')
 with pytest.raises(Denied):asyncio.run(b.command(100,'/start'))


def test_receipt_from_unknown_user_starts_registration(env):
 s,p,m,b=env
 asyncio.run(b.event(message(100,attachments=[{'type':'image','payload':{'token':'t'}}])))
 assert 'зарегистрируйтесь' in m.sent[-1][1] and not [x for x in m.sent if x[0]==OWNER]


def test_id_command_still_works_for_unknown_users(env):
 s,p,m,b=env
 text,_=asyncio.run(b.command(100,'/id'))
 assert text=='Ваш MAX ID: 100'


def test_helpers():
 assert normalize_phone('8 953 123 45 67')=='+79531234567' and normalize_phone('9531234567')=='+79531234567'
 assert normalize_phone('12')is None
 assert parse_vcard(VCF)==('+79531234567','Ivan Ivanov')
 digest=hmac.new(TOKEN.encode(),VCF.encode(),hashlib.sha256)
 import base64
 assert contact_verified({'vcf_info':VCF,'hash':base64.b64encode(digest.digest()).decode()},TOKEN)
 assert not contact_verified({'vcf_info':VCF,'hash':digest.hexdigest()},'other-token')


def test_admin_rename_keeps_correlation_label(env,tmp_path):
 from fastapi.testclient import TestClient
 from vpnmax.app import create_app
 s,p,m,b=env
 c=TestClient(create_app(s,b,'w'*48,'a'*48));h={'X-Registry-Admin':'a'*48}
 client=asyncio.run(p.issue(1,'Телефон [max-abc123def456]'))['client'];pid=s.issued(OWNER,1,'awg',client)
 t=c.get(f'/internal/admin/profiles/{pid}/target',headers=h).json()
 assert t=={'server_id':1,'protocol':'awg','public_key':client['public_key'],'name':'Телефон [max-abc123def456]'}
 r=c.post(f'/internal/admin/profiles/{pid}/name',json={'name':'Ноутбук  Маши'},headers=h)
 assert r.json()=={'name':'Ноутбук Маши [max-abc123def456]'} and s.profiles(OWNER)[0]['name']=='Ноутбук Маши [max-abc123def456]'
 assert c.post(f'/internal/admin/profiles/{pid}/name',json={'name':'<b>'},headers=h).status_code==422
 assert c.get(f'/internal/admin/profiles/{pid}/target').status_code==403
 assert c.get('/internal/admin/profiles/9999/target',headers=h).status_code==404
