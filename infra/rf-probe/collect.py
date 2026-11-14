#!/usr/bin/env python3
"""Run the reachability probe on each vantage host and hand the report to the bot.

Runs on the operator host that can reach both sides over SSH; the vantage and the
manager do not need to see each other. No secrets pass through this script: target
configs stay on the vantage host, the registry stays inside the bot container.

  RF_PROBE_VANTAGES   "name=ssh-alias[,name=ssh-alias...]"
  RF_PROBE_SINK_SSH   SSH alias of the host running the bot container
  RF_PROBE_SINK_CONTAINER  bot container name
A vantage that cannot be reached is skipped: the bot notices the silence itself.
"""
import json
import os
import re
import subprocess
import sys


def main():
    vantages = dict(item.split('=', 1) for item in os.environ['RF_PROBE_VANTAGES'].split(','))
    sink, container = os.environ['RF_PROBE_SINK_SSH'], os.environ['RF_PROBE_SINK_CONTAINER']
    failed = 0
    for name, alias in vantages.items():
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,40}', name):
            raise SystemExit(f'bad vantage name: {name}')
        try:
            probe = subprocess.run(['ssh', '-o', 'BatchMode=yes', alias, f'sudo -n /opt/rf-probe/probe.py --vantage {name}'],
                                   capture_output=True, text=True, timeout=240)
            report = json.loads(probe.stdout)
            if report['vantage'] != name or not isinstance(report['results'], list):
                raise ValueError('unexpected report')
        except Exception as exc:
            failed += 1
            print(f'{name}: probe failed ({type(exc).__name__})', file=sys.stderr)
            continue
        summary = ' '.join(f"{r['id']}:{r['status']}" for r in report['results'])
        stored = subprocess.run(['ssh', '-o', 'BatchMode=yes', sink,
                                 f'docker exec -i {container} python -m vpnmax.cli reachability'],
                                input=json.dumps(report), capture_output=True, text=True, timeout=90)
        if stored.returncode:
            failed += 1
            print(f'{name}: {summary}; bot did not accept the report', file=sys.stderr)
        else:
            print(f'{name}: {summary}; {stored.stdout.strip()}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
