import {test} from 'node:test';
import assert from 'node:assert/strict';
import {installDesktopObservation} from '../kernel/dsh/packages/mirally/mirally-product/src/desktop-observation.mjs';

const windowRef = {hwnd: 123, pid: 456};
const bounds = {left: -10, top: 20, right: 90, bottom: 120};
const windowRecord = {...windowRef, title: 'Mirally Fixture', bounds};
const image = {status: 'ok', width: 4, height: 4, data: Buffer.from('jpeg').toString('base64'), annotated_targets: [1, 99]};
const toolName = {windows: 'mirally_desktop_windows', snapshot: 'mirally_desktop_snapshot', action: 'mirally_desktop_action'};
const exec = (id = 'session', signal = new AbortController().signal) => ({agent: {id}, signal});

function nativeSnapshot() {
  return {
    window: {...windowRecord, process_token: 'internal'},
    targets: [
      {id: 1, name: 'Search', type: 'Edit', bounds, actions: ['set_value'],
        identity: {runtime: [1, 2]}, value: 'private text'},
      {id: 2, name: 'Navigate', type: 'Button', bounds, actions: ['invoke'], identity: {runtime: [1, 3]}},
    ],
    truncated: false,
    redactions: [{private: 'redaction'}],
    image_safe: true,
  };
}

function fixture() {
  const registered = new Map(), approvals = [], dispatched = [], saved = [];
  const attachment = {id: 'image'};
  const host = {
    ctx: {tools: {register: tool => registered.set(tool.name, tool)}, get: name => name === 'attachments'
      ? {saveImage: async value => {saved.push(value); return attachment;}} : null},
    nativeConversations: new Map([['session', {conversation_id: 'conversation', generation: 1}]]),
    activeCommands: new Map([['session', 'command']]),
    requestPolicies: new Map([['session', {origin: 'desktop'}]]),
    consumedCommandId: agent => host.activeCommands.get(agent?.id),
    commandPolicy: agent => host.requestPolicies.get(agent?.id),
    checkCommandScope: async () => {},
    authorizations: {run: async (_authorization, operation) => operation()},
    guard: async () => {},
    actionGate: {request: async request => {approvals.push(request); return true;}},
    rpc: async () => ({commands: [{model_selection: {connection_id: 'provider', model_id: 'vision'}}]}),
    models: {assertImageInput: (_purpose, provider, model) => {
      assert.equal(provider, 'provider');
      assert.equal(model, 'vision');
    }},
  };
  const f = {
    host, registered, approvals, dispatched, saved, attachment, now: 0,
    provide: async (kind, args) => {
      if (kind === 'screen') return image;
      if (kind === 'windows') return {windows: [windowRecord], untrusted_content: true};
      if (kind === 'snapshot') return {...nativeSnapshot(), ...(args.include_image ? {image} : {})};
      if (kind === 'action') return {status: args.operation === 'invoke' ? 'sent' : 'verified', after: nativeSnapshot()};
      return {title: 'example'};
    },
  };
  installDesktopObservation(host, {now: () => f.now, provider: async (_host, kind, signal, args) => {
    dispatched.push({kind, args});
    return f.provide(kind, args, signal);
  }});
  f.call = (name, args = {}, e = exec()) => registered.get(name).execute(args, e);
  f.snapshot = async (options = {}, e = exec()) => {
    await f.call(toolName.windows, {}, e);
    return f.call(toolName.snapshot, {window: windowRef, ...options}, e);
  };
  return f;
}

const kinds = f => f.dispatched.map(item => item.kind);
const action = (snapshot, fields = {}) => ({snapshot_id: snapshot.snapshot_id, target_id: 2, operation: 'invoke', ...fields});

test('screen approval precedes capture; image is attached to the result', async () => {
  const f = fixture(), name = 'mirally_desktop_screenshot';
  const result = await f.call(name);
  assert.equal(f.approvals.length, 1);
  assert.match(f.approvals[0].detail, /会话附件/);
  assert.deepEqual(kinds(f), ['screen']);
  assert.equal(f.saved[0].mediaType, 'image/jpeg');
  assert.deepEqual(f.registered.get(name).output.render({}, result)[1], {type: 'image', attachment: f.attachment});
});

test('reject, mobile and abort never reach the desktop provider', async () => {
  for (const mode of ['reject', 'mobile', 'abort']) {
    const f = fixture(), controller = new AbortController();
    if (mode === 'reject') f.host.actionGate.request = async () => false;
    if (mode === 'mobile') f.host.requestPolicies.set('session', {origin: 'mobile'});
    if (mode === 'abort') controller.abort();
    await assert.rejects(f.call('mirally_desktop_screenshot', {}, exec('session', controller.signal)));
    assert.deepEqual(kinds(f), []);
    assert.deepEqual(f.saved, []);
  }
});

test('Weixin observation requires approval and rechecks a revoked scope before capture', async () => {
  const f = fixture();
  f.host.requestPolicies.set('session', {origin: 'weixin'});
  await f.call(toolName.windows);
  let revoked = false;
  f.host.checkCommandScope = async () => { if (revoked) throw Error('revoked'); };
  f.host.actionGate.request = async () => { revoked = true; return true; };
  await assert.rejects(f.call('mirally_desktop_screenshot'), /revoked/);
  assert.deepEqual(kinds(f), ['windows']);
  assert.deepEqual(f.saved, []);
});

test('origin changes after confirmation prevent observation', async () => {
  const f = fixture();
  f.host.actionGate.request = async () => {
    f.host.requestPolicies.set('session', {origin: 'mobile'});
    return true;
  };
  await assert.rejects(f.call('mirally_desktop_foreground'));
  assert.deepEqual(kinds(f), []);
});

test('an explicit screen screenshot is rejected for non-vision models', async () => {
  const f = fixture();
  f.host.models.assertImageInput = () => { throw new Error('no vision'); };
  await assert.rejects(f.call('mirally_desktop_screenshot'), /no vision/);
  assert.equal(f.approvals.length, 0);
  assert.deepEqual(kinds(f), []);
});

test('snapshot is tied to an approved discovery and the actual title', async () => {
  const f = fixture();
  await assert.rejects(f.call(toolName.snapshot, {window: windowRef}), /窗口列表/);
  assert.equal(f.approvals.length, 0);
  assert.deepEqual(kinds(f), []);

  await f.call(toolName.windows);
  const base = f.provide;
  f.provide = async (kind, args) => {
    if (kind !== 'snapshot') return base(kind, args);
    assert.deepEqual(args.window, {...windowRef, title: windowRecord.title});
    return {...nativeSnapshot(), window: {...windowRecord, title: 'Changed'}};
  };
  await assert.rejects(f.call(toolName.snapshot, {window: windowRef}), /窗口标题/);
  assert.match(f.approvals.at(-1).detail, /Mirally Fixture/);
  assert.deepEqual(f.saved, []);
});

test('window snapshot images are annotated, scoped and only published as attachments', async () => {
  const f = fixture();
  const result = await f.snapshot({include_image: true});
  assert.match(f.approvals.at(-1).detail, /窗口 .*会话附件/);
  assert.deepEqual(result.image, {status: 'ok', width: 4, height: 4, annotated_targets: [1]});
  assert.deepEqual(result.attachment, f.attachment);
  assert.equal(result.untrusted_content, true);
  assert.deepEqual(kinds(f), ['windows', 'snapshot']);
  assert.equal(f.saved.length, 1);
  const json = JSON.stringify(result);
  assert.doesNotMatch(json, /process_token|identity|runtime|redactions|image_safe|private text|anBlZw/);
  const rendered = f.registered.get(toolName.snapshot).output.render({}, result);
  assert.equal(rendered.length, 2);
  assert.equal(rendered[1].type, 'image');
  assert.doesNotMatch(rendered[0].text, /attachment/);
});

test('snapshot stays usable as text for non-vision and unavailable captures', async () => {
  for (const vision of [false, true]) {
    const f = fixture();
    if (!vision) f.host.models.assertImageInput = () => { throw Error('no vision'); };
    const base = f.provide;
    f.provide = (kind, args) => kind === 'snapshot'
      ? {...nativeSnapshot(), image: {status: 'unavailable', reason: 'private diagnostic'}}
      : base(kind, args);
    const result = await f.snapshot({include_image: true});
    assert.equal(f.dispatched.at(-1).args.include_image, vision);
    assert.deepEqual(result.image, {status: 'unavailable',
      reason: vision ? 'capture_unavailable' : 'vision_or_attachment_unavailable'});
    assert.equal(f.saved.length, 0);
    assert.equal(result.targets.length, 2);
    assert.doesNotMatch(JSON.stringify(result), /private diagnostic/);
  }
});

test('semantic SetValue uses snapshot identity and presents the exact value for approval', async () => {
  const f = fixture();
  const first = await f.snapshot();
  const request = action(first, {target_id: 1, operation: 'set_value', text: '猫と犬'});
  const result = await f.call(toolName.action, request);
  assert.equal(result.status, 'verified');
  assert.equal(result.after.untrusted_content, true);
  assert.notEqual(result.after.snapshot_id, first.snapshot_id);
  assert.doesNotMatch(JSON.stringify(result), /process_token|runtime|private text/);
  assert.equal(f.approvals.at(-1).kind, 'desktop_action');
  assert.match(f.approvals.at(-1).detail, /Search[\s\S]*猫と犬/);
  assert.deepEqual(f.dispatched.at(-1).args, {
    window: nativeSnapshot().window, target: nativeSnapshot().targets[0], operation: 'set_value', text: '猫と犬',
  });
  await assert.rejects(f.call(toolName.action, request), /快照已失效/);
  assert.deepEqual(kinds(f), ['windows', 'snapshot', 'action']);
  assert.equal((await f.call(toolName.action, action(result.after))).status, 'sent');
});

test('unsupported controls and invalid action arguments never reach approval or dispatch', async () => {
  const f = fixture();
  const snapshot = await f.snapshot();
  for (const request of [
    action(snapshot, {target_id: 1}),
    action(snapshot, {text: 'unexpected'}),
    action(snapshot, {operation: 'set_value', target_id: 1, text: 'tab\t'}),
    action(snapshot, {operation: 'set_value', target_id: 1, text: '\ud800'}),
    action(snapshot, {operation: 'click'}),
    action(snapshot, {coordinate: 20}),
  ]) {
    await assert.rejects(f.call(toolName.action, request));
  }
  assert.equal(f.approvals.length, 2);
  assert.deepEqual(kinds(f), ['windows', 'snapshot']);
});

test('old references fail after refresh, another command, another session, generation change or timeout', async () => {
  const f = fixture();
  const initial = await f.snapshot();
  const fresh = await f.call(toolName.snapshot, {window: windowRef});
  await assert.rejects(f.call(toolName.action, action(initial)), /快照已失效/);
  f.host.nativeConversations.set('other', {conversation_id: 'other-conv', generation: 1});
  f.host.activeCommands.set('other', 'command');
  f.host.requestPolicies.set('other', f.host.requestPolicies.get('session'));
  await assert.rejects(f.call(toolName.action, action(fresh), exec('other')), /快照已失效/);
  f.host.activeCommands.set('session', 'another');
  await assert.rejects(f.call(toolName.action, action(fresh)), /快照已失效/);
  f.host.activeCommands.set('session', 'command');
  f.host.nativeConversations.get('session').generation++;
  await assert.rejects(f.call(toolName.action, action(fresh)), /快照已失效/);
  const later = await f.snapshot();
  f.now = 90_000;
  await assert.rejects(f.call(toolName.action, action(later)), /快照已失效/);
  assert.ok(!kinds(f).includes('action'));
});

test('an action that expires while confirming is never dispatched', async () => {
  const f = fixture();
  const snapshot = await f.snapshot();
  f.host.actionGate.request = async () => { f.now = 90_000; return true; };
  await assert.rejects(f.call(toolName.action, action(snapshot)), /快照已失效/);
  assert.deepEqual(kinds(f), ['windows', 'snapshot']);
});

test('concurrent action and snapshot calls cannot replay a reserved target', async () => {
  const f = fixture();
  const snapshot = await f.snapshot();
  const entered = Promise.withResolvers(), approved = Promise.withResolvers();
  f.host.actionGate.request = () => { entered.resolve(); return approved.promise; };
  const running = f.call(toolName.action, action(snapshot));
  await entered.promise;
  try {
    await assert.rejects(f.call(toolName.action, action(snapshot)), /另一项桌面请求/);
    await assert.rejects(f.call(toolName.snapshot, {window: windowRef}), /另一项桌面请求/);
  } finally {
    approved.resolve(true);
    await running;
  }
  assert.deepEqual(kinds(f), ['windows', 'snapshot', 'action']);
});

test('only one approved semantic operation reaches the desktop at a time', async () => {
  const f = fixture();
  const first = await f.snapshot();
  f.host.nativeConversations.set('other', {conversation_id: 'other-conv', generation: 1});
  f.host.activeCommands.set('other', 'other-command');
  f.host.requestPolicies.set('other', {origin: 'desktop'});
  const second = await f.snapshot({}, exec('other'));
  const entered = Promise.withResolvers(), returned = Promise.withResolvers();
  f.provide = () => { entered.resolve(); return returned.promise; };
  const running = f.call(toolName.action, action(first));
  await entered.promise;
  try {
    await assert.rejects(f.call(toolName.action, action(second), exec('other')), /另一项桌面动作/);
  } finally {
    returned.resolve({status: 'sent'});
    await running;
  }
  assert.deepEqual(kinds(f), ['windows', 'snapshot', 'windows', 'snapshot', 'action']);
});

test('desktop mutation permits an authorized Weixin command and rejects scheduled or revoked commands', async () => {
  const f = fixture();
  f.host.requestPolicies.set('session', {origin: 'weixin'});
  const initial = await f.snapshot();
  const result = await f.call(toolName.action, action(initial));
  assert.equal(result.status, 'sent');
  const snapshot = result.after;
  let revoked = false;
  f.host.checkCommandScope = async () => { if (revoked) throw Error('revoked'); };
  f.host.actionGate.request = async () => { revoked = true; return true; };
  await assert.rejects(f.call(toolName.action, action(snapshot)), /revoked/);
  f.host.requestPolicies.set('session', {origin: 'weixin', schedule_id: 'schedule'});
  await assert.rejects(f.call(toolName.snapshot, {window: windowRef}), /来源已改变/);
  await assert.rejects(f.call(toolName.action, action(snapshot)), /来源已改变/);
  assert.deepEqual(kinds(f), ['windows', 'snapshot', 'action']);
});

test('known native preflight rejection stays a rejection; missing outcomes consume the reference', async () => {
  for (const known of [true, false]) {
    const f = fixture();
    const snapshot = await f.snapshot();
    f.provide = async () => { throw Object.assign(Error('target changed'), known ? {code: 'desktop_action_rejected'} : {}); };
    if (known) await assert.rejects(f.call(toolName.action, action(snapshot)), /target changed/);
    else {
      const result = await f.call(toolName.action, action(snapshot));
      assert.equal(result.status, 'unknown');
      assert.equal(result.after, undefined);
    }
    await assert.rejects(f.call(toolName.action, action(snapshot)), /快照已失效/);
    assert.deepEqual(kinds(f), ['windows', 'snapshot', 'action']);
  }
});

test('an aborted permission wait cannot dispatch an action', async () => {
  const f = fixture();
  const snapshot = await f.snapshot();
  const controller = new AbortController();
  f.host.actionGate.request = async () => { controller.abort(); return true; };
  await assert.rejects(f.call(toolName.action, action(snapshot), exec('session', controller.signal)), /aborted/i);
  assert.deepEqual(kinds(f), ['windows', 'snapshot']);
});
