import asyncio
import pytest
from test_v2 import env,latest_replacement,state_of
from vpnmax.store import Denied


def payloads(buttons):return [b['payload'] for row in buttons for b in row if b['type']=='callback']


def test_main_menu_has_pdf_instruction_button(env):
 s,p,b,pid=env
 text,buttons=asyncio.run(b.command(100,'/start'))
 assert 'Инструкция в PDF' in text and 'https://vpn.example.net/guide' not in text
 assert buttons[-1]==[{'type':'callback','text':'Инструкция','payload':'/instruction'}]
 assert p.calls==[]

def test_main_actions_and_no_device_question(env):
 s,p,b,pid=env
 text,buttons=asyncio.run(b.command(100,'/start'))
 assert payloads(buttons)[:3]==['/new','/revoke','/reissue']
 assert not any(word in text for word in ['устройства','Планшет','Компьютер'])
 calls=list(p.calls)
 text,buttons=asyncio.run(b.command(100,'/device 1'))
 assert payloads(buttons)[:3]==['/new','/revoke','/reissue'] and p.calls==calls


def test_new_choose_allowed_server_then_issue_without_device(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=2,allowed_servers=? WHERE max_id=100',('[1,3]',))
 text,buttons=asyncio.run(b.command(100,'/new'))
 assert payloads(buttons)==['/new 1','/new 3','/start']
 assert p.calls==[]
 text,buttons=asyncio.run(b.command(100,'/new 3'))
 assert 'Введите название ключа' in text and 'Телефон' in text and p.calls==[]
 assert payloads(buttons)[0]=='/new 3 Ключ'
 text,_=asyncio.run(b.command(100,'Телефон'))
 assert 'Телефон' in text[0] and 'создан' in text[0] and text[1].startswith('vpn://') and p.calls==[('issue',3),('config',3)]
 assert s.usage(100)==(2,2)


@pytest.mark.parametrize('action',['revoke','reissue'])
def test_existing_server_menu_lists_only_own_actionable_keys(env,action):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=2 WHERE max_id=100')
 s.issued(200,2,'awg',{'public_key':'foreign','name':'Foreign'})
 missing=s.issued(100,4,'awg',{'public_key':'missing','name':'Missing'})
 s.set_state(missing,'missing')
 _,buttons=asyncio.run(b.command(100,'/'+action))
 assert payloads(buttons)==[f'/owned {action} 1','/start']
 _,buttons=asyncio.run(b.command(100,f'/owned {action} 1'))
 assert payloads(buttons)[0]==f'/{action} {pid}' and p.calls==[]
 with pytest.raises(Denied):asyncio.run(b.command(100,f'/owned {action} 2'))
 assert p.calls==[]


def test_reissue_selects_destination_then_confirmation_and_move(env):
 s,p,b,pid=env
 async def run():
  _,buttons=await b.command(100,f'/reissue {pid}')
  assert payloads(buttons)==[f'/reissue {pid} {sid}' for sid in [1,2,3,4]]+['/start']
  assert p.calls==[]
  _,buttons=await b.command(100,f'/reissue {pid} 3')
  assert payloads(buttons)[0].startswith('/confirm ') and p.calls==[]
  await b.command(100,payloads(buttons)[0])
  replacement=latest_replacement(s)
  assert replacement['server_id']==3 and replacement['mode']=='move' and replacement['state']=='committed'
  assert s.usage(100)==(1,1) and p.rows[1]==[] and len(p.rows[3])==1 and state_of(s,pid)=='revoked'
 asyncio.run(run())


def test_reissue_same_server_uses_reissue_scenario(env):
 s,p,b,pid=env
 async def run():
  _,buttons=await b.command(100,f'/reissue {pid} 1')
  await b.command(100,payloads(buttons)[0])
  assert latest_replacement(s)['mode']=='reissue' and state_of(s,pid)=='revoked'
  assert len(p.rows[1])==1 and s.usage(100)==(1,1)
 asyncio.run(run())


def test_old_source_server_not_allowed_for_new_still_listed(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET allowed_servers=? WHERE max_id=100',('[3]',))
 _,sources=asyncio.run(b.command(100,'/reissue'))
 assert payloads(sources)==['/owned reissue 1','/start']
 _,targets=asyncio.run(b.command(100,f'/reissue {pid}'))
 assert payloads(targets)==[f'/reissue {pid} 3','/start']
 with pytest.raises(Denied):asyncio.run(b.command(100,f'/reissue {pid} 1'))
 assert p.calls==[]


def test_foreign_or_stale_keys_cannot_enter_reissue_wizard(env):
 s,p,b,pid=env
 with pytest.raises(Denied):asyncio.run(b.command(200,f'/reissue {pid}'))
 s.set_state(pid,'revoked')
 with pytest.raises(Denied):asyncio.run(b.command(100,f'/reissue {pid}'))
 assert p.calls==[]


def test_empty_source_menu_and_quota_menu_are_non_destructive(env):
 s,p,b,pid=env
 _,buttons=asyncio.run(b.command(200,'/revoke'))
 assert payloads(buttons)==['/start']
 text,buttons=asyncio.run(b.command(100,'/new'))
 assert 'лимит' in text and '/new 1' not in payloads(buttons) and p.calls==[]


def test_revocation_wizard_requires_confirmation(env):
 s,p,b,pid=env
 async def run():
  _,servers=await b.command(100,'/revoke')
  _,keys=await b.command(100,payloads(servers)[0])
  _,confirmation=await b.command(100,payloads(keys)[0])
  assert p.calls==[]
  await b.command(100,payloads(confirmation)[0])
  assert p.rows[1]==[] and s.usage(100)==(0,1)
 asyncio.run(run())


def test_main_menu_labels_name_the_key(env):
 s,p,b,pid=env
 text,buttons=asyncio.run(b.command(100,'/start'))
 assert [x['text'] for row in buttons for x in row][:3]==['Получить новый ключ','Отозвать старый ключ','Перевыпустить старый ключ']
 assert 'отозвать старый ключ или перевыпустить старый ключ' in text


def test_key_name_is_taken_from_next_message_and_used_on_panel(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=2 WHERE max_id=100')
 async def run():
  await b.command(100,'/new 2')
  parts,_=await b.command(100,'Ноутбук Маши')
  assert parts[0].startswith('Ключ «Ноутбук Маши»') and p.rows[2][0]['name'].startswith('Ноутбук Маши [max-')
  assert s.prompt(100) is None
  # Next free text is no longer treated as a key name.
  text,_=await b.command(100,'ещё один')
  assert 'Выберите действие' in text and len(p.rows[2])==1
 asyncio.run(run())


def test_invalid_name_keeps_question_open(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=2 WHERE max_id=100')
 async def run():
  await b.command(100,'/new 2')
  text,buttons=await b.command(100,'x'*41)
  assert 'не подойдёт' in text and p.calls==[] and s.prompt(100)
  parts,_=await b.command(100,'Планшет')
  assert 'Планшет' in parts[0]
 asyncio.run(run())


def test_skip_name_and_button_press_cancels_question(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=2 WHERE max_id=100')
 async def run():
  await b.command(100,'/new 2')
  await b.command(100,'/my')
  assert s.prompt(100) is None
  text,_=await b.command(100,'Телефон')
  assert p.calls==[]
  await b.command(100,'/new 2')
  parts,_=await b.command(100,'/new 2 Ключ')
  assert 'Ключ «Ключ»' in parts[0]
 asyncio.run(run())


def test_full_quota_does_not_ask_for_name(env):
 s,p,b,pid=env
 text,_=asyncio.run(b.command(100,'/new 2'))
 assert 'лимит' in text and s.prompt(100) is None


def test_name_prompt_expires(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=2 WHERE max_id=100')
 asyncio.run(b.command(100,'/new 2'))
 with s.connect() as db:db.execute('UPDATE prompts SET expires=0')
 text,_=asyncio.run(b.command(100,'Телефон'))
 assert 'Выберите действие' in text and p.calls==[]


def test_issue_servers_setting_narrows_new_keys_and_keeps_old(env):
 s,p,b,pid=env
 with s.connect() as db:db.execute('UPDATE people SET quota=3,allowed_servers=? WHERE max_id=100',('[1,3]',))
 s.set_setting('issue_servers','[3]')
 text,buttons=asyncio.run(b.command(100,'/new'))
 assert '/new 3' in payloads(buttons) and '/new 1' not in payloads(buttons)
 with pytest.raises(Denied):asyncio.run(b.command(100,'/new 1'))
 text,buttons=asyncio.run(b.command(100,'/my'))
 assert 'Denied' not in text
 s.set_setting('issue_servers','')
 text,buttons=asyncio.run(b.command(100,'/new'))
 assert '/new 1' in payloads(buttons) and '/new 3' in payloads(buttons)
