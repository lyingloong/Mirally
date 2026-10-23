/** Public capture controls and submission races use the real product methods. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

function productFixture() {
  const calls = [], product = Object.create(MirallyProduct.prototype);
  Object.assign(product, {
    handles: new Map(),
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

function submissionFixture() {
  // Evaluate the actual function, without booting a page, host, collector or model.
  const source = readFileSync(new URL('../mirally/framework_ui/app.js', import.meta.url), 'utf8');
  const start = source.indexOf('async function sendMessage('), end = source.indexOf('\nasync function connectEvents', start);
  assert.ok(start >= 0 && end > start, 'The application must expose its real submission function');
  const a = {id: 'material-a', readingState: 'ready'}, b = {id: 'material-b', readingState: 'ready'};
  const state = {current: {id: 'one'}, submitting: false, messages: [], attachments: [a]};
  const nodes = {message: {value: 'read A'}, messages: {querySelector: () => null}, send: {}, 'allow-files': {checked: false}};
  const drafts = new Map(), requests = []; let finish, fail;
  const sandbox = {state, drafts, $: id => nodes[id], conversationId: () => state.current.id,
    request(_path, options) { requests.push(JSON.parse(options.body)); return new Promise((resolve, reject) => { finish = resolve; fail = reject; }); },
    crypto: {randomUUID: () => 'fixture-command'}, messageNode() {}, setBusy() {}, notice() {},
    renderAttachments() {}, emitState() {}, errorText: error => error.message, async loadHistory() {},
  };
  vm.runInNewContext(source.slice(start, end) + '\nglobalThis.submit = sendMessage;', sandbox);
  return {state, nodes, drafts, requests, a, b, submit: sandbox.submit,
    succeed: () => finish({accepted: true}), reject: () => fail(new Error('fixture admission failure'))};
}

test('a material added while admission is pending remains in the draft and was never sent implicitly', async () => {
  const f = submissionFixture(), pending = f.submit();
  f.state.attachments.push(f.b);
  f.succeed(); await pending;
  assert.deepEqual(f.requests[0].materials, ['material-a']);
  assert.deepEqual(f.state.attachments.map(item => item.id), ['material-b']);
  assert.equal(f.nodes.message.value, '');
});

test('editing the draft while admission is pending retains the newer text and material batch', async () => {
  const f = submissionFixture(), pending = f.submit();
  f.nodes.message.value = 'new unsent draft'; f.state.attachments.push(f.b);
  f.succeed(); await pending;
  assert.equal(f.requests[0].text, 'read A');
  assert.equal(f.nodes.message.value, 'new unsent draft');
  assert.deepEqual(f.state.attachments.map(item => item.id), ['material-b']);
});

test('switching conversations during admission only retires the submitted material in its original draft', async () => {
  const f = submissionFixture(), pending = f.submit();
  f.drafts.set('one', {text: 'unsent edits in one', attachments: [f.a, f.b]});
  f.state.current = {id: 'two'};
  f.state.attachments = [{id: 'material-c', readingState: 'ready'}]; f.nodes.message.value = 'draft in two';
  f.succeed(); await pending;
  assert.deepEqual(f.drafts.get('one').attachments.map(item => item.id), ['material-b']);
  assert.equal(f.drafts.get('one').text, 'unsent edits in one');
  assert.deepEqual(f.state.attachments.map(item => item.id), ['material-c']);
  assert.equal(f.nodes.message.value, 'draft in two');
});

test('failed admission preserves both the submitted and newly added materials for explicit retry', async () => {
  const f = submissionFixture(), pending = f.submit();
  f.state.attachments.push(f.b); f.reject();
  await assert.rejects(pending, /fixture admission failure/);
  assert.deepEqual(f.state.attachments.map(item => item.id), ['material-a', 'material-b']);
  assert.equal(f.nodes.message.value, 'read A');
  assert.equal(f.state.submitting, false);
});

function streamFixture() {
  const source = readFileSync(new URL('../mirally/framework_ui/app.js', import.meta.url), 'utf8');
  const start = source.indexOf('let partialMessage ='), end = source.indexOf('\nasync function showPage', start);
  assert.ok(start >= 0 && end > start);
  const state = {current: {id: 'one'}, messages: [], tools: [], runId: 'command-a', busy: true, lastCompletion: null};
  const rendered = [], historyReads = [], nativeHistory = [];
  const node = {scrollTop: 0, scrollHeight: 0};
  const messageNode = (role, text) => { const item = {role, textContent: text}; rendered.push(item); return item; };
  const sandbox = {state, $: () => node, conversationId: () => 'one', sessionId: session => session.id,
    messageNode, emitState() {}, setBusy: value => { state.busy = value; }, notice() {},
    async refreshInbox() {}, async refreshActions() {}, async refreshArtifacts() {}, async refreshSessions() {},
    async loadHistory() { historyReads.push(true); state.messages = nativeHistory.map(item => ({...item})); rendered.length = 0; for (const item of state.messages) messageNode(item.role, item.text); },
  };
  vm.runInNewContext(source.slice(start, end) + '\nglobalThis.consume = consumeEvent;', sandbox);
  return {state, rendered, historyReads, nativeHistory,
    event: value => sandbox.consume({session_id: 'one', ...value}),
    user(text) { const message = {role: 'user', text, id: 'command-b'}; state.messages.push(message); messageNode('user', text); state.runId = 'command-b'; return message; },
  };
}

test('stream boundaries preserve an old attempt across user supplement and separate the next native response', async () => {
  const f = streamFixture();
  await f.event({type: 'assistant-start', attempt_id: 'attempt-a'});
  await f.event({type: 'token', attempt_id: 'attempt-a', text: 'A first'});
  const user = f.user('B supplement');
  await f.event({type: 'token', attempt_id: 'attempt-a', text: ' and continued'});
  assert.equal(f.state.messages[0].text, 'A first and continued');
  assert.strictEqual(f.state.messages[1], user);
  assert.deepEqual(f.rendered.map(item => item.textContent), ['A first and continued', 'B supplement']);
  await f.event({type: 'assistant-end', attempt_id: 'attempt-a', outcome: 'committed'});
  assert.equal(f.state.lastCompletion, null); assert.equal(f.state.busy, true);
  await f.event({type: 'assistant-start', attempt_id: 'attempt-c'});
  await f.event({type: 'token', attempt_id: 'attempt-c', text: 'C handles the supplement'});
  assert.deepEqual(f.rendered.map(item => item.textContent), ['A first and continued', 'B supplement', 'C handles the supplement']);
  assert.deepEqual(f.state.messages.map(item => item.text), ['A first and continued', 'B supplement', 'C handles the supplement']);
  assert.notEqual(f.state.messages[0].id, f.state.messages[2].id);
  assert.equal(f.historyReads.length, 0);
});

test('an abandoned attempt reloads native history without declaring completion or retaining abandoned text', async () => {
  const f = streamFixture(), user = f.user('original request'); f.nativeHistory.push({...user});
  await f.event({type: 'assistant-start', attempt_id: 'attempt-abandoned'});
  await f.event({type: 'token', attempt_id: 'attempt-abandoned', text: 'uncommitted partial'});
  await f.event({type: 'assistant-end', attempt_id: 'attempt-abandoned', outcome: 'abandoned'});
  assert.equal(f.historyReads.length, 1);
  assert.deepEqual(f.state.messages.map(item => item.text), ['original request']);
  assert.deepEqual(f.rendered.map(item => item.textContent), ['original request']);
  assert.equal(f.state.lastCompletion, null); assert.equal(f.state.busy, true);
  await f.event({type: 'assistant-start', attempt_id: 'attempt-retry'});
  await f.event({type: 'token', attempt_id: 'attempt-retry', text: 'new attempt'});
  assert.deepEqual(f.state.messages.map(item => item.text), ['original request', 'new attempt']);
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
