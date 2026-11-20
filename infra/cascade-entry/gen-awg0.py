#!/usr/bin/env python3
"""Creates the server side of a new AmneziaWG 3.1 entry in the given directory (run once, on the host)."""
import base64, os, secrets, subprocess, sys
d, port, net, image = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
if os.path.exists(f'{d}/awg0.conf'):
    sys.exit('awg0.conf already exists; not overwriting')
def awg(*a, data=None):
    return subprocess.run(['docker', 'run', '--rm', '-i', '--entrypoint', 'awg', image, *a], input=data, text=True, capture_output=True, check=True).stdout.strip()
priv = awg('genkey'); pub = awg('pubkey', data=priv + '\n'); psk = awg('genpsk')
r = secrets.SystemRandom()
edges = sorted(r.sample(range(5, 2**31 - 1), 8))
h = [f'{edges[i]}-{edges[i + 1]}' for i in (0, 2, 4, 6)]
s = r.randint(10, 19)
a = r.randint(120, 128); b = r.randint(180, 188)
conf = f"""[Interface]
PrivateKey = {priv}
Address = {net}
ListenPort = {port}
Jc = {r.randint(3, 6)}
Jmin = 10
Jmax = 50
S1 = {s}
S2 = {s}
S3 = {s}
S4 = {s}
H1 = {h[0]}
H2 = {h[1]}
H3 = {h[2]}
H4 = {h[3]}
HeaderProtectionKey = {base64.b64encode(secrets.token_bytes(32)).decode()}
ContentPaddingAddition = {r.randint(1, 3)}-{r.randint(9, 17)}
RekeyAfterTime = {a}-{a + r.randint(6, 10)}
RekeyTimeout = {r.randint(5, 6)}-7
RejectAfterTime = {b}-{b + r.randint(5, 8)}
KeepaliveTimeout = {r.randint(10, 11)}-12
MaxHandshakeAttempts = {r.randint(18, 19)}-20
RandomTrailers = on
DisableCookies = on
# I1 = <r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>
# I2 = 
# I3 = 
# I4 = 
# I5 = 
"""
os.umask(0o077)
for name, text in (('awg0.conf', conf), ('wireguard_server_private_key.key', priv + '\n'), ('wireguard_server_public_key.key', pub + '\n'),
                   ('wireguard_psk.key', psk + '\n'), ('clientsTable', '[]\n')):
    with open(f'{d}/{name}', 'w') as f:
        f.write(text)
print('created', d, 'port', port, 'net', net)
