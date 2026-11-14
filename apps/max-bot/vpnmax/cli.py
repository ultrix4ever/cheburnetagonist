"""Local administrator commands; not exposed to bot users or HTTP."""
import argparse
import asyncio
import json
import os
import sys

from .api import Panel
from .store import Store


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.environ.get('DATABASE_PATH', 'data/clients.sqlite3'))
    commands = parser.add_subparsers(dest='command', required=True)
    invite = commands.add_parser('invite')
    invite.add_argument('name')
    invite.add_argument('max_id', type=int)
    assign = commands.add_parser('assign')
    assign.add_argument('profile_id', type=int)
    assign.add_argument('person_id', type=int)
    commands.add_parser('inventory')
    commands.add_parser('sync')
    commands.add_parser('operations')
    reconcile = commands.add_parser('reconcile', help='close a pending/unknown operation after checking the nodes')
    reconcile.add_argument('operation_id')
    reconcile.add_argument('outcome', choices=['failed', 'done'])
    commands.add_parser('reachability', help='store a probe report (JSON on stdin) from infra/rf-probe')
    disable = commands.add_parser('disable')
    disable.add_argument('person_id', type=int)
    args = parser.parse_args()
    store = Store(args.db)
    if args.command == 'invite':
        pid, token = store.invite(args.name, args.max_id)
        print(f'Person #{pid}; expires in 24h. Send privately to the intended MAX user only:\n/start {token}')
    elif args.command == 'assign':
        store.assign(args.profile_id, args.person_id)
        print('Assigned')
    elif args.command == 'reconcile':
        # Only after verifying on the nodes (search for the [max-<id>] label) what really happened.
        with store.connect() as db:
            cur = db.execute("UPDATE operations SET state=? WHERE id=? AND state IN ('pending','unknown')",
                             (args.outcome, args.operation_id))
            if cur.rowcount != 1:
                raise SystemExit('No pending/unknown operation with this id')
            store.log(db, None, 'operation_reconciled_' + args.outcome, args.operation_id)
        print(f'Operation marked {args.outcome}')
    elif args.command == 'reachability':
        report = json.load(sys.stdin)
        print(f"Stored {store.save_reachability(report)} of {len(report['results'])} results from {report['vantage']}")
    elif args.command == 'disable':
        with store.connect() as db:
            db.execute('UPDATE people SET active=0 WHERE id=?', (args.person_id,))
            store.log(db, None, 'person_disabled', args.person_id)
        print('Bot access disabled; existing VPN tunnels are NOT revoked.')
    elif args.command == 'sync':
        async def sync():
            protocols = {int(k): v for k, v in json.loads(os.environ['PANEL_PROTOCOLS']).items()}
            panel = Panel(os.environ['PANEL_URL'], os.environ['PANEL_API_KEY'], protocols)
            try:
                for sid, proto in protocols.items():
                    rows = await panel.clients(sid)
                    store.inventory(sid, proto, rows)
                    print(f'Server {sid}/{proto}: {len(rows)} profiles')
            finally:
                await panel.http.aclose()
        asyncio.run(sync())
    else:
        with store.connect() as db:
            query = ('SELECT id,server_id,protocol,name,person_id,state,has_config FROM profiles ORDER BY id'
                     if args.command == 'inventory' else
                     "SELECT * FROM operations WHERE state IN ('pending','unknown') ORDER BY created")
            for row in db.execute(query):
                print(json.dumps(dict(row), ensure_ascii=False))


if __name__ == '__main__':
    main()
