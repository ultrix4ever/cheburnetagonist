"""Live scoped API/bot acceptance; disposable peers and a disposable tunnel container only."""
import asyncio,json,os,pathlib,re,shlex,subprocess,tempfile,time
from vpnmax.api import Panel
from vpnmax.bot import Bot
from vpnmax.store import Store,Denied
SSH=['ssh','-i','/home/operator/.ssh/id_ed25519_vpn','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10']
MANAGER='root@203.0.113.30';TEST='root@203.0.113.10';CONTAINER='vpn-max-acceptance'
UID=900000000000000001;FOREIGN=900000000000000002
PREFIX='max-accept-'+str(int(time.time()));PROTOCOLS={1:'awg',2:'awg',3:'awg3',4:'awg'}
def remote(host,args,data=None,check=True):
 r=subprocess.run(SSH+[host,shlex.join(args)],input=data,text=True,capture_output=True,timeout=45)
 if check and r.returncode:raise RuntimeError('Remote acceptance command failed; output suppressed')
 return r
def test(args,data=None,check=True):return remote(TEST,['docker','exec','-i',CONTAINER]+args,data,check)
def down():test(['sh','-c','ip link delete mxcheck 2>/dev/null || true'],check=False)
def up(config):
 address=re.search(r'^Address\s*=\s*([^,\s]+)',config,re.M).group(1)
 config='\n'.join(l for l in config.splitlines() if not re.match(r'^(Address|DNS|MTU|Table|PostUp|PostDown|PreUp|PreDown)\s*=',l))+'\n'
 test(['sh','-c','umask 077; cat > /tmp/client.conf'],config)
 test(['sh','-c','amneziawg-go mxcheck >/dev/null 2>&1; awg setconf mxcheck /tmp/client.conf; ip address add '+shlex.quote(address)+' dev mxcheck; ip link set dev mxcheck mtu 1280 up; ip route add 1.1.1.1/32 dev mxcheck'])
def traffic():return test(['ping','-c','2','-W','3','1.1.1.1'],check=False).returncode==0
def eventual_traffic():
 for attempt in range(4):
  if traffic():return True
  time.sleep(1)
 return False
class CountPanel(Panel):
 count=0
 async def request(self,*a,**kw):
  self.count+=1
  return await super().request(*a,**kw)
async def main():
 os.umask(0o077)
 env=dict(l.split('=',1) for l in pathlib.Path('/home/operator/.local/share/vpn-max/runtime.env').read_text().splitlines() if '=' in l)
 ip=remote(MANAGER,['docker','inspect','amnezia-control-backend-1','--format','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}']).stdout.strip()
 forward=subprocess.Popen(SSH+['-N','-T','-o','ExitOnForwardFailure=yes','-L',f'127.0.0.1:18091:{ip}:8000',MANAGER],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 panel=CountPanel('http://127.0.0.1:18091/',env['PANEL_API_KEY'],PROTOCOLS)
 baseline={};results=[];container=False
 shape=lambda rows:sorted((r['public_key'],r['name'],bool(r.get('paused'))) for r in rows)
 try:
  await asyncio.sleep(1)
  for sid in PROTOCOLS:baseline[sid]=await panel.clients(sid)
  print('Baseline:',{s:len(r) for s,r in baseline.items()},flush=True)
  remote(TEST,['docker','run','-d','--name',CONTAINER,'--cap-add','NET_ADMIN','--device','/dev/net/tun','--memory','128m','--cpus','0.5','--entrypoint','/bin/sh','amnezia-awg2','-c','sleep 900']);container=True
  with tempfile.TemporaryDirectory(prefix='vpnmax-accept-') as temp:
   store=Store(pathlib.Path(temp)/'registry.sqlite3')
   for uid in (UID,FOREIGN):
    _,token=store.invite('Acceptance owner',uid);store.claim(uid,token)
   bot=Bot(store,panel,None,PROTOCOLS)
   for sid in PROTOCOLS:
    try:
     response,_=await bot.command(UID,f'/new {sid} {PREFIX}-{sid}')
     pid=int(re.search(r'#(\d+)',response).group(1));row=store.owned(UID,pid)
     for cmd in ('/config','/pause','/resume','/revoke'):
      calls=panel.count
      try:await bot.command(FOREIGN,f'{cmd} {pid}')
      except Denied:pass
      else:raise AssertionError('Foreign owner accepted')
      assert calls==panel.count
     export=await panel.config(row)
     link,_=await bot.command(UID,f'/config {pid}')
     assert export['config_amnezia'].startswith('vpn://') and export['config_amnezia'] in link
     up(export['config']);assert eventual_traffic(),'Issued tunnel traffic failed'
     print('Server',sid,'issuance, export, owner isolation and tunnel passed',flush=True)
     result={'server_id':sid,'issued':True,'owner_isolation':True,'export':True,'tunnel_traffic':True}
     for action,expected in [('pause',False),('resume',True),('revoke',False)]:
      _,buttons=await bot.command(UID,f'/{action} {pid}');confirmation=buttons[0][0]['payload']
      await bot.command(UID,confirmation)
      assert (eventual_traffic() if expected else traffic())==expected,action+' traffic mismatch'
      result[action]=True;calls=panel.count
      try:await bot.command(UID,confirmation)
      except Denied:pass
      else:raise AssertionError('Confirmation replay accepted')
      assert calls==panel.count
     results.append(result);print(json.dumps(result),flush=True)
    finally:
     down()
     for c in await panel.clients(sid):
      if c['name'].startswith(PREFIX+'-'+str(sid)):await panel.mutate('revoke',{'server_id':sid,'public_key':c['public_key']})
     assert shape(await panel.clients(sid))==shape(baseline[sid]),'Baseline mismatch'
 finally:
  failures=[]
  for sid in baseline:
   try:
    for c in await panel.clients(sid):
     if c['name'].startswith(PREFIX+'-'+str(sid)):await panel.mutate('revoke',{'server_id':sid,'public_key':c['public_key']})
    assert shape(await panel.clients(sid))==shape(baseline[sid])
   except Exception:failures.append(sid)
  if container:remote(TEST,['docker','rm','-f',CONTAINER],check=False)
  await panel.http.aclose();forward.terminate();forward.wait(timeout=15)
  pathlib.Path('/home/operator/.local/share/vpn-max/acceptance-result.json').write_text(json.dumps({'results':results,'cleanup_failures':failures,'at':int(time.time())},indent=2))
  print('Cleanup failures:',failures,flush=True)
  if failures:raise RuntimeError('Cleanup needs operator attention')
if __name__=='__main__':
 try:asyncio.run(main())
 except Exception as e:
  print('Acceptance failed:',type(e).__name__,str(e) if isinstance(e,AssertionError) else '',flush=True)
  raise SystemExit(1)
