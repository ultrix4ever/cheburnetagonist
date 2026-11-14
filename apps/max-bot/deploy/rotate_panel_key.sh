#!/bin/bash
# Rotate the vpn-max panel API key after a password change revoked all keys.
# Never prints the key; only the public prefix.
set -euo pipefail
umask 077
ENV=/opt/vpn-max/.env
cp -p "$ENV" "$ENV.before-rotate-$(date -u +%Y%m%d%H%M%S)"

read -r KEY PREFIX HASH < <(docker exec amnezia-control-backend-1 python -c \
  "from app import apikeys; k,p,h=apikeys.generate(); print(k,p,h)")
[ -n "$KEY" ] && [ -n "$PREFIX" ] && [ -n "$HASH" ]

# Scope = exactly the servers the bot is configured for, so a rotation never narrows or widens it.
SERVERS=$(grep '^PANEL_PROTOCOLS=' "$ENV" | cut -d= -f2- | python3 -c 'import json,sys; print(",".join(sorted(json.load(sys.stdin), key=int)))')
[ -n "$SERVERS" ]

docker exec -i amnezia-control-db-1 psql -v ON_ERROR_STOP=1 -q -U acontrol -d acontrol \
  -v prefix="$PREFIX" -v hash="$HASH" -v servers="$SERVERS" <<'SQL'
INSERT INTO api_keys(name,prefix,key_hash,servers,expires_at,revoked,created_at)
VALUES ('vpn-max-self-service', :'prefix', :'hash', :'servers', now() + interval '1 year', false, now());
INSERT INTO audit_log(username,action,target,detail)
VALUES ('admin','apikey_create','vpn-max-self-service','rotated by OpenClaw after password_change revoked all keys');
SQL

KEY="$KEY" python3 - "$ENV" <<'PY'
import os, sys
p = sys.argv[1]
lines = open(p).read().splitlines()
out = [('PANEL_API_KEY=' + os.environ['KEY']) if l.startswith('PANEL_API_KEY=') else l for l in lines]
assert sum(l.startswith('PANEL_API_KEY=') for l in out) == 1
open(p, 'w').write('\n'.join(out) + '\n')
PY
unset KEY HASH

cd /opt/amnezia-control
docker compose -f compose.yml -f compose.override.yml -f compose.edge.yml -f compose.max.yml \
  up -d --no-deps --force-recreate vpn-max 2>&1 | tail -2
echo "new key prefix: $PREFIX"
