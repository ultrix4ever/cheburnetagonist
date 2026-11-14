"""Daily protected off-host backup. Never emits archive/credential contents."""
import datetime
import os
import pathlib
import subprocess

os.umask(0o077)
remote=r'''
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
 for path,name in [('/opt/vpn-max/registry-admin.env','registry-admin.env'),('/opt/vpn-max/v2','release-v2')]:
  if os.path.exists(path):tar.add(path,arcname=name,filter=lambda m: None if '__pycache__' in m.name else m)
'''
folder=pathlib.Path('/root/.local/share/vpn-max/backups');folder.mkdir(mode=0o700,parents=True,exist_ok=True)
name='vpn-max-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.tgz'
result=subprocess.run(['ssh','-i','/root/.ssh/id_ed25519_vpn','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','root@203.0.113.30','python3 -'],input=remote.encode(),capture_output=True,timeout=180)
if result.returncode:
 raise SystemExit('Off-host backup failed; remote error suppressed')
path=folder/name
path.write_bytes(result.stdout);path.chmod(0o600)
print('Off-host backup saved:',path.name,'bytes:',path.stat().st_size)
