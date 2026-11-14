"""Prepare approved v2 compose on manager. Keeps pre-v2 image/config evidence."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

os.umask(0o077)
root=Path('/opt/vpn-max')
backup=root/'pre-v2';backup.mkdir(mode=0o700,exist_ok=True)
compose=Path('/opt/amnezia-control/compose.max.yml')
if not (backup/'compose.max.yml').exists():
    shutil.copyfile(compose,backup/'compose.max.yml')
    shutil.copyfile('/opt/amnezia-control/Caddyfile',backup/'Caddyfile')
    inspect=subprocess.run(['docker','inspect','amnezia-control-vpn-max-1','amnezia-control-backend-1','amnezia-control-frontend-1'],check=True,capture_output=True).stdout
    (backup/'containers.json').write_bytes(inspect)
keyfile=root/'registry-admin.env'
if not keyfile.exists():
    keyfile.write_text('REGISTRY_ADMIN_SECRET='+secrets.token_hex(32)+'\n')
    keyfile.chmod(0o600)
cfg=json.loads(compose.read_text())
cfg['services']['backend'].update(image='vpn-max-backend:v2',env_file=['/opt/vpn-max/registry-admin.env'])
cfg['services']['frontend']={'image':'vpn-max-frontend:v2'}
cfg['services']['vpn-max'].update(image='vpn-max:v2',env_file=['/opt/vpn-max/.env','/opt/vpn-max/registry-admin.env'])
compose.write_text(json.dumps(cfg,indent=2)+'\n');compose.chmod(0o600)
print('V2 compose prepared; old images/config retained; registry secret protected')
