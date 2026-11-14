"""The exported key must arrive as a separate message containing only the key."""
import asyncio
from test_v2 import env


class Max:
 def __init__(self):self.sent=[]
 async def send(self,uid,text,buttons=None):self.sent.append((uid,text,buttons))


def test_config_sends_key_alone_in_second_message(env):
 s,p,b,pid=env;b.max=Max()
 asyncio.run(b.event({'update_type':'message_callback','message':{'recipient':{'chat_type':'dialog'}},
                      'callback':{'user':{'user_id':100},'payload':f'/export {pid}','callback_id':'x'}}))
 assert len(b.max.sent)==2
 intro,key=b.max.sent
 assert 'AmneziaVPN' in intro[1] and 'vpn://' not in intro[1]
 assert key[1]=='vpn://SECRET-EXPORT'


def test_single_replies_still_one_message(env):
 s,p,b,pid=env;b.max=Max()
 asyncio.run(b.event({'update_type':'message_created','message':{'recipient':{'chat_type':'dialog'},
                      'sender':{'user_id':100},'body':{'text':'/start'}}}))
 assert len(b.max.sent)==1 and b.max.sent[0][2]


def payloads(buttons):return [x['payload'] for row in buttons for x in row if x['type']=='callback']


def test_new_key_is_sent_immediately_then_main_menu(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=2 WHERE max_id=100')
 parts,buttons=asyncio.run(b.command(100,'/new 2 Ключ'))
 assert len(parts)==3 and parts[1]=='vpn://SECRET-EXPORT' and 'Ваши ключи: 2 из 2' in parts[2]
 assert payloads(buttons)[:3]==['/new','/revoke','/reissue']


def test_reissue_sends_new_key_then_main_menu(env):
 s,p,b,pid=env
 async def run():
  _,buttons=await b.command(100,f'/reissue {pid} 2')
  parts,menu=await b.command(100,buttons[0][0]['payload'])
  assert 'отозван' in parts[0] and parts[1]=='vpn://SECRET-EXPORT'
  assert payloads(menu)[:3]==['/new','/revoke','/reissue']
 asyncio.run(run())


def test_compromised_sends_new_key_then_main_menu(env):
 s,p,b,pid=env
 async def run():
  _,buttons=await b.command(100,f'/compromised {pid}')
  parts,menu=await b.command(100,buttons[0][0]['payload'])
  assert parts[1]=='vpn://SECRET-EXPORT' and payloads(menu)[0]=='/new'
 asyncio.run(run())


def test_stale_get_key_button_starts_new_key_flow(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=2 WHERE max_id=100')
 text,buttons=asyncio.run(b.command(100,f'/config {pid}'))
 assert 'На каком сервере получить новый ключ' in text and p.calls==[]
 assert '/new 1' in payloads(buttons)


def test_key_card_shows_existing_key_via_separate_button(env):
 s,p,b,pid=env
 _,buttons=asyncio.run(b.command(100,f'/key {pid}'))
 assert f'/export {pid}' in payloads(buttons) and not any(x.startswith('/config') for x in payloads(buttons))


def test_failed_export_after_issue_still_reports_success(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=2 WHERE max_id=100')
 async def broken(row):raise RuntimeError('export down')
 p.config=broken
 parts,buttons=asyncio.run(b.command(100,'/new 2 Ключ'))
 assert 'создан' in parts[0] and 'Показать ключ' in parts[0] and len(parts)==2 and s.usage(100)==(2,2)
