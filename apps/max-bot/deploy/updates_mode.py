"""Switch how the bot receives updates. Run inside the bot container.

    python updates_mode.py poll      remove the webhook subscription (long polling then works)
    python updates_mode.py webhook   subscribe WEBHOOK_URL again
    python updates_mode.py status

The container must also run with MAX_UPDATES=poll (or unset for webhook): change the env file and
recreate the bot container. MAX documents long polling as a development mode; webhook stays the
supported production path, so keep this switch reversible.
"""
import asyncio
import os
import sys

from vpnmax.api import MaxAPI

TYPES = ['bot_started', 'message_created', 'message_callback']


async def main(mode):
    api = MaxAPI(os.environ['MAX_BOT_TOKEN'], ca_file=os.environ.get('MAX_CA_FILE'), api_socket=os.environ.get('MAX_API_SOCKET'))
    try:
        current = [s['url'] for s in (await api.http.get('subscriptions')).json().get('subscriptions', [])]
        if mode == 'poll':
            for url in current:
                r = await api.http.delete('subscriptions', params={'url': url})
                print('unsubscribed:', r.status_code, r.json().get('success'))
        elif mode == 'webhook':
            r = await api.http.post('subscriptions', json={'url': os.environ['WEBHOOK_URL'], 'update_types': TYPES,
                                                           'secret': os.environ['MAX_WEBHOOK_SECRET']})
            print('subscribed:', r.status_code, r.json().get('success'))
        left = (await api.http.get('subscriptions')).json().get('subscriptions', [])
        print('subscriptions now:', len(left), '| container mode:', os.environ.get('MAX_UPDATES', 'webhook'))
    finally:
        await api.http.aclose()


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('poll', 'webhook', 'status'):
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1]))
