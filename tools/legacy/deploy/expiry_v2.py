"""Bounded live check of the worker expiry timer on disposable profiles only."""
import asyncio,json,pathlib,re,time
from acceptance_v2 import rpc,admin,command,replace,config,UID,FOREIGN,PROTOCOLS
from acceptance import MANAGER,TEST,CONTAINER,remote,up,down,traffic,eventual_traffic

prefix='v2accept-expiry-'+str(int(time.time()))
created=False;container=False;report={}
try:
 assert not any(p['max_id']==str(UID) for p in admin()['people'])
 person_id=admin('POST','/people',dict(name=prefix,max_id=str(UID),quota=1,active=True,phone='',allowed_servers=[1,4]))['id'];created=True
 remote(TEST,['docker','run','-d','--name',CONTAINER,'--cap-add','NET_ADMIN','--device','/dev/net/tun','--memory','128m','--cpus','0.5','--entrypoint','/bin/sh','amnezia-awg2','-c','sleep 300']);container=True
 issued=command('/new 1 '+prefix);old=int(re.search(r'#(\d+)',issued['reply']).group(1));old_cfg=config(old)
 up(old_cfg);assert eventual_traffic()
 rep=replace(old,'/move',4);target_cfg=config(rep['new_id']);assert rpc(dict(kind='expire',uid=UID))['ok']
 for _ in range(30):
  row=next(r for r in admin()['replacements'] if r['id']==rep['id'])
  if row['state'] in ('cancelled','unknown'):break
  time.sleep(.5)
 assert row['state']=='cancelled', 'Expiry worker did not confirm rollback'
 assert traffic(),'Original tunnel no longer works after expiry'
 down();up(target_cfg);assert not traffic(),'Expired replacement tunnel still passes'
 report={'worker_expiry':True,'old_tunnel_preserved':True,'target_tunnel_blocked':True}
 print('Live expiry passed: worker revoked target; original tunnel preserved',flush=True)
finally:
 if container:down();remote(TEST,['docker','rm','-f',CONTAINER],check=False)
 if created:
  cleanup=rpc(dict(kind='cleanup',uids=[UID]));report['cleanup']=cleanup
  assert cleanup['ok'] and not cleanup['result']['cleanup_failed']
 pathlib.Path('/home/operator/.local/share/vpn-max/expiry-v2-result.json').write_text(json.dumps(report,indent=2))
