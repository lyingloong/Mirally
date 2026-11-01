/** Public capture controls and submission races use the real product methods. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

function productFixture() {
  const calls = [], product = Object.create(MirallyProduct.prototype);
  Object.assign(product, {
    handles: new Map(), clients: new Set(),
    async rpc(method, params) { calls.push({method, params}); return method === 'conversation.list' ? {conversations: []} : {accepted: true}; },
    async refreshModels() { calls.push({method: 'refresh-models'}); },
  });
  return {product, calls};
}

test('public generic settings cannot bypass any Activity scope or lifecycle confirmation', async () => {
  const f = productFixture();
  for (const patch of [
    {activity_enabled: true}, {activity_enabled: false}, {activity_paused: false},
    {activity_capture: {clipboard_enabled: true, input_text_enabled: true}},
    {activity_config_path: 'other-private-profile.yaml'},
    {memory_enabled: true, activity_enabled: true},
  ]) {
    await assert.rejects(f.product.publicRpc('settings.update', {patch}), error =>
      error.status === 403 && error.code === 'confirmation_required');
  }
  assert.deepEqual(f.calls, []);
});

test('ordinary model and Memory settings retain their public route', async () => {
  const f = productFixture(), params = {patch: {memory_enabled: false, model: {model_id: 'fixture-model'}}};
  assert.deepEqual(await f.product.publicRpc('settings.update', params), {accepted: true});
  assert.deepEqual(f.calls[0], {method: 'settings.update', params});
  assert.equal(f.calls.filter(call => call.method === 'refresh-models').length, 1);
  assert.equal(f.product.lastInitiativeCheck, 0);
});

test('dedicated capture APIs forward scope and confirmation to the authoritative sidecar', async () => {
  const f = productFixture();
  const configure = {patch: {visual_scope: 'foreground', clipboard_enabled: false}};
  const start = {confirmed: true, expected_fingerprint: 'explicitly-reviewed-scope'};
  await f.product.publicRpc('activity.configure', configure);
  await f.product.publicRpc('activity.start', start);
  assert.deepEqual(f.calls.filter(call => call.method.startsWith('activity.')), [
    {method: 'activity.configure', params: configure}, {method: 'activity.start', params: start},
  ]);
});

function busyProductFixture() {
  const calls = [], policy = {allowFiles: false}, product = Object.create(MirallyProduct.prototype);
  product.commandPolicies = new Map();
  const conversation = {conversation_id: 'one', session_id: 'native-one', generation: 1};
  const handle = {agent: {status: 'running', session: {snapshotEvents: () => []},
    followup() { throw new Error('A completed command must not be dispatched again'); }}};
  Object.assign(product, {
    serial: (_id, operation) => operation(), materials: {content: async () => []},
    async refreshModels() {}, models: {requireOptions: () => ({})},
    conversation: async () => conversation, ensureNative: async () => ({conversation, handle}),
    requestPolicies: new Map([['native-one', policy]]), activeCommands: new Map([['native-one', 'current-read-only']]),
    async rpc(method, params) {
      calls.push({method, params});
      if (method === 'command.list') {
        assert.equal(params.conversation_id, 'one');
        assert.equal(typeof params.command_id, 'string', 'Busy replay must query its exact command beyond the latest 200');
        return {commands: params.command_id === 'completed-with-file' ? [{command_id: 'completed-with-file', state: 'completed'}] : []};
      }
      if (method === 'command.admit') return {command_id: params.command_id, state: 'completed', should_dispatch: false};
      throw new Error('Replay must not produce a new command or external effect: ' + method);
    },
  });
  return {product, calls, policy};
}

test('a completed file command replays during a later read-only run without changing its policy', async () => {
  const f = busyProductFixture();
  const reply = await f.product.send('one', {commandId: 'completed-with-file', text: 'save the finished result', allow_file_write: true});
  assert.equal(reply.replay, true); assert.equal(reply.accepted, true);
  assert.deepEqual(f.calls.map(call => call.method), ['command.list', 'command.admit']);
  assert.deepEqual(f.calls[0].params, {conversation_id: 'one', command_id: 'completed-with-file'});
  assert.strictEqual(f.product.requestPolicies.get('native-one'), f.policy);
  assert.equal(f.policy.allowFiles, false);
  assert.equal(f.product.activeCommands.get('native-one'), 'current-read-only');
});

test('new file permission cannot escalate an active read-only run or leave an orphaned admission', async () => {
  const f = busyProductFixture();
  await assert.rejects(f.product.send('one', {commandId: 'new-file-command', text: 'save a new file', allow_file_write: true}),
    error => error.status === 409 && error.code === 'confirmation_required');
  assert.deepEqual(f.calls.map(call => call.method), ['command.list']);
  assert.deepEqual(f.calls[0].params, {conversation_id: 'one', command_id: 'new-file-command'});
  assert.strictEqual(f.product.requestPolicies.get('native-one'), f.policy);
  assert.equal(f.product.activeCommands.get('native-one'), 'current-read-only');
});

test('native observers expose attempt start, text and abandonment consistently to the owned UI', () => {
  const callbacks = new Map(), frames = [], product = Object.create(MirallyProduct.prototype);
  Object.assign(product, {ctx: {on: (type, callback) => callbacks.set(type, callback)},
    nativeConversations: new Map([['native-one', {conversation_id: 'one'}]]),
    broadcast: (type, payload) => frames.push({type, ...payload}),
  });
  product.installNativeObservers();
  const publish = frame => callbacks.get('agent/assistant-stream')({agent: {id: 'native-one'}, frame});
  publish({type: 'start', attemptId: 'attempt-a'});
  publish({type: 'chunk', attemptId: 'attempt-a', chunk: {type: 'text-delta', text: 'streamed text'}});
  publish({type: 'end', attemptId: 'attempt-a', outcome: {kind: 'abandoned'}});
  assert.deepEqual(frames, [
    {type: 'assistant-start', session_id: 'one', native_session_id: 'native-one', attempt_id: 'attempt-a'},
    {type: 'token', session_id: 'one', native_session_id: 'native-one', attempt_id: 'attempt-a', text: 'streamed text'},
    {type: 'assistant-end', session_id: 'one', native_session_id: 'native-one', attempt_id: 'attempt-a', outcome: 'abandoned'},
  ]);
});
