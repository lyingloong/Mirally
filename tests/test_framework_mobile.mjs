import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,mkdir,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {request as httpRequest} from 'node:http';
import {MobileAccess,mobileExternalUrl} from '../kernel/dsh/packages/mirally/mirally-product/src/mobile.mjs';

async function fixture(t) {
  const home=await mkdtemp(join(tmpdir(),'mirally mobile 中文 ')),ui=join(home,'ui');
  await mkdir(join(ui,'assets'),{recursive:true});await writeFile(join(ui,'mobile.html'),'<main>Mirally phone</main>');
  await writeFile(join(ui,'assets','phone.js'),'console.log("phone")');
  const calls=[];let active=false;
  const product={home,uiDirectory:ui,token:'desktop-secret',calls,handles:new Map(),
    serial:async(_id,fn)=>fn(),conversation:async id=>({conversation_id:id,session_id:'native'}),
    history:async()=>({messages:[{id:'a',seq:1,role:'user',text:'hello',content:[{type:'text',text:'hello'}]},
      {id:'b',seq:2,role:'tool',text:'private tool'},
      {id:'c',seq:3,role:'assistant',text:'answer',content:[{type:'thinking',thinking:'private reasoning'}]}],gaps:[],active_command_ids:[]}),
    rpc:async(method,params={})=>{calls.push({method,params});
      if(method==='conversation.list')return {conversations:[{conversation_id:'conversation',title:'title',metadata_version:1}]};
      if(method==='conversation.ensure')return {conversation_id:params.conversation_id,title:params.title};
      if(method==='conversation.patch')return {conversation_id:params.conversation_id,...params.patch,metadata_version:2};
      if(method==='workspace.roots')return {roots:[{id:'workspace',path:'private absolute path',label:'工作区',writable:true}]};
      return {items:[]};},
    workRequest:async(method,params,principal)=>{calls.push({method,params,principal});return {work:{id:'work',title:'work'}};},
    sendMobile:async(params,principal)=>({command_id:'mobile-'+params.command_id,device:principal.id}),
    cancel:async(id,command)=>{active=false;return {conversation_id:id,command_id:command};},
    cancelMobileCommands:async id=>{calls.push({cancelDevice:id});},
  };
  let now=Date.now();const access=new MobileAccess(product,{now:()=>now});await access.start();
  t.after(async()=>{await access.stop();await rm(home,{recursive:true,force:true});});
  const base=()=>new URL(access.url).origin;
  const request=async(path,body,token,headers={})=>{
    const response=await fetch(base()+path,{method:body===undefined?'GET':'POST',headers:{Connection:'close',...(body===undefined?{}:{'Content-Type':'application/json'}),...(token?{Authorization:'Bearer '+token}:{}),...headers},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const value=await response.json().catch(()=>null);return {status:response.status,value,headers:response.headers};
  };
  const pair=async()=>{const issued=await access.management('mobile.pair.create',{device_name:'测试手机'});const result=await request('/api/mobile/pair',{code:issued.code,device_name:'测试手机'});assert.equal(result.status,200);return {...result.value.result,issued};};
  const rpc=async(token,method,params={})=>request('/api/mobile/rpc',{method,params},token);
  return {access,home,product,calls,pair,rpc,request,base,advance:ms=>{now+=ms;}};
}

test('mobile only binds loopback, exposes its own shell, and refuses all desktop control-plane routes',async t=>{
  const f=await fixture(t);assert.equal(f.access.server.address().address,'127.0.0.1');
  const shell=await fetch(f.base()+'/mobile');assert.equal(shell.status,200);assert.match(await shell.text(),/Mirally phone/);
  assert.match(shell.headers.get('content-security-policy'),/frame-ancestors 'none'/);
  for(const path of ['/api/rpc','/api/config','/api/ui-preferences','/internal/completion','/index.html','/assets/../../mobile-access.json']) {
    const r=await fetch(f.base()+path,{headers:{Authorization:'Bearer desktop-secret'}});assert.notEqual(r.status,200,path);
  }
  assert.equal((await f.rpc('desktop-secret','conversation.list')).status,401);
});
test('pairing is local-issued, single-use, bounded and secret-free on disk/status',async t=>{
  const f=await fixture(t),p=await f.pair();
  assert.ok(p.issued.url.includes('#pair='));assert.ok(p.token.length>=40);
  assert.equal((await f.request('/api/mobile/pair',{code:p.issued.code})).status,403);
  assert.equal((await f.rpc(p.token,'conversation.list')).status,200);
  const disk=await readFile(join(f.home,'mobile-access.json'),'utf8');
  for(const secret of [p.token,p.issued.code,'desktop-secret'])assert.ok(!disk.includes(secret));
  const status=JSON.stringify(await f.access.management('mobile.status'));
  assert.ok(!status.includes('token_hash'));assert.ok(!status.includes(p.token));
  assert.equal((await f.rpc(p.token,'settings.get')).status,403);
  assert.equal((await f.rpc(p.token,'mobile.pair.create')).status,403);
});
test('pair code expires and brute force is bounded',async t=>{
  const f=await fixture(t),issued=await f.access.management('mobile.pair.create');f.advance(5*60_000+1);
  assert.equal((await f.request('/api/mobile/pair',{code:issued.code})).status,403);
  for(let i=0;i<19;i++)await f.request('/api/mobile/pair',{code:'bad'});
  assert.equal((await f.request('/api/mobile/pair',{code:'bad'})).status,429);
});
test('unconfigured origins and hostile Host headers are rejected before pairing',async t=>{
  const f=await fixture(t),issued=await f.access.management('mobile.pair.create');
  const raw=async headers=>new Promise((resolve,reject)=>{
    const data=JSON.stringify({code:issued.code});
    const request=httpRequest(f.base()+'/api/mobile/pair',{method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(data),...headers}},response=>{response.resume();response.once('end',()=>resolve(response.statusCode));});
    request.once('error',reject);request.end(data);
  });
  assert.equal((await f.request('/api/mobile/pair',{code:issued.code},null,{Origin:'https://evil.invalid'})).status,403);
  assert.equal(await raw({Host:'evil.invalid'}),403);
  await f.access.management('mobile.configure',{external_url:'https://phone.example/mobile'});
  assert.equal(await raw({Origin:'https://phone.example',Host:'phone.example'}),200);
});
test('external access URL requires explicit HTTPS configuration without credentials or arbitrary paths',()=>{
  assert.equal(mobileExternalUrl('https://phone.example/'),'https://phone.example/mobile');
  for(const value of ['http://phone.example','https://secret@phone.example','https://phone.example/api','https://phone.example/?token=secret','file:///x'])assert.throws(()=>mobileExternalUrl(value));
});
test('phone and default preferences use independent layers, revisions, reset and finite keys',async t=>{
  const f=await fixture(t),one=await f.pair(),two=await f.pair();
  let prefs=(await f.rpc(one.token,'mobile.preferences.get')).value.result;
  assert.equal(prefs.effective.home,'work');
  prefs=(await f.rpc(one.token,'mobile.preferences.set',{scope:'user',expected_revision:prefs.revision,patch:{home:'conversations'}})).value.result;
  assert.equal((await f.rpc(two.token,'mobile.preferences.get')).value.result.effective.home,'conversations');
  prefs=(await f.rpc(one.token,'mobile.preferences.set',{scope:'device',expected_revision:prefs.revision,patch:{home:'last'}})).value.result;
  assert.equal((await f.rpc(two.token,'mobile.preferences.get')).value.result.effective.home,'conversations');
  assert.equal((await f.rpc(one.token,'mobile.preferences.set',{scope:'device',expected_revision:prefs.revision-1,patch:{home:'work'}})).status,409);
  assert.equal((await f.rpc(one.token,'mobile.preferences.set',{scope:'device',expected_revision:prefs.revision,patch:{notifications:{failed:false}}})).status,400);
  assert.equal((await f.rpc(one.token,'mobile.preferences.set',{scope:'device',expected_revision:prefs.revision,patch:{hidden:['settings']}})).status,400);
  const reset=await f.rpc(one.token,'mobile.preferences.reset',{scope:'device',expected_revision:prefs.revision});assert.equal(reset.value.result.effective.home,'conversations');
  assert.equal(f.calls.filter(c=>c.method==='preference.set').length,0);
});
test('revocation and expiry invalidate tokens and principals, including in-flight response delivery',async t=>{
  const f=await fixture(t),p=await f.pair();
  let principal;const authenticate=f.access.authenticate.bind(f.access);
  f.access.authenticate=request=>principal=authenticate(request);
  f.product.history=async()=>{await f.access.management('mobile.devices.revoke',{device_id:p.device.id});return {messages:[{role:'user',seq:1,text:'must not deliver'}],gaps:[],active_command_ids:[]};};
  const result=await f.rpc(p.token,'conversation.history',{conversation_id:'c'});assert.equal(result.status,401);assert.ok(!JSON.stringify(result.value).includes('must not deliver'));assert.equal(f.access.authorize(principal),false);
  assert.equal((await f.rpc(p.token,'conversation.list')).status,401);
  const next=await f.pair();f.advance(30*86400_000+1);assert.equal((await f.rpc(next.token,'conversation.list')).status,401);
  assert.equal(f.access.authorize({id:next.device.id,revision:1}),false);
});
test('history is paged from the one transcript, excluding tool arguments and thinking',async t=>{
  const f=await fixture(t),p=await f.pair();
  let r=await f.rpc(p.token,'conversation.history',{conversation_id:'c',limit:1});
  assert.deepEqual(r.value.result.messages.map(m=>m.text),['answer']);assert.equal(r.value.result.has_more,true);
  assert.ok(!JSON.stringify(r.value).includes('private'));
  r=await f.rpc(p.token,'conversation.history',{conversation_id:'c',before_seq:3,limit:50});assert.deepEqual(r.value.result.messages.map(m=>m.text),['hello']);
  const roots=await f.rpc(p.token,'workspace.roots');assert.ok(!JSON.stringify(roots.value).includes('private absolute'));
});
test('mobile metadata uses the original owner and sends retain device identity',async t=>{
  const f=await fixture(t),p=await f.pair();
  const a=await f.rpc(p.token,'conversation.create',{title:'phone',request_id:'same'}),b=await f.rpc(p.token,'conversation.create',{title:'phone',request_id:'same'});
  assert.equal(a.value.result.conversation.conversation_id,b.value.result.conversation.conversation_id);
  const patch=await f.rpc(p.token,'conversation.update',{conversation_id:'c',expected_version:1,patch:{title:'renamed',pinned:true}});assert.equal(patch.value.result.conversation.title,'renamed');
  assert.equal((await f.rpc(p.token,'conversation.update',{conversation_id:'c',expected_version:1,patch:{settings:{}}})).status,400);
  let owner;f.product.sendMobile=async(params,principal)=>{owner=principal;return {command_id:'mobile-'+params.command_id,device:principal.id};};
  const sent=await f.rpc(p.token,'conversation.send',{conversation_id:'c',text:'hi',command_id:'request'});assert.equal(sent.value.result.device,p.device.id);
  assert.equal(owner.id,p.device.id);assert.equal(f.access.authorize(owner),true);
  assert.equal((await f.rpc(p.token,'work.run.approve',{approval_id:'a',expected_digest:'digest'})).status,403);
});
test('disabling mobile closes only its listener and persisted tokens survive a clean restart',async t=>{
  const f=await fixture(t),p=await f.pair();await f.access.stop();
  const next=new MobileAccess(f.product);t.after(()=>next.stop());await next.start();
  const r=await fetch(new URL(next.url).origin+'/api/mobile/rpc',{method:'POST',headers:{Authorization:'Bearer '+p.token,'Content-Type':'application/json'},body:JSON.stringify({method:'conversation.list'})});assert.equal(r.status,200);
  await next.management('mobile.configure',{enabled:false});assert.equal(next.server,null);assert.equal(next.state.enabled,false);
});
test('a corrupt mobile file degrades mobile only and is preserved verbatim',async t=>{
  const f=await fixture(t);await f.access.stop();await writeFile(join(f.home,'mobile-access.json'),'{bad');
  const next=new MobileAccess(f.product);t.after(()=>next.stop());await next.start();assert.equal(next.status().ready,false);assert.match(next.status().error,/保留原文件/);
  assert.equal(await readFile(join(f.home,'mobile-access.json'),'utf8'),'{bad');
});
test('disable invalidates in-flight principals permanently while keeping a reauthenticated phone usable',async t=>{
  const f=await fixture(t),p=await f.pair();let principal;
  const authenticate=f.access.authenticate.bind(f.access);f.access.authenticate=request=>principal=authenticate(request);
  await f.rpc(p.token,'conversation.list');assert.equal(f.access.authorize(principal),true);
  let finishHistory;f.product.history=()=>new Promise(resolve=>{finishHistory=resolve;});
  const inFlight=f.access.dispatch('conversation.history',{conversation_id:'c'},principal);
  const port=f.access.state.port;
  await f.access.management('mobile.configure',{enabled:false});assert.equal(f.access.authorize(principal),false);
  assert.ok(f.calls.some(call=>Object.hasOwn(call,'cancelDevice')));
  await f.access.management('mobile.configure',{enabled:true});assert.equal(f.access.state.port,port);
  assert.equal(f.access.authorize(principal),false);
  finishHistory({messages:[{role:'assistant',seq:1,text:'stale'}],gaps:[],active_command_ids:[]});
  await assert.rejects(inFlight,{status:401});
  assert.equal((await f.rpc(p.token,'conversation.list')).status,200);
});
