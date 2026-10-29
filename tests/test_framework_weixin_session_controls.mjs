/** Deterministic phone controls: real channel routing with an isolated product boundary. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WeixinChannel} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin.mjs';

const now = 1900000000000;
const first = {conversation_id: 'conversation_0123456789abcdef0123456789abcdef', title: '桌面实验'};
const second = {conversation_id: 'conversation_abcdef0123456789abcdef0123456789', title: 'GPU  训练 / 第一期'};
const deferred = () => {let resolve; const promise = new Promise(yes => {resolve = yes;}); return {promise, resolve};};

function fixture(t, conversations = [first, second]) {
  const rows = conversations.map(item => ({...item})), calls = [], deliveries = [], events = new Map();
  const state = {installation_id: 'test-installation', revision: 1, enabled: true, bound: true,
    conversation_id: rows[0]?.conversation_id ?? 'conversation-empty', peer_id: 'owner', bot_id: 'bot', token: 'fixture-token'};
  const product = {
    rpc: async (method, params = {}) => {
      calls.push({method, params: structuredClone(params)});
      if (method === 'channel.weixin.get') return {state: {...state}};
      if (method === 'conversation.list') return {conversations: rows.slice(params.offset ?? 0, (params.offset ?? 0) + params.limit)};
      if (method === 'internal.conversation.resolve') {
        const exact = rows.filter(row => row.conversation_id === params.selector);
        return {conversations: exact.length ? exact : rows.filter(row => row.title === params.selector
          || /^[A-Za-z0-9_-]{8,128}$/.test(params.selector) && row.conversation_id.replace(/^conversation_/, '').startsWith(params.selector)).slice(0, 9)};
      }
      if (method === 'conversation.ensure') {
        const old = rows.find(row => row.conversation_id === params.conversation_id);
        if (old) return {...old};
        const row = {...params}; rows.push(row); return {...row};
      }
      if (method === 'channel.weixin.admit') {
        if (state.installation_id !== params.installation_id || state.revision !== params.revision)
          throw Object.assign(Error('stale input'), {code: 'CONFLICT'});
        if (events.has(params.event_id)) return {event: {...events.get(params.event_id), duplicate: true}};
        const event = {...params, command_id: 'command-' + params.event_id, conversation_id: state.conversation_id};
        events.set(params.event_id, event); return {event: {...event}};
      }
      if (method === 'channel.weixin.event') {Object.assign(events.get(params.event_id), params); return {};}
      if (method === 'channel.weixin.enqueue') {
        assert.equal(params.revision, state.revision, 'responses must use the effective route');
        deliveries.push(structuredClone(params)); return {delivery: {...params}};
      }
      if (method === 'channel.weixin.select') {
        assert.equal(params.expected_revision, state.revision, 'selection retains the revision guard');
        if (params.conversation_id) state.conversation_id = params.conversation_id;
        else {
          const row = {conversation_id: 'conversation_new-' + rows.length, title: '新对话-2030-03-17'};
          rows.push(row); state.conversation_id = row.conversation_id;
        }
        state.revision++; return {state: {...state}};
      }
      throw Error('unexpected RPC ' + method);
    },
    conversation: async id => rows.find(row => row.conversation_id === id),
    summary: () => ({status: 'idle'}),
    cancelWeixinCommands: async id => {calls.push({method: 'cancel-weixin', id});},
    sendWeixin: async event => {calls.push({method: 'submit', event: structuredClone(event)}); return {accepted: true};},
    finishWeixin: async () => ({state: 'completed', text: 'fixture reply'}),
  };
  const channel = new WeixinChannel(product, {now: () => now});
  t.after(() => channel.stop());
  let nextEvent = 0;
  const send = (text, id = String(++nextEvent)) => channel.receive({...state}, {message_id: id, message_type: 1, message_state: 2,
    from_user_id: 'owner', to_user_id: 'bot', context_token: 'fixture-context', create_time_ms: now,
    item_list: [{type: 1, text_item: {text}}]});
  return {product, channel, rows, state, calls, deliveries, send};
}

test('Chinese phone session list marks the current conversation and offers stable copyable routes', async t => {
  const f = fixture(t);
  await f.send('会话');
  const text = f.deliveries.at(-1).text;
  assert.match(text, /当前 · 桌面实验\n切换 0123456789ab/);
  assert.ok(text.includes('切换 abcdef012345'));
  assert.ok(text.includes('切换 完整标题'));
  assert.equal(f.calls.some(call => call.method === 'submit'), false);
  assert.equal(f.state.revision, 1);
  await f.send('/sessions');
  assert.equal(f.deliveries.at(-1).text, text);
  await f.send('当前会话');
  assert.match(f.deliveries.at(-1).text, /当前会话：桌面实验/);
  assert.ok(f.deliveries.at(-1).text.length < 100, 'status does not repeat the complete help menu');
});

test('Chinese switch accepts an exact title with spaces or a stable short ID, preserving slash controls', async t => {
  const f = fixture(t);
  await f.send('切换 GPU  训练 / 第一期');
  assert.equal(f.state.conversation_id, second.conversation_id);
  assert.match(f.deliveries.at(-1).text, /已切换到：GPU  训练/);
  await f.send('/use 0123456789ab');
  assert.equal(f.state.conversation_id, first.conversation_id);
  await f.send('切换会话 ' + second.conversation_id);
  assert.equal(f.state.conversation_id, second.conversation_id);
  assert.equal(f.calls.filter(call => call.method === 'cancel-weixin').length, 3);
  assert.equal(f.calls.some(call => call.method === 'submit'), false);
});

test('duplicate titles and short-ID collisions cannot silently select another conversation', async t => {
  const sameTitle = {...second, title: first.title};
  const collision = {conversation_id: 'conversation_abcdef01234500000000000000000000', title: '另一项工作'};
  const f = fixture(t, [first, sameTitle, collision]);
  await f.send('切换 桌面实验');
  assert.equal(f.state.revision, 1);
  assert.match(f.deliveries.at(-1).text, /同名会话/);
  for (const row of [first, sameTitle]) assert.ok(f.deliveries.at(-1).text.includes('切换 ' + row.conversation_id));
  await f.send('切换 abcdef012345');
  assert.equal(f.state.revision, 1);
  assert.match(f.deliveries.at(-1).text, /编号不唯一/);
  assert.equal(f.calls.some(call => call.method === 'cancel-weixin'), false);
  await f.send('切换 ' + collision.conversation_id);
  assert.equal(f.state.conversation_id, collision.conversation_id);
});

test('an exact title that is another conversation short ID requires disambiguation', async t => {
  const titleTarget = {conversation_id: 'conversation_title-target', title: '0123456789ab'};
  const f = fixture(t, [first, titleTarget]);
  await f.send('切换 0123456789ab');
  assert.equal(f.state.revision, 1);
  assert.equal(f.calls.some(call => call.method === 'cancel-weixin'), false);
  assert.match(f.deliveries.at(-1).text, /编号不唯一/);
  for (const row of [first, titleTarget]) assert.ok(f.deliveries.at(-1).text.includes('切换 ' + row.conversation_id));
});

test('switching to the selected conversation leaves its running turn and route intact', async t => {
  const f = fixture(t);
  await f.send('切换 桌面实验');
  assert.equal(f.state.revision, 1);
  assert.equal(f.calls.some(call => call.method === 'cancel-weixin'), false);
  assert.equal(f.calls.some(call => call.method === 'channel.weixin.select'), false);
  assert.match(f.deliveries.at(-1).text, /当前已在：桌面实验/);
});

test('title lookup delegates candidate uniqueness to one product owner operation', async t => {
  const older = {conversation_id: 'conversation_older-experiment', title: first.title};
  const rows = [first, ...Array.from({length: 199}, (_, index) => ({conversation_id: 'conversation_fixture-' + index, title: '记录 ' + index})), older];
  const f = fixture(t, rows);
  await f.send('切换 桌面实验');
  assert.equal(f.state.revision, 1);
  assert.match(f.deliveries.at(-1).text, /同名会话/);
  assert.ok(f.deliveries.at(-1).text.includes(older.conversation_id));
  assert.deepEqual(f.calls.filter(call => call.method === 'internal.conversation.resolve').map(call => call.params), [{selector: '桌面实验'}]);
  assert.equal(f.calls.some(call => call.method === 'conversation.list'), false);
  await f.send('切换 ' + older.conversation_id);
  assert.equal(f.state.conversation_id, older.conversation_id);
});

test('named phone conversation uses the product owner and a deduplicated input identity', async t => {
  const f = fixture(t);
  await f.send('新对话 GPU 新实验', 'create-named');
  const created = f.rows.at(-1);
  assert.equal(created.title, 'GPU 新实验');
  assert.equal(f.state.conversation_id, created.conversation_id);
  assert.match(created.conversation_id, /^conversation_weixin_[a-f0-9]{32}$/);
  assert.equal(f.calls.filter(call => call.method === 'conversation.ensure').length, 1);
  assert.ok(f.calls.findIndex(call => call.method === 'cancel-weixin') < f.calls.findIndex(call => call.method === 'channel.weixin.select'));
  await f.send('新对话 GPU 新实验', 'create-named');
  assert.equal(f.rows.length, 3);
  assert.equal(f.state.revision, 2);
  assert.equal(f.calls.some(call => call.method === 'submit'), false);
  await f.send('新对话');
  assert.match(f.rows.at(-1).title, /^新对话-/);
  await f.send('/new 手机规划');
  assert.equal(f.rows.at(-1).title, '手机规划');
});

test('non-command conversation prose reaches the selected model without a channel routing model', async t => {
  const f = fixture(t);
  const texts = ['切换模型后再看看结果', '新对话要讨论哪些问题？', '请切换到训练实验', '切换 GPU\n先帮我分析这个想法'];
  for (const text of texts) await f.send(text);
  await Promise.all([...f.channel.jobs]);
  assert.deepEqual(f.calls.filter(call => call.method === 'submit').map(call => call.event.text), texts);
  assert.equal(f.calls.some(call => call.method === 'conversation.list' || call.method === 'channel.weixin.select'), false);
});

test('invalid or missing routes give a brief recovery action without cancellation', async t => {
  const f = fixture(t);
  for (const text of ['/use', '切换 不存在的会话', '切换 abc', '/new ' + '中'.repeat(513)]) {
    await f.send(text);
    assert.ok(f.deliveries.at(-1).text.length < 100);
  }
  assert.equal(f.state.revision, 1);
  assert.equal(f.rows.length, 2);
  assert.equal(f.calls.some(call => call.method === 'cancel-weixin'), false);
});

test('a stale title lookup cannot cancel or select after a desktop route change', async t => {
  const f = fixture(t), pending = deferred(), entered = deferred(), rpc = f.product.rpc;
  f.product.rpc = async (method, params) => {
    if (method === 'internal.conversation.resolve') {entered.resolve(); return pending.promise;}
    return rpc(method, params);
  };
  const operation = f.send('切换 GPU  训练 / 第一期');
  await entered.promise;
  f.state.revision++; f.state.conversation_id = 'conversation-desktop-newer';
  pending.resolve({conversations: f.rows});
  await operation;
  assert.equal(f.state.conversation_id, 'conversation-desktop-newer');
  assert.equal(f.calls.some(call => call.method === 'cancel-weixin' || call.method === 'channel.weixin.select'), false);
  assert.equal(f.deliveries.length, 0);
});
