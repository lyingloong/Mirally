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
function mobile(handler=async()=>({})){const api=fixture('mobile/api.ts');const origin=fixture('lib/chat/input-origin.ts');const f=fixture('mobile/app.ts',{
  ...Object.fromEntries(['MobileApiError','DEFAULT_MOBILE_PREFERENCES','mobileNavigation','pairingCodeFromHash','timeLabel'].map(name=>[name,api.run(name)])),
  inputOriginLabel:origin.run('inputOriginLabel'),createMobileRpc:()=>async(method,params={})=>{f.calls.push({method,params});return handler(method,params);},readMobileToken:()=>'',storeMobileToken(){},pairMobile:async()=>({token:'phone-token',device:{id:'phone'}}),
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
test('old work home and navigation resolve to conversation and omit work controls',async()=>{
  const f=mobile(async method=>method==='mobile.preferences.get'?{effective:{home:'work',navigation:['work','inbox','conversations','settings'],hidden:[]},revision:3}:{conversations:[]});
  f.app.token='phone';await f.app.boot();await settle();
  assert.equal(f.app.view,'conversations');
  const navigation=Array.from(f.sandbox.mobileNavigation(f.app.preferences));assert.deepEqual(navigation,['inbox','conversations','settings']);
  assert.doesNotMatch(stringify(f.app.settingsView()),/工作排序|工作通知|显示已结束或归档的工作/);
  assert.ok(f.calls.every(call=>!call.method.startsWith('work.')));
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
test('message origin labels follow recorded user channel and current surface',()=>{
  const f=fixture('lib/chat/input-origin.ts');const label=f.run('inputOriginLabel');
  assert.equal(label({role:'user',input_origin:'weixin'},'desktop'),'微信');
  assert.equal(label({role:'user',input_origin:'schedule'},'desktop'),'定时任务');
  assert.equal(label({role:'user',input_origin:'schedule'},'mobile'),'定时任务');
  assert.equal(label({role:'user',input_origin:'mobile'},'desktop'),'手机网页');
  assert.equal(label({role:'user',input_origin:'desktop'},'desktop'),null);
  assert.equal(label({role:'user',input_origin:'desktop'},'mobile'),'电脑');
  assert.equal(label({role:'user',input_origin:'mobile'},'mobile'),null);
  assert.equal(label({role:'assistant',input_origin:'weixin'},'desktop'),null);
  assert.equal(label({role:'user'},'mobile'),null);
  assert.equal(label({role:'user',input_origin:'other'},'mobile'),null);
  const m=mobile();m.app.currentConversation='c';m.app.messages=[{role:'user',text:'桌面正文',input_origin:'desktop'},{role:'user',text:'微信正文',input_origin:'weixin'},{role:'user',text:'未知正文'},{role:'assistant',text:'回应',input_origin:'weixin'}];
  const rendered=stringify(m.app.conversationsView());assert.match(rendered,/消息来自电脑/);assert.match(rendered,/消息来自微信/);assert.equal((rendered.match(/消息来自/g)||[]).length,2);
});
test('new conversation retries reuse one creation request after an unknown reply',async()=>{
  const f=mobile(async method=>{if(method==='conversation.create')throw new Error('回执未知');return{};});f.app.createConversation();await settle();f.app.createConversation();await settle();
  const creates=f.calls.filter(call=>call.method==='conversation.create');assert.equal(creates.length,2);assert.equal(creates[0].params.request_id,creates[1].params.request_id);
});
test('native outbox preserves file permission and fails closed on removed work requests',()=>{
  const f=fixture('lib/chat/outbox-store-codec.ts',{isRecord:value=>value&&typeof value==='object'&&!Array.isArray(value),normalizeOptionalString:value=>typeof value==='string'&&value.trim()?value.trim():undefined,
    normalizeQueueMode:()=>undefined,normalizeSenderIdentity:()=>undefined,readHumanMentions:()=>undefined,registerChatMessageMetadataEnglish(){},t:value=>value});
  f.sandbox.entry={id:'input',text:'处理材料',createdAt:1,mirallyAllowFileWrite:true,sendState:'queued'};
  const restored=f.run('normalizeStoredQueueItem(entry)');assert.equal(restored.mirallyAllowFileWrite,true);assert.equal(restored.text,'处理材料');
  f.sandbox.entry={...f.sandbox.entry,mirallyWork:{title:'整理报告',goal:'原任务'}};const invalid=f.run('normalizeStoredQueueItem(entry)');assert.equal(invalid.sendState,'failed');assert.match(invalid.sendError,/旧请求已失效/);assert.equal(invalid.text,'处理材料');
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
test('opening a conversation absent from the loaded list fetches its own metadata',async()=>{
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
  assert.ok(!configured.includes('tasks'),'Independent work must not appear in the sidebar');
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
