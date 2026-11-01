import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {createMirallyNativeBridge, projectStatus} = require('../mirally/framework_ui/native-bridge.js');

function fixture({native = false, confirm = false} = {}) {
  const calls = [], replies = [], geometry = [], notices = [], opened = [], captures = [];
  const activity = {enabled: false, paused: false, fingerprint: 'scope-one', database_exists: true,
    scope: [{label: '窗口标题', enabled: true}], scope_note: '仅当前窗口', capture: {capture_level: 'metadata'},
    workers: {running: true, settings_pending: false, activity: {state: 'disabled'}}, snapshot: {}, profiles: {activity: 'owned/activity.yaml', database: 'owned/activity.sqlite3'}};
  const settings = {memory_enabled: true, background_enabled: false, model: {model_id: 'fixture-model'}, memory_semantics: {max_calls_per_hour: 4, max_tokens_per_day: 24000, max_output_tokens: 1500}};
  const memory = {status: 'ready', store: {active_count: 3}, workers: {running: true, settings_pending: false, memory: {state: 'running', health: {maintenance_running: true, maintenance_status: 'running', store: {active_count: 3}}}, semantic: {state: 'disabled'}}};
  const artifacts = [{id: 'a'.repeat(36), conversation_id: 'one', name: '中文成果.md', bytes: 120, created_at: '2026-10-23T00:00:00Z'}];
  const root = {setTimeout, confirm: () => confirm, addEventListener() {},
    __mirallyActivity: {receive: value => replies.push(value)}, __mirallyContext: {receive: value => captures.push(value)},
    open: url => { opened.push(url); return {}; },
    ...(native ? {chrome: {webview: {postMessage: value => geometry.push(value), addEventListener() {}}}} : {}),
  };
  const api = {notify: text => notices.push(text), conversationIdForSession: key => key === 'session-one' ? 'one' : key === 'session-two' ? 'two' : null,
    async rpc(method, params) {
      calls.push({method, params});
      if (method === 'activity.status') return activity;
      if (method === 'memory.status') return memory;
      if (method === 'settings.get') return settings;
      if (method === 'settings.update') { Object.assign(settings, params.patch); return {...settings, workers: {status: {running: true, settings_pending: true}}}; }
      if (method === 'activity.browse') return {url: 'http://127.0.0.1:12345/?token=' + 't'.repeat(32) + (params.mode === 'monitor' ? '#diagnostics' : ''), readonly: true};
      if (method.startsWith('activity.')) return {requested: method.slice(9)};
      if (method === 'memory.search') return {memories: params.include_candidates ? [{id: 'candidate', status: 'candidate'}] : [], next_offset: null};
      if (method === 'memory.read') return {memory: {id: params.memory_id}, text: '正文'};
      throw new Error('Unexpected RPC ' + method);
    },
    async request(path) {
      calls.push({path});
      if (path.startsWith('/api/artifacts?')) return {artifacts};
      if (path.endsWith('/preview')) return {artifact: artifacts[0], text: '# 正文', truncated: true};
      if (path.endsWith('/content')) return {artifact: artifacts[0], encoding: 'base64', data: Buffer.from('# 完整正文').toString('base64')};
      throw new Error('Unexpected HTTP request');
    },
  };
  const bridge = createMirallyNativeBridge(root, api); bridge.install();
  let sequence = 0;
  async function send(action, params = {}) {
    const requestId = 'request-' + ++sequence;
    bridge.onNativeMessage({type: 'mirally-activity', action, requestId, ...params});
    for (let index = 0; index < 100; index++) {
      const reply = replies.find(item => item.requestId === requestId); if (reply) return reply;
      await new Promise(resolve => setTimeout(resolve, 1));
    }
    throw new Error('No bridge response');
  }
  return {root, api, bridge, calls, replies, geometry, notices, opened, captures, activity, memory, settings, artifacts, send};
}

test('real native geometry remains delegated while Mirally product messages use the owned RPC', async () => {
  const f = fixture({native: true}), webview = f.root.chrome.webview;
  webview.postMessage({type: 'mirally-companion', action: 'shape', width: 48});
  assert.deepEqual(f.geometry, [{type: 'mirally-companion', action: 'shape', width: 48}]);
  const reply = await f.send('status');
  assert.equal(reply.result.activity.config_fingerprint, 'scope-one');
  assert.equal(f.geometry.length, 1); assert.equal(typeof webview.addEventListener, 'function');
  f.bridge.dispose(); webview.postMessage({type: 'mirally-companion', action: 'workspace'});
  assert.equal(f.geometry.length, 2);
});

test('browser shim supports page shapes while capture and OS actions report native unavailability', () => {
  const f = fixture(), send = f.root.chrome.webview.postMessage;
  assert.equal(f.root.chrome.webview.__mirallyBrowserOnly, true);
  assert.equal(typeof f.root.chrome.webview.addEventListener, 'function');
  const handler = () => {};
  f.root.chrome.webview.addEventListener('message', handler);
  f.root.chrome.webview.removeEventListener('message', handler);
  send({type: 'mirally-companion', action: 'shape'});
  send({type: 'mirally-companion', action: 'capture-target', requestId: 'capture'});
  send({type: 'mirally-companion', action: 'drag'});
  send({type: 'mirally-ufo-input', editing: true});
  assert.match(f.captures[0].error, /需要 Mirally 桌面窗口/);
  assert.equal(f.notices.length, 1); assert.deepEqual(f.calls, []);
});

test('persisted collector snapshots cannot impersonate a live supervised collector', () => {
  const f = fixture(); f.activity.snapshot = {collector: {status: 'running'}};
  const status = projectStatus(f.activity, f.memory, f.settings);
  assert.equal(status.activity.collector.status, 'disabled'); assert.equal(status.activity.live_enabled, false);
  assert.equal(status.memory.semantics.max_calls_per_hour, 4);
  assert.equal(status.memory.semantics.effective_model, 'fixture-model');
});

test('enabling capture requires a current scope decision and submits only the reviewed fingerprint', async () => {
  const denied = fixture();
  assert.equal((await denied.send('set-enabled', {enabled: true})).result.cancelled, true);
  assert.equal(denied.calls.some(item => item.method === 'activity.start'), false);
  const accepted = fixture({confirm: true});
  assert.equal((await accepted.send('start')).result.saved, true);
  assert.deepEqual(accepted.calls.find(item => item.method === 'activity.start').params, {confirmed: true, expected_fingerprint: 'scope-one'});
});

test('stale capture session control cannot stop or resume a different approved scope', async () => {
  const f = fixture({confirm: true});
  assert.match((await f.send('pause', {session_id: 'old-scope'})).error, /已变化/);
  assert.equal(f.calls.some(item => item.method === 'activity.pause'), false);
  assert.equal((await f.send('resume', {session_id: 'mirally-activity:scope-one'})).result.saved, true);
  assert.deepEqual(f.calls.find(item => item.method === 'activity.resume').params, {confirmed: true, expected_fingerprint: 'scope-one'});
});

test('a pending native confirmation serializes mutations without starting duplicate captures', async () => {
  const f = fixture(); let confirm;
  f.api.confirmCapture = () => new Promise(resolve => { confirm = resolve; });
  f.bridge.onNativeMessage({type: 'mirally-activity', action: 'start', requestId: 'first'});
  for (let index = 0; index < 20 && !confirm; index++) await new Promise(resolve => setTimeout(resolve, 1));
  assert.match((await f.send('set-enabled', {enabled: true})).error, /尚未完成/);
  confirm(false); await new Promise(resolve => setTimeout(resolve, 1));
  assert.equal(f.calls.some(item => item.method === 'activity.start'), false);
});

test('semantic edits save actual budgets atomically and report asynchronous application honestly', async () => {
  const f = fixture(), reply = await f.send('set-semantics', {enabled: true, model: '', maxCallsPerHour: 7, maxTokensPerDay: 30000});
  assert.equal(reply.result.saved, true); assert.equal(reply.result.applied, false);
  assert.deepEqual(f.calls[0], {method: 'settings.update', params: {patch: {background_enabled: true, memory_semantics: {max_calls_per_hour: 7, max_tokens_per_day: 30000}}}});
  const unsupported = await f.send('set-semantic-model', {model: 'another/provider', maxOutputTokens: 2000});
  assert.match(unsupported.error, /模型连接页选择后台用途/); assert.equal(f.calls.length, 1);
});

test('semantic status reads the assigned background connection instead of the chat default', async () => {
  const f=fixture();f.settings.model_routes={chat:'default',background:'worker'};
  f.settings.model_connections={worker:{name:'Background',model:{model_id:'background-only'}}};
  assert.equal((await f.send('model-settings')).result.effective_model,'background-only');
  assert.equal((await f.send('status')).result.memory.semantics.effective_model,'background-only');
});

test('candidate reads require an explicit candidate search and retain real pagination/filter names', async () => {
  const f = fixture();
  await f.send('memory-search', {include_candidates: true, offset: 20, start_utc_ms: 1000, end_utc_ms: 2000, provenances: ['observed']});
  await f.send('memory-read', {memory_id: 'candidate', from_line: 51, line_count: 50});
  assert.deepEqual(f.calls[0].params, {include_candidates: true, offset: 20, start_utc_ms: 1000, end_utc_ms: 2000, provenances: ['observed']});
  assert.deepEqual(f.calls[1].params, {memory_id: 'candidate', from_line: 51, line_count: 50, include_candidates: true});
  await f.send('memory-search', {include_candidates: false});
  await f.send('memory-read', {memory_id: 'candidate'});
  assert.equal(f.calls.at(-1).params.include_candidates, false);
});

test('native Files list and preview use actual conversation receipts and show partial previews honestly', async () => {
  const f = fixture();
  const listed = await f.bridge.requestNative('sessions.files.list', {sessionKey: 'session-one', path: '', search: '中文'});
  assert.equal(listed.files.length, 1); assert.equal(listed.files[0].kind, 'modified');
  assert.equal(listed.browser.entries[0].path, 'a'.repeat(36) + '-中文成果.md');
  const read = await f.bridge.requestNative('sessions.files.get', {sessionKey: 'session-one', path: listed.files[0].path});
  assert.equal(read.file.previewKind, 'text'); assert.equal(read.file.contentEncoding, 'utf8');
  assert.match(read.file.content, /预览已截短/); assert.equal(read.file.size, 120);
  assert.equal(f.calls.at(-1).path, '/api/artifacts/' + 'a'.repeat(36) + '/preview');
});

test('native Files cannot read outside registered receipts, another conversation, or ambiguous names', async () => {
  const f = fixture();
  for (const path of ['../secret.txt', 'C:\\private\\secret.txt', '/etc/passwd', 'not-a-result.md']) {
    await assert.rejects(f.bridge.requestNative('sessions.files.get', {sessionKey: 'session-one', path}));
  }
  await assert.rejects(f.bridge.requestNative('sessions.files.get', {sessionKey: 'session-two', path: '中文成果.md'}));
  f.artifacts.push({...f.artifacts[0], id: 'b'.repeat(36)});
  await assert.rejects(f.bridge.requestNative('sessions.files.get', {sessionKey: 'session-one', path: '中文成果.md'}), /同名/);
  await assert.rejects(f.bridge.requestNative('sessions.files.set', {sessionKey: 'session-one', path: '中文成果.md', content: 'write'}), /只可读取/);
  assert.equal(f.calls.some(item => item.path?.endsWith('/preview')), false);
});

test('native artifact download returns a real receipt-scoped download URL and enforces conversation ownership', async () => {
  const f = fixture(), params = {sessionKey: 'session-one', artifactId: 'a'.repeat(36)};
  const list = await f.bridge.requestNative('artifacts.list', {sessionKey: 'session-one'});
  assert.equal(list.artifacts[0].download.mode, 'bytes');
  const download = await f.bridge.requestNative('artifacts.download', params);
  assert.equal(download.url, '/api/artifacts/' + 'a'.repeat(36) + '/download');
  assert.equal(download.artifact.title, '中文成果.md');
  assert.equal(Buffer.from(download.data, 'base64').toString('utf8'), '# 完整正文');
  await assert.rejects(f.bridge.requestNative('artifacts.download', {...params, sessionKey: 'session-two'}), /没有/);
  await assert.rejects(f.bridge.requestNative('artifacts.download', {...params, taskId: 'unmapped-task'}), /尚未支持/);
});

test('opening history uses only the authoritative loopback URL and reports blocked popups', async () => {
  const f = fixture();
  assert.equal((await f.send('browse')).result.opened, true); assert.equal(f.opened.length, 1);
  f.root.open = () => null;
  assert.match((await f.send('monitor')).error, /阻止/);
  f.api.rpc = async () => ({url: 'https://remote.invalid/?token=' + 't'.repeat(32)});
  assert.match((await f.send('browse')).error, /有效的本机/);
});

test('unknown native product fields are rejected without forwarding them to the old host', async () => {
  const f = fixture({native: true});
  assert.match((await f.send('status', {unexpected: true})).error, /不支持/);
  assert.equal(f.geometry.length, 0); assert.equal(f.calls.length, 0);
});
