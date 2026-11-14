"""Run inside vpn-max container. Registers only this dedicated bot endpoint."""
import asyncio
import os
from vpnmax.api import MaxAPI
from vpnmax.store import Store
from vpnmax.bot import button

async def main():
    api=MaxAPI(os.environ['MAX_BOT_TOKEN'],ca_file=os.environ['MAX_CA_FILE'],api_socket=os.environ.get('MAX_API_SOCKET'))
    try:
        r=await api.http.get('me'); r.raise_for_status()
        bot=r.json()
        assert bot.get('user_id')==100000002 and bot.get('is_bot'), 'Unexpected bot'
        store=Store(os.environ['DATABASE_PATH'])
        uid=int(os.environ['MAX_OWNER_ID'])
        with store.connect() as db:
            owner=db.execute('SELECT id FROM people WHERE max_id=?',(uid,)).fetchone()
        if not owner:
            _,token=store.invite('Owner',uid)
            store.claim(uid,token)
        target='https://vpn.example.net/webhook/max'
        r=await api.http.get('subscriptions'); r.raise_for_status()
        subscriptions=r.json().get('subscriptions',[])
        unknown=[s for s in subscriptions if s['url']!=target]
        if unknown:
            raise RuntimeError('Unrelated existing MAX subscription; preserved')
        r=await api.http.post('subscriptions',json={'url':target,'update_types':['bot_started','message_created','message_callback'],'secret':os.environ['MAX_WEBHOOK_SECRET']})
        r.raise_for_status()
        if not r.json().get('success'):
            raise RuntimeError('MAX rejected webhook registration')
        print('MAX bot identity verified; owner provisioned; webhook registered')
        r=await api.http.get('subscriptions'); r.raise_for_status()
        print('Webhook present:',any(s.get('url')==target for s in r.json().get('subscriptions',[])))
        try:
            await api.send(uid,'MAX-бот управления VPN запущен. Ваш аккаунт привязан. Старые ключи пока не назначены владельцам. Для начала нажмите «Мои ключи» или «Серверы».',[[button('Мои ключи','/my'),button('Серверы','/servers')]])
            print('Owner welcome message accepted by MAX')
        except Exception as e:
            print('Owner welcome not delivered:',type(e).__name__)
    finally:
        await api.http.aclose()

try:
    asyncio.run(main())
except Exception as e:
    print('MAX setup failed:',type(e).__name__)
    raise SystemExit(1)
