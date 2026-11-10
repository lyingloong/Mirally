/** Real message-controller state transitions; RPC is isolated from any Host. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const ts=createRequire(new URL('../frontend/control-ui/package.json',import.meta.url))('typescript');
const html=(strings,...values)=>({strings,values});
const stringify=value=>value==null||typeof value==='symbol'||typeof value==='function'?'':Array.isArray(value)?value.map(stringify).join(''):value?.strings?value.strings.map((part,index)=>part+stringify(value.values[index])).join(''):String(value);
const source=ts.transpileModule(readFileSync(new URL('../frontend/control-ui/ui/src/pages/mirally/messages-page.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,experimentalDecorators:true,useDefineForClassFields:false}}).outputText.replace(/^import[\s\S]*?;\s*/gm,'').replace(/^export /gm,'');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const snapshot=(title='已有消息')=>({
  items:[{id:title,delivery_id:title,title,state:'unread',updated_at:0}],
  commitments:[{id:title,title,status:'pending',due_at:0}],
  calendar:{time_zone:'Asia/Shanghai',confirmed:true},
  deliveries:[{commitment_id:title,channel:'desktop',status:'sent'}],recurrences:[],
});
function response(method,params,data) {
  if(method==='inbox.list')return {items:data.items};
  if(method==='commitment.list')return {commitments:data.commitments};
  if(method==='calendar.settings.get')return data.calendar;
  if(method==='calendar.delivery.list')return {deliveries:params.channel==='desktop'?data.deliveries:[]};
  if(method==='calendar.recurrence.list')return {recurrences:data.recurrences};
  throw new Error(`Unexpected RPC: ${method}`);
}
function fixture() {
  let handler=(method,params)=>Promise.resolve(response(method,params,snapshot()));
  const sandbox={html,nothing:Symbol(),property:()=>()=>{},state:()=>()=>{},consume:()=>()=>{},applicationContext:{},
    OpenClawLightDomElement:class{isConnected=true;connectedCallback(){}disconnectedCallback(){}},
    renderSettingsPage:content=>content,renderSettingsWorkspace:content=>content,
    renderSettingsPageHeader:options=>html`${options.title}${options.actions}`,
    renderSettingsSection:(options,content)=>html`${options.actions}${content}`,
    repeat:(items,_key,render)=>items.map(render),errorMessage:error=>error.message,
    rpcProduct:(method,params)=>handler(method,params),
    sessionStorage:{getItem:()=>null,setItem(){}},window:{addEventListener(){},removeEventListener(){}},
    customElements:{define(_name,type){sandbox.Page=type;}}};
  vm.runInNewContext(source,sandbox);
  return {page:new sandbox.Page(),
    respond(data){handler=(method,params)=>Promise.resolve(response(method,params,data));},
    pending(){
      const calls=[];
      handler=(method,params)=>new Promise((resolve,reject)=>calls.push({method,params,resolve,reject}));
      return {resolve(data=snapshot()){for(const call of calls)call.resolve(response(call.method,call.params,data));},
        reject(message='连接中断'){for(const call of calls)call.reject(new Error(message));}};
    }};
}
const state=page=>JSON.stringify({items:page.items,deliveries:page.deliveries,recurrences:page.recurrences,timeZone:page.timeZone,timeZoneConfirmed:page.timeZoneConfirmed});

test('initial load distinguishes waiting, successful empty state and failure',async()=>{
  const f=fixture(),pending=f.pending();
  assert.match(stringify(f.page.render()),/正在读取/);
  assert.doesNotMatch(stringify(f.page.render()),/这里暂时没有消息/);
  const loading=f.page.refresh();
  pending.reject('离线');await loading;
  assert.equal(f.page.hasLoaded,false);assert.equal(f.page.loading,false);
  assert.equal(f.page.failure,'读取失败，请刷新重试。');
  assert.equal(f.page.failureDetail,'离线');
  assert.match(stringify(f.page.render()),/<details[^>]*><summary>错误详情<\/summary>/);
  f.respond({...snapshot(),items:[]});await f.page.refresh();
  assert.equal(f.page.failure,'');assert.equal(f.page.failureDetail,'');assert.equal(f.page.hasLoaded,true);
  assert.doesNotMatch(stringify(f.page.render()),/错误详情/);
  assert.match(stringify(f.page.render()),/这里暂时没有消息/);
});

test('refresh keeps readable rows while pending and replaces them on success',async()=>{
  const f=fixture();await f.page.refresh();
  const pending=f.pending(),refresh=f.page.refresh();
  const during=stringify(f.page.render());
  assert.match(during,/已有消息/);assert.match(during,/刷新中/);assert.match(during,/aria-busy=true/);
  assert.doesNotMatch(during,/正在读取/);
  pending.resolve(snapshot('新消息'));await refresh;
  assert.match(stringify(f.page.render()),/新消息/);
  assert.doesNotMatch(stringify(f.page.render()),/已有消息/);
  assert.equal(f.page.loading,false);assert.equal(f.page.failure,'');
});

test('failed and malformed inbox refresh preserve the last valid snapshot with an error',async()=>{
  const f=fixture();await f.page.refresh();const previous=state(f.page);
  const pending=f.pending(),refresh=f.page.refresh();pending.reject('gateway disconnected: Failed to fetch');await refresh;
  assert.equal(state(f.page),previous);assert.match(f.page.failure,/保留上次读取/);
  assert.match(f.page.failure,/请刷新重试/);assert.doesNotMatch(f.page.failure,/gateway|fetch/);
  assert.equal(f.page.failureDetail,'gateway disconnected: Failed to fetch');
  f.respond({...snapshot('错误的新消息'),items:null});await f.page.refresh();
  assert.equal(state(f.page),previous);assert.match(f.page.failureDetail,/有效列表/);
  assert.match(stringify(f.page.render()),/role="alert"/);
  assert.equal(f.page.loading,false);
});

test('malformed reminder projections cannot partially update or corrupt the loaded snapshot',async()=>{
  const f=fixture();f.page.page='reminders';f.page.calendarView=false;
  await f.page.refresh();const previous=state(f.page);
  for(const invalid of [{recurrences:undefined},{deliveries:null},{commitments:{}},
    {calendar:{time_zone:7,confirmed:true}},{calendar:{time_zone:'UTC',confirmed:'yes'}}]) {
    f.respond({...snapshot('错误的新提醒'),calendar:{time_zone:'UTC',confirmed:true},...invalid});
    await f.page.refresh();
    assert.equal(state(f.page),previous);
    assert.match(f.page.failure,/保留上次读取/);
    assert.doesNotThrow(()=>f.page.render());
    assert.equal(f.page.loading,false);
  }
});

test('switching pages clears the old projection and rejects late responses from that page',async()=>{
  const f=fixture();await f.page.refresh();
  const old=f.pending(),oldRefresh=f.page.refresh();
  const next=f.pending();f.page.page='reminders';f.page.calendarView=false;
  f.page.updated(new Map([['page','inbox']]));
  assert.equal(f.page.hasLoaded,false);assert.equal(f.page.items.length,0);
  assert.match(stringify(f.page.render()),/正在读取/);
  next.resolve(snapshot('当前提醒'));await tick();
  assert.match(stringify(f.page.render()),/当前提醒/);
  old.resolve(snapshot('过期消息'));await oldRefresh;
  assert.match(stringify(f.page.render()),/当前提醒/);
  assert.doesNotMatch(stringify(f.page.render()),/过期消息/);
  assert.equal(f.page.failure,'');
  assert.equal(f.page.failureDetail,'');
});

test('overlapping refreshes honor the latest epoch, including loading and failure state',async()=>{
  const f=fixture();await f.page.refresh();const previous=state(f.page);
  const old=f.pending(),oldRefresh=f.page.refresh();
  const latest=f.pending(),latestRefresh=f.page.refresh();
  old.reject('过期请求失败');await oldRefresh;
  assert.equal(state(f.page),previous);assert.equal(f.page.loading,true);
  assert.equal(f.page.failureDetail,'');
  latest.reject('最新请求失败');await latestRefresh;
  assert.equal(state(f.page),previous);assert.equal(f.page.loading,false);
  assert.equal(f.page.failureDetail,'最新请求失败');
  const detached=f.pending(),detachedRefresh=f.page.refresh();
  f.page.disconnectedCallback();f.page.isConnected=false;
  detached.resolve(snapshot('离开后的结果'));await detachedRefresh;
  assert.equal(state(f.page),previous);assert.equal(f.page.failure,'');assert.equal(f.page.failureDetail,'');
});

test('retry, page change and unrelated mutations clear expired refresh diagnostics',async()=>{
  const f=fixture();await f.page.refresh();
  async function fail(){const pending=f.pending(),refresh=f.page.refresh();pending.reject('old refresh transport error');await refresh;}
  await fail();assert.notEqual(f.page.failureDetail,'');
  let pending=f.pending(),refresh=f.page.refresh();
  assert.equal(f.page.failure,'');assert.equal(f.page.failureDetail,'');
  assert.doesNotMatch(stringify(f.page.render()),/错误详情/);
  pending.resolve();await refresh;
  await fail();
  pending=f.pending();f.page.page='reminders';f.page.calendarView=false;
  f.page.updated(new Map([['page','inbox']]));
  assert.equal(f.page.failureDetail,'');assert.equal(f.page.failure,'');
  pending.resolve();await tick();
  await fail();
  await f.page.mutate(async()=>{throw new Error('操作未完成');},'完成');
  assert.equal(f.page.failure,'操作未完成');assert.equal(f.page.failureDetail,'');
  assert.doesNotMatch(stringify(f.page.render()),/错误详情|old refresh transport error/);
  await fail();
  await f.page.create({preventDefault(){}});
  assert.match(f.page.failure,/请填写提醒/);assert.equal(f.page.failureDetail,'');
});
