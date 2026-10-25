/** Retained UI transport acceptance: real client, no browser or provider calls.
 * Run: node --test tests/test_framework_original_client.mjs (Node 24+).
 */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {stripTypeScriptTypes} from 'node:module';
import {readFileSync, existsSync} from 'node:fs';
import {mkdtemp, readFile, readdir, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import vm from 'node:vm';

const root = fileURLToPath(new URL('../', import.meta.url));
const clientPath = join(root, 'frontend/control-ui/ui/src/app/mirally-client.ts');
// Transform only this boundary module. Its Gateway dependencies are type-only;
// tests do not substitute a copied transport or need a bundled UI installation.
const readModelsPath = join(root, 'frontend/control-ui/ui/src/app/mirally-read-models.ts');
const readModelsModule = 'data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(readFileSync(readModelsPath, 'utf8'), {mode: 'transform', sourceUrl: readModelsPath})).toString('base64');
const clientSource = readFileSync(clientPath, 'utf8').replace(/(["'])\.\/mirally-read-models\.(?:ts|js)\1/g, () => JSON.stringify(readModelsModule));
const source = stripTypeScriptTypes(clientSource, {mode: 'transform', sourceUrl: clientPath});
const {MirallyBrowserClient} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const nativeFetch = globalThis.fetch;
const conversationKey = id => `agent:main:${id}`;
const reply = (data, status = 200) => Response.json(data, {status});
const textOf = message => message?.content?.filter(item => item.type === 'text').map(item => item.text).join('');
async function until(predicate, label, timeout = 3000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = predicate(); if (value) return value; await delay(5); }
  throw new Error('Timed out: ' + label);
}

function browserEnvironment(t, origin = 'http://127.0.0.1:37281') {
  const globals = ['window', 'document', 'location', 'fetch'];
  const previous = new Map(globals.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const domEvents = [], routes = [];
  const document = {getElementById: () => ({}), querySelector: () => ({context: {navigate: (...args) => routes.push(args)}})};
  const window = {dispatchEvent(event) { domEvents.push(event); }};
  const location = {origin, hash: '#token=fragment-token'};
  for (const [name, value] of Object.entries({window, document, location})) Object.defineProperty(globalThis, name, {value, writable: true, configurable: true});
  t.after(() => { for (const [name, descriptor] of previous) { if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; } });
  return {window, document, location, domEvents, routes};
}

function fixture(t, options = {}) {
  const browser = browserEnvironment(t), calls = [], events = [], hellos = [], closes = [], streams = [];
  const sessions = [{id: 'one', conversation_id: 'one', session_id: 'native-one', title: '生活计划', status: 'idle'},
    {id: 'two', conversation_id: 'two', session_id: 'native-two', title: '另一会话', status: 'idle'}];
  const histories = new Map([['one', []], ['two', []]]);
  const settings = {model: {provider: 'fixture', model_id: 'fixture-model', base_url: 'https://model.invalid/v1', api_format: 'openai-chat',
    image_enabled: true, reasoning_enabled: true, max_tokens: 4096, api_key: 'private-provider-secret', api_key_configured: true}};
  const f = {...browser, calls, events, hellos, closes, streams, sessions, histories, settings, override: null};
  globalThis.fetch = async (input, init = {}) => {
    const target = new URL(input, browser.location.origin), path = target.pathname;
    const call = {path, target, init, headers: new Headers(init.headers), body: init.body ? JSON.parse(init.body) : undefined}; calls.push(call);
    const override = await f.override?.(call); if (override !== undefined) return override;
    if (path === '/api/auth') return reply({authenticated: true});
    if (path === '/api/health') return reply({ready: true, mode: 'test', product: {kernel: 'dsh'}});
    if (path === '/api/ui-preferences') return reply({preferences: {}});
    if (path === '/api/events') {
      return new Response(new ReadableStream({start(controller) {
        streams.push(controller); init.signal?.addEventListener('abort', () => controller.error(init.signal.reason), {once: true});
      }}), {headers: {'Content-Type': 'text/event-stream'}});
    }
    if (path === '/api/sessions') {
      if (init.method !== 'POST') return reply({sessions});
      const session = {id: 'created', conversation_id: 'created', session_id: 'native-created', title: call.body.title, status: 'idle'};
      sessions.push(session); histories.set(session.id, []); return reply(session, 201);
    }
    if (String(path).endsWith('/history')) return reply({messages: histories.get(decodeURIComponent(String(path).split('/')[3])) || [], gaps: []});
    if (String(path).endsWith('/messages')) return reply({accepted: true, message_seq: 7}, 202);
    if (String(path).endsWith('/cancel')) return reply({stopped: true});
    if (path === '/api/materials') return reply({id: 'material-' + calls.filter(item => item.path === path).length, readingState: 'ready'}, 201);
    if (path === '/api/rpc') {
      if (call.body.method === 'settings.get') return reply({result: settings});
      if (call.body.method === 'settings.update') { Object.assign(settings.model, call.body.params.patch.model); return reply({result: {saved: true}}); }
      return reply({error: {code: 'method_not_found', message: 'Unsupported fixture RPC'}}, 400);
    }
    throw new Error('Unexpected HTTP request: ' + path);
  };
  f.client = new MirallyBrowserClient({token: 'explicit-transport-token', onEvent: event => events.push(event), onHello: hello => hellos.push(hello), onClose: value => closes.push(value), ...options});
  f.host = browser.window.__mirallyLegacyHost;
  t.after(() => f.client.stop());
  f.start = async () => { f.client.start(); await until(() => hellos.length && streams.length, 'client connection'); };
  f.push = value => streams.at(-1).enqueue(new TextEncoder().encode('data: ' + JSON.stringify(value) + '\r\n\r\n'));
  return f;
}

test('normalized request destinations reject foreign origins before attaching credentials', async t => {
  const f = fixture(t);
  for (const path of ['https://outside.invalid/api', '//outside.invalid/api', '/\\outside.invalid/api', '/\n/outside.invalid/api']) {
    await assert.rejects(f.host.request(path), /origin|地址|本机|同源/i, path);
  }
  assert.equal(f.calls.length, 0, 'No credential-bearing fetch may be attempted for a foreign destination');
  await f.host.request('/api/health');
  assert.equal(f.calls[0].headers.get('authorization'), 'Bearer explicit-transport-token');
});

test('startup authenticates before health, advertises supported methods, and stop cancels its stream', async t => {
  const f = fixture(t); await f.start();
  assert.deepEqual(f.calls.slice(0, 2).map(item => item.path), ['/api/auth', '/api/health']);
  assert.equal(f.client.connected, true); assert.equal(f.client.recoveryScopeReady, true);
  const methods = f.hellos[0].features.methods;
  assert.ok(methods.includes('chat.send')); assert.ok(methods.includes('sessions.files.get'));
  assert.equal(methods.includes('exec.approval.resolve'), false);
  const stream = f.calls.find(item => item.path === '/api/events');
  assert.equal(stream.headers.get('authorization'), 'Bearer explicit-transport-token');
  f.client.stop(); assert.equal(stream.init.signal.aborted, true); assert.equal(f.client.connected, false);
});

test('event subscribers share the existing SSE and unsubscribe independently', async t => {
  const f = fixture(t), first = [], second = [];
  const unsubscribeFirst = f.client.addEventListener(event => first.push(event));
  const unsubscribeSecond = f.client.addEventListener(event => second.push(event));
  assert.equal(typeof unsubscribeFirst, 'function');
  await f.start();
  f.push({type: 'token', session_id: 'one', command_id: 'subscribed-run', text: '第一段'});
  await until(() => second.length === 1, 'subscribed event');
  assert.equal(first.length, 1);
  assert.equal(first[0], f.events[0]); assert.equal(second[0], f.events[0]);
  assert.equal(first[0].event, 'chat'); assert.equal(first[0].payload.runId, 'subscribed-run');

  unsubscribeFirst(); unsubscribeFirst();
  f.push({type: 'token', session_id: 'one', command_id: 'subscribed-run', text: '第二段'});
  await until(() => second.length === 2, 'event after one unsubscribe');
  assert.equal(first.length, 1); assert.equal(second[1], f.events[1]);
  assert.ok(second[1].seq > second[0].seq);

  unsubscribeSecond();
  f.push({type: 'token', session_id: 'one', command_id: 'subscribed-run', text: '第三段'});
  await until(() => f.events.length === 3, 'owner event after all unsubscribed');
  assert.equal(first.length, 1); assert.equal(second.length, 2);
  assert.equal(f.calls.filter(call => call.path === '/api/events').length, 1,
    'Board subscriptions must reuse the sole transport connection');
  assert.equal(f.closes.length, 0);
});

test('throwing event callbacks do not interrupt the owner, other subscribers, or SSE', async t => {
  const owner = [], before = [], after = [], failure = new Error('broken Board callback');
  const warnings = t.mock.method(console, 'warn', () => {});
  const f = fixture(t, {onEvent(event) { owner.push(event); if (owner.length === 1) throw failure; }});
  f.client.addEventListener(event => before.push(event));
  f.client.addEventListener(() => { throw failure; });
  f.client.addEventListener(event => after.push(event));
  await f.start();
  f.push({type: 'token', session_id: 'one', command_id: 'callback-run', text: '第一段'});
  f.push({type: 'token', session_id: 'one', command_id: 'callback-run', text: '第二段'});
  await until(() => after.length === 2, 'events after callback failures');
  assert.equal(owner.length, 2); assert.deepEqual(before, owner); assert.deepEqual(after, owner);
  assert.equal(before[0], owner[0]); assert.equal(after[1], owner[1]);
  assert.equal(warnings.mock.calls.length, 3);
  assert.ok(warnings.mock.calls.every(call => call.arguments[1] === failure));
  assert.equal(f.client.connected, true); assert.equal(f.closes.length, 0);
  assert.equal(f.calls.filter(call => call.path === '/api/events').length, 1);
});

test('configuration SSE invalidates original UI config caches without dropping native product events', async t => {
  const f = fixture(t), subscribed = [];
  f.client.addEventListener(event => subscribed.push(event));
  await f.start();
  assert.ok(f.hellos[0].features.events.includes('config.changed'));
  const nativeEvents = [{type: 'artifact', artifact_id: 'saved-file'}, {type: 'action', action_id: 'action-one'}, {type: 'inbox', inbox_id: 'inbox-one'}];
  f.push(nativeEvents[0]);
  f.push({type: 'configuration', fields: ['model'], model: {api_key: 'never-project-this-value'}});
  f.push(nativeEvents[1]); f.push(nativeEvents[2]);
  f.push({type: 'token', session_id: 'one', command_id: 'after-config', text: '仍在接收'});
  const productEvents = () => f.domEvents.filter(event => event.type === 'mirally-product-event');
  await until(() => f.events.length === 2 && productEvents().length === 3, 'config and native product events');
  assert.equal(f.events[0].event, 'config.changed');
  assert.deepEqual(f.events[0].payload, {paths: ['model']});
  assert.equal(JSON.stringify(f.events).includes('never-project-this-value'), false);
  assert.equal(subscribed[0], f.events[0]); assert.equal(subscribed[1], f.events[1]);
  assert.equal(f.events[1].event, 'chat');
  assert.deepEqual(productEvents().map(event => event.detail), nativeEvents);
  assert.equal(f.calls.filter(call => call.path === '/api/events').length, 1); assert.equal(f.closes.length, 0);
});

test('session lookup and transcript use the product conversation identity, never the native session ID', async t => {
  const f = fixture(t);
  f.histories.set('one', [{id: 'u1', role: 'user', text: '记住喝水', time: 123}, {id: 'a1', role: 'assistant', text: '好的', time: 124}]);
  const listed = await f.client.request('sessions.list');
  assert.equal(listed.sessions[0].key, conversationKey('one')); assert.equal(listed.sessions[0].sessionId, 'native-one');
  const resolved = await f.client.request('sessions.resolve', {key: conversationKey('one')});
  assert.equal(resolved.sessionId, 'native-one');
  const history = await f.client.request('chat.startup', {sessionKey: conversationKey('one')});
  assert.equal(history.sessionKey, conversationKey('one')); assert.equal(history.sessionId, 'native-one');
  assert.deepEqual(history.messages.map(textOf), ['记住喝水', '好的']);
  assert.equal(f.calls.some(item => item.path === '/api/sessions/one/history'), true);
  assert.equal(f.calls.some(item => item.path.includes('native-one')), false);
  const before = f.calls.filter(item => item.path.endsWith('/history')).length;
  await assert.rejects(f.client.request('sessions.resolve', {key: conversationKey('missing')}), /会话不存在/);
  assert.equal(f.calls.filter(item => item.path.endsWith('/history')).length, before);
});

test('new-session parameters preserve the original UI display title and initial attachment ownership', async t => {
  const f = fixture(t), message = '规划周末行程';
  const result = await f.client.request('sessions.create', {displayName: '周末旅行', idempotencyKey: 'first-turn', message,
    attachments: [{fileName: '目的地.txt', mimeType: 'text/plain', content: 'YQ=='}]});
  assert.equal(result.key, conversationKey('created')); assert.equal(result.runStarted, true); assert.equal(result.runId, 'first-turn');
  assert.deepEqual(f.calls.find(item => item.path === '/api/sessions' && item.init.method === 'POST').body, {title: '周末旅行', requestId: 'first-turn'});
  assert.equal(f.calls.find(item => item.path === '/api/materials').body.conversation_id, 'created');
  assert.deepEqual(f.calls.find(item => item.path === '/api/sessions/created/messages').body.materials, ['material-1']);
});

test('an attachment-only initial turn starts work instead of silently clearing the source materials', async t => {
  const f = fixture(t);
  const result = await f.client.request('sessions.create', {message: '', idempotencyKey: 'attachment-only',
    attachments: [{fileName: '只提供材料.txt', mimeType: 'text/plain', content: 'YQ=='}]});
  assert.equal(result.runStarted, true);
  assert.equal(f.calls.find(item => item.path === '/api/materials').body.conversation_id, 'created');
  assert.deepEqual(f.calls.find(item => item.path === '/api/sessions/created/messages').body.materials, ['material-1']);
});

test('unsupported incognito semantics fail before creating a durable session', async t => {
  const f = fixture(t);
  await assert.rejects(f.client.request('sessions.create', {incognito: true, message: '这段不能持久化'}), /无痕|incognito|尚未|不支持/);
  assert.equal(f.calls.some(item => item.init.method === 'POST'), false);
});

test('unsupported session permission modes reject restored drafts before any creation, upload or send', async t => {
  const f = fixture(t);
  for (const permissionMode of ['read-only', 'guarded', 'workspace', 'full', 'unknown', false, 0]) {
    await assert.rejects(f.client.request('sessions.create', {permissionMode, message: '恢复旧权限草稿',
      idempotencyKey: 'restored-permission-draft', mirallyAllowFileWrite: true,
      attachments: [{fileName: '旧草稿材料.txt', mimeType: 'text/plain', content: 'YQ=='}]}), /尚未支持会话权限模式|没有创建会话/);
  }
  assert.deepEqual(f.calls, [], 'Unsupported modes must not reach any authenticated network request');
  assert.equal(f.sessions.length, 2, 'No durable ordinary conversation may replace a permission-specific draft');
  const ordinary = await f.client.request('sessions.create', {permissionMode: null, displayName: '普通对话'});
  assert.equal(ordinary.key, conversationKey('created'));
});

test('unsupported session forks and other agent owners cannot silently create an empty ordinary conversation', async t => {
  const f = fixture(t);
  for (const params of [{fork:true,parentSessionKey:conversationKey('one')},{parentSessionKey:conversationKey('one')},{parentSessionId:'native-one'},{agentId:'another-agent'}]) {
    await assert.rejects(f.client.request('sessions.create',params),/尚未支持|仅支持/);
  }
  assert.equal(f.calls.some(item=>item.init.method==='POST'),false);
});

test('a rejected initial turn retains its created session identity for the original UI retry flow', async t => {
  const f = fixture(t); f.override = call => call.path.endsWith('/messages')
    ? reply({error: {code: 'model_not_configured', message: '请先配置模型'}}, 409) : undefined;
  const result = await f.client.request('sessions.create', {message: '第一次需求', idempotencyKey: 'rejected-initial'});
  assert.equal(result.key, conversationKey('created')); assert.equal(result.entry.key, result.key);
  assert.equal(result.runStarted === true, false); assert.match(result.runError.message, /请先配置模型/);
  assert.equal(f.calls.filter(item => item.path === '/api/sessions' && item.init.method === 'POST').length, 1);
});

test('chat materials are uploaded to their exact conversation and only returned IDs reach the message', async t => {
  const f = fixture(t);
  const result = await f.client.request('chat.send', {sessionKey: conversationKey('two'), sessionId: 'native-two', message: '分析材料', idempotencyKey: 'run-two',
    allow_file_write: true, attachments: [{fileName: '中文.md', mimeType: 'text/markdown', content: 'YQ=='}]});
  assert.equal(result.status, 'started'); assert.equal(result.runId, 'run-two');
  assert.equal(result.messageSeq, 7);
  assert.deepEqual(f.calls.find(item => item.path === '/api/materials').body, {conversation_id: 'two', request_id: 'run-two:0', name: '中文.md', media_type: 'text/markdown', data: 'YQ=='});
  assert.deepEqual(f.calls.find(item => item.path === '/api/sessions/two/messages').body,
    {commandId: 'run-two', text: '分析材料', materials: ['material-1'], allow_file_write: false});
});

test('file permission belongs to the exact submitted command and stays identical on retry', async t => {
  const f = fixture(t), send = (id, run, permission) => f.client.request('chat.send', {sessionKey: conversationKey(id), message: '整理结果', idempotencyKey: run, mirallyAllowFileWrite: permission});
  assert.equal(f.host.allowWorkFileOutput, undefined, 'No transient per-conversation capability may leak into another draft');
  await send('one', 'approved', true); await send('one', 'ordinary'); await send('two', 'other'); await send('one', 'approved', true);
  assert.deepEqual(f.calls.filter(item => item.path.endsWith('/messages')).map(item => item.body.allow_file_write), [true, false, false, true]);
  const retried = f.calls.filter(item => item.body?.commandId === 'approved');
  assert.deepEqual(retried[0].body, retried[1].body, 'A durable outbox replay must preserve the admitted command hash');
  await send('one', 'not-a-boolean', 'true'); assert.equal(f.calls.at(-1).body.allow_file_write, false);
});

test('material parsing failures stop submission and preserve the backend error', async t => {
  const f = fixture(t); f.override = call => call.path === '/api/materials' ? reply({id: 'broken', readingState: 'error', note: '这份文档已损坏'}) : undefined;
  await assert.rejects(f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '看材料', attachments: [{fileName: 'broken.docx', content: 'YQ=='}]}), /这份文档已损坏/);
  assert.equal(f.calls.some(item => item.path.endsWith('/messages')), false);
});

test('backend codes survive mapping and pre-aborted requests never produce effects', async t => {
  const f = fixture(t); f.override = call => call.path.endsWith('/messages') ? reply({error: {code: 'confirmation_required', message: '请先确认执行范围'}}, 403) : undefined;
  await assert.rejects(f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '执行'}), error =>
    error.code === 'confirmation_required' && error.gatewayCode === 'confirmation_required' && /请先确认/.test(error.message));
  const controller = new AbortController(); controller.abort(); const before = f.calls.length;
  await assert.rejects(f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '不要执行'}, {signal: controller.signal}), {name: 'AbortError'});
  assert.equal(f.calls.length, before);
  await assert.rejects(f.client.request('exec.approval.resolve', {approved: true}), error => error.code === 'METHOD_NOT_FOUND');
  assert.equal(f.calls.length, before);
});

test('Stop maps the original UI run identity to the product command ID', async t => {
  const f = fixture(t);
  await f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '继续', idempotencyKey: 'run-one'});
  const result = await f.client.request('chat.abort', {sessionKey: conversationKey('one'), runId: 'run-one'});
  assert.equal(result.aborted, true);
  assert.deepEqual(f.calls.find(item => item.path === '/api/sessions/one/cancel').body, {commandId: 'run-one'});
});

test('configuration reads expose no API key and provider saves map only reviewed model fields', async t => {
  const f = fixture(t), config = await f.client.request('config.get');
  assert.equal(JSON.stringify(config).includes('private-provider-secret'), false);
  assert.equal(config.config.models.providers.fixture.apiKey, undefined);
  const models = await f.client.request('models.list'); assert.equal(JSON.stringify(models).includes('private-provider-secret'), false);
  await f.client.request('config.patch', {baseHash: config.hash, raw: JSON.stringify({models: {providers: {fixture: {baseUrl: 'https://new.invalid/v1', api: 'openai-completions', apiKey: 'new-private-secret', models: [{id: 'next-model', input: ['text'], reasoning: false, maxTokens: 2048}]}}}})});
  const update = f.calls.find(item => item.body?.method === 'settings.update');
  assert.deepEqual(update.body.params.patch, {model: {provider: 'fixture', base_url: 'https://new.invalid/v1', model_id: 'next-model', api_format: 'openai-chat', image_enabled: false, reasoning_enabled: false, max_tokens: 2048, api_key: 'new-private-secret'}});
  assert.equal(JSON.stringify(await f.client.request('config.get')).includes('new-private-secret'), false);
  const count = f.calls.filter(item => item.body?.method === 'settings.update').length;
  await assert.rejects(f.client.request('config.patch', {baseHash: 'stale-hash', patch: {models: {providers: {}}}}), /配置已变化/);
  await assert.rejects(f.client.request('config.patch', {patch: {activity_enabled: true}}), error => error.code === 'METHOD_NOT_FOUND');
  assert.equal(f.calls.filter(item => item.body?.method === 'settings.update').length, count);
});

test('interleaved, chunk-split UTF-8 SSE preserves conversation, run, and transcript identity', async t => {
  const f = fixture(t); await f.start();
  await f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '第一件事', idempotencyKey: 'run-one'});
  await f.client.request('chat.send', {sessionKey: conversationKey('two'), message: '第二件事', idempotencyKey: 'run-two'});
  f.push({type: 'assistant-start', session_id: 'one', attempt_id: 'a'});
  const bytes = new TextEncoder().encode('event: token\r\ndata: ' + JSON.stringify({type: 'token', session_id: 'one', text: '你好'}) + '\r\n\r\n');
  for (const byte of bytes) f.streams.at(-1).enqueue(Uint8Array.of(byte));
  f.push({type: 'assistant-start', session_id: 'two', attempt_id: 'b'}); f.push({type: 'token', session_id: 'two', text: '另一件'});
  f.push({type: 'token', session_id: 'one', text: '，一起做'});
  f.histories.set('one', [{id: 'persisted-one', role: 'assistant', text: '你好，一起做', __openclaw: {id: 'persisted-one', seq: 4, runId: 'run-one', runTerminal: true}}]);
  f.push({type: 'done', session_id: 'one'});
  await until(() => f.events.some(item => item.payload.state === 'final'), 'final event');
  const deltas = f.events.filter(item => item.event === 'chat' && item.payload.state === 'delta').map(item => item.payload);
  assert.deepEqual(deltas.map(item => [item.sessionKey, item.runId, textOf(item.message)]),
    [[conversationKey('one'), 'run-one', '你好'], [conversationKey('two'), 'run-two', '另一件'], [conversationKey('one'), 'run-one', '你好，一起做']]);
  const final = f.events.find(item => item.payload.state === 'final').payload;
  assert.equal(final.message.id, 'persisted-one'); assert.equal(final.runId, 'run-one');
  // The retained session-row reconciler consumes one payload.key per event.
  const changed = f.events.find(item => item.event === 'sessions.changed').payload;
  assert.equal(changed.key, conversationKey('one')); assert.equal(changed.agentId, 'main'); assert.equal(changed.hasActiveRun, false);
});

test('provider reasoning streams through the original thinking view and survives final/history separately from text', async t => {
  const f = fixture(t); await f.start();
  const listed = await f.client.request('sessions.list');
  assert.equal(listed.sessions.find(session => session.key === conversationKey('one')).reasoningLevel, 'on');
  // Read the actual retained transcript gate; generation capability must not
  // accidentally bypass the user's view toggle or hide real historical blocks.
  const projectionSource = readFileSync(join(root, 'frontend/control-ui/ui/src/pages/chat/components/chat-transcript-projection.ts'), 'utf8');
  const gate = /const showReasoning = ([^;]+);/.exec(projectionSource)?.[1];
  assert.ok(gate, 'The original thinking display gate must remain explicit');
  const visibleGate = (showThinking, activeSession) => vm.runInNewContext(gate, {props: {showThinking}, activeSession});
  assert.equal(visibleGate(true, listed.sessions[0]), true);
  assert.equal(visibleGate(false, listed.sessions[0]), false);
  const renderSource = readFileSync(join(root, 'frontend/control-ui/ui/src/app/mirally-stream-presentation.ts'), 'utf8');
  const {withLiveReasoningMessages} = await import('data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(renderSource, {mode: 'transform'})).toString('base64'));
  // Exercise the actual original thinking extractor, with only its plain-record
  // dependency supplied. No copied implementation or browser/DOM substitution.
  const extractSource = readFileSync(join(root, 'frontend/control-ui/ui/src/lib/chat/message-extract.ts'), 'utf8');
  const start = extractSource.indexOf('function extractThinking('), end = extractSource.indexOf('\nexport function extractThinkingCached', start);
  const extract = {asOptionalRecord: value => value && typeof value === 'object' ? value : undefined};
  vm.runInNewContext(stripTypeScriptTypes(extractSource.slice(start, end), {mode: 'transform'}) + '\nglobalThis.thinking = extractThinking;', extract);
  const push = value => f.push({session_id: 'one', command_id: 'reasoning-run', native_session_id: 'native-one', ...value});
  push({type: 'assistant-start', attempt_id: 'reason-attempt'});
  push({type: 'reasoning', attempt_id: 'reason-attempt', text: '先核对'});
  push({type: 'reasoning', attempt_id: 'reason-attempt', text: '条件。'});
  await until(() => f.events.filter(item => item.payload.state === 'delta').length === 2, 'live reasoning');
  const history = [{id: 'u1', role: 'user', content: [{type: 'text', text: '求解'}]}];
  const visible = withLiveReasoningMessages(f.client, conversationKey('one'), 'reasoning-run', history);
  assert.equal(visible.length, 2); assert.equal(extract.thinking(visible[1]), '先核对条件。');
  assert.equal(history.length, 1, 'Render-only reasoning must not mutate transcript or cache state');
  assert.equal(f.client.getReasoningStream(conversationKey('two'), 'reasoning-run').length, 0);
  assert.equal(f.client.getReasoningStream(conversationKey('one'), 'different-run').length, 0);
  assert.ok(f.events.every(item => !textOf(item.payload.message)?.includes('条件')), 'Reasoning never enters text delta snapshots');
  push({type: 'token', attempt_id: 'reason-attempt', text: '结果为 42。'});
  push({type: 'assistant-end', attempt_id: 'reason-attempt', outcome: 'committed', native_seq: 4});
  const persisted = {id: 'actual-message', role: 'assistant', time: 123, text: '结果为 42。', native_seq: 4, native_session_id: 'native-one',
    content: [{type: 'thinking', thinking: '先核对条件。'}, {type: 'text', text: '结果为 42。'}],
    __openclaw: {id: 'actual-message', seq: 5, runId: 'reasoning-run', runTerminal: true}};
  f.histories.set('one', [persisted]);
  await until(() => f.events.filter(item => item.payload.state === 'delta').length === 3, 'answer delta');
  const restored = await f.client.request('chat.history', {sessionKey: conversationKey('one')});
  assert.equal(restored.sessionInfo.reasoningLevel, 'on');
  assert.equal(extract.thinking(restored.messages[0]), '先核对条件。');
  assert.equal(textOf(restored.messages[0]), '结果为 42。');
  assert.equal(withLiveReasoningMessages(f.client, conversationKey('one'), 'reasoning-run', restored.messages).length, 1, 'Committed native receipt replaces the transient thinking row');
  push({type: 'done'});
  await until(() => f.events.some(item => item.payload.state === 'final'), 'reasoning final');
  const final = f.events.find(item => item.payload.state === 'final').payload.message;
  assert.equal(extract.thinking(final), '先核对条件。'); assert.equal(textOf(final), '结果为 42。');
  assert.equal(f.client.getReasoningStream(conversationKey('one'), 'reasoning-run').length, 0);
  f.settings.model.reasoning_enabled = false;
  const oldHistory = await f.client.request('chat.history', {sessionKey: conversationKey('one')});
  assert.equal(visibleGate(true, oldHistory.sessionInfo), true, 'Changing generation effort must not hide actual reasoning already in native history');
  assert.equal(extract.thinking(oldHistory.messages[0]), '先核对条件。');
});

test('abandoned and older attempts cannot leak reasoning or late text into a replacement attempt', async t => {
  const f = fixture(t); await f.start();
  const push = value => f.push({session_id: 'one', command_id: 'attempt-run', ...value});
  push({type: 'assistant-start', attempt_id: 'old'});
  push({type: 'reasoning', attempt_id: 'old', text: '已放弃推理'});
  push({type: 'assistant-end', attempt_id: 'old', outcome: 'abandoned'});
  push({type: 'reasoning', attempt_id: 'old', text: '迟到'});
  push({type: 'assistant-start', attempt_id: 'new'});
  push({type: 'reasoning', attempt_id: 'new', text: '新推理'});
  push({type: 'token', attempt_id: 'old', text: '旧正文'});
  push({type: 'token', attempt_id: 'new', text: '新正文'});
  await until(() => f.events.some(item => textOf(item.payload.message) === '新正文'), 'replacement attempt');
  assert.deepEqual(f.client.getReasoningStream(conversationKey('one'), 'attempt-run').map(message => message.content[0].thinking), ['新推理']);
  assert.ok(f.events.every(item => !textOf(item.payload.message)?.includes('旧正文')));
  assert.ok(f.events.some(item => item.payload.replace === true && item.payload.deltaText === ''), 'Abandonment clears the old transient answer without manufacturing completion');
  assert.equal(f.events.some(item => item.payload.state === 'final'), false);
  f.client.stop();
  assert.equal(f.client.getReasoningStream(conversationKey('one'), 'attempt-run').length, 0);
});

test('failed completion cannot be presented as success and cancellation retains its aborted outcome', async t => {
  const f = fixture(t); await f.start();
  await f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '这次失败', idempotencyKey: 'failed-run'});
  f.push({type: 'done', session_id: 'one', failed: true});
  await until(() => f.events.some(item => item.event === 'chat'), 'failed completion');
  assert.equal(f.events.find(item => item.event === 'chat').payload.state, 'error');
  await f.client.request('chat.send', {sessionKey: conversationKey('two'), message: '停止它', idempotencyKey: 'stopped-run'});
  f.push({type: 'done', session_id: 'two', cancelled: true});
  await until(() => f.events.some(item => item.payload.state === 'aborted'), 'aborted completion');
  assert.equal(f.events.find(item => item.payload.state === 'aborted').payload.runId, 'stopped-run');
});

test('completed replay settles its own run without borrowing a later turns reply', async t => {
  const f = fixture(t); await f.start();
  f.histories.set('one', [{id: 'user-first', role: 'user', text: '最初的需求'}, {id: 'reply-first', role: 'assistant', text: '最初的回复'},
    {id: 'user-later', role: 'user', text: '后来的需求'}, {id: 'reply-later', role: 'assistant', text: '后来的回复'}]);
  f.override = call => call.path.endsWith('/messages') ? reply({accepted: true, replay: true,
    command: {state: 'completed', runtime_request_id: 'user-first'}}) : undefined;
  const result = await f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '最初的需求', idempotencyKey: 'run-first'});
  assert.equal(result.status, 'ok');
  const final = await until(() => f.events.find(item => item.event === 'chat' && item.payload.runId === 'run-first' && item.payload.state === 'final'), 'replayed terminal state');
  // A terminal signal without a message may request authoritative history.
  // A supplied reply must belong to the replayed command, not the newest turn.
  if (final.payload.message) assert.equal(final.payload.message.id, 'reply-first');
  assert.deepEqual((await f.client.request('sessions.list')).sessions[0].activeRunIds, []);
});

test('creating an already completed initial turn does not start a new optimistic run after its terminal event', async t => {
  const f = fixture(t); await f.start();
  f.override = call => call.path.endsWith('/messages') ? reply({accepted: true, replay: true, command: {state: 'completed'}}) : undefined;
  const result = await f.client.request('sessions.create', {message: '已经完成的首轮', idempotencyKey: 'completed-create'});
  assert.equal(result.runStarted, false); assert.equal(result.runError, undefined);
  assert.equal(result.key, conversationKey('created'));
});

test('an earlier admission failure cannot erase a newer run in the same conversation', async t => {
  const f = fixture(t); let release;
  f.override = call => call.path.endsWith('/messages') && call.body.commandId === 'earlier'
    ? new Promise(resolve => {release = () => resolve(reply({error: {code: 'rejected', message: '早先请求失败'}}, 409));}) : undefined;
  const earlier = f.client.request('chat.send', {sessionKey: conversationKey('one'), message: 'A', idempotencyKey: 'earlier'});
  const rejected = assert.rejects(earlier, /早先请求失败/);
  await until(() => release, 'first request is pending');
  await f.client.request('chat.send', {sessionKey: conversationKey('one'), message: 'B', idempotencyKey: 'newer'});
  release(); await rejected;
  assert.deepEqual((await f.client.request('sessions.list')).sessions[0].activeRunIds, ['newer']);
});

test('completed replay and an older terminal event preserve another pending run and its visibility', async t => {
  const f = fixture(t); await f.start();
  const send = run => f.client.request('chat.send', {sessionKey: conversationKey('one'), message: run, idempotencyKey: run});
  await send('run-a'); await send('run-b');
  f.push({type: 'done', session_id: 'one', command_id: 'run-a'});
  await until(() => f.events.some(item => item.event === 'sessions.changed'), 'first run completed');
  assert.deepEqual((await f.client.request('sessions.list')).sessions[0].activeRunIds, ['run-b']);
  const changed = f.events.find(item => item.event === 'sessions.changed').payload;
  assert.equal(changed.hasActiveRun, true); assert.deepEqual(changed.activeRunIds, ['run-b']);
  f.override = call => call.path.endsWith('/messages') && call.body.commandId === 'completed-old'
    ? reply({accepted: true, replay: true, command: {state: 'completed'}}) : undefined;
  assert.equal((await send('completed-old')).status, 'ok');
  assert.deepEqual((await f.client.request('sessions.list')).sessions[0].activeRunIds, ['run-b']);
  const replay = f.events.filter(item => item.event === 'sessions.changed').at(-1).payload;
  assert.equal(replay.hasActiveRun, true); assert.deepEqual(replay.activeRunIds, ['run-b']);
  await f.client.request('chat.abort', {sessionKey: conversationKey('one'), runId: 'run-b'});
  assert.deepEqual((await f.client.request('sessions.list')).sessions[0].activeRunIds, []);
});

test('an old stream history read cannot publish completion into a restarted connection', async t => {
  const f = fixture(t); await f.start(); let release;
  f.override = call => call.path.endsWith('/history') && !release
    ? new Promise(resolve => {release = () => resolve(reply({messages: []}));}) : undefined;
  await f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '旧连接', idempotencyKey: 'old-connection'});
  f.push({type: 'done', session_id: 'one', command_id: 'old-connection'});
  await until(() => release, 'old completion waits for history');
  f.client.stop(); f.client.start(); await until(() => f.hellos.length === 2 && f.streams.length === 2, 'new connection');
  release(); await delay(25);
  assert.equal(f.events.some(item => item.event === 'chat' && item.payload.state === 'final'), false);
});

test('ambiguous HTTP loss follows the original durable outbox reconnect path with the same command identity', async t => {
  const queueSource = readFileSync(join(root, 'frontend/control-ui/ui/src/pages/chat/chat-send-queue-state.ts'), 'utf8');
  const start = queueSource.indexOf('export function settleQueuedChatSendFailure('), end = queueSource.indexOf('\nexport async function waitForPendingChatSettings', start);
  assert.ok(start >= 0 && end > start);
  const settleSource = stripTypeScriptTypes(queueSource.slice(start, end), {mode: 'transform'}).replace('export function', 'function');
  for (const mode of ['fetch rejected after possible admission', 'accepted response JSON was truncated']) await t.test(mode, async t => {
    const f = fixture(t); await f.start(); let first = true, failure;
    f.override = call => {
      if (!call.path.endsWith('/messages') || !first) return;
      first = false;
      if (mode.startsWith('fetch')) throw new TypeError('Network response was lost');
      return {ok: true, status: 202, async json() {throw new SyntaxError('Unexpected end of JSON input');}};
    };
    const params = {sessionKey: conversationKey('one'), message: 'create deliverable', idempotencyKey: 'durable-original-id', mirallyAllowFileWrite: true};
    await assert.rejects(f.client.request('chat.send', params), error => {failure = error; return /gateway disconnected:/i.test(error.message) && error.code === 'GATEWAY_DISCONNECTED' && error.cause instanceof Error;});
    assert.equal(f.client.connected, false); assert.equal(f.closes.length, 1); assert.equal(f.closes[0].willRetry, true);
    const queued = {id: 'outbox-row', sendRunId: params.idempotencyKey, mirallyAllowFileWrite: true, sendAttempts: 1, sendState: 'sending'};
    const owner = {chatQueue: [queued], connected: false}, scope = {sessionKey: params.sessionKey};
    const sandbox = {Error, GatewayPayloadLimitError: class extends Error {}, GatewayRequestError: class extends Error {},
      deliveryStateWriter: () => () => {throw new Error('An ambiguous send must not become a terminal failed attempt');},
      isActiveLeafChangedError: () => false, formatConnectError: error => error.message,
      readQueuedMessageById: (host, id) => host.chatQueue.find(item => item.id === id), retryableGatewayDelayMs: () => null,
      updateQueuedMessage(host, id, update) {const index = host.chatQueue.findIndex(item => item.id === id); if (index < 0) return null; return host.chatQueue[index] = update(host.chatQueue[index]);},
      visibleSessionMatches: () => true, setChatError: (host, error) => {host.chatError = error;}, recordChatSendTiming() {}};
    vm.runInNewContext(settleSource + '\nglobalThis.settle = settleQueuedChatSendFailure;', sandbox);
    assert.equal(sandbox.settle(owner, queued, {...queued}, failure, {}, scope, () => {}), 'pending');
    assert.equal(owner.chatQueue[0].sendState, 'waiting-reconnect');
    assert.equal(owner.chatQueue[0].sendRunId, params.idempotencyKey); assert.equal(owner.chatQueue[0].mirallyAllowFileWrite, true);
    await until(() => f.hellos.length === 2 && f.client.connected, 'automatic reconnect', 4000);
    await f.client.request('chat.send', params);
    const attempts = f.calls.filter(item => item.path.endsWith('/messages'));
    assert.equal(attempts.length, 2); assert.deepEqual(attempts[0].body, attempts[1].body);
  });
});

test('a delayed network failure from an older generation cannot disconnect its replacement', async t => {
  const f = fixture(t); await f.start(); let rejectOld;
  f.override = call => call.path.endsWith('/messages') && call.body.commandId === 'old-network'
    ? new Promise((_resolve, reject) => {rejectOld = reject;}) : undefined;
  const pending = f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '旧请求', idempotencyKey: 'old-network'});
  const rejected = assert.rejects(pending, /gateway disconnected:/);
  await until(() => rejectOld, 'old HTTP request waits');
  f.client.stop(); f.client.start(); await until(() => f.hellos.length === 2 && f.client.connected, 'replacement connection');
  await f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '新请求', idempotencyKey: 'new-network'});
  rejectOld(new TypeError('Old response was lost')); await rejected;
  assert.equal(f.client.connected, true); assert.equal(f.closes.length, 0);
  assert.deepEqual((await f.client.request('sessions.list')).sessions[0].activeRunIds, ['new-network']);
});

test('automatic reconnect advances generation before a previously pending POST fails', async t => {
  const f = fixture(t); await f.start(); let rejectOld;
  f.override = call => {
    if (!call.path.endsWith('/messages')) return;
    if (call.body.commandId === 'old-pending') return new Promise((_resolve, reject) => {rejectOld = reject;});
    if (call.body.commandId === 'break-connection') throw new TypeError('Current transport failed');
  };
  const pending = f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '旧请求还未返回', idempotencyKey: 'old-pending'});
  const rejected = assert.rejects(pending, /gateway disconnected:/);
  await until(() => rejectOld, 'old request pending on the first connection');
  await assert.rejects(f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '触发自动重连', idempotencyKey: 'break-connection'}), /gateway disconnected:/);
  await until(() => f.hellos.length === 2 && f.client.connected, 'automatic replacement without stop/start', 4000);
  assert.equal(f.closes.length, 1);
  await f.client.request('chat.send', {sessionKey: conversationKey('one'), message: '替代连接的新请求', idempotencyKey: 'replacement-run'});
  rejectOld(new TypeError('First connection finally rejected')); await rejected;
  assert.equal(f.client.connected, true); assert.equal(f.closes.length, 1);
  assert.deepEqual((await f.client.request('sessions.list')).sessions[0].activeRunIds, ['replacement-run']);
});

test('fresh history restores a real active command and Stop before any model token arrives', async t => {
  const f = fixture(t); f.sessions[0].status = 'running'; f.sessions[0].active_command_ids = ['server-owned-run'];
  f.override = call => call.path.endsWith('/history') ? reply({messages: [], active_command_ids: ['server-owned-run'],
    in_flight: {command_id: 'server-owned-run', native_session_id: 'native-one', started_at: 1700000000000}}) : undefined;
  await f.start();
  assert.ok(f.hellos[0].features.methods.includes('sessions.abort'));
  const history = await f.client.request('chat.startup', {sessionKey: conversationKey('one')});
  assert.deepEqual(history.activeRunIds, ['server-owned-run']);
  assert.deepEqual(history.inFlightRun, {runId: 'server-owned-run', startedAt: 1700000000000, sessionAbortable: true});
  assert.equal(f.events.length, 0, 'Run recovery cannot depend on a token or an earlier local send');
  const stopped = await f.client.request('sessions.abort', {key: conversationKey('one'), runId: history.inFlightRun.runId});
  assert.equal(stopped.status, 'aborted'); assert.equal(stopped.aborted, true);
  assert.deepEqual(f.calls.find(item => item.path === '/api/sessions/one/cancel').body, {commandId: 'server-owned-run'});
});

test('configured native model availability does not expose credentials or imply an online validation', async t => {
  const f = fixture(t);
  assert.equal((await f.client.request('models.list')).models[0].available, true);
  f.settings.model.base_url = '';
  assert.equal((await f.client.request('models.list')).models[0].available, false);
  assert.equal(f.calls.every(item => item.path === '/api/rpc' && item.body.method === 'settings.get'), true);
});

test('tool start and real result have ordered, detailed receipts accepted by the real original UI reducer', async t => {
  const f = fixture(t); await f.start();
  const args = {query: '当前工作'}, result = {content: [{type: 'text', text: '真实来源：会议记录'}]};
  f.push({type: 'tool', session_id: 'one', command_id: 'tool-run', id: 'tool-call', name: 'mirally_context', state: 'running', args});
  f.push({type: 'tool', session_id: 'one', command_id: 'tool-run', id: 'tool-call', name: 'mirally_context', state: 'completed', result});
  await until(() => f.events.filter(item => item.event === 'agent').length === 2, 'tool start/result');
  const payloads = f.events.filter(item => item.event === 'agent').map(item => item.payload);
  assert.ok(Number.isSafeInteger(payloads[0].seq) && payloads[0].seq > 0 && payloads[1].seq > payloads[0].seq);
  assert.ok(payloads.every(item => Number.isFinite(item.ts) && item.ts > 0));
  const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source, {mode: 'transform'})).toString('base64');
  const readSource = path => readFileSync(join(root, 'frontend/control-ui', path), 'utf8');
  const records = moduleUrl(readSource('packages/normalization-core/src/record-coerce.ts'));
  const strings = moduleUrl(readSource('packages/normalization-core/src/string-coerce.ts'));
  const utf16 = moduleUrl(readSource('packages/normalization-core/src/utf16-slice.ts'));
  const approvals = await import(moduleUrl(readSource('ui/src/lib/chat/tool-approval-reviews.ts')
    .replaceAll('"@openclaw/normalization-core/record-coerce"', JSON.stringify(records))
    .replaceAll('"@openclaw/normalization-core/utf16-slice"', JSON.stringify(utf16))));
  const streamState = await import(moduleUrl(readSource('ui/src/pages/chat/tool-stream-state.ts')));
  const recordFunctions = await import(records), stringFunctions = await import(strings);
  const identitySource = readSource('ui/src/pages/chat/tool-stream-identity.ts');
  const identityStart = identitySource.indexOf('export function buildToolStreamIdentity('), identityEnd = identitySource.indexOf('\nfunction addToolMessageRef', identityStart);
  assert.ok(identityStart >= 0 && identityEnd > identityStart);
  const identities = await import(moduleUrl(identitySource.slice(identityStart, identityEnd)));
  // Execute the actual reducer and its sequencing/row builders unchanged. Only
  // unrelated activity domains and browser scheduling are supplied at its seam.
  const reducer = stripTypeScriptTypes(readSource('ui/src/pages/chat/tool-stream.ts'), {mode: 'transform'})
    .replace(/^import\b[\s\S]*?from\s+["'][^"']+["'];?\s*$/gm, '').replace(/^export /gm, '');
  const sandbox = {readRecord: recordFunctions.asNullableObjectRecord, toTrimmedString: stringFunctions.normalizeNullableString,
    ...approvals, ...streamState, ...identities,
    Value: {Check: () => false}, AgentActivityItemSchema: {},
    uiSessionEventMatches: (host, session) => host.sessionKey === session,
    handleStreamStatus: () => false, handlePreambleProgress: () => false,
    window: {setTimeout, clearTimeout}, clearTimeout,
    truncateText: text => ({text, truncated: false})};
  vm.runInNewContext(reducer + '\nglobalThis.reduceTool = handleAgentEvent;', sandbox);
  const host = {sessionKey: conversationKey('one'), chatRunId: null, toolStreamById: new Map(), toolStreamOrder: [], chatToolMessages: []};
  t.after(() => { if (host.toolStreamSyncTimer) clearTimeout(host.toolStreamSyncTimer); });
  assert.equal(sandbox.reduceTool(host, payloads[0]), true);
  assert.equal(host.toolStreamById.values().next().value.resultReceived, undefined);
  assert.equal(sandbox.reduceTool(host, payloads[1]), true, 'The result must not be rejected as a duplicate seq=0');
  const entry = host.toolStreamById.values().next().value;
  assert.equal(entry.resultReceived, true); assert.equal(entry.message.__openclawToolStreamResultReceived, true);
  assert.deepEqual(entry.args, args); assert.equal(entry.output, '真实来源：会议记录');
  assert.equal(entry.message.content.some(block => block.type === 'toolresult'), true);
  assert.equal(host.chatToolMessages.length, 1);
});

test('isolated test-mode Host accepts original-client ownership, retries, and exact Stop', {timeout: 60000}, async t => {
  const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!existsSync(python) || !existsSync(join(root, 'kernel/dsh/packages/core/agent/lib/index.js'))) {
    t.skip('Build the owned kernel and prepare the repository .venv before real Host acceptance'); return;
  }
  const home = await mkdtemp(join(tmpdir(), 'mirally 原前端 host '));
  let logs = '', child;
  t.after(async () => {
    if (child?.exitCode === null) {
      child.stdin.end();
      try { await Promise.race([once(child, 'exit'), delay(7000, undefined, {ref: false}).then(() => {throw new Error('Host shutdown timed out');})]); }
      catch { child.kill(); await Promise.race([once(child, 'exit'), delay(3000, undefined, {ref: false})]); }
    }
    await rm(home, {recursive: true, force: true});
  });
  child = spawn(process.execPath, [join(root, 'kernel/dsh/packages/mirally/mirally-product/start.mjs'), '--root', root, '--home', home, '--python', python, '--test-mode'],
    {cwd: root, env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe']});
  child.stdout.on('data', chunk => { logs += chunk; }); child.stderr.on('data', chunk => { logs += chunk; });
  let runtime;
  await until(() => { if (child.exitCode !== null) throw new Error('Isolated Host failed: ' + logs); try {runtime = JSON.parse(readFileSync(join(home, 'runtime.json'), 'utf8')); return runtime;} catch {} }, 'isolated Host startup', 18000);
  assert.equal(runtime.mode, 'test');
  const browser = browserEnvironment(t, runtime.url), events = [], hellos = [];
  globalThis.fetch = (path, init) => nativeFetch(new URL(path, runtime.url), init);
  const client = new MirallyBrowserClient({token: runtime.token, onEvent: event => events.push(event), onHello: hello => hellos.push(hello)});
  t.after(() => client.stop()); client.start();
  await until(() => hellos.length, 'real Host handshake');
  const firstParams = {displayName: '真实隔离对话', message: '你好', idempotencyKey: 'initial-real-turn',
    attachments: [{fileName: '材料.txt', mimeType: 'text/plain', content: Buffer.from('真实原生材料').toString('base64')}]};
  const first = await client.request('sessions.create', firstParams);
  await until(() => events.find(item => item.event === 'chat' && item.payload.state === 'final' && item.payload.runId === 'initial-real-turn'), 'real initial reply', 12000);
  const history = await client.request('chat.history', {sessionKey: first.key});
  assert.equal(history.messages.filter(item => item.role === 'user').length, 1);
  const firstUser = history.messages.find(item => item.role === 'user');
  assert.equal(firstUser.__openclaw.idempotencyKey, 'initial-real-turn:user');
  assert.ok(Number.isSafeInteger(firstUser.__openclaw.seq) && firstUser.__openclaw.seq > 0);
  assert.equal(history.messages.find(item => item.role === 'assistant').__openclaw.runId, 'initial-real-turn');
  assert.match(textOf(history.messages.find(item => item.role === 'assistant')), /Mirally 测试模型已收到：你好/);
  const nativeId = history.sessionId; assert.ok(nativeId.startsWith('mirally-'));
  const secondAck = await client.request('chat.send', {sessionKey: first.key, sessionId: nativeId, message: '继续第二轮', idempotencyKey: 'second-real-turn'});
  await until(() => events.find(item => item.event === 'chat' && item.payload.state === 'final' && item.payload.runId === 'second-real-turn'), 'real second reply', 12000);
  const later = await client.request('chat.history', {sessionKey: first.key});
  assert.equal(later.sessionId, nativeId); assert.equal(later.messages.filter(item => item.role === 'user').length, 2);
  assert.match(textOf(later.messages.at(-1)), /继续第二轮/);
  assert.equal(later.messages.filter(item => item.role === 'user').at(-1).__openclaw.seq, secondAck.messageSeq);
  const receipts = (await readdir(join(home, 'materials'))).filter(name => name.endsWith('.json'));
  assert.equal(receipts.length, 1);
  const material = JSON.parse(await readFile(join(home, 'materials', receipts[0]), 'utf8'));
  assert.equal(material.name, '材料.txt'); assert.equal(material.conversation_id, first.key.replace(/^agent:main:/, ''));
  const host = browser.window.__mirallyLegacyHost, id = host.conversationIdForSession(first.key);
  assert.equal(id, first.key.replace(/^agent:main:/, ''));
  const summary = async target => (await host.request('/api/sessions')).sessions.find(item => item.id === target);

  await t.test('POST creation identity survives sequential and concurrent retries without rebinding native ownership', async () => {
    const direct = await host.request('/api/sessions', {method: 'POST', body: JSON.stringify({title: '真实隔离对话', requestId: 'initial-real-turn'})});
    assert.equal(direct.id, id); assert.equal(direct.session_id, nativeId);
    const attempts = await Promise.all([0, 1].map(() => host.request('/api/sessions', {method: 'POST',
      body: JSON.stringify({title: '同时新建', requestId: 'concurrent-original-ui-request'})})));
    assert.equal(attempts[0].id, attempts[1].id); assert.equal(attempts[0].session_id, attempts[1].session_id);
    assert.notEqual(attempts[0].id, id);
    const all = (await host.request('/api/sessions')).sessions;
    assert.equal(all.filter(item => item.id === attempts[0].id).length, 1);
    assert.equal(all.filter(item => item.id === id).length, 1);
  });

  await t.test('material request identity yields the same receipt and permits replay without another message', async () => {
    const payload = {conversation_id: id, request_id: 'initial-real-turn:0', name: '材料.txt', media_type: 'text/plain', data: firstParams.attachments[0].content};
    const repeated = await host.request('/api/materials', {method: 'POST', body: JSON.stringify(payload)});
    assert.equal(repeated.id, material.id); assert.equal(repeated.conversation_id, id);
    const replay = await host.request(`/api/sessions/${encodeURIComponent(id)}/messages`, {method: 'POST',
      body: JSON.stringify({commandId: 'initial-real-turn', text: firstParams.message, materials: [repeated.id], allow_file_write: false})});
    assert.equal(replay.accepted, true); assert.equal(replay.replay, true);
    assert.equal(replay.command.state, 'completed');
    const after = await client.request('chat.history', {sessionKey: first.key});
    assert.deepEqual(after.messages.map(item => item.id), later.messages.map(item => item.id));
    assert.equal((await readdir(join(home, 'materials'))).filter(name => name.endsWith('.json')).length, 1);
  });

  await t.test('a completed initial turn retries with the same key and leaves no phantom active run', async () => {
    const retried = await client.request('sessions.create', firstParams);
    assert.equal(retried.key, first.key); assert.equal(retried.entry.sessionId, nativeId);
    const listed = await client.request('sessions.list');
    assert.equal(listed.sessions.length, 2, 'Only the original and independent concurrent request exist');
    const entry = listed.sessions.find(item => item.key === first.key);
    assert.equal(entry.hasActiveRun, false); assert.deepEqual(entry.activeRunIds, []);
    const after = await client.request('chat.history', {sessionKey: first.key});
    assert.deepEqual(after.messages.map(item => item.id), later.messages.map(item => item.id));
    assert.equal((await readdir(join(home, 'materials'))).filter(name => name.endsWith('.json')).length, 1);
  });

  await t.test('main sends create the reserved conversation before material intake and keep reply ownership', async () => {
    assert.equal(await summary('main'), undefined);
    const sent = await client.request('chat.send', {sessionKey: conversationKey('main'), message: '主会话第一轮', idempotencyKey: 'main-initial-turn',
      attachments: [{fileName: '主会话材料.txt', mimeType: 'text/plain', content: Buffer.from('只属于主会话').toString('base64')}]});
    assert.equal(sent.sessionKey, conversationKey('main'));
    const final = await until(() => events.find(item => item.event === 'chat' && item.payload.state === 'final' && item.payload.runId === 'main-initial-turn'), 'main-owned reply', 12000);
    assert.equal(final.payload.sessionKey, conversationKey('main'));
    assert.match(textOf(final.payload.message), /主会话第一轮/);
    const mainHistory = await client.request('chat.history', {sessionKey: conversationKey('main')});
    assert.equal(mainHistory.messages.filter(item => item.role === 'user').length, 1);
    assert.equal(mainHistory.sessionId, (await summary('main')).session_id);
    const owned = await Promise.all((await readdir(join(home, 'materials'))).filter(name => name.endsWith('.json'))
      .map(async name => JSON.parse(await readFile(join(home, 'materials', name), 'utf8'))));
    assert.equal(owned.find(item => item.name === '主会话材料.txt').conversation_id, 'main');
    const original = await client.request('chat.history', {sessionKey: first.key});
    assert.deepEqual(original.messages.map(item => item.id), later.messages.map(item => item.id));
  });

  await t.test('late Stop for a completed command neither freezes nor interrupts newer work', async () => {
    const old = (await host.rpc('command.list', {conversation_id: id, command_id: 'second-real-turn'})).commands[0];
    assert.equal(old.state, 'completed');
    const before = await summary(id), newRun = 'newer-run-must-finish';
    await client.request('chat.send', {sessionKey: first.key, message: '请继续完成新工作。'.repeat(30), idempotencyKey: newRun});
    await until(() => events.find(item => item.payload.runId === newRun && item.payload.state === 'delta'), 'new run is actually streaming', 12000);
    const marker = events.length;
    const stopped = await client.request('chat.abort', {sessionKey: first.key, runId: old.command_id});
    assert.equal(stopped.aborted, false);
    const after = await summary(id);
    assert.equal(after.frozen, false); assert.equal(after.generation, before.generation); assert.equal(after.session_id, before.session_id);
    const during = (await host.rpc('command.list', {conversation_id: id, command_id: newRun})).commands[0];
    assert.ok(['applied', 'completed'].includes(during.state));
    await until(() => events.find(item => item.payload.runId === newRun && item.payload.state === 'final'), 'new work survives late Stop', 12000);
    assert.equal(events.slice(marker).some(item => item.payload.sessionKey === first.key && ['aborted', 'error'].includes(item.payload.state)), false);
    assert.equal((await host.rpc('command.list', {conversation_id: id, command_id: newRun})).commands[0].state, 'completed');
    assert.equal((await summary(id)).frozen, false);
  });

  await t.test('busy follow-up is durable before consumption and retains its own optimistic receipt', async () => {
    const originalRun = 'busy-receipt-original', followupRun = 'busy-receipt-followup';
    await client.request('chat.send', {sessionKey: first.key, message: '先完成这一条长需求。'.repeat(15), idempotencyKey: originalRun});
    await until(() => events.find(item => item.payload.runId === originalRun && item.payload.state === 'delta'), 'original busy reply starts', 12000);
    const ack = await client.request('chat.send', {sessionKey: first.key, message: '补充需求也要保留', idempotencyKey: followupRun});
    assert.ok(Number.isSafeInteger(ack.messageSeq) && ack.messageSeq > 0);
    const admitted = await client.request('chat.history', {sessionKey: first.key});
    const pending = admitted.messages.filter(item => item.role === 'user' && item.__openclaw?.idempotencyKey === `${followupRun}:user`);
    assert.equal(pending.length, 1); assert.equal(pending[0].__openclaw.seq, ack.messageSeq);
    for (const run of [originalRun, followupRun]) await until(() => events.find(item => item.payload.runId === run && item.payload.state === 'final'), 'owned busy completion ' + run, 12000);
    const finished = await client.request('chat.history', {sessionKey: first.key});
    assert.equal(finished.messages.filter(item => item.role === 'user' && item.__openclaw?.idempotencyKey === `${followupRun}:user`).length, 1);
    assert.equal(finished.messages.find(item => item.id === pending[0].id).__openclaw.seq, ack.messageSeq);
    assert.ok(finished.messages.some(item => item.role === 'assistant' && item.__openclaw?.runId === originalRun));
    assert.ok(finished.messages.some(item => item.role === 'assistant' && item.__openclaw?.runId === followupRun));
  });

  await t.test('a fresh client without any stream or token can recover and stop native in-flight work', async () => {
    const runId = 'fresh-client-stops-native-work';
    await client.request('chat.send', {sessionKey: first.key, message: '等待中的模型工作。'.repeat(80), idempotencyKey: runId});
    const seen = [], fresh = new MirallyBrowserClient({token: runtime.token, onEvent: value => seen.push(value)});
    t.after(() => fresh.stop());
    const restored = await fresh.request('chat.history', {sessionKey: first.key});
    assert.equal(restored.inFlightRun.runId, runId); assert.equal(restored.inFlightRun.sessionAbortable, true);
    assert.ok(restored.activeRunIds.includes(runId)); assert.equal(seen.length, 0);
    assert.equal((await fresh.request('sessions.abort', {key: first.key, runId})).aborted, true);
    const stopped = await summary(id); assert.equal(stopped.status, 'idle'); assert.equal(stopped.frozen, true);
    assert.deepEqual(stopped.active_command_ids, []);
    assert.equal((await host.rpc('command.list', {conversation_id: id, command_id: runId})).commands[0].state, 'cancelled');
    const after = await fresh.request('chat.history', {sessionKey: first.key}); assert.equal(after.inFlightRun, undefined);
  });
});
