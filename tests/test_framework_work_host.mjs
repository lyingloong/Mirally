import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WorkHost, WORK_METHODS, workSummary} from '../kernel/dsh/packages/mirally/mirally-product/src/work-host.mjs';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

function fixture() {
  const calls = [], events = [];
  const product = {rpc: async (method, params) => {
    calls.push({method, params});
    if (method === 'conversation.ensure') return {conversation_id: params.conversation_id ?? 'new-conversation'};
    return {work: {work_id: 'work-one', conversation_id: params.conversation_id}};
  }, broadcast: (type, payload) => events.push({type, payload}),
  mobile: {authorize: principal => principal?.id === 'device' && principal.revision === 3},
  weixin: {authorize: async principal => {if (principal.revision !== 2) throw Error('revoked');}}};
  return {product, host: new WorkHost(product), calls, events};
}

test('Work control never accepts actor/principal authority from the JSON payload', async () => {
  const {host, calls} = fixture();
  await host.request('work.run.prepare', {actor: 'desktop', principal: {origin: 'desktop'}, work_id: 'w', request_id: 'same'},
    {origin: 'mobile', id: 'device', revision: 3});
  assert.equal(calls[0].params.actor, 'mobile:device:3');
  assert.equal(calls[0].params.principal, undefined);
  await assert.rejects(host.request('work.run.start', {approval_token: 'forged'}, {origin: 'mobile', id: 'other', revision: 3}), {code: 'work_scope_revoked'});
  assert.equal(calls.length, 1);
});

test('Work creation without a conversation reuses one stable primary conversation across retries', async () => {
  const {host, calls} = fixture();
  await host.request('work.create', {title: '实验', request_id: 'user-request'});
  await host.request('work.create', {title: '实验', request_id: 'user-request'});
  assert.deepEqual(calls[0], calls[2]);
  assert.match(calls[0].params.conversation_id, /^conversation_work_/);
  assert.equal(calls[1].params.conversation_id, calls[0].params.conversation_id);
  assert.equal(calls[1].params.request_id, calls[3].params.request_id);
});

test('Work creation from the existing work page preserves its material and transcript ownership', async () => {
  const {host, calls} = fixture();
  await host.request('work.create', {title: '报告', conversation_id: 'existing'});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].params.conversation_id, 'existing');
});

test('Work action idempotency identities are scoped to the real paired origin', async () => {
  const {host, calls} = fixture();
  await host.request('work.run.prepare', {work_id: 'w', request_id: 'same'});
  await host.request('work.run.prepare', {work_id: 'w', request_id: 'same'}, {origin: 'mobile', id: 'device', revision: 3});
  assert.notEqual(calls[0].params.request_id, calls[1].params.request_id);
  assert.equal(calls[1].params.actor, 'mobile:device:3');
  assert.ok(WORK_METHODS.has('work.run.artifact'));
  await assert.rejects(host.request('scripts.run', {confirmed: true}), {code: 'work_scope_revoked'});
});

test('Work list and detail project the current conversation activity independently of work and run status', async () => {
  const works = [{work_id: 'one', conversation_id: 'c', status: 'idle'},
    {work_id: 'two', conversation_id: 'c', status: 'failed'}];
  const conversation = {conversation_id: 'c', session_id: 'current', frozen: false, archived: false};
  let reads = 0;
  const product = {handles: new Map([['current', {agent: {status: 'running'}}],
    ['old', {agent: {status: 'running'}}]]), activeCommands: new Map([['current', 'cmd'], ['old', 'old-cmd']]),
  rpc: async (method) => {
    if (method === 'internal.conversation.activity_batch') { reads++; return {conversations: [{...conversation}]}; }
    if (method === 'work.list') return {works};
    if (method === 'work.get') return {work: works[0], runs: [{state: 'failed'}]};
    throw Error(method);
  }};
  const host = new WorkHost(product);
  const listed = await host.request('work.list');
  assert.deepEqual(listed.works.map(w => w.conversation_activity), ['running', 'running']);
  assert.equal(reads, 1);
  assert.equal(listed.works[0].status, 'idle');
  assert.equal(listed.works[1].status, 'failed');
  const detail = await host.request('work.get', {work_id: 'one'});
  assert.equal(detail.work.conversation_activity, 'running');
  assert.equal(detail.runs[0].state, 'failed');
  product.handles.get('current').agent.status = 'idle';
  product.activeCommands.delete('current');
  assert.equal((await host.request('work.get', {work_id: 'one'})).work.conversation_activity, 'idle');
  // The previous generation is still running but is no longer the owner.
  conversation.session_id = 'new';
  assert.equal((await host.request('work.get', {work_id: 'one'})).work.conversation_activity, 'idle');
  // Revoked source freezes the current generation before the native agent stops.
  conversation.session_id = 'old'; conversation.frozen = true;
  assert.equal((await host.request('work.get', {work_id: 'one'})).work.conversation_activity, 'idle');
  conversation.frozen = false; conversation.archived = true;
  assert.equal((await host.request('work.get', {work_id: 'one'})).work.conversation_activity, 'idle');
});

test('Unavailable or inconsistent conversation activity is unknown and remote scope is rechecked', async () => {
  let authorized = true;
  const product = {handles: new Map([['s', {agent: {status: 'running'}}]]), activeCommands: new Map(),
    mobile: {authorize: () => authorized},
    rpc: async (method) => {
      if (method === 'work.list') return {works: [{conversation_id: 'missing'}, {conversation_id: 'c'}]};
      if (method === 'internal.conversation.activity_batch') return {conversations: [{conversation_id: 'c', session_id: 's', frozen: false, archived: false}]};
      return {};
    }};
  const host = new WorkHost(product);
  assert.deepEqual((await host.request('work.list')).works.map(w => w.conversation_activity), ['unknown', 'unknown']);
  const original = product.rpc;
  product.rpc = async (...args) => { if (args[0] === 'internal.conversation.activity_batch') throw Error('sidecar unavailable'); return original(...args); };
  assert.deepEqual((await host.request('work.list')).works.map(w => w.conversation_activity), ['unknown', 'unknown']);
  product.rpc = async (...args) => { const value = await original(...args); if (args[0] === 'internal.conversation.activity_batch') authorized = false; return value; };
  await assert.rejects(host.request('work.list', {}, {origin: 'mobile', id: 'p', revision: 1}), {code: 'work_scope_revoked'});
  const publicProduct = Object.create(MirallyProduct.prototype);
  await assert.rejects(publicProduct.publicRpc('internal.conversation.activity_batch', {conversation_ids: ['c']}), {code: 'method_not_allowed'});
});

test('Mobile messages get an unforgeable internal read-only scope and stable command identity', async () => {
  const product = Object.assign(Object.create(MirallyProduct.prototype), {mobile: {authorize: p => p?.id === 'device'}});
  const seen = [];
  product.send = async (...args) => {seen.push(args); return {command_id: args[1].commandId};};
  const input = {conversation_id: 'c', text: '你好', command_id: 'request', allow_file_write: true, materials: ['forged']};
  const first = await product.sendMobile(input, {id: 'device', origin: 'mobile', revision: 3});
  const next = await product.sendMobile(input, {id: 'device', origin: 'mobile', revision: 3});
  assert.equal(first.command_id, next.command_id);
  assert.match(first.command_id, /^mobile-/);
  assert.equal(seen[0][1].allow_file_write, undefined);
  assert.equal(seen[0][2].origin, 'mobile');
  await assert.rejects(product.sendMobile(input, {id: 'revoked'}), {code: 'mobile_scope_revoked'});
});

test('Mobile final tool gate denies writes, scripts and plugin bypasses, revocation denies even reads', async () => {
  const product = Object.assign(Object.create(MirallyProduct.prototype), {
    mobile: {authorize: () => true}, commandPolicies: new Map([['mobile-x', {origin: 'mobile', principal: {id: 'device'}}]]),
    activeCommands: new Map([['agent', 'mobile-x']]), consumedCommandId: () => 'mobile-x',
  });
  for (const name of ['mirally_works', 'mirally_work_status', 'mirally_read_file']) await product.checkCommandScope({id: 'agent'}, name);
  for (const name of ['mirally_create_work', 'mirally_create_file', 'run_skill_script', 'mcp__run'])
    await assert.rejects(product.checkCommandScope({id: 'agent'}, name), {code: 'mobile_read_only'});
  product.mobile.authorize = () => false;
  await assert.rejects(product.checkCommandScope({id: 'agent'}, 'mirally_works'), {code: 'mobile_scope_revoked'});
  product.commandPolicies.clear();
  await assert.rejects(product.checkCommandScope({id: 'agent'}, 'mirally_works'), {code: 'mobile_scope_revoked'});
  await assert.rejects(product.send('c', {text: 'forged', commandId: 'mobile-forged'}), {code: 'invalid_request'});
});

test('Work summaries use observation time and factual status without manufacturing a percentage', () => {
  const text = workSummary({id: 'work_abc123', title: '实验', status: 'unknown', updated_at: 1900000000000,
    observation: {summary: '日志已截断，无法确定阶段', observed_at: 1900000000001}}, {detail: true});
  assert.match(text, /待核对/); assert.match(text, /日志已截断/); assert.doesNotMatch(text, /%/);
  assert.match(text, /work-use/);
  assert.doesNotMatch(text, /停止真实运行/);
  const measured = workSummary({id: 'work_log', title: '实验', source: {kind: 'log'},
    observation: {summary: 'old'.repeat(500) + 'latest', truncated: true, progress: {current: 4, total: 10}, metrics: {loss: 0.3}}}, {detail: true});
  assert.match(measured, /latest/); assert.match(measured, /4\/10/); assert.match(measured, /loss: 0.3/);
  assert.match(measured, /仅观察外部状态/); assert.doesNotMatch(measured, /work-cancel/);
});

test('Work inbox projects one durable event beside existing messages and acknowledges through its real owner', async () => {
  const calls = [];
  const host = new WorkHost({rpc: async (method, params) => {
    calls.push({method, params});
    if (method === 'inbox.list') return {items: [{delivery_id: 'reminder', updated_at: 1}]};
    if (method === 'work.notifications') return {events: [{event_id: 'event', title: '完成', text: '真实结果',
      created_at: 2, work_id: 'work', work: {conversation_id: 'conversation'}}]};
    return {};
  }});
  const inbox = await host.inbox();
  assert.equal(inbox.items[0].delivery_id, 'work:event');
  assert.equal(inbox.items[0].conversation_id, 'conversation');
  assert.equal(inbox.items[1].delivery_id, 'reminder');
  await host.ackInbox({delivery_id: 'work:event', state: 'read'});
  assert.deepEqual(calls.at(-1), {method: 'work.notifications.ack', params: {event_id: 'event', channel: 'inbox', status: 'sent'}});
  await host.ackInbox({delivery_id: 'reminder', state: 'read'});
  assert.equal(calls.at(-1).method, 'inbox.ack');
});

test('Work source polling scans bounded pages and reaches sources past the first 200', async () => {
  const calls = [];
  const works = Array.from({length: 245}, (_, i) => ({id: `work-${i}`, intent: 'watching', source: i < 30 || i >= 220 ? {kind: 'log'} : null}));
  const host = new WorkHost({rpc: async (method, params) => {
    calls.push({method, params});
    if (method === 'work.list') return {works: works.slice(params.offset, params.offset + params.limit),
      has_more: params.offset + params.limit < works.length, next_offset: Math.min(works.length, params.offset + params.limit)};
    return {observation: {observed_at: 1}};
  }, broadcast() {}});
  for (let i = 0; i < 4; i++) {
    host.lastObserve = 0;
    const before = calls.length;
    await host.tick();
    assert.ok(calls.slice(before).filter(c => c.method === 'work.observe').length <= 20);
    assert.equal(calls.slice(before).filter(c => c.method === 'work.list').length, 1);
  }
  assert.ok(calls.some(c => c.params?.work_id === 'work-244'));
  assert.ok(calls.filter(c => c.method === 'work.list').some(c => c.params.offset === 0));
});
