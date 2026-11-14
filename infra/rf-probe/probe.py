#!/usr/bin/env python3
"""Reachability probe: can a client inside a restricted network really use each VPN node?

Runs on a vantage host inside that network. For every target it brings up a real
AmneziaWG client in a throwaway network namespace and checks, in order:

  handshake  the node answered the obfuscated handshake (UDP path + protocol not blocked)
  traffic    ICMP through the tunnel reaches the internet
  http       an HTTPS request through the tunnel completes (catches TCP throttling)

The host's own routing is never changed: only the tunnel interface lives in the namespace,
its UDP socket stays in the host namespace. Standard library only (Python 3.9+).

Targets are ordinary client configs in TARGETS_DIR/*.conf whose first line is
    # rf-probe {"id": 1, "name": "Node name", "engine": "awg"}
They contain private keys: directory 0700, files 0600, never in Git.
"""
import argparse
import concurrent.futures
import fcntl
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import time

VERSION = 1
BIN = pathlib.Path(os.environ.get('RF_PROBE_BIN', '/opt/rf-probe/bin'))
TARGETS_DIR = pathlib.Path(os.environ.get('RF_PROBE_TARGETS', '/etc/rf-probe/targets'))
STATE_DIR = pathlib.Path(os.environ.get('RF_PROBE_STATE', '/var/lib/rf-probe'))
RUN_DIR = pathlib.Path('/run/rf-probe')
UAPI_DIR = pathlib.Path('/var/run/amneziawg')
ENGINES = {'awg': 'amneziawg-go-awg', 'awg3': 'amneziawg-go-awg3'}
# Keys understood by awg-quick only; `awg setconf` rejects them.
QUICK_ONLY = {'address', 'dns', 'mtu', 'table', 'preup', 'postup', 'predown', 'postdown', 'saveconfig'}
PING_TARGETS = ('1.1.1.1', '8.8.8.8')
# Certificates of both are valid for the bare IP, so no DNS is needed inside the namespace.
HTTP_TARGETS = ('https://1.1.1.1/cdn-cgi/trace', 'https://8.8.8.8/')
HANDSHAKE_WAIT = 15


class Target:
    def __init__(self, path):
        text = pathlib.Path(path).read_text()
        head = re.match(r'#\s*rf-probe\s+(\{.*\})\s*$', text.splitlines()[0] if text else '')
        if not head:
            raise ValueError(f'{path}: first line must be "# rf-probe {{json}}"')
        meta = json.loads(head.group(1))
        self.id, self.name, self.engine = int(meta['id']), str(meta['name']), meta['engine']
        if self.engine not in ENGINES:
            raise ValueError(f'{path}: unknown engine')
        self.address = self.endpoint = None
        kept = []
        for line in text.splitlines():
            key, _, value = line.partition('=')
            key, value = key.strip().lower(), value.strip()
            if key == 'address':
                self.address = next((a.strip() for a in value.split(',') if ':' not in a), None)
            if key == 'endpoint':
                self.endpoint = value
            if key not in QUICK_ONLY:
                kept.append(line)
        if not self.address or not self.endpoint:
            raise ValueError(f'{path}: IPv4 Address and Endpoint are required')
        self.host = self.endpoint.rsplit(':', 1)[0].strip('[]')
        self.stripped = '\n'.join(kept) + '\n'
        self.iface = f'rfp{self.id}'  # also the namespace name


def run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, 'timeout'


def awg(*args):
    return run([str(BIN / 'ld-musl-x86_64.so.1'), str(BIN / 'awg'), *args])


def cleanup(name, engine=None):
    if engine and engine.poll() is None:
        engine.terminate()
        try:
            engine.wait(5)
        except subprocess.TimeoutExpired:
            engine.kill()
    # A leftover engine from a crashed run still owns the interface: find it by its argument.
    run(['pkill', '-f', f'amneziawg-go-[a-z0-9]+ -f {name}$'])
    run(['ip', 'netns', 'del', name])
    run(['ip', 'link', 'del', name])
    for leftover in (UAPI_DIR / f'{name}.sock', RUN_DIR / f'{name}.conf'):
        try:
            leftover.unlink()
        except FileNotFoundError:
            pass


def icmp(host):
    code, _ = run(['ping', '-c', '2', '-W', '2', '-q', host], timeout=8)
    return code == 0


def probe(target):
    started = time.time()
    result = dict(id=target.id, name=target.name, status='error', stage='setup', detail='',
                  handshake_ms=None, rtt_ms=None, exit_ip=None, icmp=None, checked=int(started))
    name, ns, engine = target.iface, ['ip', 'netns', 'exec', target.iface], None

    def fail(stage, detail, status='fail'):
        result.update(status=status, stage=stage, detail=detail[:200])
        return result

    try:
        cleanup(name)
        result['icmp'] = icmp(target.host)
        conf = RUN_DIR / f'{name}.conf'
        fd = os.open(conf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(target.stripped)
        engine = subprocess.Popen([str(BIN / ENGINES[target.engine]), '-f', name],
                                  env={'LOG_LEVEL': 'error', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin'},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 6
        while not (UAPI_DIR / f'{name}.sock').exists():
            if engine.poll() is not None or time.time() > deadline:
                return fail('setup', 'client engine did not start', 'error')
            time.sleep(0.1)
        steps = [
            lambda: awg('setconf', name, str(conf)),
            lambda: run(['ip', 'netns', 'add', name]),
            # The UDP socket was opened in the host namespace and stays there.
            lambda: run(['ip', 'link', 'set', name, 'netns', name]),
            lambda: run(['ip', '-n', name, 'link', 'set', 'lo', 'up']),
            lambda: run(['ip', '-n', name, 'address', 'add', target.address, 'dev', name]),
            lambda: run(['ip', '-n', name, 'link', 'set', 'mtu', '1280', 'up', 'dev', name]),
            lambda: run(['ip', '-n', name, 'route', 'add', 'default', 'dev', name]),
        ]
        for number, step in enumerate(steps):
            code, out = step()
            if code:
                return fail('setup', f'local step {number} failed', 'error')
        conf.unlink()

        up = time.time()
        handshake = 0
        while time.time() - up < HANDSHAKE_WAIT and not handshake:
            # Any packet makes the client start a handshake; replies are checked later.
            run(ns + ['ping', '-c', '1', '-W', '1', '-q', PING_TARGETS[0]], timeout=4)
            code, out = awg('show', name, 'latest-handshakes')
            fields = out.split()
            handshake = int(fields[-1]) if code == 0 and fields and fields[-1].isdigit() else 0
        if not handshake:
            return fail('handshake', 'host answers ping, VPN handshake does not complete' if result['icmp']
                        else 'no handshake and host does not answer ping')
        result['handshake_ms'] = int((time.time() - up) * 1000)

        for address in PING_TARGETS:
            code, out = run(ns + ['ping', '-c', '3', '-W', '2', '-q', address], timeout=12)
            rtt = re.search(r'= [\d.]+/([\d.]+)/', out)
            if code == 0 and rtt:
                result['rtt_ms'] = round(float(rtt.group(1)))
                break
        else:
            return fail('traffic', 'handshake completed, no traffic through the tunnel')

        for url in HTTP_TARGETS:
            code, out = run(ns + ['curl', '-s', '-m', '10', '-o', '-', '-w', '\nHTTP %{http_code}\n', url], timeout=14)
            status = re.search(r'^HTTP (\d{3})$', out, re.M)
            if code == 0 and status and status.group(1) != '000':
                exit_ip = re.search(r'^ip=(\S+)$', out, re.M)
                result['exit_ip'] = exit_ip.group(1) if exit_ip else None
                break
        else:
            return fail('http', 'ping passes, HTTPS through the tunnel does not')
        result.update(status='ok', stage='ok', detail='')
        return result
    except Exception as exc:  # never let one target hide the others
        return fail('setup', type(exc).__name__, 'error')
    finally:
        cleanup(name, engine)
        result['duration_ms'] = int((time.time() - started) * 1000)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--vantage', default=os.environ.get('RF_PROBE_VANTAGE', socket.gethostname()))
    parser.add_argument('--only', type=int, action='append', help='probe only this target id (repeatable)')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('root is required to create interfaces and namespaces')
    os.umask(0o077)
    for directory in (RUN_DIR, STATE_DIR):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = open(RUN_DIR / 'lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('another probe run is in progress') from None

    targets, broken = [], []
    for path in sorted(TARGETS_DIR.glob('*.conf')):
        try:
            targets.append(Target(path))
        except Exception as exc:
            broken.append(f'{path.name}: {exc}'[:200])
    if args.only:
        targets = [t for t in targets if t.id in args.only]
    started = int(time.time())
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(targets), 1)) as pool:
        results = list(pool.map(probe, targets))
    report = dict(version=VERSION, vantage=args.vantage, started=started, finished=int(time.time()),
                  results=sorted(results, key=lambda r: r['id']), broken_targets=broken)
    text = json.dumps(report, ensure_ascii=False)
    temp = STATE_DIR / 'latest.json.tmp'
    temp.write_text(text)
    temp.replace(STATE_DIR / 'latest.json')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
