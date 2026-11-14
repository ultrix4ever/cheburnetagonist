"""Daily protected off-host backup with verification and retention.

Never prints archive or credential contents. Configuration through the environment:

  VPN_MAX_BACKUP_SSH   SSH destination of the manager (alias or user@host), required
  VPN_MAX_BACKUP_DIR   where archives are kept (default /root/.local/share/vpn-max/backups)
  VPN_MAX_BACKUP_DAILY_DAYS   keep every archive this many days (default 30)
  VPN_MAX_BACKUP_MONTHS       then keep the newest archive of each month this long (default 12)

Old archives are removed only after the new one has been verified: the PostgreSQL dump has its
magic header, the registry copy passes SQLite integrity_check, the panel SSH identity is present.
A failed or unverifiable run deletes nothing. The newest KEEP_MIN archives are never removed.
"""
import datetime
import os
import pathlib
import re
import sqlite3
import subprocess
import tarfile
import tempfile

os.umask(0o077)
KEEP_MIN = 7
NAME = re.compile(r'vpn-max-(\d{8}T\d{6}Z)\.tgz')

REMOTE = r'''
import io,os,subprocess,sys,tarfile
os.umask(0o077)
snapshot="import os,sqlite3;os.umask(0o077);a=sqlite3.connect('/data/clients.sqlite3');b=sqlite3.connect('/data/backup-snapshot.sqlite3');a.backup(b);b.close();a.close()"
subprocess.run(['docker','exec','amnezia-control-vpn-max-1','python','-c',snapshot],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
dump=subprocess.run(['docker','exec','amnezia-control-db-1','pg_dump','-U','acontrol','-d','acontrol','-Fc'],check=True,capture_output=True).stdout
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as tar:
 info=tarfile.TarInfo('panel.pgdump');info.size=len(dump);info.mode=0o600;tar.addfile(info,io.BytesIO(dump))
 for path,name in [('/opt/vpn-max/.env','vpn-max.env'),('/opt/vpn-max/panel-key-id','panel-key-id'),('/opt/vpn-max/data/backup-snapshot.sqlite3','registry.sqlite3')]:tar.add(path,arcname=name)
 def exclude(member):
  return None if '/postgres' in member.name else member
 tar.add('/opt/amnezia-control',arcname='panel',filter=exclude)
 # manager-awg3: server keys of a VPN node co-hosted with the manager, when there is one.
 for path,name in [('/opt/vpn-max/registry-admin.env','registry-admin.env'),('/opt/vpn-max/v2','release-v2'),('/opt/amnezia/awg3','manager-awg3')]:
  if os.path.exists(path):tar.add(path,arcname=name,filter=lambda m: None if '__pycache__' in m.name else m)
'''


def verify(path):
    with tarfile.open(path) as tar:
        names = set(tar.getnames())
        if tar.extractfile('panel.pgdump').read(5) != b'PGDMP':
            raise ValueError('PostgreSQL dump header')
        if 'panel/data/ssh/id_ed25519' not in names:
            raise ValueError('panel SSH identity missing')
        with tempfile.TemporaryDirectory() as folder:
            copy = pathlib.Path(folder) / 'registry.sqlite3'
            copy.write_bytes(tar.extractfile('registry.sqlite3').read())
            db = sqlite3.connect(copy)
            try:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('registry integrity')
            finally:
                db.close()
    return len(names)


def expired(archives, now, daily_days, months):
    """Archives to delete, given {path: datetime}. Pure function: tested without touching disk."""
    ordered = sorted(archives, key=archives.get, reverse=True)
    keep = set(ordered[:KEEP_MIN])
    newest_of_month = {}
    for path in ordered:  # newest first
        newest_of_month.setdefault(archives[path].strftime('%Y%m'), path)
    for path in ordered:
        age = now - archives[path]
        if age <= datetime.timedelta(days=daily_days):
            keep.add(path)
        elif age <= datetime.timedelta(days=31 * months) and newest_of_month[archives[path].strftime('%Y%m')] == path:
            keep.add(path)
    return [path for path in ordered if path not in keep]


def main():
    target = os.environ['VPN_MAX_BACKUP_SSH']
    folder = pathlib.Path(os.environ.get('VPN_MAX_BACKUP_DIR', '/root/.local/share/vpn-max/backups'))
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
                             target, 'python3 -'], input=REMOTE.encode(), capture_output=True, timeout=180)
    if result.returncode:
        raise SystemExit('Off-host backup failed; remote error suppressed')
    path = folder / ('vpn-max-' + now.strftime('%Y%m%dT%H%M%SZ') + '.tgz')
    path.write_bytes(result.stdout)
    path.chmod(0o600)
    try:
        members = verify(path)
    except Exception as exc:
        path.rename(path.with_suffix('.tgz.unverified'))
        raise SystemExit(f'Backup saved but NOT verified ({type(exc).__name__}); nothing was rotated') from None
    print('Off-host backup saved and verified:', path.name, 'bytes:', path.stat().st_size, 'members:', members)

    archives = {}
    for item in folder.iterdir():
        match = NAME.fullmatch(item.name)
        if match:
            archives[item] = datetime.datetime.strptime(match.group(1), '%Y%m%dT%H%M%SZ').replace(tzinfo=datetime.timezone.utc)
    old = expired(archives, now, int(os.environ.get('VPN_MAX_BACKUP_DAILY_DAYS', 30)),
                  int(os.environ.get('VPN_MAX_BACKUP_MONTHS', 12)))
    for item in old:
        item.unlink()
    print('Retention: kept', len(archives) - len(old), 'removed', len(old))


if __name__ == '__main__':
    main()
