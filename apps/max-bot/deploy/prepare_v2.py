"""Prepare build contexts from the pinned upstream checkout, with fail-closed patches.
Usage: python3 prepare_v2.py UPSTREAM_REPO OUTPUT_DIR
Then run npm ci && npm run build in OUTPUT_DIR/frontend and copy dist to frontend-build.
"""
from pathlib import Path
import shutil
import sys

upstream=Path(sys.argv[1]).resolve();output=Path(sys.argv[2]).resolve()
project=Path(__file__).resolve().parents[1]
output.mkdir(mode=0o700,parents=True,exist_ok=True)
frontend=output/'frontend'
shutil.copytree(upstream/'frontend',frontend,dirs_exist_ok=True,ignore=shutil.ignore_patterns('node_modules','dist'))
for name in ['MaxUsersPage.tsx','MaxUsersPage.css']:
    shutil.copyfile(project/'panel-extension'/name,frontend/'src'/name)
path=frontend/'src/App.tsx';text=path.read_text()
old="import { ApiKeysPage } from './ApiKeysPage'"
assert text.count(old)==1
text=text.replace(old,old+"\nimport { MaxUsersPage } from './MaxUsersPage'")
old="'audit' | 'apikeys'";assert text.count(old)==1
text=text.replace(old,old+" | 'maxusers'")
old="            <button\n              className={view === 'apikeys' ? 'navlink navlink-active' : 'navlink'}"
assert text.count(old)==1
text=text.replace(old,"            <button className={view === 'maxusers' ? 'navlink navlink-active' : 'navlink'} onClick={() => setView('maxusers')}>Пользователи MAX</button>\n"+old)
old="        ) : view === 'apikeys' ? (";assert text.count(old)==1
text=text.replace(old,"        ) : view === 'maxusers' ? (\n          <MaxUsersPage onUnauthorized={logout} />\n"+old)
path.write_text(text)
backend=output/'backend-build';backend.mkdir(exist_ok=True)
text=(upstream/'backend/app/main.py').read_text()
old='    app.include_router(v1.router, prefix="/api")';assert text.count(old)==1
text=text.replace(old,old+'\n    from app.api import max_awg3_v1, max_users\n    app.include_router(max_awg3_v1.router, prefix="/api")\n    app.include_router(max_users.router, prefix="/api")')
(backend/'main.py').write_text(text)
for name in ['max_awg3_v1.py','max_users.py']:shutil.copyfile(project/'panel-extension'/name,backend/name)
(backend/'Dockerfile').write_text('FROM ghcr.io/mihsergeev/amnezia-control-backend@sha256:8e850c85cf57d5ed790c45194a1af28a81dbd99dd51854933100c178d2aa0564\nCOPY --chown=10001:10001 main.py /srv/app/main.py\nCOPY --chown=10001:10001 max_awg3_v1.py max_users.py /srv/app/api/\n')
front_image=output/'frontend-build';front_image.mkdir(exist_ok=True)
(front_image/'Dockerfile').write_text('FROM ghcr.io/mihsergeev/amnezia-control-frontend@sha256:426750a4972184e787d93e9311f221cfab14ca2cb2370eb5d81fa2bac42ff9e0\nCOPY dist /usr/share/nginx/html\nRUN chmod -R a+rX /usr/share/nginx/html\n')
print('Pinned frontend/backend contexts prepared; no deployment or credentials changed')
