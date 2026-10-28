/** Exercise the actual Lit page class without a host, account, collector, or model. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const requireUi = createRequire(new URL('../frontend/control-ui/package.json', import.meta.url));
const ts = requireUi('typescript');
const source = readFileSync(new URL('../frontend/control-ui/ui/src/pages/mirally/channels-page.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022, experimentalDecorators: true,
  useDefineForClassFields: false,
}}).outputText.replace(/^import[\s\S]*?;\s*/gm, '').replace(/^export /gm, '');
const baseStatus = {state: 'unbound', enabled: false, bound: false, installation_id: 'fixture', revision: 1};
const stringify = value => value === undefined || value === null || typeof value === 'symbol' || typeof value === 'function' ? ''
  : Array.isArray(value) ? value.map(stringify).join('') : value?.strings
    ? value.strings.map((part, index) => part + stringify(value.values[index])).join('') : String(value);
const html = (strings, ...values) => ({strings, values});
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};};

function fixture(handler = method => method === 'conversation.list' ? {conversations: []}
  : method === 'settings.get' ? {weixin_remote_control_enabled: false} : {...baseStatus}) {
  const calls = [], timers = new Map(), listeners = new Map(), toggles=[]; let timerId = 0;
  const document = {hidden: false, addEventListener: (name, fn) => listeners.set('document.' + name, fn),
    removeEventListener: name => listeners.delete('document.' + name)};
  const sandbox = {
    html, nothing: Symbol('nothing'), live: value => value, document,
    customElement: () => target => target, state: () => () => {},
    OpenClawLightDomElement: class {isConnected = true; connectedCallback() {} disconnectedCallback() {}},
    window: {addEventListener: (name, fn) => listeners.set('window.' + name, fn),
      removeEventListener: name => listeners.delete('window.' + name)},
    setTimeout: (fn, delay) => {const id = ++timerId; timers.set(id, {fn, delay}); return id;},
    clearTimeout: id => timers.delete(id),
    rpcProduct: async (method, params = {}) => {calls.push({method, params}); return handler(method, params);},
    renderSettingsEmpty: value => value,
    renderSettingsValue: value => value,
    renderSettingsPage: value => value,
    renderSettingsWorkspace: value => value,
    renderSettingsPageHeader: props => html`${props.title}${props.subtitle}${props.actions}`,
    renderSettingsRow: props => html`${props.title}${props.description}${props.control}`,
    renderSettingsSection: (props, content) => html`${props.title}${props.description}${content}`,
    renderSettingsStatus: props => props.label,
    renderSettingsToggle: props => {toggles.push(props);return props.ariaLabel;},
  };
  vm.runInNewContext(compiled + '\nglobalThis.Page = MirallyChannelsPage;', sandbox);
  return {page: new sandbox.Page(), document, calls, timers, listeners, toggles,
    render: page => stringify(page.render())};
}

test('channels renders local QR, requires explicit bind, and never renders protocol errors', () => {
  const f = fixture();
  f.page.status = {...baseStatus, state: 'waiting_scan', login: {id: 'login-1', qr_content: 'https://fixture.test/qr', expires_at: 1900000000000}};
  const qr = f.render(f.page);
  assert.match(qr, /wa-qr-code/); assert.match(qr, /https:\/\/fixture.test\/qr/);
  assert.doesNotMatch(qr, /<img|确认绑定<\/button>/);
  f.page.status = {...baseStatus, state: 'awaiting_confirmation', peer_id: 'my-weixin', last_error: 'secret-token remote-body'};
  const bind = f.render(f.page);
  assert.match(bind, /mirally-weixin-bind/); assert.match(bind, /my-weixin/);
  assert.doesNotMatch(bind, /secret-token|remote-body/); assert.equal(f.calls.length, 0);
});

test('Weixin help and optional web controls are available in separate closed disclosures', () => {
  const f = fixture();
  f.page.status = {...baseStatus, state: 'connected', bound: true, enabled: true};
  const rendered = f.render(f.page);
  assert.match(rendered, /电脑需联网并保持 Mirally 运行/);
  assert.match(rendered, /<details data-testid="mirally-weixin-help"><summary>微信使用帮助/);
  assert.match(rendered, /<details data-testid="mirally-mobile-options"><summary>手机网页（可选）/);
  assert.doesNotMatch(rendered, /<details[^>]*\bopen\b/);
  for (const command of ['/history [更早游标]', '/works', '/work 工作编号', '/work-use 工作编号', '/work-review 完整审批编号', '/work-approve 确认码', '/work-cancel 完整运行编号', '/work-logs 完整运行编号 [字节偏移]', '/work-notify [on|off]', '/stop']) {
    assert.ok(rendered.includes(command), `The real settings should introduce ${command}`);
  }
  assert.match(rendered, /会话后台命令/);
  assert.match(rendered, /“菜单”或“帮助”查看全部命令/);
  assert.match(rendered, /每次返回最近 6 条/);
  assert.doesNotMatch(rendered, /暂不执行命令/);
  assert.equal(f.calls.length, 0, 'Reading instructions must not configure or execute anything');
});

test('verification challenges and expired credentials have distinct recovery controls', () => {
  const f = fixture();
  f.page.status = {...baseStatus, state: 'need_verifycode', login: {id: 'challenge', needs_verify_code: true}};
  assert.match(f.render(f.page), /autocomplete="one-time-code"/);
  f.page.status = {...baseStatus, bound: true, enabled: true, state: 'login_required'};
  assert.match(f.render(f.page), /重新扫码登录/);
  f.page.status = {...baseStatus, state: 'expired'};
  assert.match(f.render(f.page), /重新生成二维码/);
});

test('mutation busy guard sends once and binding uses an explicit captured revision', async () => {
  const pending = deferred(); const f = fixture(() => pending.promise);
  f.page.acceptStatus({...baseStatus, state: 'awaiting_confirmation', revision: 9});
  f.page.selection = 'existing';
  const params = f.page.bindingParams();
  const first = f.page.mutate('weixin.bind', params), duplicate = f.page.mutate('weixin.bind', params);
  assert.equal(f.calls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0])), {method: 'weixin.bind', params: {expected_revision: 9, conversation_id: 'existing'}});
  pending.resolve({...baseStatus, state: 'connected', bound: true, enabled: true, revision: 10, conversation_id: 'existing'});
  await Promise.all([first, duplicate]);
  assert.equal(f.page.busy, false); assert.equal(f.page.status.revision, 10);
});

test('a late status read cannot replace a completed binding or reselect its conversation', async () => {
  const read = deferred(); const f = fixture(method => method === 'weixin.status' ? read.promise
    : {...baseStatus, revision: 3, state: 'connected', bound: true, conversation_id: 'new'});
  const refresh = f.page.refresh(false);
  await f.page.mutate('weixin.bind', {expected_revision: 2});
  read.resolve({...baseStatus, revision: 2, state: 'awaiting_confirmation'});
  await refresh;
  assert.equal(f.page.status.revision, 3); assert.equal(f.page.selection, 'new');
});

test('polling does not overwrite an unsaved selection and rejects older revisions', () => {
  const f = fixture();
  f.page.acceptStatus({...baseStatus, bound: true, revision: 5, conversation_id: 'one'});
  f.page.selection = 'two'; f.page.selectionDirty = true;
  f.page.acceptStatus({...baseStatus, bound: true, revision: 5, conversation_id: 'one'});
  assert.equal(f.page.selection, 'two');
  f.page.acceptStatus({...baseStatus, revision: 4});
  assert.equal(f.page.status.revision, 5); assert.equal(f.page.selection, 'two');
});

test('detach removes listeners/timers and discards late login responses', async () => {
  const pending = deferred(); const f = fixture(() => pending.promise);
  f.page.connectedCallback(); assert.equal(f.listeners.size, 2);
  f.page.loading = false;
  f.page.schedulePoll(); assert.equal(f.timers.size, 1);
  const mutation = f.page.mutate('weixin.login.start');
  f.page.disconnectedCallback(); f.page.isConnected = false;
  pending.resolve({...baseStatus, state: 'waiting_scan', login: {id: 'late', qr_content: 'private-qr'}});
  await mutation;
  assert.equal(f.page.status, null); assert.equal(f.page.verificationCode, ''); assert.equal(f.timers.size, 0);
  assert.equal(f.listeners.size, 0);
});

test('hidden documents do not poll; visible connected pages schedule one two-second refresh', async () => {
  const f = fixture(); f.document.hidden = true;
  await f.page.refresh(true); assert.equal(f.calls.length, 0); assert.equal(f.timers.size, 0);
  f.document.hidden = false;
  await f.page.refresh(true);
  assert.deepEqual(f.calls.map(call => call.method), ['weixin.status', 'settings.get', 'conversation.list']);
  assert.equal(f.calls[2].params.archived_filter, 'active');
  assert.equal(f.timers.size, 1); assert.equal([...f.timers.values()][0].delay, 2000);
  f.document.hidden = true; f.page.visibilityChanged(); assert.equal(f.timers.size, 0);
});

test('unknown deliveries and unbind are explicit; failures do not leak remote payloads', async () => {
  const f = fixture(() => {throw new Error('secret bot token and raw response');});
  f.page.status = {...baseStatus, state: 'connected', bound: true, enabled: true, outbox: {pending: 1, unknown: 2, failed: 1}};
  assert.match(f.render(f.page), /为避免重复发送/);
  assert.doesNotMatch(f.render(f.page), /mirally-weixin-unbind-confirm/);
  f.page.confirmingUnbind = true;
  assert.match(f.render(f.page), /mirally-weixin-unbind-confirm/);
  await f.page.mutate('weixin.unbind', {expected_revision: 1});
  assert.match(f.page.error, /刷新连接状态/);
  assert.doesNotMatch(f.render(f.page), /secret bot token|raw response/);
});

test('conversation records normalize the public identity and passive polls preserve mutation failures', async () => {
  let fail = true;
  const f = fixture(method => {
    if (method === 'conversation.list') return {conversations: [{conversation_id: 'current', title: 'Current'}, {id: 'legacy', title: 'Legacy'}, {title: 'Missing'}]};
    if (method === 'weixin.status') return {...baseStatus};
    if (method === 'settings.get') return {weixin_remote_control_enabled: false};
    if (fail) throw new Error('private response');
    return {...baseStatus};
  });
  await f.page.refresh(true);
  assert.deepEqual(JSON.parse(JSON.stringify(f.page.conversations)), [{id: 'current', title: 'Current'}, {id: 'legacy', title: 'Legacy'}]);
  await f.page.mutate('weixin.login.start');
  const message = f.page.error;
  assert.ok(message); await f.page.refresh(false); assert.equal(f.page.error, message);
  fail = false; await f.page.refresh(true); assert.equal(f.page.error, '');
});

test('remote control requires a binding and persists explicit settings changes', async () => {
  let enabled = false;
  const f = fixture((method, params) => {
    if (method === 'weixin.status') return {...baseStatus};
    if (method === 'settings.get') return {weixin_remote_control_enabled: enabled};
    if (method === 'settings.update') {
      assert.equal(params.patch.weixin_remote_control_enabled, true);
      enabled = true; return {weixin_remote_control_enabled: enabled};
    }
    return {conversations: []};
  });
  await f.page.refresh(true);
  f.render(f.page); assert.equal(f.toggles.at(-1).disabled,true);
  f.page.acceptStatus({...baseStatus, state: 'connected', bound: true, enabled: true});
  f.render(f.page); assert.equal(f.toggles.at(-1).disabled,false);
  await f.page.setRemoteControl(true);
  assert.equal(f.page.remoteControl, true);
  assert.equal(f.calls.filter(call => call.method === 'settings.update').length, 1);
  assert.match(f.page.notice, /已允许/);
});

test('remote control save failure preserves the previous value and hides protocol errors', async () => {
  const f = fixture(method => {
    if (method === 'settings.update') throw new Error('private response');
    return {...baseStatus};
  });
  f.page.acceptStatus({...baseStatus, state: 'connected', bound: true, enabled: true});
  f.page.remoteControl = false;
  await f.page.setRemoteControl(true);
  assert.equal(f.page.remoteControl, false);
  assert.match(f.page.error, /未能保存/);
  assert.doesNotMatch(f.render(f.page), /private response/);
  f.render(f.page); assert.equal(f.toggles.at(-1).checked,false);
});
