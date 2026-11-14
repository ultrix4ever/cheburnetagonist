"""Acceptance-only backend helper: official JWT login + ordinary step-up, no bypass."""
import json,os,sys,urllib.request,urllib.error
from pathlib import Path
req=json.load(sys.stdin)
cache=Path('/tmp/vpn-max-v2-accept-jwt')
headers={'Content-Type':'application/json'}
def call(method,path,body=None):
 r=urllib.request.Request('http://127.0.0.1:8000/api/'+path,method=method,headers=headers,data=json.dumps(body).encode() if body is not None else None)
 with urllib.request.urlopen(r,timeout=100) as response:
  content=response.read();return json.loads(content) if content else None
try:
 os.umask(0o077)
 if not cache.exists():cache.write_text(call('POST','auth/login',{'username':os.environ['VPNPANEL_ADMIN_USER'],'password':os.environ['VPNPANEL_ADMIN_PASSWORD']})['access_token']);cache.chmod(0o600)
 token=cache.read_text()
 if req.get('token'):print(json.dumps({'ok':True,'result':token}));raise SystemExit(0)
 headers['Authorization']='Bearer '+token
 body=req.get('body')
 if req['method'] in ('POST','PUT'):
  body=body or {};body['password']=os.environ['VPNPANEL_ADMIN_PASSWORD']
 result=call(req['method'],'max-users'+req.get('path',''),body)
 print(json.dumps({'ok':True,'result':result},ensure_ascii=False))
except urllib.error.HTTPError as e:
 print(json.dumps({'ok':False,'http':e.code}))
except Exception as e:
 print(json.dumps({'ok':False,'error':type(e).__name__}))
