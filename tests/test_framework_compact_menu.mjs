/** Menu placement and ownership around the real retained compaction handler. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const ts = createRequire(new URL('../frontend/control-ui/package.json', import.meta.url))('typescript');
function compile(path) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const result = ts.transpileModule(source, {compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
    experimentalDecorators: true, useDefineForClassFields: false,
  }});
  return result.outputText.replace(/^import[\s\S]*?;\s*/gm, '').replace(/^export /gm, '');
}
const handlerCode = compile('../frontend/control-ui/ui/src/pages/chat/chat-pane-session-menu.ts');
const menuCode = compile('../frontend/control-ui/ui/src/pages/chat/components/chat-header-session-menu.ts');
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

function handlerFixture(execute) {
  const calls = [], toasts = [];
  const row = {key: 'agent:main:one', sessionId: 'native-one'};
  const gateway = {snapshot: {phase: 'connected', client: {id: 'first'}}};
  const state = {
    sessionKey: row.key, row, chatMessage: 'unfinished draft',
    chatAttachments: [{id: 'material'}],
    refreshCurrentChat: async () => { calls.push({kind: 'refresh'}); },
  };
  const sandbox = {
    ChatPaneContext: class {
      connectionGeneration = 1;
      headerOutcomeOwner = 'first:one';
      isConnected = true;
      presented = true;
      requestUpdate() {}
      ownsHeaderOutcome(owner) { return this.presented && owner === this.headerOutcomeOwner; }
    },
    readSessionMethodAccess: () => ({allowed: true}),
    areUiSessionKeysEquivalent: (left, right) => left === right,
    selectedChatSessionRow: current => current.row,
    resolveChatAgentId: () => 'main',
    formatUiError: error => error instanceof Error ? error.message : String(error),
    t: key => key,
    showToast: value => { toasts.push(value.message); },
    executeSlashCommand: async (...args) => {
      calls.push({kind: 'command', args});
      return execute(...args);
    },
  };
  vm.runInNewContext(handlerCode + '\nglobalThis.Page = ChatPaneSessionMenu;', sandbox);
  const page = new sandbox.Page();
  Object.assign(page, {
    state, context: {gateway, sessions: {}, agentSelection: {state: {selectedId: 'main'}}},
    headerSessionMutationAbortController: new AbortController(),
  });
  const compact = () => page.handleHeaderSessionAction({kind: 'compact-context'}, row);
  return {page, state, row, gateway, calls, toasts, compact};
}

test('menu compaction uses the current session executor, refreshes, and preserves the composer', async () => {
  const f = handlerFixture(async () => ({action: 'refresh'}));
  const attachments = f.state.chatAttachments;
  await f.compact();
  const [command, refresh] = f.calls;
  assert.deepEqual(command.args.slice(1, 4), ['agent:main:one', 'compact', '']);
  assert.equal(command.args[4].sessions, f.page.context.sessions);
  assert.equal(command.args[4].isCurrent(), true);
  assert.equal(refresh.kind, 'refresh');
  assert.deepEqual(f.toasts, ['chat.composer.contextCompacted']);
  assert.equal(f.state.chatMessage, 'unfinished draft');
  assert.strictEqual(f.state.chatAttachments, attachments);
  assert.equal(f.page.isHeaderSessionCompacting(f.row), false);
});

test('a skipped compaction presents its outcome without claiming context was compacted', async () => {
  const f = handlerFixture(async () => ({action: 'refresh', content: 'Nothing to compact.'}));
  await f.compact();
  assert.deepEqual(f.toasts, ['Nothing to compact.']);
  assert.equal(f.calls.filter(call => call.kind === 'refresh').length, 1);
});

test('a failed compaction stays in the owning error surface and releases its busy state', async () => {
  const f = handlerFixture(async () => ({failed: true, content: 'Compaction failed.'}));
  await f.compact();
  assert.equal(f.state.chatError, 'Compaction failed.');
  assert.equal(f.state.lastError, 'Compaction failed.');
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.toasts, []);
  assert.equal(f.page.isHeaderSessionCompacting(f.row), false);
});

test('an unexpected executor error releases busy state and can be retried', async () => {
  let attempts = 0;
  const f = handlerFixture(async () => {
    if (++attempts === 1) throw new Error('Connection interrupted');
    return {action: 'refresh'};
  });
  await f.compact();
  assert.equal(f.state.chatError, 'Connection interrupted');
  assert.equal(f.page.isHeaderSessionCompacting(f.row), false);
  await f.compact();
  assert.equal(attempts, 2);
  assert.equal(f.calls.filter(call => call.kind === 'refresh').length, 1);
});

test('repeated menu activation cannot start a second compaction before the first settles', async () => {
  const pending = deferred();
  const f = handlerFixture(() => pending.promise);
  const saving = f.compact();
  assert.equal(f.page.isHeaderSessionCompacting(f.row), true);
  await f.compact();
  assert.equal(f.calls.length, 1);
  pending.resolve({action: 'refresh'});
  await saving;
  assert.equal(f.page.isHeaderSessionCompacting(f.row), false);
});

test('switching conversations discards late outcomes without touching the new draft', async () => {
  const pending = deferred();
  const f = handlerFixture(() => pending.promise);
  const saving = f.compact();
  f.state.sessionKey = 'agent:main:two';
  f.state.row = {key: f.state.sessionKey, sessionId: 'native-two'};
  f.state.chatMessage = 'second draft';
  pending.resolve({action: 'refresh'});
  await saving;
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.toasts, []);
  assert.equal(f.state.chatError, undefined);
  assert.equal(f.state.chatMessage, 'second draft');
});

test('session replacement and connection replacement reject late outcomes', async () => {
  for (const replace of [
    f => { f.state.row = {...f.row, sessionId: 'replacement'}; },
    f => { f.gateway.snapshot.client = {id: 'reconnected'}; },
  ]) {
    const pending = deferred();
    const f = handlerFixture(() => pending.promise);
    const saving = f.compact();
    replace(f);
    pending.resolve({failed: true, content: 'Old operation failed.'});
    await saving;
    assert.equal(f.calls.length, 1);
    assert.deepEqual(f.toasts, []);
    assert.equal(f.state.chatError, undefined);
    assert.equal(f.page.isHeaderSessionCompacting(f.row), false);
  }
});

test('archived, running, disconnected, and replaced targets do not dispatch compaction', async () => {
  for (const prepare of [
    f => { f.row.archived = true; },
    f => { f.row.hasActiveRun = true; },
    f => { f.state.chatRunId = 'live-run'; },
    f => { f.gateway.snapshot.phase = 'offline'; },
    f => { f.state.row = {...f.row, sessionId: 'replacement'}; },
  ]) {
    const f = handlerFixture(async () => { throw new Error('Must not dispatch'); });
    prepare(f);
    await f.compact();
    assert.deepEqual(f.calls, []);
    assert.deepEqual(f.toasts, []);
  }
});

const html = (strings, ...values) => ({strings, values});
const stringify = value => value == null || typeof value === 'symbol' || typeof value === 'function'
  ? '' : Array.isArray(value) ? value.map(stringify).join('')
  : value?.strings ? value.strings.map((part, i) => part + stringify(value.values[i])).join('')
  : String(value);

function menuFixture(compact) {
  const actions = [];
  const sandbox = {
    html, nothing: Symbol('nothing'), property: () => () => {}, state: () => () => {},
    OpenClawLightDomElement: class {},
    EMPTY_SESSION_MENU_DATA: {label: 'Current conversation', sessionId: 'native-one'},
    SessionMenuActions: class { handleSelect() { return false; } },
    compactSessionMenuViewForValue: () => undefined,
    renderMirallySessionOrganization: () => '',
    icons: new Proxy({}, {get: (_target, key) => String(key)}),
    t: key => key,
    customElements: {get() {}, define() {}},
  };
  vm.runInNewContext(menuCode + '\nglobalThis.Page = ChatHeaderSessionMenu;', sandbox);
  const page = new sandbox.Page();
  page.compact = compact;
  page.onAction = value => { actions.push(value.kind); };
  return {page, actions};
}

test('standard and compact/mobile header menus expose the same compaction action', () => {
  for (const compact of [false, true]) {
    const f = menuFixture(compact);
    const rendered = stringify(f.page.render());
    assert.match(rendered, /value="compact-context"/);
    assert.match(rendered, /压缩上下文/);
    const dropdown = {open: true};
    f.page.handleSelect({detail: {item: {value: 'compact-context'}}, currentTarget: dropdown});
    assert.deepEqual(f.actions, ['compact-context']);
    assert.equal(dropdown.open, false);
  }
});

test('the header menu respects compaction capability restrictions and renders a busy outcome', () => {
  const f = menuFixture(true);
  f.page.actionDisabledReasons = {'compact-context': 'Disconnected'};
  const dropdown = {open: true};
  f.page.handleSelect({detail: {item: {value: 'compact-context'}}, currentTarget: dropdown});
  assert.deepEqual(f.actions, []);
  f.page.contextCompacting = true;
  assert.match(stringify(f.page.render()), /正在压缩…/);
});
