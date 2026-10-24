/** Real read-model module acceptance, without a browser, server, or provider.
 * Run: node --test tests/test_framework_read_models.mjs (Node 24+).
 */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {stripTypeScriptTypes} from 'node:module';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const modulePath = fileURLToPath(new URL('../frontend/control-ui/ui/src/app/mirally-read-models.ts', import.meta.url));
const source = stripTypeScriptTypes(readFileSync(modulePath, 'utf8'), {mode: 'transform', sourceUrl: modulePath});
const {readMirallyModel: read, MIRALLY_READ_METHODS} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const now = Date.parse('2026-10-23T08:00:00.000Z');
const key = id => `agent:main:${id}`;
const byId = (page, id) => page.jobs.find(item => item.id === id);
const ids = page => page.jobs.map(item => item.id);
const code = expected => error => error.code === expected && error.gatewayCode === expected;

// Use the actual commitment.list/delivery.list/system.status wire fields; only
// the owner I/O is supplied by the fixture, never a copied projection module.
function fixture() {
  const commitments = [
    {id: 'water', request_id: 'r-water', title: 'Water', body: '喝一杯水', due_at: now + 2000,
      expires_at: null, conversation_id: 'daily', channel: 'inbox', status: 'pending', sources: [], created_at: now - 5000},
    {id: 'bills', request_id: null, title: 'Bills', body: '缴电费', due_at: now + 1000,
      expires_at: null, conversation_id: 'main', channel: 'inbox', status: 'pending', sources: [], created_at: now - 4000},
    {id: 'done', request_id: null, title: 'Finished', body: '已经提醒', due_at: now - 2000,
      expires_at: null, conversation_id: 'daily', channel: 'inbox', status: 'fulfilled', sources: [], created_at: now - 9000},
    {id: 'cancelled', request_id: null, title: 'Cancelled', body: '取消事项', due_at: now + 3000,
      expires_at: null, conversation_id: null, channel: 'inbox', status: 'cancelled', sources: [], created_at: now - 7000},
    {id: 'expired', request_id: null, title: 'Expired', body: '过期事项', due_at: now - 3000,
      expires_at: now - 1000, conversation_id: null, channel: 'inbox', status: 'expired', sources: [], created_at: now - 8000},
  ];
  const deliveries = [{id: 'delivery-done', kind: 'commitment', reference_id: 'done', channel: 'inbox',
    status: 'delivered', claim_token: null, receipt: {channel: 'inbox', durable: true}, sources: [],
    created_at: now - 2000, updated_at: now - 1900}];
  const status = {schema_version: 2, ready: true, scheduler: 'host_tick', now_utc_ms: now,
    model_bridge: 'host_managed', transcript_owner: 'dsh'};
  const calls = [];
  const api = {async rpc(method, params) {
    calls.push({method, params});
    if (method === 'commitment.list') return {commitments};
    if (method === 'delivery.list') return {deliveries};
    if (method === 'system.status') return status;
    throw new Error('Unexpected owner request: ' + method);
  }};
  return {commitments, deliveries, status, calls, api,
    async get(method = 'cron.list', params = {enabled: 'all'}) {
      const result = await read(method, params, api);
      assert.equal(result.handled, true);
      return result.value;
    }};
}

test('capability advertisement contains only implemented reminder reads', () => {
  assert.deepEqual(MIRALLY_READ_METHODS, ['cron.status', 'cron.list', 'cron.get']);
});

test('unknown methods fall through without querying an owner', async () => {
  const f = fixture();
  assert.deepEqual(await read('other.get', {}, f.api), {handled: false});
  assert.deepEqual(f.calls, []);
});

test('pending reminder projects real identity, content, schedule, ownership and channel', async () => {
  const f = fixture(), original = structuredClone(f.commitments);
  assert.deepEqual(await f.get('cron.get', {id: 'water'}), {
    id: 'water', name: 'Water', description: '喝一杯水', agentId: 'main', sessionKey: key('daily'),
    enabled: true, createdAtMs: now - 5000, updatedAtMs: now - 5000,
    schedule: {kind: 'at', at: new Date(now + 2000).toISOString()},
    sessionTarget: `session:${key('daily')}`, wakeMode: 'now',
    payload: {kind: 'systemEvent', text: '喝一杯水'},
    delivery: {mode: 'announce', channel: 'inbox'}, state: {nextRunAtMs: now + 2000},
  });
  assert.deepEqual(f.commitments, original, 'Read projection must not modify owner records');
});

test('durable delivery proves completion and supplies its real timestamps', async () => {
  const f = fixture();
  f.deliveries.push({kind: 'initiative', reference_id: 'done', status: 'failed', created_at: now, updated_at: now});
  const row = await f.get('cron.get', {id: 'done'});
  assert.equal(row.enabled, false); assert.equal(row.updatedAtMs, now - 1900);
  assert.deepEqual(row.state, {lastRunAtMs: now - 2000, lastRunStatus: 'ok', lastDeliveryStatus: 'delivered', lastDelivered: true});
});

test('failed and cancelled receipts retain distinct unsuccessful outcomes', async () => {
  const f = fixture();
  for (const [id, status] of [['water', 'failed'], ['cancelled', 'cancelled']]) {
    f.deliveries.push({kind: 'commitment', reference_id: id, status, created_at: now, updated_at: now + 1});
  }
  const page = await f.get();
  assert.equal(byId(page, 'water').state.lastRunStatus, 'error');
  assert.equal(byId(page, 'cancelled').state.lastRunStatus, 'skipped');
  for (const id of ['water', 'cancelled']) {
    assert.equal(byId(page, id).state.lastDelivered, false);
    assert.equal(byId(page, id).state.lastDeliveryStatus, 'not-delivered');
  }
});

test('unconfirmed delivery cannot become a successful run or delivered receipt', async () => {
  for (const status of ['pending', 'sending', 'unknown']) {
    const f = fixture();
    f.deliveries[0].status = status;
    const row = await f.get('cron.get', {id: 'done'});
    assert.equal(row.state.lastDeliveryStatus, 'unknown');
    assert.equal(Object.hasOwn(row.state, 'lastRunStatus'), false);
    assert.equal(Object.hasOwn(row.state, 'lastDelivered'), false);
  }
});

test('queued and terminal reminders have no invented next run', async () => {
  const f = fixture(); f.commitments[0].status = 'queued';
  const page = await f.get();
  for (const id of ['water', 'done', 'cancelled', 'expired']) {
    const row = byId(page, id);
    assert.equal(row.enabled, false); assert.equal(Object.hasOwn(row.state, 'nextRunAtMs'), false);
  }
});

test('cron status derives scheduler activation and next due time from actual owners', async () => {
  const f = fixture();
  assert.deepEqual(await f.get('cron.status', {}), {
    enabled: true, triggersEnabled: false, jobs: 5, nextWakeAtMs: now + 1000,
  });
  assert.deepEqual(f.calls.map(call => call.method).sort(), ['commitment.list', 'delivery.list', 'system.status']);
});

test('ready alone and scheduler declaration alone cannot claim an enabled scheduler', async () => {
  for (const patch of [{ready: false}, {ready: 'true'}, {scheduler: 'other'}, {scheduler: undefined}]) {
    const f = fixture(); Object.assign(f.status, patch);
    const result = await f.get('cron.status', {});
    assert.equal(result.enabled, false); assert.equal(result.triggersEnabled, false);
  }
});

test('no active reminders yields no claimed future wake, with a proven empty owner inventory', async () => {
  const f = fixture(); f.commitments.splice(0); f.deliveries.splice(0);
  assert.deepEqual(await f.get('cron.status', {}), {enabled: true, triggersEnabled: false, jobs: 0, nextWakeAtMs: null});
  const page = await f.get();
  assert.deepEqual(page.jobs, []); assert.equal(page.total, 0); assert.equal(page.nextOffset, null); assert.equal(page.hasMore, false);
  assert.ok(f.calls.some(call => call.method === 'commitment.list'));
});

test('default, includeDisabled and explicit enabled filters select the right reminders', async () => {
  const f = fixture();
  assert.deepEqual(ids(await f.get('cron.list', {})), ['bills', 'water']);
  assert.equal((await f.get('cron.list', {includeDisabled: true})).total, 5);
  assert.deepEqual(new Set(ids(await f.get('cron.list', {enabled: 'disabled'}))), new Set(['done', 'cancelled', 'expired']));
});

test('pagination has stable snapshot ownership, no overlap and exact terminal metadata', async () => {
  const f = fixture(), pages = [];
  for (const offset of [0, 2, 4]) pages.push(await f.get('cron.list', {enabled: 'all', limit: 2, offset}));
  assert.equal(new Set(pages.map(page => page.snapshotRevision)).size, 1);
  assert.match(pages[0].snapshotRevision, /^[a-f0-9]{64}$/);
  assert.deepEqual(pages.map(page => [page.total, page.limit, page.offset, page.nextOffset, page.hasMore]),
    [[5, 2, 0, 2, true], [5, 2, 2, 4, true], [5, 2, 4, null, false]]);
  assert.equal(new Set(pages.flatMap(ids)).size, 5);
});

test('snapshot revision changes when a reminder fact changes', async () => {
  const f = fixture(), previous = await f.get();
  f.commitments[0].due_at += 5000;
  const next = await f.get();
  assert.notEqual(next.snapshotRevision, previous.snapshotRevision);
});

test('query searches both title and body and trims surrounding whitespace', async () => {
  const f = fixture();
  assert.deepEqual(ids(await f.get('cron.list', {enabled: 'all', query: '  WATER  '})), ['water']);
  assert.deepEqual(ids(await f.get('cron.list', {enabled: 'all', query: '缴电费'})), ['bills']);
  assert.equal((await f.get('cron.list', {enabled: 'all', query: '不存在'})).total, 0);
});

test('schedule and trigger filters do not invent recurring or conditional jobs', async () => {
  const f = fixture();
  assert.equal((await f.get('cron.list', {enabled: 'all', scheduleKind: 'at', trigger: 'unconditional'})).total, 5);
  for (const scheduleKind of ['every', 'cron', 'on-exit', 'stream']) {
    assert.equal((await f.get('cron.list', {enabled: 'all', scheduleKind})).total, 0);
  }
  assert.equal((await f.get('cron.list', {enabled: 'all', trigger: 'conditional'})).total, 0);
});

test('session filters use product conversation keys and canonicalize the main alias', async () => {
  const f = fixture();
  assert.deepEqual(new Set(ids(await f.get('cron.list', {enabled: 'all', sessionKey: key('daily')}))), new Set(['water', 'done']));
  assert.deepEqual(ids(await f.get('cron.list', {enabled: 'all', sessionKey: 'main'})), ['bills']);
  assert.equal((await f.get('cron.list', {enabled: 'all', sessionKey: 'native-session-id'})).total, 0);
});

test('agent and last-result filters only return matching owner facts', async () => {
  const f = fixture();
  assert.equal((await f.get('cron.list', {enabled: 'all', agentId: 'main', sessionAgentId: 'main'})).total, 5);
  for (const filter of [{agentId: 'other'}, {sessionAgentId: 'other'}]) {
    assert.equal((await f.get('cron.list', {enabled: 'all', ...filter})).total, 0);
  }
  assert.deepEqual(ids(await f.get('cron.list', {enabled: 'all', lastRunStatus: 'ok'})), ['done']);
  assert.equal((await f.get('cron.list', {enabled: 'all', lastRunStatus: 'unknown'})).total, 4);
});

test('sort fields and direction control known values without promoting unscheduled rows', async () => {
  const f = fixture();
  assert.deepEqual(ids(await f.get('cron.list', {enabled: 'all', sortBy: 'name', sortDir: 'asc'})), ['bills', 'cancelled', 'expired', 'done', 'water']);
  assert.deepEqual(ids(await f.get('cron.list', {enabled: 'all', sortBy: 'name', sortDir: 'desc'})), ['water', 'done', 'expired', 'cancelled', 'bills']);
  assert.equal((await f.get('cron.list', {enabled: 'all', sortBy: 'updatedAtMs', sortDir: 'desc'})).jobs[0].id, 'done');
  assert.deepEqual(ids(await f.get('cron.list', {enabled: 'enabled', sortBy: 'nextRunAtMs', sortDir: 'desc'})), ['water', 'bills']);
});

test('compact inventory includes explicit nullable receipt fields expected by the original UI', async () => {
  const f = fixture();
  const page = await f.get('cron.list', {enabled: 'all', compact: true, lastRunStatus: 'ok'});
  assert.deepEqual(page.jobs, [{id: 'done', name: 'Finished', agentId: 'main', enabled: false,
    updatedAtMs: now - 1900, nextRunAtMs: null, lastRunAtMs: now - 2000, lastRunStatus: 'ok'}]);
  const pending = (await f.get('cron.list', {compact: true})).jobs[0];
  assert.equal(pending.lastRunAtMs, null); assert.equal(pending.lastRunStatus, null);
});

test('exact reads accept the legacy jobId alias and report missing or invalid identities', async () => {
  const f = fixture();
  assert.equal((await f.get('cron.get', {jobId: 'water'})).id, 'water');
  await assert.rejects(f.get('cron.get', {id: 'missing'}), code('NOT_FOUND'));
  for (const params of [{}, {id: ''}, {id: 42}]) await assert.rejects(f.get('cron.get', params), code('INVALID_PARAMS'));
});

test('invalid pagination and filter inputs reject instead of returning misleading pages', async () => {
  const f = fixture();
  for (const params of [{limit: 0}, {limit: 201}, {limit: 1.2}, {offset: -1}, {offset: 6},
    {enabled: 'bad'}, {scheduleKind: 'bad'}, {lastRunStatus: 'bad'}, {trigger: 'bad'},
    {sortBy: 'bad'}, {sortDir: 'bad'}, {query: 7}]) {
    await assert.rejects(f.get('cron.list', {enabled: 'all', ...params}), code('INVALID_PARAMS'));
  }
});

test('unsupported read domains explicitly reject without false empty inventories or owner calls', async () => {
  const f = fixture();
  for (const method of ['sessions.observer.visibility', 'sessions.branches.list', 'sessions.companion.state',
    'question.list', 'question.get', 'tasks.list', 'tasks.get', 'tasks.history', 'cron.runs', 'cron.scratch.get']) {
    await assert.rejects(read(method, {}, f.api), code('METHOD_NOT_FOUND'));
  }
  assert.deepEqual(f.calls, []);
});

test('either capped owner inventory prevents claiming a complete cron page or status', async () => {
  for (const owner of ['commitments', 'deliveries']) {
    const f = fixture();
    while (f[owner].length < 200) f[owner].push({...f[owner][0]});
    for (const method of MIRALLY_READ_METHODS) {
      await assert.rejects(f.get(method, {id: 'water'}), error => code('METHOD_NOT_FOUND')(error) && /分页/.test(error.message));
    }
  }
});

test('invalid owner shapes and invalid durable timestamps fail instead of fabricating empty state', async () => {
  for (const invalidOwner of [{}, {commitments: null}, {commitments: [], deliveries: null}]) {
    await assert.rejects(read('cron.list', {}, {async rpc() {return invalidOwner;}}));
  }
  for (const patch of [{id: ''}, {due_at: NaN}, {due_at: -1}, {due_at: '2026-10-23'},
    {created_at: 8.65e15}, {status: 'made-up'}, {body: undefined}]) {
    const f = fixture(); Object.assign(f.commitments[0], patch);
    await assert.rejects(f.get(), /身份、时间或状态/);
  }
  const f = fixture(); f.deliveries[0].updated_at = 'invalid';
  await assert.rejects(f.get(), /投递记录缺少有效时间/);
});

test('owner I/O failures propagate without manufacturing success or fallback emptiness', async () => {
  for (const failedMethod of ['commitment.list', 'delivery.list', 'system.status']) {
    const f = fixture(), owner = f.api.rpc, failure = Object.assign(new Error('Owner unavailable'), {code: 'OWNER_UNAVAILABLE'});
    f.api.rpc = async (method, params) => {if (method === failedMethod) throw failure; return owner(method, params);};
    await assert.rejects(f.get('cron.status', {}), error => error === failure);
  }
});
