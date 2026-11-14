"""Live panel/registry acceptance. Captures all secret-bearing outputs; no MAX test messages."""
import asyncio,json,os,pathlib,re,subprocess,time
from acceptance import SSH,MANAGER,TEST,CONTAINER,remote,up,down,traffic,eventual_traffic
from vpnmax.api import Panel,RemoteError
UID=900000000000001;FOREIGN=900000000000002;PREFIX='v2accept-'+str(int(time.time()))
PROTOCOLS={1:'awg',2:'awg',3:'awg3',4:'awg'}
def rpc(request):return json.loads(remote(MANAGER,['docker','exec','-i','-e','PYTHONPATH=/app','amnezia-control-vpn-max-1','python','/tmp/live_v2_rpc.py'],json.dumps(request)).stdout)
def admin(method='GET',path='',body=None):
 r=json.loads(remote(MANAGER,['docker','exec','-i','amnezia-control-backend-1','python','/tmp/live_v2_admin.py'],json.dumps(dict(method=method,path=path,body=body))).stdout)
 if not r.get('ok'):raise RuntimeError('Panel API failed: '+str(r.get('http',r.get('error'))))
 return r['result']
def command(text,uid=UID,ok=True):
 r=rpc(dict(kind='command',uid=uid,command=text))
 if ok and not r['ok']:raise RuntimeError('Bot command failed: '+r['error'])
 return r['result'] if ok else r
def replace(pid,mode='/reissue',sid=None):
 if mode=='/reissue' and sid is None:
  sid=next(p['server_id'] for p in admin()['profiles'] if p['id']==pid)
 menu=command(f'{mode} {pid}'+(f' {sid}' if sid else ''));command(menu['buttons'][0][0]['payload'])
 r=rpc(dict(kind='replacement',uid=UID));assert r['ok'];return r['result']
def config(pid):
 r=rpc(dict(kind='config',uid=UID,pid=pid));assert r['ok'];return r['result']['config']
def person(snapshot,pid):return next(p for p in snapshot['people'] if p['id']==pid)
def tunnel(cfg,expected=True):
 down();up(cfg);assert (eventual_traffic() if expected else traffic())==expected,'Tunnel traffic mismatch'
async def main():
 os.umask(0o077)
 env=dict(l.split('=',1) for l in pathlib.Path('/home/operator/.local/share/vpn-max/runtime.env').read_text().splitlines() if '=' in l)
 ip=remote(MANAGER,['docker','inspect','amnezia-control-backend-1','--format','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}']).stdout.strip()
 forward=subprocess.Popen(SSH+['-N','-T','-o','ExitOnForwardFailure=yes','-L',f'127.0.0.1:18091:{ip}:8000',MANAGER],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 panel=Panel('http://127.0.0.1:18091/',env['PANEL_API_KEY'],PROTOCOLS)
 baseline={};results=[];container=False;created=False
 shape=lambda rows:sorted((r['public_key'],r['name'],bool(r.get('paused'))) for r in rows)
 def passed(text):results.append(text);print(text,flush=True)
 try:
  for attempt in range(15):
   try:
    response=await panel.http.get('api/health');response.raise_for_status();break
   except Exception:
    if forward.poll() is not None:raise RuntimeError('SSH forward failed')
    await asyncio.sleep(.5)
  for sid in PROTOCOLS:baseline[sid]=await panel.clients(sid)
  print('Fresh baseline:',{s:len(r) for s,r in baseline.items()},flush=True)
  before=admin();assert not any(str(p['max_id']) in [str(UID),str(FOREIGN)] for p in before['people'])
  created=True
  for uid in [UID,FOREIGN]:admin('POST','/people',dict(name=PREFIX+'-'+str(uid),max_id=str(uid),quota=1,active=True,phone='',allowed_servers=[1,2,3,4]))
  state=admin();user=next(p for p in state['people'] if p['max_id']==str(UID));person_id=user['id']
  remote(TEST,['docker','run','-d','--name',CONTAINER,'--cap-add','NET_ADMIN','--device','/dev/net/tun','--memory','128m','--cpus','0.5','--entrypoint','/bin/sh','amnezia-awg2','-c','sleep 1200']);container=True
  rpc(dict(kind='catalog'))
  issued=command('/new 1 '+PREFIX);old=int(re.search(r'#(\d+)',issued['reply']).group(1))
  assert command('/new 3 excess',ok=False)['error']=='Denied'
  assert command('/config '+str(old),uid=FOREIGN,ok=False)['error']=='Denied'
  old_cfg=config(old);tunnel(old_cfg);assert person(admin(),person_id)['used']==1
  passed('panel admission, cross-server personal quota, foreign-owner rejection, issued tunnel')
  rep=replace(old,'/move',3);current=rep['new_id'];tunnel(config(current));assert person(admin(),person_id)['used']==1
  command('/switch '+rep['id']);tunnel(old_cfg,False);tunnel(config(current))
  passed('cross-family move at full quota, old tunnel blocked, target works')
  rep=replace(current);current=rep['new_id'];tunnel(config(current));command('/switch '+rep['id'])
  assert person(admin(),person_id)['used']==1;passed('same-node reissue at full quota')
  rep=replace(current,'/move',4);target_cfg=config(rep['new_id']);command('/cancel '+rep['id'])
  tunnel(target_cfg,False);tunnel(config(current));passed('cancel move revokes target and preserves original')
  menu=command('/compromised '+str(current));command(menu['buttons'][0][0]['payload'])
  state=admin();owned=[p for p in state['profiles'] if p['person_id']==person_id and p['state']=='active'];assert len(owned)==1
  current=owned[0]['id'];tunnel(config(current));assert person(state,person_id)['used']==1;passed('immediate compromised-key replacement')
  form=dict(name=user['name'],quota=1,active=False,phone='',allowed_servers=[1,2,3,4]);admin('PUT',f'/people/{person_id}',form)
  assert command('/my',ok=False)['error']=='Denied' and traffic()
  form['active']=True;form['quota']=0;admin('PUT',f'/people/{person_id}',form)
  assert traffic() and command('/new 2 excess',ok=False)['error']=='Denied';passed('bot-only block and lowered quota preserve VPN')
  job=admin('POST',f'/people/{person_id}/revoke-all')['job_id']
  for _ in range(40):
   status=next(j for j in admin()['jobs'] if j['id']==job)
   if status['state'] not in ['pending','processing']:break
   time.sleep(.5)
  assert status['state']=='done','Bulk revoke did not complete'
  assert not traffic(),'Revoked tunnel still passes traffic';passed('full VPN revoke job completed; real traffic blocked')
 finally:
  if container:down();remote(TEST,['docker','rm','-f',CONTAINER],check=False)
  cleanup=rpc(dict(kind='cleanup',uids=[UID,FOREIGN])) if created else {'ok':True,'result':{'cleanup_failed':False}}
  failures=[]
  for sid in baseline:
   try:assert shape(await panel.clients(sid))==shape(baseline[sid])
   except Exception:failures.append(sid)
  await panel.http.aclose();forward.terminate();forward.wait(timeout=15)
  report=dict(results=results,baseline_failures=failures,cleanup=cleanup,at=int(time.time()))
  pathlib.Path('/home/operator/.local/share/vpn-max/acceptance-v2-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
  print('Cleanup/baseline:',not failures and cleanup.get('ok') and not cleanup.get('result',{}).get('cleanup_failed'),flush=True)
  if failures or not cleanup.get('ok') or cleanup.get('result',{}).get('cleanup_failed'):raise RuntimeError('Cleanup requires review')
if __name__=='__main__':
 try:asyncio.run(main())
 except Exception as e:
  print('Live acceptance failed:',type(e).__name__,str(e) if isinstance(e,(AssertionError,RemoteError)) else '',flush=True);raise SystemExit(1)
