from pathlib import Path
import httpx,ssl,json,os,sys
from urllib.parse import urlparse
os.umask(0o077)
root=Path('/home/operator/.local/share/vpn-max')
env=dict(line.split('=',1) for line in (root/'guide-upload.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
trust=ssl.create_default_context();trust.load_verify_locations(cafile=str(root/'max-root-ca.pem'))
try:
 with httpx.Client(verify=trust,timeout=60,follow_redirects=False) as client:
  r=client.post('https://platform-api2.max.ru/uploads',params={'type':'file'},headers={'Authorization':env['MAX_BOT_TOKEN']})
  if r.status_code!=200:raise RuntimeError('Upload allocation HTTP '+str(r.status_code))
  allocation=r.json();url=allocation['url'];parsed=urlparse(url)
  assert parsed.scheme=='https'
  print('Upload host:',parsed.hostname,flush=True)
  with open('/home/operator/.openclaw/workspace/artifacts/vpn-guide/instruction.pdf','rb') as f:
   response=client.post(url,files={'data':('AMN-instruction.pdf',f,'application/pdf')})
  if not response.is_success:raise RuntimeError('Upload HTTP '+str(response.status_code))
  payload=response.json()
  token=payload.get('token') or allocation.get('token')
  if not token:raise RuntimeError('No file token in result; keys: '+','.join(payload))
  (root/'guide-upload.json').write_text(json.dumps({'token':token}))
  print('PDF uploaded; reusable token stored privately')
except Exception as exc:
 print(type(exc).__name__+': '+(str(exc) if isinstance(exc,(RuntimeError,AssertionError)) else 'upload failed (details suppressed)'))
 sys.exit(1)
