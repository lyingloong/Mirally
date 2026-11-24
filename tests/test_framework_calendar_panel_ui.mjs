/** Real calendar-panel load states; RPC is isolated from any Host. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const ts=createRequire(new URL('../frontend/control-ui/package.json',import.meta.url))('typescript');
const html=(strings,...values)=>({strings,values});
const stringify=value=>value==null||typeof value==='symbol'||typeof value==='function'?'':Array.isArray(value)?value.map(stringify).join(''):value?.strings?value.strings.map((part,index)=>part+stringify(value.values[index])).join(''):String(value);
const source=ts.transpileModule(readFileSync(new URL('../frontend/control-ui/ui/src/pages/mirally/calendar-panel.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,experimentalDecorators:true,useDefineForClassFields:false}}).outputText.replace(/^import[\s\S]*?;\s*/gm,'').replace(/^export /gm,'');
const event=(title='已有日程')=>({event_id:title,title,start_at_utc_ms:1762300800000,end_at_utc_ms:null,time_zone:'Asia/Shanghai',version:1,status:'active'});
const snapshot=(title='已有日程',zone='Asia/Shanghai')=>({settings:{time_zone:zone,confirmed:true,detected_time_zone:zone},events:[event(title)]});
function response(method,params,data) {
  if(method==='calendar.settings.get')return data.settings;
  if(method==='calendar.event.list')return {events:data.events};
  if(method==='calendar.time.resolve')return {utc_ms:0,iso:`${params.local_time}:00`,time_zone:data.settings?.time_zone??null};
  if(method==='calendar.event.update'||method==='calendar.event.cancel')return {event:event()};
  throw new Error(`Unexpected RPC: ${method}`);
}
function fixture() {
  let handler=(method,params)=>Promise.resolve(response(method,params,snapshot()));
  const calls=[];
  const sandbox={html,nothing:Symbol(),state:()=>()=>{},consume:()=>()=>{},applicationContext:{},
    MirallyLightDomElement:class{isConnected=true;connectedCallback(){}disconnectedCallback(){}},
    renderSettingsSection:(options,content)=>html`${options.title}${options.actions}${content}`,
    sessionNavigationTarget:()=>({options:{}}),errorMessage:error=>error.message,
    rpcProduct:(method,params)=>{calls.push({method,params});return handler(method,params);},
    window:{addEventListener(){},removeEventListener(){}},
    customElements:{define(_name,type){sandbox.Page=type;}}};
  vm.runInNewContext(source,sandbox);
  return {page:new sandbox.Page(),calls,
    handle(fn){handler=fn;},
    respond(data){handler=(method,params)=>Promise.resolve(response(method,params,data));},
    pending(){
      const calls=[];
      handler=(method,params)=>new Promise((resolve,reject)=>calls.push({method,params,resolve,reject}));
      return {resolve(data=snapshot()){for(const call of calls)call.resolve(response(call.method,call.params,data));},
        reject(message='连接中断'){for(const call of calls)call.reject(new Error(message));}};
    }};
}

test('first load distinguishes waiting from failure and retries into the real empty state',async()=>{
  const f=fixture(),waiting=f.pending();
  const pendingText=stringify(f.page.render());
  assert.match(pendingText,/正在读取/);assert.match(pendingText,/aria-busy=true/);
  assert.doesNotMatch(pendingText,/还没有日程|请先在通用设置中确认时区/);
  const loading=f.page.refresh();
  waiting.reject('连接中断');await loading;
  assert.equal(f.page.hasLoaded,false);assert.equal(f.page.loading,false);
  assert.equal(f.page.failure,'日程读取失败，请刷新重试。');
  assert.equal(f.page.failureDetail,'连接中断');
  const failed=stringify(f.page.render());
  assert.match(failed,/role="alert"/);assert.match(failed,/日程读取失败，请刷新重试。/);
  assert.match(failed,/<details[^>]*><summary>错误详情<\/summary>/);assert.match(failed,/连接中断/);
  assert.match(failed,/aria-busy=false/);
  assert.match(failed,/disabled=false @click=>刷新<\/button>/);
  assert.doesNotMatch(failed,/还没有日程|请先在通用设置中确认时区|正在读取/);
  const retry=f.pending(),refresh=f.page.refresh();
  assert.equal(f.page.failure,'');assert.equal(f.page.failureDetail,'');
  assert.doesNotMatch(stringify(f.page.render()),/错误详情|连接中断/);
  retry.resolve({settings:snapshot().settings,events:[]});await refresh;
  assert.equal(f.page.hasLoaded,true);assert.equal(f.page.failure,'');
  const empty=stringify(f.page.render());
  assert.match(empty,/还没有日程/);
  assert.doesNotMatch(empty,/role="alert"|错误详情|正在读取/);
});

test('refresh keeps readable events while pending and replaces them on success',async()=>{
  const f=fixture();await f.page.refresh();
  const waiting=f.pending(),refresh=f.page.refresh();
  const during=stringify(f.page.render());
  assert.match(during,/已有日程/);assert.match(during,/刷新中/);assert.match(during,/aria-busy=true/);
  assert.doesNotMatch(during,/正在读取|还没有日程/);
  waiting.resolve(snapshot('新的日程'));await refresh;
  assert.match(stringify(f.page.render()),/新的日程/);
  assert.doesNotMatch(stringify(f.page.render()),/已有日程|正在读取|错误详情/);
  assert.equal(f.page.loading,false);assert.equal(f.page.failure,'');
});

test('a failed refresh keeps the last events and explains the failure',async()=>{
  const f=fixture();await f.page.refresh();
  const waiting=f.pending(),refresh=f.page.refresh();
  waiting.reject('worker 重启');await refresh;
  assert.equal(f.page.hasLoaded,true);assert.equal(f.page.loading,false);
  assert.equal(f.page.failure,'刷新失败，保留上次日程，请刷新重试。');
  assert.equal(f.page.failureDetail,'worker 重启');
  assert.equal(f.page.events.length,1);
  const after=stringify(f.page.render());
  assert.match(after,/已有日程/);assert.match(after,/role="alert"/);
  assert.match(after,/<details[^>]*><summary>错误详情<\/summary>/);
  assert.doesNotMatch(after,/还没有日程|正在读取/);
});

test('the timezone hint follows only a successfully read unconfirmed settings result',async()=>{
  const f=fixture();
  f.respond({settings:{time_zone:null,confirmed:false,detected_time_zone:'Asia/Shanghai'},events:[event()]});
  await f.page.refresh();
  assert.match(stringify(f.page.render()),/请先在通用设置中确认时区/);
  const waiting=f.pending(),refresh=f.page.refresh();
  waiting.reject('连接中断');await refresh;
  assert.match(stringify(f.page.render()),/请先在通用设置中确认时区/);
  const g=fixture();
  g.handle(async(method,params)=>{if(method==='calendar.settings.get')throw new Error('时区设置读取失败');return response(method,params,snapshot());});
  await g.page.refresh();
  assert.equal(g.page.settings,null);assert.equal(g.page.hasLoaded,false);
  assert.match(g.page.failure,/请刷新重试/);
  assert.doesNotMatch(stringify(g.page.render()),/请先在通用设置中确认时区|还没有日程/);
});

test('a malformed events payload fails without throwing or faking an empty list',async()=>{
  const f=fixture();await f.page.refresh();const previous=JSON.stringify(f.page.events);
  f.respond({...snapshot('错误的新日程'),events:null});
  await f.page.refresh();
  assert.equal(JSON.stringify(f.page.events),previous);
  assert.equal(f.page.failure,'刷新失败，保留上次日程，请刷新重试。');
  assert.match(f.page.failureDetail,/有效日程/);
  const out=stringify(f.page.render());
  assert.match(out,/role="alert"/);assert.match(out,/已有日程/);
  assert.doesNotMatch(out,/还没有日程|正在读取|错误的新日程/);
  assert.equal(f.page.loading,false);
  const g=fixture();g.respond({settings:snapshot().settings,events:'invalid'});
  await g.page.refresh();
  assert.equal(g.page.hasLoaded,false);
  assert.equal(g.page.failure,'日程读取失败，请刷新重试。');
  assert.doesNotMatch(stringify(g.page.render()),/还没有日程/);
});

test('overlapping refreshes honor the latest epoch for loading and diagnostics',async()=>{
  const f=fixture();await f.page.refresh();
  const old=f.pending(),oldRefresh=f.page.refresh();
  const latest=f.pending(),latestRefresh=f.page.refresh();
  old.reject('过期请求失败');await oldRefresh;
  assert.equal(f.page.loading,true);assert.equal(f.page.failure,'');assert.equal(f.page.failureDetail,'');
  latest.reject('最新请求失败');await latestRefresh;
  assert.equal(f.page.loading,false);
  assert.equal(f.page.failure,'刷新失败，保留上次日程，请刷新重试。');
  assert.equal(f.page.failureDetail,'最新请求失败');
});

test('an incomplete calendar settings payload is a failed read, never an unconfirmed timezone',async()=>{
  const first=fixture();first.respond({settings:{},events:[]});
  await first.page.refresh();
  assert.equal(first.page.hasLoaded,false);assert.equal(first.page.settings,null);
  assert.equal(first.page.failure,'日程读取失败，请刷新重试。');
  assert.match(first.page.failureDetail,/日历状态不完整/);
  const failed=stringify(first.page.render());
  assert.match(failed,/role="alert"/);
  assert.doesNotMatch(failed,/请先在通用设置中确认时区|还没有日程/);
  const f=fixture();await f.page.refresh();
  f.respond({...snapshot(),settings:{}});
  await f.page.refresh();
  assert.deepEqual(f.page.settings,{time_zone:'Asia/Shanghai',confirmed:true,detected_time_zone:'Asia/Shanghai'});
  assert.equal(f.page.events.length,1);assert.equal(f.page.hasLoaded,true);
  assert.equal(f.page.failure,'刷新失败，保留上次日程，请刷新重试。');
  const kept=stringify(f.page.render());
  assert.match(kept,/已有日程/);
  assert.doesNotMatch(kept,/还没有日程|正在读取/);
});

test('a timezone change closes the open editor and keeps the list readable',async()=>{
  const f=fixture();await f.page.refresh();
  f.page.edit(f.page.events[0]);
  assert.equal(f.page.editingZone,'Asia/Shanghai');
  assert.match(stringify(f.page.render()),/开始（Asia\/Shanghai）/);
  f.respond(snapshot('已有日程','America/New_York'));
  await f.page.refresh();
  assert.equal(f.page.editing,null);assert.equal(f.page.hasLoaded,true);assert.equal(f.page.events.length,1);
  assert.equal(f.page.failure,'时区已改变，请重新打开编辑确认时间。');assert.equal(f.page.failureDetail,'');
  const out=stringify(f.page.render());
  assert.match(out,/已有日程/);assert.match(out,/role="alert"/);
  assert.doesNotMatch(out,/mirally-product-form|开始（|正在读取|还没有日程/);
});

test('a draft bound to the previous timezone is refused before any write',async()=>{
  const f=fixture();await f.page.refresh();
  f.page.edit(f.page.events[0]);
  f.respond({...snapshot(),settings:{time_zone:'America/New_York',confirmed:true,detected_time_zone:'America/New_York'}});
  await f.page.save({preventDefault(){}});
  assert.ok(f.calls.some(call=>call.method==='calendar.time.resolve'));
  assert.ok(!f.calls.some(call=>call.method==='calendar.event.update'),'a stale draft must never reach the write RPC');
  assert.equal(f.page.editing,null);
  assert.equal(f.page.failure,'时区已改变，请重新打开编辑确认时间。');assert.equal(f.page.failureDetail,'');
  assert.match(stringify(f.page.render()),/已有日程/);
});

test('an unchanged timezone still saves the draft with the timezone it was opened in',async()=>{
  const f=fixture();await f.page.refresh();
  f.page.edit(f.page.events[0]);
  f.page.title='改期的日程';f.page.start='2026-12-01T09:00';f.page.reminder='30';
  await f.page.save({preventDefault(){}});
  const update=f.calls.find(call=>call.method==='calendar.event.update');
  assert.ok(update);
  assert.equal(update.params.expected_time_zone,'Asia/Shanghai');
  assert.equal(update.params.patch.start_at,'2026-12-01T09:00:00');
  assert.equal(update.params.patch.end_at,null);
  assert.equal(JSON.stringify(update.params.patch.reminder_offsets_minutes),'[30]');
  assert.equal(f.page.editing,null);assert.equal(f.page.failure,'');assert.equal(f.page.busy,false);
});
