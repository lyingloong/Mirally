const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parseHTML} = require('../tools/testing/dependencies.cjs').loadLinkedom();
const activity = require('../mirally/assets/control-ui-activity.js');

const status = overrides => ({
  activity: {configured:true, enabled:true, database_exists:true, collection_level:'content',
    capture:[{label:'屏幕截图',enabled:true},{label:'音频',enabled:false}],
    collector:{status:'running',snapshot:true,updated_at_utc_ms:1234}, controls:{start:true,pause:false,resume:false,stop:false}},
  memory: {configured:true,enabled:true,health:{store:{active_count:4,statuses:{candidate:2}}},
    semantics:{enabled:false},runtime:{verified:false,status:'running'}},
  ...overrides,
});

function fixture(marker = true) {
  const {window} = parseHTML('<html><body><openclaw-app></openclaw-app><aside class="settings-sidebar"><nav class="settings-sidebar__nav"><a class="settings-sidebar__item" href="/settings/appearance">外观</a></nav></aside><main id="control-ui-main"><openclaw-router-outlet><div id="native-content">原生设置</div></openclaw-router-outlet></main></body></html>');
  const frames = new Map(), timers = new Map(), messages = [], events = new Map();
  let sequence = 0, routeListener;
  const root = {document:window.document, MutationObserver:window.MutationObserver,
    location:{pathname:'/settings/appearance',search:marker?'?mirally=activity':''},
    crypto:{randomUUID:()=>`request-${++sequence}`},
    chrome:{webview:{postMessage:message=>messages.push(message)}},
    requestAnimationFrame:callback=>{const id=++sequence;frames.set(id,callback);return id;},
    cancelAnimationFrame:id=>frames.delete(id),
    setTimeout:callback=>{const id=++sequence;timers.set(id,callback);return id;},
    clearTimeout:id=>timers.delete(id),
    addEventListener:(name,callback)=>events.set(name,callback),
  };
  const navigateCalls = [];
  root.document.querySelector('openclaw-app').context = {basePath:'',
    router:{subscribe:callback=>{routeListener=callback;return ()=>{routeListener=null;};}},
    navigate:(route,options)=>{navigateCalls.push({route,options});root.location={pathname:'/settings/'+(route==='config'?'appearance':route),search:route==='config'?'?section=__appearance__':options.search};routeListener?.();},
  };
  const outlet = root.document.querySelector('openclaw-router-outlet');
  outlet.hidden = outlet.inert = false;
  const api = activity.install(root);
  const document = root.document;
  const flush = async () => {
    for (let n=0;n<8;n++) {
      await Promise.resolve();
      const current=[...frames.values()];frames.clear();
      for (const callback of current) callback();
    }
  };
  const receive = async (action,result,error) => {
    const message=[...messages].reverse().find(item=>item.action===action);
    assert.ok(message,`no request for ${action}`);
    api.receive({requestId:message.requestId,result,error});
    await flush();
    return message;
  };
  const click = selector => document.querySelector(selector).dispatchEvent(new window.Event('click',{bubbles:true}));
  const route = async (pathname,search='') => {root.location={pathname,search};routeListener?.();await flush();};
  return {root,document,outlet,api,messages,timers,events,flush,receive,click,route,navigateCalls,Event:window.Event};
}

test('model settings use their own fixed bridge while the activity page is not visible', async () => {
  const f=fixture(false);await f.flush();
  const read=f.api.modelSettings.read();await f.flush();
  assert.deepEqual(f.messages.map(message=>message.action),['model-settings']);
  const expected={model:'',effective_model:'fixture/main',max_output_tokens:1500,enabled:true,canSave:true};
  await f.receive('model-settings',expected);assert.deepEqual(await read,expected);
  const save=f.api.modelSettings.save({model:'fixture/small',maxOutputTokens:800,enabled:false,action:'stop'});
  await f.flush();
  const message=f.messages.at(-1);
  assert.equal(message.action,'set-semantic-model');assert.equal(message.type,'mirally-activity');
  assert.equal(message.model,'fixture/small');assert.equal(message.maxOutputTokens,800);
  assert.equal(Object.hasOwn(message,'enabled'),false);
  assert.equal(f.timers.size,0);
  await f.receive('set-semantic-model',{saved:true,applied:true});
  assert.equal((await save).saved,true);
  assert.equal(f.document.querySelector('.mirally-activity'),null);
});

test('read-only memory browser uses its independent fixed bridge across Activity navigation and cancels on Gateway replacement',async()=>{
  const f=fixture(false);await f.flush();
  const pending=f.api.memoryBrowser.search({query:'memo',provenances:['model_inference'],kinds:['episodic'],start_utc_ms:100,end_utc_ms:200,include_candidates:true,limit:20,offset:0,action:'stop',enabled:true});
  await f.route('/settings/appearance','?mirally=activity');await f.route('/chat');
  const search=f.messages.find(message=>message.action==='memory-search');
  assert.deepEqual(search.provenances,['model_inference']);assert.deepEqual(search.kinds,['episodic']);assert.equal(search.start_utc_ms,100);
  assert.equal(Object.hasOwn(search,'enabled'),false);
  await f.receive('memory-search',{memories:[]});assert.deepEqual(await pending,{memories:[]});
  const read=f.api.memoryBrowser.read({memory_id:'record',from_line:101,line_count:100,action:'start'});
  const rejected=assert.rejects(read,/已离开/);
  f.document.querySelector('openclaw-app').context={};await f.route('/chat');await rejected;
});

test('Activity settings link navigates to native Memory instead of keeping a second detail browser',async()=>{
  const f=fixture();await f.flush();await f.receive('status',status());
  assert.equal(f.document.querySelector('[data-slot="search"]'),null);
  f.click('[data-action="open-memory"]');
  assert.deepEqual(f.navigateCalls.at(-1),{route:'memory',options:{pathname:'/settings/memory/memories',search:''}});
  assert.equal(f.messages.some(message=>message.action==='memory-search' || message.action==='memory-read'),false);
});

test('model bridge validates writes and cancels reads on disposal or Gateway replacement', async () => {
  const f=fixture(false);await f.flush();
  for(const value of [{model:'bad model'},{model:'x',maxOutputTokens:127},{model:'x',maxOutputTokens:16001},{model:'x',maxOutputTokens:2.5}])
    await assert.rejects(f.api.modelSettings.save(value));
  assert.equal(f.messages.length,0);
  const cancelled=f.api.modelSettings.read();
  const rejected=assert.rejects(cancelled,/已离开/);
  f.api.modelSettings.cancel();await rejected;
  const stale=f.api.modelSettings.read();
  const retired=assert.rejects(stale,/已离开/);
  f.root.document.querySelector('openclaw-app').context={router:{subscribe:()=>()=>{}}};
  await f.route('/settings/model-providers');await retired;
  assert.equal(f.api.receive({requestId:f.messages.at(-1).requestId,result:{canSave:true}}),false);
});

test('memory model bridge transmits only explicitly edited fields and rejects an empty update',async()=>{
  const f=fixture(false);await f.flush();
  for(const edits of [{model:''},{model:'fixture/model'},{maxOutputTokens:2048}]) {
    const pending=f.api.modelSettings.save(edits);await f.flush();
    const message=f.messages.at(-1);
    assert.equal(Object.hasOwn(message,'model'),Object.hasOwn(edits,'model'));
    assert.equal(Object.hasOwn(message,'maxOutputTokens'),Object.hasOwn(edits,'maxOutputTokens'));
    await f.receive('set-semantic-model',{saved:true});await pending;
  }
  for(const edits of [{},{model:undefined},{maxOutputTokens:undefined},null])await assert.rejects(f.api.modelSettings.save(edits));
  assert.equal(f.messages.length,3);
});

test('activity settings marker is distinct from native activity and memory pages', () => {
  assert.equal(activity.isPage({pathname:'/settings/appearance',search:'?mirally=activity'}),true);
  assert.equal(activity.isPage({pathname:'/console/settings/appearance/',search:'?other=1&mirally=activity'}),true);
  for (const pathname of ['/activity','/settings/memory','/chat','/settings/general','/settings/appearance/extra'])
    assert.equal(activity.isPage({pathname,search:'?mirally=activity'}),false);
  assert.equal(activity.isPage({pathname:'/settings/appearance',search:'?mirally=else'}),false);
});

test('recording preference is distinct from a paused live collector and technical details are folded', async () => {
  const f=fixture();await f.flush();
  await f.receive('status',status({activity:{configured:true,enabled:true,live_enabled:true,database_exists:true,
    config_path:'D:/private/activity.toml',scope_note:'范围详细说明',auto_start_error:'采集来源暂不可用',
    collector:{snapshot:false,status:'paused'},controls:{'set-enabled':true,resume:true},
    capture:[{label:'UIA 结构',enabled:true},{label:'输入操作元数据',enabled:true}]}}));
  const toggle=f.document.querySelector('[data-action="set-enabled"]');
  assert.equal(toggle.getAttribute('aria-checked'),'true');assert.equal(toggle.disabled,false);
  assert.equal(f.document.querySelector('[data-status="collector"]').textContent,'已暂停');
  assert.match(f.document.querySelector('[data-slot="preference-note"]').textContent,/每次打开/);
  assert.match(f.document.querySelector('[data-slot="activity-warning"]').textContent,/采集来源暂不可用/);
  assert.match(f.document.querySelector('[data-slot="capture"]').textContent,/窗口与控件/);
  assert.match(f.document.querySelector('[data-slot="capture"]').textContent,/键鼠操作/);
  const details=f.document.querySelector('.mirally-activity__advanced');
  assert.equal(details.hasAttribute('open'),false);
  assert.ok(details.contains(f.document.querySelector('[data-slot="activity-config"]')));
  assert.ok(details.contains(f.document.querySelector('[data-slot="capture-note"]')));
  assert.equal(details.contains(f.document.querySelector('[data-slot="semantic-budget"]')),false);
  assert.match(f.document.querySelector('[data-slot="semantic-description"]').textContent,/不调用模型/);
});

test('recording switch saves the desired preference once and refreshes authoritative state', async () => {
  const f=fixture();await f.flush();
  await f.receive('status',status({activity:{configured:true,enabled:true,live_enabled:true,
    collector:{snapshot:false,status:'running'},controls:{'set-enabled':true,pause:true}}}));
  f.click('[data-action="set-enabled"]');f.click('[data-action="set-enabled"]');await f.flush();
  const writes=f.messages.filter(item=>item.action==='set-enabled');
  assert.equal(writes.length,1);assert.equal(writes[0].enabled,false);
  assert.equal(f.timers.size,0);
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').disabled,true);
  assert.equal(f.document.querySelector('[data-action="pause"]').disabled,true);
  await f.receive('set-enabled',{saved:true,enabled:false,capture:{status:'stopped'},capture_error:null});
  await f.receive('status',status({activity:{configured:true,enabled:false,live_enabled:false,
    collector:{snapshot:false,status:'stopped'},controls:{'set-enabled':true,start:true}}}));
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').getAttribute('aria-checked'),'false');
  assert.match(f.document.querySelector('[data-slot="receipt"]').textContent,/已保存/);
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').disabled,false);
});

function semanticStatus(overrides = {}) {
  const initial=status();
  return status({memory:{...initial.memory,controls:{'set-semantics':true},
    semantics:{enabled:false,model:'',max_calls_per_hour:24,max_tokens_per_day:60000},...overrides}});
}

test('semantic settings stay in ordinary settings with independent enabled state and authoritative budget defaults',async()=>{
  const f=fixture();await f.flush();await f.receive('status',semanticStatus());
  const toggle=f.document.querySelector('[data-action="set-semantics"]');
  assert.equal(toggle.getAttribute('aria-checked'),'false');assert.equal(toggle.disabled,false);
  assert.equal(f.document.querySelector('[data-semantic-field="hour"]').value,'24');
  assert.equal(f.document.querySelector('[data-semantic-field="day"]').value,'60000');
  assert.equal(f.document.querySelector('.mirally-activity__advanced').contains(toggle),false);
  f.click('[data-action="set-semantics"]');f.click('[data-action="set-semantics"]');
  assert.equal(f.messages.filter(row=>row.action==='set-semantics').length,1);
  assert.equal(f.messages.at(-1).enabled,true);assert.equal(Object.hasOwn(f.messages.at(-1),'maxCallsPerHour'),false);
  assert.equal(f.timers.size,0);
  assert.equal(f.messages.some(row=>['start','stop','set-enabled'].includes(row.action)),false);
  await f.receive('set-semantics',{saved:true,applied:true});
  await f.receive('status',semanticStatus({semantics:{enabled:true,max_calls_per_hour:6,max_tokens_per_day:80000}}));
  assert.equal(toggle.getAttribute('aria-checked'),'true');
  assert.equal(f.document.querySelector('[data-semantic-field="hour"]').value,'6');
  assert.match(f.document.querySelector('[data-slot="semantic-receipt"]').textContent,/保存并应用/);
});

test('semantic budget validation rejects empty, fractional, negative and excessive values before writing',()=>{
  for(const hour of ['',-1,0.5,10001,'NaN']) assert.ok(activity.semanticDraft('',hour,60000).error);
  for(const day of ['',-1,1.5,10000001,'Infinity']) assert.ok(activity.semanticDraft('',24,day).error);
  assert.deepEqual(activity.semanticDraft(' provider/model ',0,0),{model:'provider/model',maxCallsPerHour:0,maxTokensPerDay:0});
  assert.ok(activity.semanticDraft('provider/model name',24,60000).error);
});

test('semantic model and budget drafts survive status refresh and save only through their own action',async()=>{
  const f=fixture();await f.flush();await f.receive('status',semanticStatus());
  const input=(name,value)=>{const node=f.document.querySelector(`[data-semantic-field="${name}"]`);node.value=value;node.dispatchEvent(new f.Event('input',{bubbles:true}));};
  input('model','custom/vision');input('hour','36');input('day','90000');
  f.click('[data-action="refresh"]');await f.receive('status',semanticStatus());
  assert.equal(f.document.querySelector('[data-semantic-field="hour"]').value,'36');
  f.document.querySelector('[data-slot="semantic-form"]').dispatchEvent(new f.Event('submit',{cancelable:true}));
  const sent=f.messages.at(-1);
  assert.equal(sent.action,'set-semantics');assert.equal(sent.enabled,false);
  assert.equal(sent.model,'custom/vision');assert.equal(sent.maxCallsPerHour,36);assert.equal(sent.maxTokensPerDay,90000);
  await f.receive('set-semantics',{saved:true,applied:false,reload_error:'后台未运行'});
  await f.receive('status',semanticStatus({semantics:{enabled:false,model:'custom/vision',max_calls_per_hour:36,max_tokens_per_day:90000}}));
  assert.match(f.document.querySelector('[data-slot="semantic-receipt"]').textContent,/已保存.*尚未应用.*后台未运行/);
  assert.equal(f.document.querySelector('[data-action="save-semantics"]').disabled,false);
});

test('semantic save failures preserve drafts and navigation rejects late completion',async()=>{
  const f=fixture();await f.flush();await f.receive('status',semanticStatus());
  const model=f.document.querySelector('[data-semantic-field="model"]');model.value='private/custom';model.dispatchEvent(new f.Event('input',{bubbles:true}));
  f.document.querySelector('[data-slot="semantic-form"]').dispatchEvent(new f.Event('submit',{cancelable:true}));
  await f.receive('set-semantics',undefined,'配置只读');
  assert.equal(model.value,'private/custom');assert.match(f.document.querySelector('[data-slot="error"]').textContent,/配置只读/);
  f.click('[data-action="set-semantics"]');const sent=f.messages.at(-1);
  await f.route('/chat');await f.route('/settings/appearance','?mirally=activity');
  assert.equal(f.api.receive({requestId:sent.requestId,result:{saved:true,applied:true}}),false);
  assert.equal(f.document.querySelector('[data-action="set-semantics"]').disabled,true);
});

test('semantic status reports actual success, pending work, next attempt and model errors as inert text',async()=>{
  const f=fixture();await f.flush();await f.receive('status',semanticStatus({
    semantics:{enabled:true,model:'provider/model',max_calls_per_hour:24,max_tokens_per_day:60000},
    runtime:{semantic:{state:'retry_pending',lastSuccessUtcIso:'2026-10-15T01:00:00Z',lastError:'<script>retry</script>'},semantic_maintenance:{pending_windows:3,next_attempt_utc_ms:1792026000000}}}));
  assert.match(f.document.querySelector('[data-slot="semantic-model"]').textContent,/provider\/model/);
  assert.equal(f.document.querySelector('[data-slot="semantic-pending"]').textContent,'3 个窗口');
  assert.doesNotMatch(f.document.querySelector('[data-slot="semantic-success"]').textContent,/尚无记录/);
  assert.doesNotMatch(f.document.querySelector('[data-slot="semantic-next"]').textContent,/等待新活动/);
  assert.match(f.document.querySelector('[data-slot="semantic-failure"]').textContent,/最近一次失败.*<script>retry<\/script>/);
  assert.equal(f.document.querySelectorAll('.mirally-activity script').length,0);
});

test('semantic inherited model reflects the current host route, not an older successful model',async()=>{
  const f=fixture();await f.flush();await f.receive('status',semanticStatus({
    semantics:{enabled:true,model:'',effective_model:'current/model',max_calls_per_hour:24,max_tokens_per_day:60000},
    runtime:{semantic:{lastProvider:'old',lastModel:'route'}}}));
  assert.equal(f.document.querySelector('[data-slot="semantic-model"]').textContent,'沿用对话模型：current/model');
});

test('raw storage pressure explicitly pauses collection until manual continuation after cleanup',async()=>{
  const f=fixture();await f.flush();const initial=status();
  await f.receive('status',status({activity:{...initial.activity,collector:{status:'paused',snapshot:false,pause_reason:'semantic_memory_pending_storage_quota'}}}));
  const warning=f.document.querySelector('[data-slot="activity-warning"]');
  assert.match(warning.textContent,/原始记录存储已满，活动采集已暂停/);
  assert.match(warning.textContent,/完成语义整理并清理释放空间后，请点击继续/);
  assert.equal(f.messages.some(row=>row.action==='resume'),false);
});

test('semantic budget usage accepts finite nonnegative counts, labels UTC and rejects invalid diagnostics',async()=>{
  const f=fixture();await f.flush();await f.receive('status',semanticStatus({runtime:{semantic_maintenance:{usage:{requests_hourly:5,tokens_daily:12000}}}}));
  assert.match(f.document.querySelector('[data-slot="semantic-usage"]').textContent,/本小时调用已用或预留 5 \/ 24 次.*12,000 \/ 60,000 tokens.*本地整理预算.*不是服务商余额/);
  for(const usage of [{requests_hourly:-1,tokens_daily:Infinity},{requests_hourly:NaN,tokens_daily:'<script>bad</script>'},{}]) {
    f.click('[data-action="refresh"]');await f.receive('status',semanticStatus({runtime:{semantic_maintenance:{usage}}}));
    assert.match(f.document.querySelector('[data-slot="semantic-usage"]').textContent,/未确认 \/ 24 次.*未确认 \/ 60,000 tokens/);
  }
});

test('persisted semantic failure remains visible after plugin restart and explains exhausted raw protection',async()=>{
  const f=fixture();await f.flush();await f.receive('status',semanticStatus({
    semantics:{enabled:true},runtime:{semantic:{state:'idle',lastError:null},
      semantic_maintenance:{last_failure:{error:'持久化的模型失败',exhausted:true,failed_at_utc_ms:1700000000000,next_attempt_utc_ms:null}}}}));
  assert.match(f.document.querySelector('[data-slot="semantic-failure"]').textContent,/持久化的模型失败.*3 次尝试上限，原始记录仍保留/);
  assert.equal(f.document.querySelector('[data-status="semantics"]').textContent,'总结重试已达上限');
  assert.match(f.document.querySelector('[data-slot="semantic-next"]').textContent,/已达尝试上限/);
  f.click('[data-action="refresh"]');await f.receive('status',semanticStatus({semantics:{enabled:true},runtime:{semantic:{state:'running'},semantic_maintenance:{last_failure:null}}}));
  assert.equal(f.document.querySelector('[data-slot="semantic-failure"]').textContent,'无已报告的失败');
  assert.equal(f.document.querySelector('[data-status="semantics"]').textContent,'正在总结');
});

test('current hourly budget wait is separate from a historical empty-response failure and preserves raw diagnostics',async()=>{
  const f=fixture();await f.flush();
  const attempt='2026-10-15T07:27:00Z', next=Date.parse('2026-10-15T08:00:00Z');
  await f.receive('status',semanticStatus({semantics:{enabled:true,max_calls_per_hour:6,max_tokens_per_day:6000000},
    runtime:{semantic:{state:'budget_exhausted',lastError:'Expecting value: line 1 column 1 (char 0)',lastAttemptUtcIso:attempt},
      semantic_maintenance:{next_attempt_utc_ms:next,usage:{requests_hourly:6,tokens_daily:547122},last_failure:{exhausted:false}}}}));
  assert.equal(f.document.querySelector('[data-status="semantics"]').textContent,'等待本地整理预算恢复');
  const wait=f.document.querySelector('[data-slot="semantic-wait"]').textContent;
  assert.match(wait,/当前等待：每小时整理次数已达上限/);assert.ok(wait.includes(new Date(next).toLocaleString()));
  const failure=f.document.querySelector('[data-slot="semantic-failure"]').textContent;
  assert.match(failure,/最近一次失败.*模型未返回可用的记忆内容/);assert.ok(failure.includes(new Date(attempt).toLocaleString()));
  assert.doesNotMatch(failure,/Expecting value|3 次尝试上限/);
  assert.match(f.document.querySelector('[data-slot="diagnostics"]').textContent,/Expecting value/);
  assert.doesNotMatch(f.document.querySelector('[data-slot="memory-warning"]').textContent,/Expecting value/);
  assert.match(f.document.querySelector('[data-slot="semantic-usage"]').textContent,/6 \/ 6 次.*547,122 \/ 6,000,000 tokens.*不是服务商余额/);
});

test('budget display gives UTC window reset in local time and handles zero and unconfirmed limits honestly',()=>{
  const now=Date.parse('2026-10-15T07:30:00Z');
  const feedback=activity.semanticFeedback({semantics:{max_calls_per_hour:6,max_tokens_per_day:6000},runtime:{semantic:{state:'budget_exhausted'},semantic_maintenance:{usage:{requests_hourly:1,tokens_daily:6000}}}},now);
  assert.ok(feedback.usage.includes(new Date('2026-10-15T08:00:00Z').toLocaleString()));
  assert.ok(feedback.usage.includes(new Date('2026-10-16T00:00:00Z').toLocaleString()));
  assert.match(feedback.wait,/每日 tokens 已达上限/);
  const zero=activity.semanticFeedback({semantics:{max_calls_per_hour:0,max_tokens_per_day:0}},now);
  assert.match(zero.usage,/上限为 0，需调整预算后继续/);assert.match(zero.usage,/未确认 \/ 0/);
});

test('historical errors and exhausted jobs do not overwrite a different current semantic activity',()=>{
  for(const [state,label] of [['running','正在总结'],['idle','等待新活动'],['budget_exhausted','等待本地整理预算恢复']]) {
    const memory={semantics:{enabled:true},runtime:{semantic:{state,lastError:'historical error'}}};
    assert.equal(activity.statusView({memory}).semantics,label);
  }
  assert.equal(activity.statusView({memory:{semantics:{enabled:true},runtime:{semantic:{state:'running'},semantic_maintenance:{last_failure:{exhausted:true}}}}}).semantics,'正在总结');
});

test('successful chunks waiting for consolidation are distinct from a completed summary or blocked merge',()=>{
  for(const [state,label] of [['waiting_merge','分片已整理，等待汇总'],['consolidation_blocked','汇总受阻，等待处理']]) {
    assert.equal(activity.statusView(semanticStatus({semantics:{enabled:true},runtime:{semantic:{state,lastError:state==='consolidation_blocked'?'merge_capacity_exceeded':null}}})).semantics,label);
  }
});

test('status sent before a semantic edit cannot overwrite its authoritative refreshed state',async()=>{
  const f=fixture();await f.flush();await f.receive('status',semanticStatus());
  f.click('[data-action="refresh"]');const earlier=f.messages.at(-1);
  f.click('[data-action="set-semantics"]');
  await f.receive('set-semantics',{saved:true,applied:true});
  await f.receive('status',semanticStatus({semantics:{enabled:true,max_calls_per_hour:24,max_tokens_per_day:60000}}));
  f.api.receive({requestId:earlier.requestId,result:semanticStatus()});await f.flush();
  assert.equal(f.document.querySelector('[data-action="set-semantics"]').getAttribute('aria-checked'),'true');
});

test('recording preference failures remain visible and unavailable profiles cannot toggle', async () => {
  const f=fixture();await f.flush();
  await f.receive('status',status({activity:{enabled:true,error:'配置不可读',controls:{'set-enabled':false}}}));
  f.click('[data-action="set-enabled"]');await f.flush();
  assert.equal(f.messages.some(item=>item.action==='set-enabled'),false);
  f.click('[data-action="refresh"]');await f.flush();
  await f.receive('status',status({activity:{enabled:false,controls:{'set-enabled':true},collector:{snapshot:false,status:'stopped'}}}));
  f.click('[data-action="set-enabled"]');await f.flush();
  await f.receive('set-enabled',{saved:true,enabled:true,capture_error:'屏幕捕获启动失败'});
  await f.receive('status',status({activity:{enabled:true,auto_start_error:'屏幕捕获启动失败',controls:{'set-enabled':true},collector:{snapshot:false,status:'error'}}}));
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').getAttribute('aria-checked'),'true');
  assert.match(f.document.querySelector('[data-slot="error"]').textContent,/屏幕捕获启动失败/);
  assert.match(f.document.querySelector('[data-slot="receipt"]').textContent,/已保存.*未能更新/);
});

test('leaving the page cancels preference completion and cannot change a newly mounted switch', async () => {
  const f=fixture();await f.flush();
  await f.receive('status',status({activity:{enabled:false,controls:{'set-enabled':true}}}));
  f.click('[data-action="set-enabled"]');await f.flush();
  const pending=f.messages.find(item=>item.action==='set-enabled');
  await f.route('/chat');await f.route('/settings/appearance','?mirally=activity');
  f.api.receive({requestId:pending.requestId,result:{saved:true,enabled:true}});await f.flush();
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').disabled,true);
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').getAttribute('aria-checked'),'false');
  await f.receive('status',status({activity:{enabled:false,controls:{'set-enabled':true}}}));
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').disabled,false);
});

test('recording preference requires a saved receipt and preserves the switch on failure', async () => {
  const f=fixture();await f.flush();
  await f.receive('status',status({activity:{enabled:false,controls:{'set-enabled':true}}}));
  f.click('[data-action="set-enabled"]');await f.flush();
  await f.receive('set-enabled',{enabled:true});
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').getAttribute('aria-checked'),'false');
  assert.match(f.document.querySelector('[data-slot="error"]').textContent,/未能确认/);
  assert.equal(f.messages.filter(item=>item.action==='status').length,1);
  f.click('[data-action="set-enabled"]');await f.flush();
  await f.receive('set-enabled',null,'偏好文件无法保存');
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').getAttribute('aria-checked'),'false');
  assert.match(f.document.querySelector('[data-slot="error"]').textContent,/偏好文件无法保存/);
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').disabled,false);
});

test('default local recording and memory configuration is usable and model summary problems stay explicit', async () => {
  const defaults=activity.statusView({activity:{configured:false,enabled:true},memory:{configured:false,enabled:true}});
  assert.equal(defaults.activityEnabled,'默认已开启');assert.equal(defaults.memoryEnabled,'默认已开启');
  assert.equal(defaults.semantics,'已关闭');
  for(const [state,label] of [['budget_exhausted','等待本地整理预算恢复'],['retry_pending','总结失败，等待重试'],['host_unavailable','模型服务暂不可用'],['running','正在总结']]) {
    const view=activity.statusView({memory:{semantics:{enabled:true},runtime:{semantic:{state}}}});
    assert.equal(view.semantics,label);
  }
  const f=fixture();await f.flush();
  await f.receive('status',status({activity:{configured:false,enabled:true,database_exists:true,controls:{'set-enabled':true}},
    memory:{configured:false,enabled:true,semantics:{enabled:true},runtime:{semantic:{state:'retry_pending',lastError:'模型服务认证失败'}}}}));
  assert.match(f.document.querySelector('[data-slot="preference-note"]').textContent,/使用默认记录设置/);
  assert.doesNotMatch(f.document.querySelector('[data-slot="activity-warning"]').textContent,/开始前|还没有/);
  assert.match(f.document.querySelector('[data-slot="semantic-failure"]').textContent,/最近一次失败.*模型服务认证失败/);
  assert.doesNotMatch(f.document.querySelector('[data-slot="memory-warning"]').textContent,/还没有/);
  assert.equal(f.document.querySelector('[data-status="semantics"]').textContent,'总结失败，等待重试');
});

test('local organization follows the maintenance worker instead of the Gateway process', async () => {
  const runtime={verified:true,status:'running',running:true,ready:true};
  const label=fields=>activity.statusView({memory:{enabled:true,runtime:{...runtime,...fields}}}).memoryRuntime;
  assert.equal(activity.statusView({memory:{enabled:false,runtime}}).memoryRuntime,'已关闭');
  assert.equal(label({maintenance_running:false,maintenance_status:'ready'}),'已关闭');
  assert.equal(label({maintenance_running:false,maintenance_status:'disabled'}),'已关闭');
  assert.equal(label({maintenance_running:true,maintenance_status:'not_ready'}),'等待活动记录');
  assert.equal(label({maintenance_running:true,maintenance_status:'degraded'}),'整理遇到问题');
  assert.equal(label({maintenance_running:true,maintenance_status:'ready'}),'运行中');
  assert.equal(label({maintenance_running:false,maintenance_status:'other_writer'}),'等待其他整理进程');
  assert.equal(label({verified:false,maintenance_running:true}),'运行未确认');
  assert.equal(label({}),'运行中');
  const f=fixture();await f.flush();
  await f.receive('status',status({memory:{enabled:true,runtime:{...runtime,maintenance_running:true,
    maintenance_status:'degraded',error:'活动来源数据库暂不可读'}}}));
  assert.equal(f.document.querySelector('[data-status="memoryRuntime"]').textContent,'整理遇到问题');
  assert.match(f.document.querySelector('[data-slot="memory-warning"]').textContent,/活动来源数据库暂不可读/);
});

test('late status cannot overwrite state refreshed after saving the recording preference', async () => {
  const f=fixture();await f.flush();
  await f.receive('status',status({activity:{enabled:true,controls:{'set-enabled':true}}}));
  f.click('[data-action="refresh"]');await f.flush();
  const oldRead=f.messages.filter(item=>item.action==='status').at(-1);
  f.click('[data-action="set-enabled"]');await f.flush();
  assert.equal(f.document.querySelector('[data-action="refresh"]').disabled,true);
  await f.receive('set-enabled',{saved:true,enabled:false});
  await f.receive('status',status({activity:{enabled:false,collector:{snapshot:false,status:'stopped'},controls:{'set-enabled':true}}}));
  f.api.receive({requestId:oldRead.requestId,result:status({activity:{enabled:true,collector:{snapshot:false,status:'running'},controls:{'set-enabled':true}}})});await f.flush();
  assert.equal(f.document.querySelector('[data-action="set-enabled"]').getAttribute('aria-checked'),'false');
  assert.equal(f.document.querySelector('[data-status="collector"]').textContent,'已停止');
  assert.equal(f.document.querySelector('[data-action="refresh"]').disabled,false);
});

test('capture control invalidates earlier status and stale read errors cannot replace its result', async () => {
  const f=fixture();await f.flush();
  await f.receive('status',status({activity:{enabled:true,session_id:'capture-1',collector:{snapshot:false,status:'running'},controls:{pause:true}}}));
  f.click('[data-action="refresh"]');await f.flush();
  const oldRead=f.messages.filter(item=>item.action==='status').at(-1);
  f.click('[data-action="pause"]');await f.flush();
  await f.receive('pause',{paused:true});
  await f.receive('status',status({activity:{enabled:true,session_id:'capture-1',collector:{snapshot:false,status:'paused'},controls:{resume:true}}}));
  f.api.receive({requestId:oldRead.requestId,error:'过时状态读取失败'});await f.flush();
  assert.equal(f.document.querySelector('[data-status="collector"]').textContent,'已暂停');
  assert.equal(f.document.querySelector('[data-slot="error"]').hidden,true);
  assert.equal(f.document.querySelector('[data-action="refresh"]').disabled,false);
});

test('the recording switch is the only start entry when the saved preference is off', async () => {
  const f=fixture();await f.flush();
  await f.receive('status',status({activity:{enabled:false,controls:{start:true,'set-enabled':true},collector:{snapshot:false,status:'stopped'}}}));
  const start=f.document.querySelector('[data-action="start"]');
  assert.equal(start.hidden,true);assert.equal(start.disabled,true);
  f.click('[data-action="start"]');await f.flush();
  assert.equal(f.messages.some(item=>item.action==='start'),false);
  f.click('[data-action="refresh"]');await f.flush();
  await f.receive('status',status({activity:{enabled:true,controls:{start:true,'set-enabled':true},collector:{snapshot:false,status:'stopped'}}}));
  assert.equal(start.hidden,false);assert.equal(start.disabled,false);
  assert.equal(start.textContent,'重新开始');
  f.click('[data-action="start"]');await f.flush();
  assert.equal(f.messages.filter(item=>item.action==='start').length,1);
});

test('saved collector state and unverified memory runtime never imply a live process', () => {
  const view=activity.statusView(status());
  assert.equal(view.activityEnabled,'已启用');
  assert.equal(view.collector,'最近记录状态 · 运行中');
  assert.match(view.collectorNote,/不代表/);
  assert.equal(view.memoryRuntime,'运行未确认');
  assert.equal(view.activeCount,'4');
  assert.equal(view.candidateCount,'2');
  const live=activity.statusView(status({activity:{configured:true,enabled:false,collector:{snapshot:false,status:'paused'}},memory:{runtime:{verified:true,status:'ready'}}}));
  assert.equal(live.activityEnabled,'已关闭');
  assert.equal(live.collector,'已暂停');
  assert.equal(live.memoryRuntime,'已就绪');
});

test('hourly retention separates configuration, completed cleanup and pending memory', () => {
  const memory={enabled:true,raw_retention:{enabled:true,interval_seconds:3600,state:'scheduled',reclaimed_bytes:1048576,last_cleanup_utc_ms:1700000000000}};
  const view=activity.statusView({memory});
  assert.equal(view.retentionInterval,'每小时');
  assert.equal(view.retentionState,'等待下次清理');
  assert.equal(view.retentionReleased,'上次实际释放 1.0 MiB');
  assert.match(view.retentionLast,/上次清理/);
  assert.equal(activity.statusView({memory:{...memory,enabled:false}}).retentionState,'等待记忆启用');
  assert.equal(activity.statusView({memory:{...memory,enabled:false,raw_retention:{enabled:false,configured_enabled:true}}}).retentionState,'等待记忆启用');
  assert.equal(activity.statusView({memory:{enabled:true,raw_retention:{enabled:false}}}).retentionState,'已关闭');
  const retry=activity.statusView({memory:{...memory,raw_retention:{...memory.raw_retention,state:'degraded',blocked_reason:'raw_compaction_pending'}}});
  assert.equal(retry.retentionState,'清理未完成，等待重试');
  assert.match(retry.retentionWarning,/等待数据库释放空间/);
  assert.equal(retry.retentionReleased,'本次实际释放 1.0 MiB');
});

test('bridge fixes routing fields, rejects unsupported actions and ignores unknown replies', async () => {
  const f=fixture(false);
  await f.flush();
  const client=activity.createClient(f.root);
  const pending=client.request('status',{type:'override',action:'start',requestId:'override'});
  const sent=f.messages.at(-1);
  assert.equal(sent.type,'mirally-activity');
  assert.equal(sent.action,'status');
  assert.notEqual(sent.requestId,'override');
  assert.equal(client.receive({requestId:'unknown',result:{}}),false);
  assert.equal(client.receive({requestId:sent.requestId,result:{ready:true}}),true);
  assert.deepEqual(await pending,{ready:true});
  assert.equal(client.receive({requestId:sent.requestId,result:{ready:false}}),false);
  await assert.rejects(client.request('edit-config'),/不支持/);
  assert.equal(f.messages.length,1);
});

test('bridge timeout and cancellation settle outstanding requests without retaining timers', async () => {
  const f=fixture(false), client=activity.createClient(f.root);
  const timed=client.request('status');
  const rejection=assert.rejects(timed,/超时/);
  f.timers.values().next().value();
  await rejection;
  f.timers.clear();
  const cancelled=client.request('status');
  const cancelledRejection=assert.rejects(cancelled,/已离开/);
  client.cancel();
  await cancelledRejection;
  assert.equal(f.timers.size,0);
});

test('capture confirmation can stay pending without a timeout or inviting a duplicate start', async () => {
  const f=fixture();await f.flush();await f.receive('status',status());
  f.click('[data-action="start"]');
  const request=f.messages.at(-1);
  assert.equal(request.action,'start');
  assert.equal(f.timers.size,0,'start confirmation must not have an ordinary request timeout');
  assert.equal(f.document.querySelector('[data-action="start"]').disabled,true);
  f.click('[data-action="start"]');
  assert.equal(f.messages.filter(item=>item.action==='start').length,1,'duplicate start events must remain blocked');
  f.click('[data-action="refresh"]');await f.receive('status',status());
  assert.equal(f.document.querySelector('[data-action="start"]').disabled,true,'status refresh must not enable another start during confirmation');
  await f.route('/chat');
  assert.equal(f.api.receive({requestId:request.requestId,result:{cancelled:true}}),false);
  assert.equal(f.timers.size,0);
});

test('all capture controls keep their completion pending without an ordinary request timeout', async () => {
  const f=fixture(false);await f.flush();
  const client=activity.createClient(f.root);
  for (const action of ['pause','resume','stop']) {
    const pending=client.request(action,{session_id:'owned-session'});
    const rejection=assert.rejects(pending,/已离开/);
    assert.equal(f.timers.size,0,`${action} must not expire while a collector operation is pending`);
    client.cancel();await rejection;
  }
});

test('pause, resume and stop bind to the session reported by status', async () => {
  const f=fixture();await f.flush();
  const initial=status();
  for (const action of ['pause','resume','stop']) {
    await f.receive('status',status({activity:{...initial.activity,session_id:`session-${action}`,
      collector:{snapshot:false,status:action==='resume'?'paused':'running'},controls:{[action]:true},
      scope_note:'本次采集沿用启动时的范围'}}));
    f.click(`[data-action="${action}"]`);
    assert.equal(f.messages.at(-1).action,action);
    assert.equal(f.messages.at(-1).session_id,`session-${action}`);
    assert.equal(f.timers.size,0);
    await f.receive(action,{ok:true});
    assert.equal(f.messages.at(-1).action,'status');
  }
  assert.equal(f.document.querySelector('[data-slot="capture-note"]').textContent,'本次采集沿用启动时的范围');
});

test('old control replies cannot authorize or update a new capture after navigation', async () => {
  const f=fixture();await f.flush();
  const initial=status();
  await f.receive('status',status({activity:{...initial.activity,session_id:'old-session',controls:{stop:true}}}));
  f.click('[data-action="stop"]');
  const old=f.messages.at(-1);
  assert.equal(old.session_id,'old-session');
  await f.route('/chat');await f.route('/settings/appearance','?mirally=activity');
  assert.equal(f.document.querySelector('[data-action="stop"]').hidden,true);
  assert.equal(f.document.querySelector('[data-status="collector"]').textContent,'最近记录状态 · 运行中');
  assert.match(f.document.querySelector('[data-slot="receipt"]').textContent,/上次状态.*正在更新/);
  assert.equal(f.api.receive({requestId:old.requestId,result:{ok:true}}),false);
  await f.receive('status',status({activity:{...initial.activity,session_id:'new-session',
    collector:{snapshot:false,status:'running'},controls:{stop:true}}}));
  f.click('[data-action="stop"]');
  assert.equal(f.messages.at(-1).session_id,'new-session');
  assert.equal(f.document.querySelector('[data-status="collector"]').textContent,'运行中');
});

test('live controls without a confirmed session identity are rejected locally', async () => {
  const f=fixture();await f.flush();
  const initial=status();
  await f.receive('status',status({activity:{...initial.activity,controls:{stop:true}}}));
  f.click('[data-action="stop"]');
  assert.deepEqual(f.messages.map(item=>item.action),['status']);
  assert.match(f.document.querySelector('[data-slot="error"]').textContent,/会话尚未确认/);
});

test('first page visit reads only status and preserves the existing native outlet', async () => {
  const f=fixture();await f.flush();
  assert.deepEqual(f.messages.map(item=>item.action),['status']);
  assert.equal(f.outlet.hidden,true);
  assert.equal(f.outlet.inert,true);
  assert.equal(f.document.querySelector('#native-content').textContent,'原生设置');
  assert.equal(f.document.querySelectorAll('openclaw-router-outlet').length,1);
  assert.equal(f.document.querySelectorAll('.mirally-activity').length,1);
  assert.equal(f.document.querySelector('.mirally-activity__entry').getAttribute('aria-current'),'page');
  await f.receive('status',status());
  await f.flush();
  assert.equal(f.messages.length,1,'DOM updates must not poll status');
  assert.equal(f.document.querySelector('[data-status="collector"]').textContent,'最近记录状态 · 运行中');
  assert.equal(f.document.querySelector('[data-status="memoryRuntime"]').textContent,'运行未确认');
});

test('revisiting immediately shows cached status while every operation waits for fresh permission', async () => {
  const f=fixture();await f.flush();
  const initial=semanticStatus();
  await f.receive('status',initial);
  await f.route('/chat');await f.route('/settings/appearance','?mirally=activity');
  assert.equal(f.document.querySelector('[data-status="collector"]').textContent,activity.statusView(initial).collector);
  assert.match(f.document.querySelector('[data-slot="receipt"]').textContent,/上次状态.*正在更新/);
  for(const node of f.document.querySelectorAll('.mirally-activity button, .mirally-activity input, .mirally-activity select'))
    assert.equal(node.disabled,true, node.outerHTML);
  const count=f.messages.length;
  for(const action of ['start','pause','resume','stop','set-enabled','set-semantics','save-semantics','browse','monitor']) f.click(`[data-action="${action}"]`);
  await f.flush();
  assert.equal(f.messages.length,count,'cached permissions must not dispatch operations even through synthetic events');
  await f.receive('status',initial);
  assert.equal(f.document.querySelector('[data-action="set-semantics"]').disabled,false);
  assert.ok(f.document.querySelector('[data-action="open-memory"]'));
  assert.equal(f.document.querySelector('[data-slot="receipt"]').textContent,'状态已更新');
});

test('cached status remains explicitly stale and locked after a disconnected refresh', async () => {
  const f=fixture();await f.flush();await f.receive('status',semanticStatus());
  await f.route('/chat');await f.route('/settings/appearance','?mirally=activity');
  await f.receive('status',null,'后台未连接');
  assert.match(f.document.querySelector('[data-slot="receipt"]').textContent,/上次状态.*更新失败/);
  assert.match(f.document.querySelector('[data-slot="error"]').textContent,/后台未连接/);
  for(const node of f.document.querySelectorAll('.mirally-activity button, .mirally-activity input, .mirally-activity select')) {
    if(node.dataset.action==='refresh') assert.equal(node.disabled,false);
    else assert.equal(node.disabled,true,node.outerHTML);
  }
});

test('a different Gateway context cannot reuse cached presentation or an earlier status reply', async () => {
  const f=fixture();await f.flush();await f.receive('status',semanticStatus());
  f.click('[data-action="refresh"]');const old=f.messages.at(-1);
  const app=f.document.querySelector('openclaw-app');
  app.context={...app.context};
  await f.route('/settings/appearance','?mirally=activity');
  assert.equal(f.document.querySelector('[data-status="collector"]').textContent,'—');
  assert.doesNotMatch(f.document.querySelector('[data-slot="receipt"]').textContent,/上次状态/);
  assert.equal(f.api.receive({requestId:old.requestId,result:semanticStatus()}),false);
  assert.equal(f.document.querySelector('[data-action="set-semantics"]').disabled,true);
  await f.receive('status',semanticStatus());
  assert.equal(f.document.querySelector('[data-action="set-semantics"]').disabled,false);
});

test('leaving settings restores native outlet attributes and cancels stale replies', async () => {
  const f=fixture();f.outlet.setAttribute('aria-hidden','false');
  await f.flush();
  const old=f.messages[0];
  await f.route('/chat');
  assert.equal(f.outlet.hidden,false);
  assert.equal(f.outlet.inert,false);
  assert.equal(f.outlet.getAttribute('aria-hidden'),'false');
  assert.equal(f.document.querySelector('.mirally-activity'),null);
  assert.equal(f.api.receive({requestId:old.requestId,result:status()}),false);
  assert.equal(f.timers.size,0);
  await f.flush();
  assert.equal(f.messages.length,1);
  await f.route('/settings/appearance','?mirally=activity');
  assert.equal(f.messages.length,2,'returning performs a fresh status request');
});

test('settings sidebar replacement remounts one product entry and host menu uses native navigation', async () => {
  const f=fixture(false);await f.flush();
  assert.equal(f.messages.length,0);
  const old=f.document.querySelector('.settings-sidebar__nav');
  const replacement=f.document.createElement('nav');replacement.className='settings-sidebar__nav';
  old.replaceWith(replacement);await f.flush();
  assert.equal(f.document.querySelectorAll('.mirally-activity__entry').length,1);
  assert.equal(f.api.open(),true);await f.flush();
  assert.deepEqual(f.navigateCalls,[{route:'appearance',options:{search:'?mirally=activity'}}]);
  assert.equal(f.document.querySelector('.mirally-activity__entry').getAttribute('href'),'/settings/appearance?mirally=activity');
  assert.equal(activity.isPage(f.root.location),true,'opening must retain the marker through native navigation');
  assert.equal(f.messages[0].action,'status');
});

test('unconfigured page disables data access and does not invent capture controls', async () => {
  const f=fixture();await f.flush();
  await f.receive('status',{activity:{configured:false,enabled:false,database_exists:false},memory:{configured:false,enabled:false}});
  for (const action of ['browse','monitor']) assert.equal(f.document.querySelector(`[data-action="${action}"]`).disabled,true);
  for (const action of ['start','pause','resume','stop']) assert.equal(f.document.querySelector(`[data-action="${action}"]`).hidden,true);
  assert.equal(f.document.querySelector('[data-slot="search"]'),null);
  assert.equal(f.document.querySelector('[data-status="activityEnabled"]').textContent,'默认已关闭');
});

test('configuration changes, alternate memory sources and synthesis budgets remain explicit', async () => {
  const f=fixture();await f.flush();
  const initial=status();
  await f.receive('status',status({activity:{...initial.activity,live_enabled:true,config_changed:true,
    warning:'本次使用原范围',collector:{snapshot:false,status:'paused'},controls:{resume:true,stop:true}},
    memory:{...initial.memory,source_matches_activity:false,semantics:{enabled:true,max_calls_per_hour:6,max_tokens_per_day:60000}},
    profiles:{warning:'使用已有的工作空间配置'}}));
  assert.equal(f.document.querySelector('[data-status="activityCurrent"]').textContent,'本次已开启');
  assert.match(f.document.querySelector('[data-slot="activity-warning"]').textContent,/停止后重新开始/);
  assert.match(f.document.querySelector('[data-slot="activity-warning"]').textContent,/本次使用原范围/);
  assert.match(f.document.querySelector('[data-slot="memory-warning"]').textContent,/另一份活动来源/);
  assert.match(f.document.querySelector('[data-slot="semantic-budget"]').textContent,/每小时最多 6 次/);
  assert.equal(f.document.querySelector('[data-slot="profile-warning"]').textContent,'使用已有的工作空间配置');
  assert.equal(f.document.querySelector('[data-action="resume"]').hidden,false);
});

test('capture start cancellation remains inert and successful controls refresh actual state', async () => {
  const f=fixture();await f.flush();await f.receive('status',status());
  f.click('[data-action="start"]');
  assert.deepEqual(f.messages.map(item=>item.action),['status','start']);
  await f.receive('start',{cancelled:true});
  assert.equal(f.messages.length,2);
  assert.equal(f.document.querySelector('[data-slot="receipt"]').textContent,'未开始采集');
  assert.equal(f.document.querySelector('[data-slot="error"]').hidden,true);
  f.click('[data-action="start"]');await f.receive('start',{started:true});
  assert.equal(f.messages.at(-1).action,'status');
  await f.receive('status',status({activity:{configured:true,enabled:true,database_exists:true,
    collector:{snapshot:false,status:'running'},controls:{pause:true,stop:true}}}));
  assert.equal(f.document.querySelector('[data-action="start"]').hidden,true);
  assert.equal(f.document.querySelector('[data-action="pause"]').hidden,false);
  assert.equal(f.document.querySelector('[data-status="collector"]').textContent,'运行中');
  assert.match(f.document.querySelector('[data-slot="monitor-note"]').textContent,/暂停、恢复或停止/);
});

test('history and monitor buttons only request their explicit viewing actions', async () => {
  const f=fixture();await f.flush();await f.receive('status',status());
  f.click('[data-action="browse"]');await f.receive('browse',{opened:true});
  f.click('[data-action="monitor"]');await f.receive('monitor',{opened:true});
  assert.deepEqual(f.messages.map(item=>item.action),['status','browse','monitor']);
});
