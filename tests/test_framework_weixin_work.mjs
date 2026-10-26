import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WeixinWorks} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-work.mjs';
import {WeixinChannel} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin.mjs';

const now = 1900000000000;
const work = {intent: 'watching', id: 'work_a12345678901abcd', title: '训练实验', conversation_id: 'work-conversation', status: 'running', updated_at: now};
const event = (text, extra = {}) => ({text, command_id: `request-${text}`, installation_id: 'install', revision: 2,
  conversation_id: 'conversation', ...extra});
function fixture() {
  const calls = [], replies = [];
  let revision = 2, conversationId = 'conversation';
  const channel = {
    now: () => now,
    authorize: async p => {
      if (p.revision !== revision || p.conversation_id !== conversationId) throw Object.assign(Error('revoked'), {code: 'weixin_scope_revoked'});
      return {installation_id: 'install', revision, conversation_id: conversationId};
    },
    reply: async (e, text) => {await channel.authorize(e); replies.push({event: e, text});},
    store: async (method, params) => {
      calls.push({method, params});
      if (method === 'select') {revision++; conversationId = params.conversation_id; return {state: {revision, conversation_id: conversationId}};}
    },
    product: {cancelWeixinCommands: async id => calls.push({method: 'cancel-turns', id}),
      workRequest: async (method, params, principal) => {
        await channel.authorize(principal); calls.push({method, params, principal});
        if (method === 'work.list') return {works: [work]};
        if (method === 'work.observe') return {work, observation: {summary: '第 4 轮，日志更新时间已核实', observed_at: now}};
        if (method === 'work.get') return {work, runs: [{id: 'run_real', status: 'running'}]};
        if (method === 'work.create') return {work: {...work, title: params.title, goal: params.goal, status: 'idle'}};
        if (method === 'work.run.prepare') return {approval: {approval_id: 'approval', digest: 'd'.repeat(64), expires_at: now + 300000, work_id: params.work_id, spec: {script: params.script, script_sha256: 'a'.repeat(64), arguments: params.arguments ?? [], cwd: params.cwd ?? '.', output_paths: params.output_paths ?? [], timeout_seconds: params.timeout_seconds ?? 300}}};
        if (method === 'work.run.approve') return {approval_token: 'private-token'};
        if (method === 'work.run.start') return {run: {id: 'run_real', work_id: work.id, status: 'running'}};
        if (method === 'work.run.cancel') return {run: {id: params.run_id, work_id: work.id, status: 'cancelling'}};
        throw Error(`unexpected ${method}`);
      }},
  };
  const controls = new WeixinWorks(channel);
  return {channel, controls, calls, replies};
}

test('Weixin common work questions and IDs return real work facts without another model turn', async () => {
  const f = fixture();
  assert.ok(f.controls.handles('我的工作怎么样了'));
  assert.ok(f.controls.handles('/work a12345678901'));
  assert.equal(f.controls.handles('帮我想一下实验的方向'), false);
  await f.controls.control(event('我的工作怎么样了'));
  await f.controls.control(event('/work a12345678901'));
  assert.match(f.replies[0].text, /训练实验/);
  assert.match(f.replies[1].text, /第 4 轮/);
  assert.equal(f.calls.some(c => c.method === 'select'), false, 'viewing never changes the chat route');
});

test('Weixin create only creates a Work; /work-use explicitly changes the existing authoritative conversation route', async () => {
  const f = fixture();
  await f.controls.control(event('/work-new 整理报告'));
  assert.equal(f.calls.filter(c => c.method.startsWith('work.run')).length, 0);
  assert.match(f.replies[0].text, /尚未启动/);
  const create = f.calls.find(c => c.method === 'work.create');
  assert.equal(create.params.conversation_id, undefined);
  await f.controls.control(event('/work-use a12345678901'));
  assert.equal(f.calls.find(c => c.method === 'select').params.conversation_id, 'work-conversation');
  assert.equal(f.replies.at(-1).event.revision, 3);
  assert.match(f.replies.at(-1).text, /后续消息将继续/);
});

test('Weixin work running is prepare then exact approval; repeats use one approved action and stable Run request', async () => {
  const f = fixture();
  await f.controls.control(event('/work-run a12345678901 {"script":"实验.py","arguments":["中文 空格"]}'));
  assert.equal(f.calls.filter(c => c.method === 'work.run.start').length, 0);
  assert.match(f.replies[0].text, /本机账户/);
  assert.match(f.replies[0].text, /不是系统沙箱/);
  const code = /\/work-approve ([A-F0-9]+)/.exec(f.replies[0].text)[1];
  await f.controls.control(event(`/work-approve ${code}`));
  await f.controls.control(event(`/work-approve ${code}`));
  assert.equal(f.calls.filter(c => c.method === 'work.run.approve').length, 1);
  const starts = f.calls.filter(c => c.method === 'work.run.start');
  assert.equal(starts.length, 2); assert.equal(starts[0].params.request_id, starts[1].params.request_id);
  assert.equal(starts[0].principal.origin, 'weixin');
  assert.doesNotMatch(f.replies.map(r => r.text).join('\n'), /private-token/);
});

test('Weixin rejects invalid JSON, unsupported control fields, stale codes and old conversation authority', async () => {
  const f = fixture();
  await f.controls.control(event('/work-run a12345678901 python script.py'));
  await f.controls.control(event('/work-run a12345678901 {"script":"x.py","actor":"desktop"}'));
  await f.controls.control(event('/work-approve OLD'));
  assert.equal(f.calls.filter(c => c.method.startsWith('work.run')).length, 0);
  await f.controls.control(event('/work-run a12345678901 {"script":"x.py"}'));
  const code = /\/work-approve ([A-F0-9]+)/.exec(f.replies.at(-1).text)[1];
  await f.controls.control(event('/work-use a12345678901'));
  await f.controls.control(event(`/work-approve ${code}`, {revision: 3, conversation_id: 'work-conversation'}));
  assert.match(f.replies.at(-1).text, /确认码已失效/);
  assert.equal(f.calls.some(c => c.method === 'work.run.start'), false);
});

test('Weixin work cancellation names a real Run and does not cancel the assistant turn', async () => {
  const f = fixture();
  await f.controls.control(event('/work-cancel run_real'));
  assert.deepEqual(f.calls[0].params, {run_id: 'run_real'});
  assert.match(f.replies[0].text, /cancelling/);
  assert.equal(f.calls.some(c => c.method === 'cancel-turns'), false);
});

test('Weixin review loads full detail after the compact list and approves the exact desktop action', async () => {
  const f = fixture(), request = f.channel.product.workRequest;
  const approval = {id: 'approval-desktop', work_id: work.id, digest: 'd'.repeat(64), expires_at: now + 300000,
    spec: {script: 'desktop.py', script_sha256: 'a'.repeat(64), arguments: ['one', '中文 参数'], cwd: 'nested dir', output_paths: ['result.txt'], timeout_seconds: 120}};
  f.channel.product.workRequest = (method, params, principal) => method === 'work.list'
    ? {works: [{...work, pending_approvals: [{id: approval.id, status: 'pending'}]}]}
    : method === 'work.get' ? {work: {...work, pending_approvals: [approval]}} : request(method, params, principal);
  await f.controls.control(event('/work-review approval-desktop'));
  assert.match(f.replies[0].text, /desktop.py/);
  assert.ok(f.replies[0].text.includes('a'.repeat(64)));
  for (const value of ['one', '中文 参数', 'nested dir', 'result.txt', '120 秒']) assert.ok(f.replies[0].text.includes(value));
  const code = /\/work-approve ([A-F0-9]+)/.exec(f.replies[0].text)[1];
  await f.controls.control(event(`/work-approve ${code}`));
  assert.equal(f.calls.find(c => c.method === 'work.run.approve').params.expected_digest, 'd'.repeat(64));
  assert.equal(f.calls.some(c => c.method === 'work.run.prepare'), false);
  assert.equal(f.calls.filter(c => c.method === 'work.run.start').length, 1);
});

test('Weixin refuses to make a confirmation code from missing or clipped approval details', async () => {
  const f = fixture();
  await assert.rejects(f.controls.review(event('/work-review x'), work, {id: 'x', digest: 'd'.repeat(64)}), /不完整/);
  const approval = {id: 'x', work_id: work.id, digest: 'd'.repeat(64), expires_at: now + 300000,
    spec: {script: 'x.py', script_sha256: 'a'.repeat(64), arguments: ['x'.repeat(11000)], cwd: '.', output_paths: [], timeout_seconds: 120}};
  await assert.rejects(f.controls.review(event('/work-review x'), work, approval), /过长/);
  assert.equal(f.controls.approvals.size, 0);
});

function notificationsFixture() {
  const calls = [], state = {bound: true, enabled: true, installation_id: 'i', revision: 2, conversation_id: 'c', context_token: 'context'};
  const settings = {notifications: {completed: true}, channels: {weixin: false}};
  const notification = {event_id: 'event', kind: 'completed', work_id: work.id, title: '工作运行完成', text: '报告已生成', created_at: now,
    work: {...work, notifications: {completed: true}}, deliveries: {}};
  let deliveryStatus = 'pending';
  const channel = new WeixinChannel({rpc: async (method, params) => {
    calls.push({method, params});
    if (method === 'channel.weixin.get') return {state};
    if (method === 'work.settings.get') return {settings};
    if (method === 'work.notifications') return {events: [notification]};
    if (method === 'work.notifications.get') return {event: notification};
    if (method === 'work.notifications.claim') {
      const should_enqueue = !notification.deliveries.weixin;
      notification.deliveries.weixin ??= {status: 'queued', installation_id: params.installation_id, revision: params.revision};
      return {event: notification, should_enqueue};
    }
    if (method === 'channel.weixin.enqueue') return {delivery: {status: deliveryStatus}};
    if (method === 'work.notifications.ack') return {};
    throw Error(`Unexpected RPC ${method}`);
  }, workRequest() {}}, {now: () => now});
  return {channel, calls, state, settings, notification, setStatus: status => {deliveryStatus = status;}};
}

test('Weixin work notifications require global opt-in and use the existing durable outbox without marking queued as sent', async () => {
  const f = notificationsFixture();
  await f.channel.notifyWorks();
  assert.equal(f.calls.some(c => c.method === 'channel.weixin.enqueue'), false);
  f.settings.channels.weixin = true;
  await f.channel.notifyWorks();
  const enqueue = f.calls.find(c => c.method === 'channel.weixin.enqueue');
  assert.equal(enqueue.params.key, 'work-notification:event');
  assert.equal(enqueue.params.command_id, undefined, 'a work event is not a model turn reply');
  assert.equal(enqueue.params.context_token, undefined, 'use stored token with a stable outbox digest across repeated checks');
  assert.equal(f.calls.some(c => c.method === 'work.notifications.ack'), false);
  f.setStatus('failed');
  await f.channel.notifyWorks();
  assert.equal(f.calls.find(c => c.method === 'work.notifications.ack').params.status, 'failed');
});

test('Weixin queued notifications recheck global and per-work settings; stale events are retained without sending', async () => {
  const f = notificationsFixture(); f.settings.channels.weixin = true;
  f.notification.deliveries.weixin = {status: 'queued', installation_id: 'i', revision: 2};
  const delivery = {key: 'work-notification:event', installation_id: 'i', revision: 2};
  assert.equal(await f.channel.canDeliverWork(delivery), true);
  f.settings.channels.weixin = false;
  assert.equal(await f.channel.canDeliverWork(delivery), false);
  f.settings.channels.weixin = true; f.notification.work.notifications.completed = false;
  assert.equal(await f.channel.canDeliverWork(delivery), false);
  f.notification.work.notifications.completed = true; f.notification.created_at = now - 600001;
  await f.channel.notifyWorks();
  assert.equal(f.calls.some(c => c.method === 'channel.weixin.enqueue'), false);
  assert.equal(f.calls.find(c => c.method === 'work.notifications.ack').params.status, 'suppressed');
});

test('Weixin notification destination is claimed before enqueue and cannot move across rebind or route revision', async () => {
  const f = notificationsFixture(); f.settings.channels.weixin = true;
  await f.channel.notifyWorks();
  const names = f.calls.map(c => c.method);
  assert.ok(names.indexOf('work.notifications.claim') < names.indexOf('channel.weixin.enqueue'));
  const count = f.calls.filter(c => c.method === 'channel.weixin.enqueue').length;
  f.state.installation_id = 'new-installation'; f.state.revision = 3;
  await f.channel.notifyWorks();
  assert.equal(f.calls.filter(c => c.method === 'channel.weixin.enqueue').length, count);
  assert.equal(await f.channel.canDeliverWork({key: 'work-notification:event', installation_id: 'new-installation', revision: 3}), false);
});

test('Weixin resumes a crash between claim and enqueue with the same durable key, retaining unknown outcomes', async () => {
  const f = notificationsFixture(); f.settings.channels.weixin = true;
  f.notification.deliveries.weixin = {status: 'queued', installation_id: 'i', revision: 2};
  await f.channel.notifyWorks();
  f.setStatus('unknown');
  await f.channel.notifyWorks();
  const enqueues = f.calls.filter(c => c.method === 'channel.weixin.enqueue');
  assert.equal(enqueues.length, 2);
  assert.deepEqual(enqueues[0].params, enqueues[1].params, 'a replay only looks up/inserts the exact same outbox identity');
  assert.equal(f.calls.find(c => c.method === 'work.notifications.ack').params.status, 'unknown');
});
