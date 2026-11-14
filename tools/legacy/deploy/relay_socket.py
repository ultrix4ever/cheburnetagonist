"""Manage ONLY the private relay socket on the management host."""
import os
import pathlib
import stat
import sys
import time

path=pathlib.Path('/opt/vpn-max/relay/max-api.sock')
if sys.argv[1]=='prepare':
    path.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    path.parent.chmod(0o755)
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise SystemExit('Unexpected relay path type')
        path.unlink()
elif sys.argv[1]=='permissions':
    for _ in range(40):
        if path.exists():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise SystemExit('Unexpected relay path type')
            os.chown(path,10001,10001)
            path.chmod(0o600)
            break
        time.sleep(0.25)
    else:
        raise SystemExit('Relay socket not created')
else:
    raise SystemExit('Unknown operation')
