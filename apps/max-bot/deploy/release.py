#!/usr/bin/env python3
"""Release the bot code from this checkout to the manager. Bot container only.

    python3 apps/max-bot/deploy/release.py --host <ssh-alias>

What it does, in order, stopping at the first failure:
  1. refuses a dirty or untested tree (the image tag is the Git commit);
  2. builds `vpn-max:git-<commit>` on the manager as an overlay on the image that is
     running now: only vpnmax/*.py is replaced. Dependencies, the CA bundle and the PDF
     guide are runtime assets that are not in this public repository and stay as they are;
  3. imports the new code and rehearses the schema migration on a copy of the live registry;
  4. saves the current compose file and container description to pre-<tag>/;
  5. switches the image tag and recreates the bot service alone (`--no-deps`);
  6. waits for the health check; on failure restores the previous compose file and image.

The panel, database, proxy and VPN containers are never restarted. Dependency or base-image
changes need a full `docker build apps/max-bot` and are outside this script.
"""
import argparse
import io
import pathlib
import subprocess
import sys
import tarfile

APP = pathlib.Path(__file__).resolve().parents[1]

REMOTE = r'''
import json, os, pathlib, shutil, sqlite3, subprocess, sys, tempfile, time
tag, container, root = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
os.umask(0o077)
image = 'vpn-max:' + tag

def sh(*cmd, check=True, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and p.returncode:
        raise SystemExit(f'FAILED: {" ".join(cmd[:4])}...\n{(p.stdout + p.stderr)[-1500:]}')
    return p.stdout.strip()

def label(name):
    return sh('docker', 'inspect', container, '--format', '{{index .Config.Labels "%s"}}' % name)

current = sh('docker', 'inspect', container, '--format', '{{.Config.Image}}')
files = label('com.docker.compose.project.config_files').split(',')
project, service = label('com.docker.compose.project'), label('com.docker.compose.service')
compose = pathlib.Path(next(f for f in files if f.endswith('compose.max.yml')))
data = next(m['Source'] for m in json.loads(sh('docker', 'inspect', container))[0]['Mounts'] if m['Destination'] == '/data')
if current == image:
    raise SystemExit(f'{image} is already running')

release = root / 'release'
(release / 'Dockerfile').write_text(f'FROM {current}\nCOPY --chown=10001:10001 vpnmax/*.py /app/vpnmax/\n')
sh('docker', 'build', '-q', '-t', image, str(release))
sandbox = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:size=16m']
sh(*sandbox, '--entrypoint', 'python', image, '-c', 'import vpnmax.app, vpnmax.cli')
print('built and importable:', image, 'on top of', current)

with tempfile.TemporaryDirectory() as copy:
    src = sqlite3.connect(f'file:{data}/clients.sqlite3?mode=ro', uri=True)
    dst = sqlite3.connect(copy + '/clients.sqlite3')
    src.backup(dst); dst.close(); src.close()
    for path in (copy, copy + '/clients.sqlite3'):
        os.chown(path, 10001, 10001)
    print('migration rehearsal:', sh(*sandbox, '-v', copy + ':/data', '--entrypoint', 'python', image, '-c',
        'from vpnmax.store import Store\ns=Store("/data/clients.sqlite3")\n'
        'with s.connect() as db: print(db.execute("pragma integrity_check").fetchone()[0],'
        ' db.execute("select count(*) from people").fetchone()[0], "people")'))

backup = root / ('pre-' + tag)
backup.mkdir(mode=0o700, exist_ok=True)
if not (backup / 'compose.max.yml').exists():
    shutil.copy2(compose, backup / 'compose.max.yml')
    (backup / 'container.json').write_text(sh('docker', 'inspect', container))

def ids():
    return dict(line.split() for line in sh('docker', 'ps', '--no-trunc', '--format', '{{.Names}} {{.ID}}').splitlines())

def switch(to):
    cfg = json.loads(compose.read_text())
    cfg['services'][service]['image'] = to
    compose.write_text(json.dumps(cfg, indent=2) + '\n')
    compose.chmod(0o600)
    base = ['docker', 'compose', '-p', project] + [x for f in files for x in ('-f', f)]
    sh(*base, 'config', '-q')
    sh(*base, 'up', '-d', '--no-deps', service)
    for _ in range(45):
        if sh('docker', 'inspect', container, '--format', '{{.State.Health.Status}}', check=False) == 'healthy':
            return True
        time.sleep(2)
    return False

before = ids()
if not switch(image):
    print('NOT healthy, rolling back to', current)
    ok = switch(current)
    raise SystemExit('ROLLED BACK: previous image is ' + ('healthy' if ok else 'NOT healthy, act now'))
after = ids()
others = sorted(n for n in before if n != container and before[n] != after.get(n))
print('released:', image, '| previous kept:', current, '| other containers restarted:', others or 'none')
print('rollback: set the image back in', compose, 'and run compose up -d --no-deps', service)
'''


def run(*cmd, **kwargs):
    return subprocess.run(cmd, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--host', required=True, help='SSH alias of the manager host')
    parser.add_argument('--container', default='amnezia-control-vpn-max-1')
    parser.add_argument('--root', default='/opt/vpn-max', help='bot directory on the manager')
    args = parser.parse_args()

    dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'vpnmax'], cwd=APP, capture_output=True, text=True, check=True).stdout
    if dirty:
        raise SystemExit('Commit apps/max-bot/vpnmax first: the image tag is the Git commit.\n' + dirty)
    tag = 'git-' + subprocess.run(['git', 'rev-parse', '--short=12', 'HEAD'], cwd=APP, capture_output=True, text=True, check=True).stdout.strip()
    run(sys.executable, '-m', 'pytest', '-q', cwd=APP)

    bundle = io.BytesIO()
    with tarfile.open(fileobj=bundle, mode='w:gz') as tar:
        for path in sorted((APP / 'vpnmax').glob('*.py')):
            tar.add(path, arcname=f'vpnmax/{path.name}')
    remote_dir = f'{args.root}/release'
    run('ssh', '-o', 'BatchMode=yes', args.host,
        f'rm -rf {remote_dir} && install -d -m 700 {remote_dir} && tar xzf - -C {remote_dir}', input=bundle.getvalue())
    run('ssh', '-o', 'BatchMode=yes', args.host, f'python3 - {tag} {args.container} {args.root}', input=REMOTE.encode())


if __name__ == '__main__':
    main()
