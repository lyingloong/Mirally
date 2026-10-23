import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const require = createRequire(import.meta.url);
const {createExperienceAdapter} = require('../mirally/framework_ui/experience-adapter.js');
const office = require('../mirally/assets/control-ui-office.js');

function fixture({native = false} = {}) {
  const listeners = new Set(), calls = [], notices = [];
  const state = {current: {id: 'one', title: 'First'}, sessions: [{id: 'one', title: 'First'}, {id: 'two', title: 'Second'}],
    connected: true, busy: false, messages: [], attachments: [], actions: [], artifacts: [], page: 'chat'};
  let draft = 'original draft';
  const node = {querySelector: () => null, querySelectorAll: () => []};
  const root = {document: {querySelector: selector => ['#page-chat', '#sidebar footer'].includes(selector) ? node : null},
    chrome: native ? {webview: {postMessage: message => calls.push(['native', message])}} : {},
    dispatchEvent: event => notices.push(event), CustomEvent: class {constructor(type, options) { this.type = type; this.detail = options.detail; }}};
  const api = {state, subscribe(callback) {listeners.add(callback); return () => listeners.delete(callback);},
    ensureSession: async () => {calls.push(['ensure']); state.current = {id: 'created'};},
    getDraft: () => draft, setDraft: value => {draft = value;},
    send: async (...args) => {calls.push(['send', ...args]); args[1]?.onOutboxAdmitted?.(); return true;},
    stop: () => calls.push(['stop']), attachFiles: async files => calls.push(['files', files]),
    nativeInput: () => ({kind: 'file-input'}), selectSession: id => {calls.push(['select', id]); state.current = {id};},
    showPage: page => {state.page = page;}, openFiles: () => calls.push(['open-files']),
    removeAttachment: id => {state.attachments = state.attachments.filter(item => item.id !== id);},
    getHistory: () => state.messages, getActions: () => state.actions, getArtifacts: () => state.artifacts,
    openArtifact: id => calls.push(['open-artifact', id]),
  };
  const adapter = createExperienceAdapter(api, root);
  root.__mirallyExperience = adapter;
  return {api, root, state, adapter, calls, notices, notify: () => [...listeners].forEach(callback => callback())};
}

test('retained surfaces project the owned UI state and preserve per-conversation identity', () => {
  const f = fixture();
  const first = f.adapter.active();
  assert.equal(first.state.chatMessage, 'original draft');
  assert.strictEqual(f.adapter.active(), first);
  f.state.current = {id: 'two'};
  assert.notStrictEqual(f.adapter.active(), first);
  assert.throws(() => first.state.handleChatDraftChange('wrong conversation'), /会话已经切换/);
  f.state.current = {id: 'one'};
  assert.strictEqual(f.adapter.active(), first);
});
test('a rotated native session retires pending capture identity without dropping the logical work draft key', () => {
  const f = fixture();
  const before = f.adapter.active();
  f.state.current = {...f.state.current, session_id: 'new-native', generation: 2};
  const after = f.adapter.active();
  assert.notStrictEqual(after, before);
  assert.notStrictEqual(after.state, before.state);
  assert.equal(after.paneId, before.paneId);
  assert.equal(after.state.sessionKey, before.state.sessionKey);
  assert.throws(() => before.state.handleChatDraftChange('retired'), /会话已经切换/);
  assert.throws(() => before.state.handleAbortChat(), /会话已经切换/);
});
test('companion appearance delegates to persistent home UI preferences', async () => {
  const f = fixture();
  f.state.uiPreferences = {'mirally.companion.pet': 'fox'};
  f.api.setUiPreference = async (key, value) => {f.state.uiPreferences[key] = value; f.calls.push(['ui-preference', key, value]);};
  assert.equal(f.adapter.getPreference('mirally.companion.pet'), 'fox');
  await f.adapter.setPreference('mirally.companion.form', 'pet');
  assert.deepEqual(f.calls[0], ['ui-preference', 'mirally.companion.form', 'pet']);
});
test('work submission uses the native admission callback while retaining the conversation draft', async () => {
  const f = fixture(); let admitted = 0;
  await f.adapter.active().state.handleSendChat('prepared work', {allow_file_write: true, onOutboxAdmitted: () => admitted++});
  assert.equal(admitted, 1);
  assert.deepEqual(f.calls[0].slice(0, 2), ['send', 'prepared work']);
  assert.equal(f.calls[0][2].preserveDraft, true);
  assert.equal(f.calls[0][2].allow_file_write, true);
  assert.equal(f.adapter.active().state.chatMessage, 'original draft');
});
test('running model work still accepts an explicit follow-up but submitting state remains visible', () => {
  const f = fixture(); f.state.busy = true;
  assert.equal(f.adapter.active().state.chatSending, false);
  assert.equal(f.adapter.active().runActivity.working, true);
  f.state.submitting = true;
  assert.equal(f.adapter.active().state.chatSending, true);
});
test('real pending desktop questions and other sessions drive attention state', () => {
  const f = fixture();
  f.state.actions = [{id: 'a', conversation_id: 'one', state: 'waiting_for_user', question: {id: 'confirm', text: 'Allow?'}},
    {id: 'b', conversation_id: 'two', state: 'running'}];
  const pane = f.adapter.active();
  assert.equal(pane.questionProjection.prompts.length, 1);
  assert.equal(pane.runActivity.working, true);
  assert.equal(f.adapter.background(pane.state)[0].working, true);
  f.adapter.openAttention(); assert.equal(f.state.page, 'actions');
  pane.state.handleAbortChat(); assert.deepEqual(f.calls.at(-1), ['stop']);
});
test('material reading and thumbnails come from actual UI attachments', () => {
  const f = fixture();
  f.state.attachments = [{id: 'a', fileName: 'photo.png', media_type: 'image/png', state: 'reading', progress: .5, previewUrl: 'blob:local-photo'}];
  const pane = f.adapter.active();
  assert.equal(pane.chatState.attachmentReads.pendingReads, 1);
  assert.equal(pane.chatState.attachmentReads.project(pane.state.chatAttachments)[0].state, 'reading');
  assert.equal(pane.state.chatAttachments[0].mimeType, 'image/png');
  assert.deepEqual(f.adapter.attachmentPreviews(), ['blob:local-photo']);
  f.adapter.removeAttachment(pane, 'a'); assert.equal(f.state.attachments.length, 0);
});
test('real tool events project current progress and completion without mixing another conversation', () => {
  const f = fixture(); f.state.runId = 'command';
  f.state.tools = [{id: 'file', session_id: 'one', name: 'mirally_create_file', state: 'running', runId: 'command'},
    {id: 'foreign', session_id: 'two', name: 'read', state: 'running', runId: 'command'},
    {id: 'context', session_id: 'one', name: 'mirally_context', state: 'failed', error: 'offline', runId: 'older'}];
  const state = f.adapter.active().state;
  assert.deepEqual(state.toolStreamOrder, ['file', 'context']);
  assert.equal(state.toolStreamById.get('file').activity[0].title, '保存交付文件');
  assert.equal(state.toolStreamById.get('file').resultReceived, false);
  assert.equal(state.toolStreamById.get('context').runId, 'older');
  assert.equal(state.toolStreamById.get('context').resultReceived, true);
  assert.equal(state.toolStreamById.get('context').isError, true);
  f.state.tools.push({id: 'file', session_id: 'one', name: 'mirally_create_file', state: 'completed', runId: 'command'});
  assert.equal(f.adapter.active().state.toolStreamById.get('file').resultReceived, true);
});
test('browser-only bridge reports unavailable native capture and does not claim a desktop operation', () => {
  const f = fixture(); const results = [];
  f.root.__mirallyContext = {receive: result => results.push(result)};
  f.root.chrome.webview.postMessage({type: 'mirally-companion', action: 'capture-target', requestId: 'capture'});
  assert.match(results[0].error, /需要 Mirally 桌面窗口/);
  assert.equal(results[0].requestId, 'capture');
  f.root.chrome.webview.postMessage({type: 'mirally-companion', action: 'drag'});
  assert.equal(f.notices[0].type, 'mirally-experience-notice');
  assert.equal(f.calls.length, 0);
});
test('a real WebView2 bridge stays untouched', () => {
  const f = fixture({native: true});
  assert.equal(f.adapter.native, true);
  f.root.chrome.webview.postMessage({type: 'mirally-companion', action: 'dock'});
  assert.equal(f.calls[0][0], 'native');
});
test('subscription retirement stops presentation updates', () => {
  const f = fixture(); let renders = 0;
  const dispose = f.adapter.subscribe(() => renders++);
  f.notify(); dispose(); f.notify();
  assert.equal(renders, 1);
});
test('cancelled partial text does not become a new completed reply notice', () => {
  const track = office.createReplyTracker();
  track({scope: 'one', turn: 'one', reply: null, working: false});
  track({scope: 'one', turn: 'two', reply: null, working: true});
  assert.equal(track({scope: 'one', turn: 'two', reply: {key: 'partial', text: 'partial'}, working: false,
    completion: {phase: 'interrupted', sessionKey: 'one', runId: 'request'}}), null);
});
test('retained intake delegates files through the framework adapter without synthetic component DOM', async () => {
  const f = fixture(); const sandbox = {module: {exports: {}}, window: f.root, globalThis: f.root};
  vm.runInNewContext(readFileSync(new URL('../mirally/assets/control-ui-intake.js', import.meta.url), 'utf8'), sandbox);
  assert.strictEqual(sandbox.module.exports.active(), f.adapter.active());
  const files = [{name: 'notes.txt'}];
  assert.equal(await sandbox.module.exports.attach(files), true);
  assert.equal(f.calls[0][0], 'files');
  assert.equal(f.calls[0][1][0].name, 'notes.txt');
  f.api.attachFiles = async () => false;
  assert.equal(await sandbox.module.exports.attach(files), false);
});
