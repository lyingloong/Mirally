import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AsyncLocalStorage} from 'node:async_hooks';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';
import {installBasicCapabilities} from '../kernel/dsh/packages/mirally/mirally-product/src/basic-capabilities.mjs';

function fixture() {
  const hooks = new Map(), calls = [], messages = [{id: 'remote-message', source: {kind: 'user'}}];
  const product = Object.assign(Object.create(MirallyProduct.prototype), {
    nativeConversations: new Map([['native', {conversation_id: 'conversation', generation: 1}]]),
    nativeMessageCommands: new Map([['remote-message', 'weixin-remote'], ['desktop-message', 'desktop-next']]),
    activeCommands: new Map([['native', 'weixin-remote']]),
    commandPolicies: new Map([['weixin-remote', {origin: 'weixin', installation_id: 'installation', revision: 7, conversation_id: 'conversation'}],
      ['desktop-next', {origin: 'desktop'}]]),
    ctx: {on(name, callback) { hooks.set(name, callback); }},
    weixin: {async authorize(scope) { calls.push(['authorize', scope]); if (scope.revision !== 7) throw Object.assign(new Error('revoked'), {code: 'weixin_scope_revoked'}); }},
    broadcast() {},
    guard:async()=>({allow:true}),
    rpc:async method=>{assert.equal(method,'settings.get');return {weixin_remote_control_enabled:false};},
    authorizations:new AsyncLocalStorage(),
  });
  const agent = {id: 'native', session: {snapshotEvents: () => messages.map(message => ({type: 'user/message', data: {message}}))}};
  product.installNativeObservers();
  return {product, hooks, calls, agent, messages};
}

test('Weixin uses the same tool scope as desktop while retaining binding authentication', async () => {
  const f=fixture(); let effects=0;
  for (const name of ['mirally_set_preference','mirally_create_file','run_skill_script','mcp__server__write','mirally_web_search'])
    await f.hooks.get('tools/execute')({agent:f.agent,name,arguments:{},signal:new AbortController().signal},()=>{effects++;});
  assert.equal(effects,5);
  f.product.commandPolicies.get('weixin-remote').revision=6;
  await assert.rejects(f.product.checkCommandScope(f.agent,'mirally_create_file'),{code:'weixin_scope_revoked'});
});

test('scope follows consumed input rather than a later admitted desktop command', async () => {
  const f=fixture();f.product.activeCommands.set('native','desktop-next');
  assert.equal(f.product.commandPolicy(f.agent).origin,'weixin');
  await f.product.checkCommandScope(f.agent,'mirally_set_preference');
  f.messages.push({id:'desktop-message',source:{kind:'user'}});
  assert.equal(f.product.commandPolicy(f.agent).origin,'desktop');
});

test('Weixin search reaches the real capability check and the configured provider', async () => {
  const f=fixture(),tools=new Map();let web=false,queries=[];
  f.product.ctx.tools={register:tool=>tools.set(tool.name,tool)};
  f.product.rpc=async method=>method==='settings.get'?{weixin_remote_control_enabled:true}
    :method==='capabilities.get'?{web_enabled:web,revision:1,search_connection:'chat'}
    :method==='internal.settings.get'?{model:{provider:'fixture',model_id:'fixture'}}:null;
  installBasicCapabilities(f.product,{webProviders:{deepseek:()=>({search:async args=>{queries.push(args);return {results:[{title:'来源'}]};}})}});
  const exec={agent:f.agent,name:'mirally_web_search',arguments:{query:'测试网页',count:2},signal:new AbortController().signal};
  const invoke=()=>f.hooks.get('tools/execute')(exec,()=>tools.get(exec.name).execute(exec.arguments,exec));
  await assert.rejects(invoke(),/网页研究尚未开启/);assert.deepEqual(queries,[]);
  web=true;assert.equal((await invoke()).results[0].title,'来源');assert.deepEqual(queries,[{query:'测试网页',count:2}]);
});

test('Weixin web result is withheld when remote authority is revoked while awaiting the provider', async () => {
  const f=fixture();let release,started;
  const begun=new Promise(resolve=>started=resolve);
  const exec={agent:f.agent,name:'mirally_web_fetch',arguments:{url:'https://example.com'},signal:new AbortController().signal};
  const pending=f.hooks.get('tools/execute')(exec,async()=>{started();return new Promise(resolve=>release=resolve);});
  await begun;f.product.commandPolicies.get('weixin-remote').revision=6;release({text:'late'});
  await assert.rejects(pending,{code:'weixin_scope_revoked'});
});

test('scheduled Weixin turns retain channel authority and allow product writes', async () => {
  const f=fixture();const policy=f.product.commandPolicies.get('weixin-remote');
  Object.assign(policy,{schedule_id:'schedule-1',target_conversation_id:'target',command_id:'weixin-remote'});
  let route={installation_id:'installation',revision:7,conversation_id:'conversation'}, enabled=true;
  f.product.rpc=async()=>({weixin_remote_control_enabled:enabled});
  f.product.scheduledActions={authorizeWeixinOccurrence:async()=>({...route})};
  await f.product.checkCommandScope(f.agent,'mirally_run_command');
  await f.product.checkCommandScope(f.agent,'mirally_set_preference');
  await f.product.checkCommandScope(f.agent,'mirally_create_event');
  await f.product.checkCommandScope(f.agent,'mirally_write_workspace_file');
  route.revision++;await assert.rejects(f.product.checkCommandScope(f.agent,'mirally_run_command'),{code:'schedule_scope_revoked'});
  route.revision--;enabled=false;await f.product.checkCommandScope(f.agent,'mirally_run_command');
});

test('Weixin pre-step admission recognizes a fresh command before its user/message is appended after restart', async () => {
  const f = fixture();
  f.product.commandPolicies.delete('weixin-remote');
  f.product.commandPolicies.set('weixin-new', {origin: 'weixin', installation_id: 'installation', revision: 7, conversation_id: 'conversation'});
  f.product.activeCommands.set('native', 'weixin-new');
  await f.product.checkCommandScope(f.agent, undefined, {admission: true});
  await assert.rejects(f.product.checkCommandScope(f.agent, 'mirally_context'), {code: 'weixin_scope_revoked'});
});

test('Weixin revocation rejects even allowed read tools; unknown recovered remote command fails closed', async () => {
  const f = fixture();
  f.product.commandPolicies.get('weixin-remote').revision = 6;
  await assert.rejects(f.product.checkCommandScope(f.agent, 'mirally_context'), {code: 'weixin_scope_revoked'});
  f.product.commandPolicies.clear();
  await assert.rejects(f.product.checkCommandScope(f.agent, 'mirally_context'), {code: 'weixin_scope_revoked'});
  assert.equal(f.product.commandPolicy(undefined), null);
});

test('Public input cannot impersonate a trusted Weixin command or expose internal channel credentials', async () => {
  const f = fixture();
  await assert.rejects(f.product.send('conversation', {text: 'hello', commandId: 'weixin-forged'}), {code: 'invalid_request'});
  await assert.rejects(f.product.send('conversation', {text: 'hello', commandId: 'ordinary'}, {origin: 'weixin'}), {code: 'invalid_request'});
  await assert.rejects(f.product.publicRpc('channel.weixin.get'), {code: 'method_not_allowed'});
  await assert.rejects(f.product.publicRpc('internal.conversation.continuation'), {code: 'method_not_allowed'});
});

test('Weixin reply projection selects final assistant text for the exact command, never reasoning, tools or another turn', async () => {
  const f = fixture();
  f.product.rpc = async () => ({commands: [{command_id: 'weixin-remote', generation: 1, state: 'completed'}]});
  f.product.conversation = async () => ({generation: 1, frozen: false, archived: false});
  f.product.history = async () => ({messages: [
    {role: 'assistant', command_id: 'desktop-next', text: 'private other reply'},
    {role: 'toolResult', command_id: 'weixin-remote', text: 'private tool payload'},
    {role: 'assistant', command_id: 'weixin-remote', text: '', content: [{type: 'thinking', thinking: 'private reasoning'}]},
    {role: 'assistant', command_id: 'weixin-remote', id: 'final', text: 'final reply'},
  ]});
  const result = await f.product.weixinResult({conversation_id: 'conversation', command_id: 'weixin-remote'});
  assert.equal(result.text, 'final reply'); assert.equal(result.ref.message_id, 'final');
  f.product.conversation = async () => ({generation: 2, frozen: false});
  assert.deepEqual(await f.product.weixinResult({conversation_id: 'conversation', command_id: 'weixin-remote'}), {state: 'cancelled'});
});
