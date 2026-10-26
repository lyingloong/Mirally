/** Real phone/work Lit controllers against controlled RPCs, without GUI or account. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
const uiRequire=createRequire(new URL('../frontend/control-ui/package.json',import.meta.url));
const ts=uiRequire('typescript');
const root=new URL('../frontend/control-ui/ui/src/',import.meta.url);
function compile(file){return ts.transpileModule(readFileSync(new URL(file,root),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,experimentalDecorators:true,useDefineForClassFields:false}}).outputText.replace(/^import[\s\S]*?;\s*/gm,'').replace(/^export /gm,'');}
const stringify=value=>value==null||typeof value==='symbol'||typeof value==='function'?'':Array.isArray(value)?value.map(stringify).join(''):value?.strings?value.strings.map((part,index)=>part+stringify(value.values[index])).join(''):String(value);
const html=(strings,...values)=>({strings,values});
function fixture(file,extra={}){
  const storage=new Map(),calls=[],timers=new Map();let timer=0;
  const sandbox={html,nothing:Symbol(),live:value=>value,unsafeHTML:value=>value,customElement:()=>target=>target,state:()=>()=>{},property:()=>()=>{},
    OpenClawLightDomElement:class{isConnected=true;connectedCallback(){}disconnectedCallback(){}dispatchEvent(){}},
    document:{hidden:false,addEventListener(){},removeEventListener(){}},window:{},location:{hash:'',pathname:'/mobile',search:''},history:{replaceState(){}},
    sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    CustomEvent:class{constructor(type,data){this.type=type;this.detail=data.detail;}},URL,URLSearchParams,AbortController,crypto:{randomUUID:()=>`id-${calls.length}`},
    setTimeout:(fn,delay)=>{const id=++timer;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
    toSanitizedMarkdownHtml:text=>text,...extra};
  vm.createContext(sandbox);vm.runInContext(compile(file),sandbox);
  return {sandbox,storage,calls,timers,run:code=>vm.runInContext(code,sandbox)};
}
function mobile(handler=async()=>({})){const api=fixture('mobile/api.ts');const f=fixture('mobile/app.ts',{
  ...Object.fromEntries(['MobileApiError','DEFAULT_MOBILE_PREFERENCES','mobileNavigation','pairingCodeFromHash','timeLabel'].map(name=>[name,api.run(name)])),
  createMobileRpc:()=>async(method,params={})=>{f.calls.push({method,params});return handler(method,params);},readMobileToken:()=>'',storeMobileToken(){},pairMobile:async()=>({token:'phone-token',device:{id:'phone'}}),
});f.app=f.run('new MirallyMobileApp()');return f;}
const settle=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
test('restricted phone client sends only its bearer, no cookies or redirects',async()=>{
  const requests=[];const f=fixture('mobile/api.ts',{fetch:async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>({result:{ok:true}})};}});
  await f.run("createMobileRpc('phone-secret')('conversation.list', {limit: 10})");
  assert.equal(requests[0].url,'/api/mobile/rpc');assert.equal(requests[0].options.credentials,'omit');assert.equal(requests[0].options.redirect,'error');
  assert.equal(requests[0].options.headers.Authorization,'Bearer phone-secret');assert.deepEqual(JSON.parse(requests[0].options.body),{method:'conversation.list',params:{limit:10}});
});
test('pairing uses a single short code and no existing Host token',async()=>{
  const requests=[];const f=fixture('mobile/api.ts',{fetch:async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>({token:'t'})};}});
  await f.run("pairMobile('123456', '手机')");assert.equal(requests[0].url,'/api/mobile/pair');assert.equal(requests[0].options.headers.Authorization,undefined);
  assert.equal(f.run("pairingCodeFromHash('#pair=one%20time')"),'one time');
});
test('hidden entries keep settings reachable and navigation repairs duplicate IDs',()=>{
  const f=fixture('mobile/api.ts');const result=f.run("mobileNavigation({...DEFAULT_MOBILE_PREFERENCES,navigation:['inbox','inbox','bogus'],hidden:['work','conversations','settings']})");
  assert.deepEqual(Array.from(result),['inbox','settings']);
});
test('boot honors conversation or per-browser last-page preference without selecting Weixin route',async()=>{
  const f=mobile(async method=>method==='mobile.preferences.get'?{effective:{home:'last'},revision:3}: {conversations:[]});
  f.storage.set('mirally.mobile.view.v1','conversations');f.app.token='phone';await f.app.boot();await settle();
  assert.equal(f.app.view,'conversations');assert.ok(f.calls.every(call=>!call.method.startsWith('weixin.')));
});
test('metadata mutations send captured owner version and support archive/restore',async()=>{
  const f=mobile(async method=>method==='conversation.list'?{conversations:[]}:{});f.app.currentConversation='c1';f.app.currentConversationMeta={conversation_id:'c1',metadata_version:7,title:'旧标题'};f.app.conversations=[{...f.app.currentConversationMeta}];
  f.app.patchConversation({archived:true});await settle();const call=f.calls.find(call=>call.method==='conversation.update');
  assert.equal(call.params.expected_version,7);assert.equal(call.params.conversation_id,'c1');assert.equal(call.params.patch.archived,true);
});
test('notification controls use the shared work policy revision, never phone display prefs',async()=>{
  const f=mobile(async()=>({settings:{revision:12}}));f.app.workSettings={settings:{revision:11}};f.app.notificationPatch({channels:{weixin:true}});await settle();
  const call=f.calls.find(call=>call.method==='work.settings.update');assert.equal(call.params.expected_revision,11);assert.equal(f.calls.some(call=>call.method==='mobile.preferences.set'),false);
});
test('unknown send retains phone draft and reuses the same command ID on retry',async()=>{
  const f=mobile(async method=>{if(method==='conversation.send')throw new Error('offline');return {};});f.app.currentConversation='c';f.app.draft='继续检查';
  f.app.send({preventDefault(){}});await settle();const first=f.calls.find(call=>call.method==='conversation.send');
  assert.equal(f.app.draft,'继续检查');f.app.send({preventDefault(){}});await settle();const sends=f.calls.filter(call=>call.method==='conversation.send');assert.equal(sends.length,2);assert.equal(sends[1].params.command_id,first.params.command_id);
});
test('leaving a conversation during submission does not clear another conversation draft',async()=>{
  let acknowledge;const pending=new Promise(resolve=>acknowledge=resolve);const f=mobile(async method=>method==='conversation.send'?pending:{});f.app.currentConversation='a';f.app.draft='发往 A';
  f.app.send({preventDefault(){}});f.app.currentConversation='b';f.app.draft='B 的草稿';acknowledge({accepted:true});await settle();assert.equal(f.app.draft,'B 的草稿');
});
test('history contains only real user/assistant body and remote image loading is disabled',()=>{
  const f=mobile();f.app.currentConversation='c';f.app.messages=[{role:'user',text:'用户正文'},{role:'assistant',text:'回复正文'},{role:'toolResult',text:'隐藏参数'}];
  const rendered=stringify(f.app.conversationsView());assert.match(rendered,/用户正文/);assert.match(rendered,/回复正文/);assert.doesNotMatch(rendered,/隐藏参数/);assert.match(rendered,/不会更改微信/);
  const source=readFileSync(new URL('mobile/app.ts',root),'utf8');assert.match(source,/remoteImages:false/);
});
test('work order puts actual attention states first and completion filtering is explicit',()=>{
  const f=fixture('pages/mirally/work-panel.ts',{timeLabel:value=>String(value)});
  f.sandbox.works=[{id:'done',status:'completed',updated_at:99},{id:'run',status:'running',updated_at:8},{id:'approve',status:'needs_approval',updated_at:1}];
  const result=f.run("sortWorks(works,'attention',false)");assert.deepEqual(Array.from(result,item=>item.id),['approve','run']);
});
test('work run approval binds digest and stable execution ID then refreshes facts',async()=>{
  const f=fixture('pages/mirally/work-panel.ts',{timeLabel:value=>String(value)});const panel=f.run('new MirallyWorkPanel()');
  panel.selected='w';panel.rpc=async(method,params)=>{f.calls.push({method,params});if(method==='work.run.approve')return{approval_token:'one-time'};return{work:{id:'w'},runs:[]};};
  panel.approve({id:'approval_1',digest:'immutable-digest'});await settle();
  const approval=f.calls.find(call=>call.method==='work.run.approve'),start=f.calls.find(call=>call.method==='work.run.start');assert.equal(approval.params.expected_digest,'immutable-digest');assert.equal(start.params.approval_token,'one-time');assert.equal(start.params.request_id,'ui-start:approval_1');assert.ok(f.calls.some(call=>call.method==='work.get'));
});
test('unknown execution approval failure never starts or pretends a run succeeded',async()=>{
  const f=fixture('pages/mirally/work-panel.ts',{timeLabel:value=>String(value)});const panel=f.run('new MirallyWorkPanel()');panel.selected='w';
  panel.rpc=async(method,params)=>{f.calls.push({method,params});if(method==='work.run.approve')throw new Error('指纹已失效');return{work:{id:'w'},runs:[]};};
  panel.approve({id:'a',digest:'old'});await settle();assert.ok(!f.calls.some(call=>call.method==='work.run.start'));assert.match(panel.error,/指纹已失效/);
});
test('watching an external source never renders a stop or rerun of that process',()=>{
  const f=fixture('pages/mirally/work-panel.ts',{timeLabel:value=>String(value)});const panel=f.run('new MirallyWorkPanel()');panel.detail={work:{id:'w',title:'外部实验',intent:'watching',source:{path:'experiment.json'},status:'observing'},runs:[]};
  const text=stringify(panel.workDetail());assert.match(text,/核对最新状态/);assert.doesNotMatch(text,/停止这次运行/);assert.doesNotMatch(text,/暂停/);
});
test('work create and prepare retries retain request IDs until reviewed content changes',async()=>{
  const f=fixture('pages/mirally/work-panel.ts',{timeLabel:value=>String(value)});const panel=f.run('new MirallyWorkPanel()');panel.selected='w';
  panel.rpc=async(method,params)=>{f.calls.push({method,params});if(['work.create','work.run.prepare'].includes(method))throw new Error('回执未知');return{work:{id:'w'},runs:[]};};
  panel.draft={title:'实验 A',goal:'验证',source_kind:'none',path:''};panel.create({preventDefault(){}});await settle();panel.create({preventDefault(){}});await settle();
  const creates=f.calls.filter(call=>call.method==='work.create');assert.equal(creates[0].params.request_id,creates[1].params.request_id);
  panel.runDraft={script:'run.py',arguments:'["--check"]',cwd:'.',output_paths:'result.txt',timeout:'300'};panel.prepare({preventDefault(){}});await settle();panel.prepare({preventDefault(){}});await settle();
  const prepares=f.calls.filter(call=>call.method==='work.run.prepare');assert.equal(prepares[0].params.request_id,prepares[1].params.request_id);
  panel.runDraft.arguments='["--changed"]';panel.prepare({preventDefault(){}});await settle();assert.notEqual(f.calls.filter(call=>call.method==='work.run.prepare').at(-1).params.request_id,prepares[0].params.request_id);
});
test('new conversation retries reuse one creation request after an unknown reply',async()=>{
  const f=mobile(async method=>{if(method==='conversation.create')throw new Error('回执未知');return{};});f.app.createConversation();await settle();f.app.createConversation();await settle();
  const creates=f.calls.filter(call=>call.method==='conversation.create');assert.equal(creates.length,2);assert.equal(creates[0].params.request_id,creates[1].params.request_id);
});
test('native outbox restores the exact Work ownership and refuses corrupt ownership',()=>{
  const f=fixture('lib/chat/outbox-store-codec.ts',{isRecord:value=>value&&typeof value==='object'&&!Array.isArray(value),normalizeOptionalString:value=>typeof value==='string'&&value.trim()?value.trim():undefined,
    normalizeQueueMode:()=>undefined,normalizeSenderIdentity:()=>undefined,readHumanMentions:()=>undefined,registerChatMessageMetadataEnglish(){},t:value=>value});
  f.sandbox.entry={id:'input',text:'处理材料',createdAt:1,mirallyWork:{title:'整理报告',goal:'保留原材料与会话'},mirallyAllowFileWrite:true,sendState:'queued'};
  const restored=f.run('normalizeStoredQueueItem(entry)');assert.equal(restored.mirallyWork.title,'整理报告');assert.equal(restored.mirallyAllowFileWrite,true);
  f.sandbox.entry={...f.sandbox.entry,mirallyWork:{title:'',goal:9}};const invalid=f.run('normalizeStoredQueueItem(entry)');assert.equal(invalid.sendState,'failed');assert.match(invalid.sendError,/工作信息无法恢复/);
});
test('acknowledged work creation and preparation permit a new same-content user action',async()=>{
  const f=fixture('pages/mirally/work-panel.ts',{timeLabel:value=>String(value)});const panel=f.run('new MirallyWorkPanel()');
  panel.rpc=async(method,params)=>{f.calls.push({method,params});if(method==='work.create')return{work:{id:'w'+f.calls.length}};if(method==='work.run.prepare')return{approval:{id:'a'+f.calls.length,status:'pending'}};return{work:{id:panel.selected},runs:[]};};
  const draft={title:'相同标题',goal:'相同目标',source_kind:'none',path:''};panel.draft={...draft};panel.create({preventDefault(){}});await settle();panel.draft={...draft};panel.create({preventDefault(){}});await settle();
  const creates=f.calls.filter(call=>call.method==='work.create');assert.equal(creates.length,2);assert.notEqual(creates[0].params.request_id,creates[1].params.request_id);
  panel.runDraft={script:'run.py',arguments:'[]',cwd:'.',output_paths:'result.txt',timeout:'300'};
  panel.prepare({preventDefault(){}});await settle();panel.review.status='expired';panel.prepare({preventDefault(){}});await settle();
  const prepares=f.calls.filter(call=>call.method==='work.run.prepare');assert.equal(prepares.length,2);assert.notEqual(prepares[0].params.request_id,prepares[1].params.request_id);
});
test('archiving the selected conversation preserves detail metadata and supports restore outside the active filter',async()=>{
  let metadata={conversation_id:'selected',title:'正在看的会话',metadata_version:7,archived:false,pinned:false};
  const f=mobile(async(method,params)=>{
    if(method==='conversation.list')return{conversations:metadata.archived?[]:[metadata],has_more:false,next_offset:0};
    if(method==='conversation.get')return{conversation:{...metadata}};
    if(method==='conversation.history')return{messages:[],has_more:false};
    if(method==='conversation.update'){assert.equal(params.expected_version,metadata.metadata_version);metadata={...metadata,...params.patch,metadata_version:metadata.metadata_version+1};return{conversation:{...metadata}};}
    return{};
  });
  f.app.token='phone';f.app.view='conversations';f.app.currentConversation='selected';f.app.currentConversationMeta={...metadata};f.app.conversations=[{...metadata}];
  f.app.patchConversation({archived:true});await settle();
  assert.equal(f.app.currentConversationMeta.archived,true);assert.equal(f.app.currentConversationMeta.metadata_version,8);assert.equal(f.app.conversations.length,0);
  let rendered=stringify(f.app.conversationsView());assert.match(rendered,/恢复会话/);assert.doesNotMatch(rendered,/mirally-mobile-composer/);
  f.app.patchConversation({archived:false});await settle();assert.equal(f.app.currentConversationMeta.archived,false);assert.equal(f.app.currentConversationMeta.metadata_version,9);
  rendered=stringify(f.app.conversationsView());assert.match(rendered,/归档会话/);assert.match(rendered,/mirally-mobile-composer/);
});
test('opening a work conversation absent from the loaded list fetches its own metadata',async()=>{
  const f=mobile(async(method)=>method==='conversation.list'?{conversations:[],has_more:false}:method==='conversation.get'?{conversation:{conversation_id:'not-in-page',title:'工作主会话',metadata_version:19,archived:false}}:method==='conversation.history'?{messages:[{role:'assistant',text:'原来的工作记录'}]}:{});
  f.app.token='phone';f.app.view='conversations';f.app.currentConversation='not-in-page';await f.app.refresh();
  assert.equal(f.app.currentConversationMeta.title,'工作主会话');assert.equal(f.app.currentConversationMeta.metadata_version,19);assert.equal(f.app.conversations.length,0);
  const rendered=stringify(f.app.conversationsView());assert.match(rendered,/工作主会话/);assert.match(rendered,/原来的工作记录/);assert.match(rendered,/重命名/);
  assert.ok(f.calls.some(call=>call.method==='conversation.get'&&call.params.conversation_id==='not-in-page'));
});
test('every retained Mirally sidebar destination is enabled by the actual shell route filter',()=>{
  const hostSource=ts.createSourceFile('app-host.ts',readFileSync(new URL('app/app-host.ts',root),'utf8'),ts.ScriptTarget.Latest,true);
  let method;
  const find=node=>{if(ts.isMethodDeclaration(node)&&node.name.getText(hostSource)==='enabledRouteIds')method=node;ts.forEachChild(node,find);};find(hostSource);
  assert.ok(method,'The real shell must expose its route capability method');
  const executable=ts.transpileModule('const shell={'+method.getText(hostSource)+'}; shell.enabledRouteIds();',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
  const enabled=Array.from(vm.runInNewContext(executable));
  const navigation=ts.createSourceFile('app-navigation.ts',readFileSync(new URL('app-navigation.ts',root),'utf8'),ts.ScriptTarget.Latest,true);
  let sidebar;
  const locate=node=>{if(ts.isVariableDeclaration(node)&&node.name.getText(navigation)==='SIDEBAR_NAV_ROUTES')sidebar=node.initializer;ts.forEachChild(node,locate);};locate(navigation);
  assert.ok(sidebar);let literal=sidebar;while(ts.isSatisfiesExpression(literal)||ts.isAsExpression(literal))literal=literal.expression;
  assert.ok(ts.isArrayLiteralExpression(literal));const configured=literal.elements.map(item=>item.text);
  assert.ok(configured.includes('tasks'),'Work is a real configurable sidebar destination');
  for(const route of configured)assert.ok(enabled.includes(route),`The shell must not filter out the configured ${route} page`);
});
test('desktop phone webpage enrollment is an optional independent extension with no Weixin prerequisite',()=>{
  const f=fixture('pages/mirally/mobile-settings.ts',{
    renderSettingsSection:(props,content)=>html`${props.title}${props.description}${content}`,
    renderSettingsRow:props=>html`${props.title}${props.description}${props.control}`,
    renderSettingsStatus:props=>props.label,timeLabel:value=>String(value),
  });
  const panel=f.run('new MirallyMobileSettings()');panel.status={enabled:true,url:'http://127.0.0.1:1234/mobile',external_url:'',devices:[]};
  const rendered=stringify(panel.render());
  assert.match(rendered,/手机网页（可选扩展）/);
  assert.match(rendered,/只通过微信使用时，无需配置本节，也无需私人 HTTPS/);
  assert.match(rendered,/仅远程打开手机网页需要配置/);
  assert.match(rendered,/微信可直接使用/);
  assert.match(rendered,/不影响微信绑定/);
  assert.equal(panel.status.enabled,true,'Clarifying the optional path must preserve the existing service setting');
});
