"""Run on management host as root once, after protected backup. No secret stdout."""
import json
import os
import pathlib
import secrets
import subprocess

os.umask(0o077)
path = pathlib.Path('/opt/vpn-max/.env')
settings = dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))
if not settings.get('PANEL_API_KEY'):
    program = '''
import os,json,urllib.request,urllib.error
from datetime import datetime,timedelta,timezone
password=os.environ['VPNPANEL_ADMIN_PASSWORD']
headers={}
def req(method,path,body=None):
 r=urllib.request.Request('http://127.0.0.1:8000/api/'+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers=dict(headers,**{'Content-Type':'application/json'}))
 with urllib.request.urlopen(r,timeout=20) as response:
  data=response.read()
  return json.loads(data) if data else None
try:
 login=req('POST','auth/login',{'username':os.environ['VPNPANEL_ADMIN_USER'],'password':password})
 headers['Authorization']='Bearer '+login['access_token']
 row=req('POST','apikeys',{'name':'vpn-max-self-service','password':password})
 try:
  req('PUT','apikeys/'+str(row['id']),{'password':password,'all_servers':False,'server_ids':[1,2,3,4],'expires_at':(datetime.now(timezone.utc)+timedelta(days=365)).isoformat()})
 except Exception:
  req('DELETE','apikeys/'+str(row['id']))
  raise
 print(json.dumps({'key':row['key'],'id':row['id']}))
except urllib.error.HTTPError as e:
 print(json.dumps({'error':'Panel HTTP '+str(e.code)}))
'''
    proc = subprocess.run(['docker','exec','-i','--user','10001','amnezia-control-backend-1','python','-'], input=program, text=True, capture_output=True, timeout=90)
    if proc.returncode:
        raise SystemExit('Panel key provisioning failed; secret-bearing traceback suppressed')
    result=json.loads(proc.stdout)
    if 'error' in result:
        raise SystemExit(result['error'])
    settings['PANEL_API_KEY']=result['key']
    pathlib.Path('/opt/vpn-max/panel-key-id').write_text(str(result['id'])+'\n')
settings.update(MAX_WEBHOOK_SECRET=settings.get('MAX_WEBHOOK_SECRET') or secrets.token_hex(32),
                MAX_CA_FILE='/app/certs/max-root-ca.pem', PANEL_URL='http://backend:8000/',
                PANEL_PROTOCOLS='{"1":"awg","2":"awg","3":"awg3","4":"awg"}',
                DATABASE_PATH='/data/clients.sqlite3')
path.write_text(''.join(k+'='+v+'\n' for k,v in settings.items()))
path.chmod(0o600)
print('Scoped panel key and MAX environment provisioned; scope servers 1-4, expiry 365 days')
