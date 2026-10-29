/** Work conversion uses the actual conversation menu and the normal chat owner. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const ts=createRequire(new URL('../frontend/control-ui/package.json',import.meta.url))('typescript');
function compile(path) {
  const source=readFileSync(new URL(path,import.meta.url),'utf8');
  return ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,experimentalDecorators:true,useDefineForClassFields:false}}).outputText
    .replace(/^import[\s\S]*?;\s*/gm,'').replace(/^export /gm,'');
}
const menuCode=compile('../frontend/control-ui/ui/src/pages/chat/components/chat-header-session-menu.ts');
const handlerCode=compile('../frontend/control-ui/ui/src/pages/chat/chat-pane-session-menu.ts');
const requestCode=compile('../frontend/control-ui/ui/src/pages/mirally/work-conversation-request.ts');
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const html=(strings,...values)=>({strings,values});
const stringify=value=>value==null||typeof value==='symbol'||typeof value==='function'?'':Array.isArray(value)?value.map(stringify).join(''):value?.strings?value.strings.map((part,i)=>part+stringify(value.values[i])).join(''):String(value);

function menuFixture(handler) {
  const actions=[], calls=[], timers=[];
  const sandbox={html,nothing:Symbol('nothing'),property:()=>()=>{},state:()=>()=>{},
    OpenClawLightDomElement:class {isConnected=true; connectedCallback(){} disconnectedCallback(){}},
    EMPTY_SESSION_MENU_DATA:{label:'对话',sessionId:'native'},
    SessionMenuActions:class {handleSelect(){return false;}},
    compactSessionMenuViewForValue:()=>undefined,renderMirallySessionOrganization:()=>'',
    icons:new Proxy({},{get:(_target,key)=>String(key)}),t:key=>key,
    customElements:{get(){},define(){}},window:{addEventListener(){},removeEventListener(){}},
    document:{hidden:false,addEventListener(){},removeEventListener(){}},
    getProductHost:()=>({conversationIdForSession:key=>key.replace('agent:main:','')}),
    rpcProduct:async(method,params)=>{calls.push({method,params:JSON.parse(JSON.stringify(params))});return handler(method,params);},
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){}};
  vm.runInNewContext(menuCode+'\nglobalThis.Page=ChatHeaderSessionMenu;',sandbox);
  const page=new sandbox.Page();page.mirallySessionKey='agent:main:one';page.onAction=action=>actions.push(JSON.parse(JSON.stringify(action)));
  const select=()=>page.handleSelect({detail:{item:{value:'mirally-work'}},currentTarget:{open:true},preventDefault(){}});
  return {page,actions,calls,select};
}

function handlerFixture(send) {
  const calls=[], errors=[], navigations=[],row={key:'agent:main:one',sessionId:'native-one'};
  const gateway={snapshot:{phase:'connected',client:{id:'first'}}};
  const state={connected:true,sessionKey:row.key,row,chatMessage:'未发送的草稿',chatAttachments:[{id:'material'}],chatReplyTarget:{sourceMessageId:'reply'},chatQueue:[],
    handleSendChat:async(...args)=>{calls.push(args);return send(...args);}};
  const sandbox={ChatPaneContext:class {connectionGeneration=1;headerOutcomeOwner='first:one';isConnected=true;presented=true;requestUpdate(){} ownsHeaderOutcome(owner){return this.presented&&owner===this.headerOutcomeOwner;}},
    areUiSessionKeysEquivalent:(left,right)=>left===right,selectedChatSessionRow:current=>current.row,
    formatUiError:error=>error instanceof Error?error.message:String(error),t:key=>key};
  vm.runInNewContext(requestCode+'\n'+handlerCode+'\nglobalThis.Page=ChatPaneSessionMenu;',sandbox);
  const page=new sandbox.Page();Object.assign(page,{state,context:{gateway,sessions:{},agentSelection:{state:{}},navigate:(route,options)=>navigations.push({route,options})},headerSessionMutationAbortController:new AbortController()});
  const follow=()=>page.handleHeaderSessionAction({kind:'follow-work'},row);
  return {page,state,row,calls,errors,navigations,follow};
}

test('work menu only offers conversion after a real empty lookup, and opens a unique linked work',async()=>{
  let works=[];
  const f=menuFixture(()=>({works}));
  await f.page.loadWork();assert.match(stringify(f.page.render()),/加入工作/);
  f.select();assert.deepEqual(f.actions,[{kind:'follow-work'}]);
  works=[{id:'work-gpu'}];await f.page.loadWork();
  assert.match(stringify(f.page.render()),/查看关联工作/);assert.match(stringify(f.page.render()),/mirally-chat-work-badge/);
  f.select();assert.deepEqual(f.actions.at(-1),{kind:'view-work',workId:'work-gpu'});
  assert.deepEqual(f.calls.map(x=>x.params.conversation_id),['one','one']);
  assert.ok(f.calls.every(call=>call.params.include_archived===true));
});

test('multiple legacy works open the list; lookup errors never pretend there is no work',async()=>{
  let response={works:[{id:'work-one'}]};
  const f=menuFixture(()=>{if(response instanceof Error)throw response;return response;});
  await f.page.loadWork();f.select();assert.deepEqual(f.actions.at(-1),{kind:'view-work',workId:'work-one'});
  response={works:[{id:'work-one'},{id:'work-two'}]};
  await f.page.loadWork();f.select();assert.deepEqual(f.actions.at(-1),{kind:'view-work'});
  response={works:[{id:'work-one'}],has_more:true};
  await f.page.loadWork();f.select();assert.deepEqual(f.actions.at(-1),{kind:'view-work'});
  response=new Error('connection');await f.page.loadWork();
  assert.match(stringify(f.page.render()),/重新读取工作状态/);assert.doesNotMatch(stringify(f.page.render()),/mirally-chat-work-badge/);
  assert.equal(f.page.currentWorkStatus,'unavailable');
});

test('an archived work is recoverable from its actual detail, without an active badge or duplicate creation',async()=>{
  let response={works:[{id:'archived-work',intent:'archived'}]};
  const f=menuFixture(()=>response);
  await f.page.loadWork();assert.match(stringify(f.page.render()),/查看已归档工作/);
  assert.doesNotMatch(stringify(f.page.render()),/mirally-chat-work-badge|加入工作/);
  f.select();assert.deepEqual(f.actions.at(-1),{kind:'view-work',workId:'archived-work'});
  response={works:[{id:'archived-work',intent:'archived'}],has_more:true};
  await f.page.loadWork();assert.equal(f.page.currentWorkStatus,'ambiguous');
  assert.doesNotMatch(stringify(f.page.render()),/mirally-chat-work-badge|加入工作/);
  f.select();assert.deepEqual(f.actions.at(-1),{kind:'view-work'});
});

test('old work lookup never paints its badge or association on a newer conversation',async()=>{
  const old=deferred(),f=menuFixture((_method,params)=>params.conversation_id==='one'?old.promise:{works:[]});
  const pending=f.page.loadWork();f.page.mirallySessionKey='agent:main:two';
  assert.equal(f.page.currentWorkStatus,'loading');assert.doesNotMatch(stringify(f.page.render()),/mirally-chat-work-badge/);
  await f.page.loadWork();old.resolve({works:[{id:'old-work'}]});await pending;
  assert.equal(f.page.currentWorkStatus,'none');f.select();assert.deepEqual(f.actions,[{kind:'follow-work'}]);
});

test('conversion sends one ordinary turn with no ambient draft, attachments or reply target',async()=>{
  const pending=deferred(),f=handlerFixture(()=>pending.promise),attachments=f.state.chatAttachments,reply=f.state.chatReplyTarget;
  const first=f.follow();await f.follow();assert.equal(f.calls.length,1);
  assert.match(f.calls[0][0],/当前的对话/);assert.match(f.calls[0][0],/加入工作列表/);
  assert.equal(f.calls[0][1].replyTargetOverride,null);assert.equal(f.calls[0][1].followUpMode,'queue');
  assert.equal(f.state.chatMessage,'未发送的草稿');assert.strictEqual(f.state.chatAttachments,attachments);assert.strictEqual(f.state.chatReplyTarget,reply);
  pending.resolve(true);await first;assert.equal(f.state.chatError,undefined);
});

test('an archived, running or already queued conversion cannot submit another turn',async()=>{
  for(const setup of [f=>{f.row.archived=true;},f=>{f.state.chatRunId='active';},f=>{f.state.connected=false;},f=>{f.state.chatQueue.push({text:'请根据我们当前的对话整理一项工作并加入工作列表，结合已有内容确定简洁的名称和目标；如果已有对应工作，请继续维护。'});}]){
    const f=handlerFixture(()=>true);setup(f);await f.follow();assert.equal(f.calls.length,0);
  }
});

test('late failure cannot appear in a different conversation; work navigation uses the correct route',async()=>{
  const pending=deferred(),f=handlerFixture(()=>pending.promise),first=f.follow();
  f.state.sessionKey='agent:main:two';f.state.row={key:f.state.sessionKey,sessionId:'native-two'};f.state.chatMessage='新草稿';
  pending.reject(new Error('old failure'));await first;assert.equal(f.state.chatError,undefined);assert.equal(f.state.chatMessage,'新草稿');
  await f.page.handleHeaderSessionAction({kind:'view-work',workId:'work-gpu'},f.row);assert.deepEqual(f.navigations,[]);
  f.state.sessionKey=f.row.key;f.state.row=f.row;
  await f.page.handleHeaderSessionAction({kind:'view-work',workId:'work-gpu'},f.row);
  assert.equal(f.navigations[0].route,'tasks');assert.equal(f.navigations[0].options.search,'?work=work-gpu');
  await f.page.handleHeaderSessionAction({kind:'view-work'},f.row);
  assert.equal(f.navigations[1].options.search,'?filter=all');
});
