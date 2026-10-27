/** Exercise the actual chat permission element with an isolated product host. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
const requireUi=createRequire(new URL('../frontend/control-ui/package.json',import.meta.url));
const ts=requireUi('typescript');
const source=readFileSync(new URL('../frontend/control-ui/ui/src/pages/mirally/action-approvals.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,experimentalDecorators:true,useDefineForClassFields:false}}).outputText.replace(/^import[\s\S]*?;\s*/gm,'').replace(/^export /gm,'');
const html=(strings,...values)=>({strings,values});
const stringify=value=>value==null||typeof value==='symbol'||typeof value==='function'?'':Array.isArray(value)?value.map(stringify).join(''):value?.strings?value.strings.map((s,i)=>s+stringify(value.values[i])).join(''):String(value);
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function fixture(handler) {
  const calls=[], listeners=new Map();
  const sandbox={html,nothing:Symbol('nothing'),customElement:()=>target=>target,property:()=>()=>{},state:()=>()=>{},
    OpenClawLightDomElement:class{isConnected=true;connectedCallback(){} disconnectedCallback(){}},
    window:{addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)},
    setTimeout:()=>1,clearTimeout:()=>{},errorMessage:error=>error.message,
    rpcProduct:async(method,params)=>{calls.push({method,params});return handler(method,params);}};
  vm.runInNewContext(compiled+'\nglobalThis.Page=MirallyActionApprovals;',sandbox);
  return {page:new sandbox.Page(),calls,listeners};
}
test('current session permission loads, updates and retains pending approval',async()=>{
  let enabled=false;
  const f=fixture((method,params)=>{if(method==='action.permissions.set'){enabled=params.enabled;return {};}
    if(method==='action.permissions.get')return {session_auto_approve:enabled};
    return {approvals:[{id:'a',conversation_id:'one',title:'运行命令',detail:'echo hi'}]};});
  f.page.sessionKey='agent:main:one'; await f.page.refresh();
  assert.equal(f.page.sessionAutoApprove,false); assert.match(stringify(f.page.render()),/重启后失效/);
  assert.match(stringify(f.page.render()),/微信仍受独立授权设置约束/); assert.match(stringify(f.page.render()),/echo hi/);
  await f.page.setPermission(true); assert.equal(f.page.sessionAutoApprove,true);
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.find(c=>c.method==='action.permissions.set').params)),{conversation_id:'one',enabled:true});
});
test('stale session replies do not grant or display a different session',async()=>{
  const pending=deferred();
  const f=fixture((method,params)=>method==='action.permissions.set'?pending.promise:method==='action.permissions.get'?{session_auto_approve:params.conversation_id==='one'}:{approvals:[]});
  f.page.sessionKey='agent:main:one'; await f.page.refresh();
  const saving=f.page.setPermission(false);
  f.page.sessionKey='agent:main:two'; f.page.updated(new Map([['sessionKey','agent:main:one']]));
  await Promise.resolve(); await Promise.resolve(); pending.resolve({}); await saving; await Promise.resolve();
  assert.equal(f.page.sessionAutoApprove,false); assert.equal(f.page.permissionBusy,false);
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.find(c=>c.method==='action.permissions.set').params)),{conversation_id:'one',enabled:false});
});
test('approval refresh during a toggle cannot leave the switch busy',async()=>{
  const pending=deferred(); let enabled=false;
  const f=fixture((method,params)=>{if(method==='action.permissions.set')return pending.promise.then(()=>{enabled=params.enabled;});
    if(method==='action.permissions.get')return {session_auto_approve:enabled}; return {approvals:[]};});
  f.page.sessionKey='agent:main:one'; f.page.connectedCallback(); await f.page.refresh();
  const saving=f.page.setPermission(true);
  f.listeners.get('mirally-product-event')({detail:{action:'approval-changed',conversation_id:'one'}});
  pending.resolve(); await saving; await Promise.resolve();
  assert.equal(f.page.permissionBusy,false); assert.equal(f.page.sessionAutoApprove,true);
});
test('global trust remains visible even when session auto approval is off',async()=>{
  const f=fixture(method=>method==='action.permissions.get'?{session_auto_approve:false,default_trusted:true}:{approvals:[]});
  f.page.sessionKey='agent:main:one'; await f.page.refresh();
  const rendered=stringify(f.page.render());
  assert.equal(f.page.sessionAutoApprove,false);
  assert.match(rendered,/全局持续信任已开启，电脑端动作自动批准/);
  assert.match(rendered,/关闭本开关不会关闭全局信任/);
});
