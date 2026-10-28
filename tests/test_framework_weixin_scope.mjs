import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

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
  });
  const agent = {id: 'native', session: {snapshotEvents: () => messages.map(message => ({type: 'user/message', data: {message}}))}};
  product.installNativeObservers();
  return {product, hooks, calls, agent, messages};
}

test('Weixin final native tool hook denies product writes, files, scripts and arbitrary MCP/plugin tools', async () => {
  const f = fixture(); let effects = 0;
  for (const name of ['mirally_set_preference', 'mirally_create_reminder', 'mirally_create_file', 'mirally_write_workspace_file',
    'run_skill_script', 'mcp__server__readOnlyNamedButWrites', 'trusted_plugin_echo', 'mirally_web_fetch', 'desktop_click']) {
    await assert.rejects(f.hooks.get('tools/execute')({agent: f.agent, name, arguments: {}, signal: new AbortController().signal},
      () => {effects++;}), {code: 'weixin_read_only'});
  }
  assert.equal(effects, 0, 'no denied tool reaches its concrete effect');
  for (const name of ['mirally_context', 'mirally_preferences', 'mirally_reminders', 'mirally_workspace_roots', 'mirally_list_files', 'mirally_read_file', 'mirally_search_files']) {
    await f.hooks.get('tools/execute')({agent: f.agent, name, arguments: {}, signal: new AbortController().signal}, () => {effects++;});
  }
  assert.equal(effects, 7);
});

test('Weixin tool scope follows consumed input, not a later admitted desktop command', async () => {
  const f = fixture();
  f.product.activeCommands.set('native', 'desktop-next');
  assert.equal(f.product.commandPolicy(f.agent).origin, 'weixin');
  await assert.rejects(f.product.checkCommandScope(f.agent, 'mirally_set_preference'), {code: 'weixin_read_only'});
  f.messages.push({id: 'desktop-message', source: {kind: 'user'}});
  await f.product.checkCommandScope(f.agent, 'mirally_set_preference');
  assert.equal(f.product.commandPolicy(f.agent).origin, 'desktop');
});

test('session job queries remain available while actions require the current user setting', async () => {
  const f=fixture(); let enabled=false;
  f.product.rpc=async method => {assert.equal(method,'settings.get'); return {weixin_remote_control_enabled:enabled};};
  await f.product.checkCommandScope(f.agent,'mirally_job_list');
  await f.product.checkCommandScope(f.agent,'mirally_job_status');
  for (const name of ['mirally_run_command','mirally_stop_job','mirally_action','mirally_browser_open','mirally_desktop_screenshot'])
    await assert.rejects(f.product.checkCommandScope(f.agent,name),{code:'weixin_action_unavailable'});
  enabled=true;
  for (const name of ['mirally_run_command','mirally_stop_job','mirally_action','mirally_browser_open','mirally_desktop_screenshot'])
    await f.product.checkCommandScope(f.agent,name);
  await assert.rejects(f.product.checkCommandScope(f.agent,'mcp__arbitrary'),{code:'weixin_read_only'});
  enabled=false;
  await assert.rejects(f.product.checkCommandScope(f.agent,'mirally_run_command'),{code:'weixin_action_unavailable'});
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
