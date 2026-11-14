const { chromium } = require('playwright');
const fs=require('fs'), http=require('http'), path=require('path'), assert=require('assert');
(async()=>{
const root='/home/operator/.local/share/vpn-max/v2-build/frontend/dist';
const server=http.createServer((req,res)=>{const p=path.join(root,req.url==='/'?'index.html':req.url.split('?')[0]);fs.readFile(p,(e,b)=>{if(e){res.writeHead(404);return res.end()}res.setHeader('Content-Type',p.endsWith('.js')?'application/javascript':p.endsWith('.css')?'text/css':p.endsWith('.html')?'text/html':'application/octet-stream');res.end(b)})});
await new Promise(r=>server.listen(18092,'127.0.0.1',r));
const browser=await chromium.launch({headless:true});
try{
const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>localStorage.setItem('acontrol_token','test-only'));
const snapshot={people:[{id:1,name:'Тестовый пользователь',max_id:'123456',active:1,quota:2,used:1,phone:'',allowed_servers:[1,3]}],profiles:[{id:1,server_id:1,name:'Телефон',person_id:1,state:'active',has_config:true},{id:2,server_id:3,name:'Старый профиль',person_id:null,state:'active',has_config:false}],servers:[{id:1,name:'Сервер 1',online:true},{id:3,name:'Сервер 3',online:true}],replacements:[],jobs:[],audit:[],operations:[]};
const writes=[];
await page.route('**/api/**',async route=>{const req=route.request(),u=new URL(req.url());let body={},status=200;
if(u.pathname==='/api/max-users' && req.method()==='GET')body=snapshot;
else if(u.pathname==='/api/max-users/people' && req.method()==='POST'){const d=req.postDataJSON();writes.push(d);assert.equal(d.max_id,'987654');assert.equal(d.quota,3);snapshot.people.push({...d,id:2,used:0});body={id:2};status=201}
else if(u.pathname==='/api/config')body={version:'test'};
else if(u.pathname==='/api/auth/2fa')body={enabled:false};
else if(u.pathname.startsWith('/api/max-users'))throw new Error('Unexpected registry request '+req.method()+' '+u.pathname);
else if(['/api/servers','/api/cascades','/api/stats/nodes'].includes(u.pathname))body=[];
else if(u.pathname==='/api/stats/overview')body={per_server:[]};
await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
});
await page.goto('http://127.0.0.1:18092');
await page.getByRole('button',{name:'Пользователи MAX',exact:true}).click();
await page.getByRole('heading',{name:'Пользователи MAX',exact:true}).waitFor();
await page.getByRole('button',{name:'Тестовый пользователь',exact:true}).click();
await page.getByRole('button',{name:'Отозвать весь VPN-доступ',exact:true}).waitFor();
await page.screenshot({path:'/home/operator/.local/share/vpn-max/v2-build/ui-desktop.png',fullPage:true});
await page.getByRole('button',{name:'Добавить пользователя',exact:true}).click();
await page.getByLabel('Имя',{exact:true}).fill('Новый пользователь');
await page.getByLabel('Числовой MAX ID',{exact:true}).fill('987654');
await page.getByLabel('Лимит ключей на всех серверах',{exact:true}).fill('3');
await page.getByRole('button',{name:'Сохранить',exact:true}).click();
await page.locator('input[type=password]').fill('test-only-password');
await page.getByRole('button',{name:'Продолжить',exact:true}).click();
await page.getByRole('button',{name:'Новый пользователь',exact:true}).waitFor();
assert.equal(writes.length,1);assert.equal(errors.length,0);
await page.setViewportSize({width:390,height:844});
await page.screenshot({path:'/home/operator/.local/share/vpn-max/v2-build/ui-mobile.png',fullPage:true});
console.log('UI passed: navigation, profile card, user creation with step-up, desktop/mobile rendering, no browser errors');
} finally{await browser.close();server.close()}
})().catch(e=>{console.error(e.message);process.exit(1)});
