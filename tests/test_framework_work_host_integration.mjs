/** Real Node control plane -> JSONL sidecar -> SQLite -> supervised Python, no network/model. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, rm} from 'node:fs/promises';
import {join, resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {ProductSidecar} from '../kernel/dsh/packages/mirally/mirally-product/src/sidecar.mjs';
import {WorkHost} from '../kernel/dsh/packages/mirally/mirally-product/src/work-host.mjs';
import {WeixinWorks} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-work.mjs';
import {WeixinChannel} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin.mjs';

const poll = async (callback, check) => {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const value = await callback(); if (check(value)) return value;
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  throw Error('real run did not settle');
};

test('Work Host real sidecar preserves one conversation, reviews desktop action on phone, executes and retrieves actual results', async t => {
  const root = resolve('.'), home = await mkdtemp(join(tmpdir(), 'Mirally Host 工作 中文 '));
  const sidecar = new ProductSidecar({python: join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
    root, home, logger: {warn() {}}});
  t.after(async () => {await sidecar.stop(); await rm(home, {recursive: true, force: true});});
  await sidecar.start();
  await sidecar.request('capabilities.configure', {expected_revision: 1, patch: {workspace_write_enabled: true}});
  await mkdir(join(home, 'workspace'), {recursive: true});
  await writeFile(join(home, 'workspace', '真实 script.py'), 'from pathlib import Path\nimport sys\nprint("done:",sys.argv[1],flush=True)\nPath("报告.txt").write_text("real result",encoding="utf-8")\n', 'utf8');
  const product = {rpc: (method, params) => sidecar.request(method, params), broadcast() {},
    mobile: {authorize: p => ['first', 'second'].includes(p.id)}};
  const host = new WorkHost(product);
  const principal = {origin: 'mobile', id: 'first', revision: 1};
  const first = await host.request('work.create', {title: '真实实验', request_id: 'same-create'}, principal);
  const duplicate = await host.request('work.create', {title: '真实实验', request_id: 'same-create'}, principal);
  assert.equal(first.work.id, duplicate.work.id);
  assert.equal((await sidecar.request('conversation.list')).conversations.length, 1);
  const {approval} = await host.request('work.run.prepare', {work_id: first.work.id, script: '真实 script.py',
    arguments: ['中文 参数'], output_paths: ['报告.txt'], request_id: 'prepare'});
  const {approval_token} = await host.request('work.run.approve', {approval_id: approval.id, expected_digest: approval.digest}, principal);
  const input = {approval_id: approval.id, approval_token, request_id: 'start'};
  await assert.rejects(host.request('work.run.start', input, {...principal, id: 'second'}), {code: 'FORBIDDEN'});
  await assert.rejects(host.request('work.run.approve', {approval_id: approval.id, expected_digest: approval.digest},
    {...principal, id: 'second'}), {code: 'FORBIDDEN'});
  const {run} = await host.request('work.run.start', input, principal);
  assert.equal((await host.request('work.run.start', input, principal)).run.id, run.id);
  const done = await poll(() => host.request('work.run.get', {run_id: run.id}, principal), value => value.run.status === 'completed');
  assert.equal(done.run.exit_code, 0);
  const logs = await host.request('work.run.logs', {run_id: run.id}, principal);
  assert.match(logs.text, /中文 参数/);
  const artifact = await host.request('work.run.artifact', {run_id: run.id, path: '报告.txt'}, principal);
  assert.equal(Buffer.from(artifact.data, 'base64').toString('utf8'), 'real result');
  assert.equal((await host.request('work.get', {work_id: first.work.id}, principal)).runs.length, 1);
  const {settings} = await host.request('work.settings.get', {}, principal);
  assert.equal(settings.channels.weixin, false, 'test cannot send a surprise real Weixin notification');

  // Exercise the real compact list/full detail contract through deterministic
  // Weixin controls. The channel identity is a fixture; there is no API send.
  const pending = await host.request('work.run.prepare', {work_id: first.work.id, script: '真实 script.py',
    arguments: ['微信审阅的真实参数'], output_paths: ['报告.txt'], timeout_seconds: 90, request_id: 'wechat-desktop-prepare'});
  assert.equal((await host.request('work.list')).works[0].pending_approvals[0].spec, undefined);
  const state = {installation_id: 'fixture-installation', revision: 4, conversation_id: first.work.conversation_id};
  const authorize = async scope => {
    assert.equal(scope.installation_id, state.installation_id); assert.equal(scope.revision, state.revision);
    return state;
  };
  product.weixin = {authorize}; product.workRequest = (...args) => host.request(...args);
  const replies = [], controls = new WeixinWorks({product, authorize, now: Date.now,
    reply: async (_event, text) => replies.push(text)});
  const event = {...state, command_id: 'review', text: `/work-review ${pending.approval.id}`};
  await controls.control(event);
  assert.match(replies.at(-1), /微信审阅的真实参数/); assert.match(replies.at(-1), /90 秒/);
  assert.ok(replies.at(-1).includes(pending.approval.spec.script_sha256));
  const code = /\/work-approve ([A-F0-9]+)/.exec(replies.at(-1))[1];
  await controls.control({...event, command_id: 'confirm', text: `/work-approve ${code}`});
  assert.match(replies.at(-1), /运行已接纳/);
  const second = await poll(() => host.request('work.get', {work_id: first.work.id}), value => value.runs[0]?.status === 'completed');
  assert.equal(second.runs.length, 2);
  assert.match((await host.request('work.run.logs', {run_id: second.runs[0].id})).text, /微信审阅的真实参数/);

  // Temporary credentials only populate the real SQL channel. API construction
  // throws, proving notification admission never contacts the real provider.
  const connectFixture = async suffix => {
    const saved = await sidecar.request('channel.weixin.save', {token: `offline-fixture-${suffix}`, peer_id: `owner-${suffix}`,
      bot_id: `bot-${suffix}`, base_url: 'https://ilinkai.weixin.qq.com'});
    const bound = await sidecar.request('channel.weixin.bind', {expected_revision: saved.state.revision, conversation_id: first.work.conversation_id});
    await sidecar.request('channel.weixin.admit', {installation_id: bound.state.installation_id, revision: bound.state.revision,
      event_id: `context-${suffix}`, text: 'offline-context', context_token: `fixture-context-${suffix}`, created_at: Date.now()});
    return bound.state;
  };
  const oldRoute = await connectFixture('first');
  await host.request('work.settings.update', {expected_revision: settings.revision, patch: {channels: {weixin: true}}});
  const channel = new WeixinChannel(product, {apiFactory: () => {throw Error('network is forbidden in this test');}});
  await channel.notifyWorks();
  const pendingOutbox = await sidecar.request('channel.weixin.outbox', {installation_id: oldRoute.installation_id});
  assert.ok(pendingOutbox.deliveries.length >= 2);
  const delivery = pendingOutbox.deliveries[0], eventId = delivery.key.slice('work-notification:'.length);
  assert.equal(await channel.canDeliverWork(delivery), true);
  const newRoute = await connectFixture('second');
  await channel.notifyWorks();
  assert.equal((await sidecar.request('channel.weixin.outbox', {installation_id: newRoute.installation_id})).deliveries.length, 0,
    'old claimed notifications cannot be adopted by a new binding');
  const receipt = (await sidecar.request('work.notifications.get', {event_id: eventId})).event.deliveries.weixin;
  assert.equal(receipt.installation_id, oldRoute.installation_id);
});
