/** Channel orchestration against API/product doubles; durable SQL/wire tests live separately. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WeixinChannel, weixinTextParts, WEIXIN_TEXT, mediaFailureText} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin.mjs';
import {WeixinApiError} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-api.mjs';

const now = 1900000000000;
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};};
const waitFor = async predicate => {
  const deadline = Date.now() + 2500;
  while (!predicate()) {
    if (Date.now() >= deadline) throw Error('channel fixture did not settle');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
};
const untilAbort = signal => new Promise((resolve, reject) => {
  const done = () => reject(Object.assign(Error('fixture abort'), {kind: 'aborted'}));
  if (signal.aborted) done(); else signal.addEventListener('abort', done, {once: true});
});

function fixture(t, {unbound = false, qrResults = [], sendText, updates, mode, clock = () => now} = {}) {
  let state = {installation_id: unbound ? null : 'installation', revision: 1, bound: !unbound, enabled: !unbound,
    peer_id: unbound ? null : 'owner', bot_id: unbound ? null : 'bot', token: unbound ? null : 'private-bot-token',
    base_url: 'https://ilinkai.weixin.qq.com', conversation_id: unbound ? null : 'conversation-one', cursor: '', context_token: null,
    ...(mode ? {mode} : {})};
  const calls = [], events = new Map(), deliveries = new Map(), completions = new Map();
  const snapshots = () => ({...state, outbox_counts: {pending: [...deliveries.values()].filter(d => d.status === 'pending').length,
    unknown: [...deliveries.values()].filter(d => d.status === 'unknown').length, failed: [...deliveries.values()].filter(d => d.status === 'failed').length}});
  const current = p => {
    if (p.revision !== undefined && p.revision !== state.revision || p.expected_revision !== undefined && p.expected_revision !== state.revision)
      throw Object.assign(Error('scope changed'), {code: 'CONFLICT'});
  };
  const product = {
    rpc: async (method, params = {}) => {
      calls.push({kind: 'rpc', method, params: structuredClone(params)});
      const name = method.replace('channel.weixin.', '');
      if (name === 'get') return {state: snapshots()};
      if (name === 'save') {
        state = {...state, installation_id: 'new-installation', revision: state.revision + 1,
          token: params.token, bot_id: params.bot_id, peer_id: params.peer_id, base_url: params.base_url,
          enabled: false, bound: false, conversation_id: null};
        return {state: snapshots()};
      }
      if (['bind', 'enable', 'select', 'unbind'].includes(name)) {
        current(params);
        state.revision++;
        for (const delivery of deliveries.values()) if (delivery.status === 'pending') delivery.status = 'cancelled';
        if (name === 'bind') Object.assign(state, {bound: true, enabled: true, conversation_id: params.conversation_id ?? 'new-conversation'});
        if (name === 'enable') state.enabled = params.enabled;
        if (name === 'select') state.conversation_id = params.conversation_id ?? 'new-conversation';
        if (name === 'unbind') Object.assign(state, {bound: false, enabled: false, token: null, installation_id: null, conversation_id: null});
        return {state: snapshots()};
      }
      if (name === 'admit') {
        current(params);
        if (params.created_at !== undefined && (!Number.isSafeInteger(params.created_at) || params.created_at < 0)) throw Error('bad timestamp');
        const old = events.get(params.event_id);
        if (old) {
          if (old.text !== params.text) throw Object.assign(Error('event content conflict'), {code: 'CONFLICT'});
          return {event: {...old, duplicate: true}};
        }
        const event = {...params, conversation_id: state.conversation_id, command_id: `command-${params.event_id}`, state: 'queued'};
        events.set(params.event_id, event); return {event: {...event}};
      }
      if (name === 'event') {
        const event = events.get(params.event_id); Object.assign(event, params); return {event: {...event}};
      }
      if (name === 'events') return {events: [...events.values()].filter(event => !params.states || params.states.includes(event.state))};
      if (name === 'enqueue') {
        current(params);
        const old = [...deliveries.values()].find(delivery => delivery.key === params.key);
        if (old) {
          if (['text', 'conversation_id', 'command_id', 'context_token'].some(key => old[key] !== params[key]))
            throw Object.assign(Error('delivery content conflict'), {code: 'CONFLICT'});
          return {delivery: {...old}};
        }
        const delivery = {...params, delivery_id: `delivery-${deliveries.size}`, status: 'pending'};
        deliveries.set(delivery.delivery_id, delivery); return {delivery: {...delivery}};
      }
      if (name === 'outbox') return {deliveries: [...deliveries.values()].filter(delivery => delivery.status === 'pending').slice(0, params.limit)};
      if (name === 'claim') {
        const delivery = deliveries.get(params.delivery_id);
        if (delivery.status !== 'pending') return {delivery: {...delivery}};
        delivery.status = 'sending'; return {delivery: {...delivery, state: snapshots()}};
      }
      if (name === 'ack') {
        const delivery = deliveries.get(params.delivery_id); Object.assign(delivery, {status: params.status, receipt: params.receipt});
        return {delivery: {...delivery}};
      }
      if (name === 'cursor') {current(params); state.cursor = params.cursor; return {state: snapshots()};}
      if (method === 'conversation.list') return {conversations: [{conversation_id: 'conversation-one', title: '桌面工作'}, {conversation_id: 'conversation-two', title: '另一项工作'}]};
      if (method === 'internal.conversation.resolve') return {conversations: ['conversation-one', 'conversation-two']
        .filter(id => id === params.selector).map(id => ({conversation_id: id, title: id === 'conversation-one' ? '桌面工作' : '另一项工作'}))};
      throw Error(`unsupported fixture RPC ${method}`);
    },
    conversation: async id => ({conversation_id: id, title: id === 'conversation-one' ? '桌面工作' : '微信对话'}),
    summary: () => ({status: 'idle'}),
    broadcast: (type, value) => calls.push({kind: 'broadcast', type, value}),
    sendWeixin: async event => {
      calls.push({kind: 'submit', event: {...event}});
      completions.set(event.event_id, deferred()); return {accepted: true};
    },
    finishWeixin: event => completions.get(event.event_id).promise,
    weixinResult: async () => null,
    canDeliverWeixin: async () => true,
    cancel: async id => {
      calls.push({kind: 'cancel', id});
      // Mirror the product: interaction.stop returns the commands it cancelled.
      const cancelled = [...events.values()].filter(event => !['completed', 'cancelled', 'rejected'].includes(event.state))
        .map(event => event.command_id);
      for (const completion of completions.values()) completion.resolve({state: 'cancelled'});
      return {cancelled_command_ids: cancelled};
    },
    cancelWeixinCommands: async id => {
      calls.push({kind: 'cancel-channel', id}); for (const completion of completions.values()) completion.resolve({state: 'cancelled'});
    },
  };
  const apiFactory = origin => ({
    getQr: async options => {calls.push({kind: 'qr', origin, options}); return {qrcode: 'private-qr', qrcode_img_content: 'https://liteapp.weixin.qq.com/q/test'};},
    pollQr: async options => {
      calls.push({kind: 'poll-qr', origin, options});
      return qrResults.length ? qrResults.shift() : untilAbort(options.signal);
    },
    updates: async options => {
      calls.push({kind: 'poll', origin, options});
      return updates ? updates(options, calls) : untilAbort(options.signal);
    },
    sendText: async options => {
      calls.push({kind: 'send', origin, options});
      return sendText ? sendText(options, calls) : {ret: 0, message_id: String(calls.length)};
    },
  });
  const channel = new WeixinChannel(product, {apiFactory, now: clock});
  t.after(async () => {await channel.stop();});
  return {channel, product, calls, events, deliveries, completions, state: snapshots};
}

function inbound(id = 'message-one', text = '实验进展怎么样？', extra = {}) {
  return {message_id: id, message_type: 1, message_state: 2, from_user_id: 'owner', to_user_id: 'bot',
    context_token: 'private-context', create_time_ms: now, item_list: [{type: 1, text_item: {text}}], ...extra};
}

test('Weixin voice is transcribed into the bound conversation once', async t => {
  const f=fixture(t), message=inbound('voice','', {item_list:[{type:3,voice_item:{text:'实验进展怎么样'}}]});
  await f.channel.receive(f.state(),message);await f.channel.receive(f.state(),message);
  const submitted=f.calls.filter(call=>call.kind==='submit');
  assert.equal(submitted.length,1);assert.equal(submitted[0].event.conversation_id,'conversation-one');
  assert.equal(submitted[0].event.text,'微信语音转写：实验进展怎么样');
  assert.ok(f.calls.some(call=>call.method==='channel.weixin.admit' && /^[a-f0-9]{64}$/.test(call.params.content_identity)));
  f.completions.get('voice').resolve({state:'completed',text:'已收到',ref:{turn_id:'voice'}});
  await waitFor(()=>f.events.get('voice').state==='completed');
});

test('Weixin invalid media never dispatches a partial message', async t => {
  const f=fixture(t), message=inbound('invalid-media','',{item_list:[{type:1,text_item:{text:'执行这个'}},{type:9}]});
  await f.channel.receive(f.state(),message);
  assert.equal(f.calls.some(call=>call.kind==='submit'),false);assert.equal(f.events.get('invalid-media').state,'rejected');
});

test('pending media waiting on its command cannot block later channel replies', async t => {
  const f=fixture(t);f.channel.connection=new AbortController();
  f.channel.outboundMedia.readiness=async()=> 'wait';
  for(let index=0;index<101;index++) await f.channel.store('enqueue',{installation_id:'installation',revision:1,key:`media-artifact:command:${index}`,text:'descriptor',conversation_id:'conversation-one',command_id:`command-${index}`});
  await f.channel.store('enqueue',{installation_id:'installation',revision:1,key:'reply',text:'后续消息'});
  await f.channel.pump();
  assert.equal(f.calls.filter(call=>call.kind==='send').at(-1)?.options.text,'后续消息');
  assert.equal([...f.deliveries.values()].filter(item=>item.status==='pending').length,101);
});

test('Weixin text segmentation preserves Unicode, bounds wire payload and identifies clipped replies', () => {
  const text = '😀中'.repeat(4000);
  const parts = weixinTextParts(text);
  assert.equal(parts.length, 3);
  assert.match(parts[0], /^（1\/3） /);
  assert.equal(parts.map(part => part.replace(/^（\d+\/\d+） /, '')).join(''), text);
  for (const part of parts) {
    assert.ok(Buffer.byteLength(part) < 16384);
    assert.doesNotMatch(part, /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u);
  }
  const clipped = weixinTextParts('x'.repeat(15000));
  assert.match(clipped.at(-1), /微信只显示前 14000 字/);
  assert.ok(clipped.at(-1).startsWith(WEIXIN_TEXT.partPrefix(5, 5) + '…'), 'the clip point is marked');
  assert.ok(clipped.at(-1).includes('…（微信只显示前 14000 字'));
  // A long text that already ends with the marker must still obey the limit:
  // the suffix alone is not proof that this reply was clipped.
  const marked = weixinTextParts('x'.repeat(50000) + '…' + WEIXIN_TEXT.truncated)
    .map(part => part.replace(/^（\d+\/\d+） /, '')).join('');
  assert.equal(marked, 'x'.repeat(14000) + '…' + WEIXIN_TEXT.truncated);
  assert.deepEqual(weixinTextParts('   '), []);
});

test('Weixin QR verification stores credentials but requires local binding before transport starts', async t => {
  const f = fixture(t, {unbound: true, qrResults: [{status: 'need_verifycode'}, {status: 'confirmed',
    bot_token: 'private-bot-token', ilink_bot_id: 'bot', ilink_user_id: 'owner', baseurl: 'https://ilinkai.weixin.qq.com'}]});
  await f.channel.start();
  await f.channel.request('weixin.login.start');
  await waitFor(() => f.channel.phase === 'need_verifycode');
  const scanning = await f.channel.status();
  assert.equal(scanning.login.needs_verify_code, true);
  assert.doesNotMatch(JSON.stringify(scanning), /private-bot-token|private-qr/);
  await f.channel.request('weixin.login.verify', {code: '123456'});
  await waitFor(() => f.channel.phase === 'awaiting_confirmation');
  assert.equal(f.state().bound, false); assert.equal(f.state().enabled, false);
  assert.equal(f.calls.filter(call => call.kind === 'poll').length, 0);
  assert.equal(f.calls.filter(call => call.kind === 'poll-qr')[1].options.verifyCode, '123456');
  assert.equal((await f.channel.status()).login, undefined);
  await f.channel.request('weixin.bind', {expected_revision: f.state().revision, conversation_id: 'conversation-one'});
  await waitFor(() => f.calls.some(call => call.kind === 'poll'));
  assert.equal(f.state().bound, true); assert.equal(f.state().conversation_id, 'conversation-one');
  assert.doesNotMatch(JSON.stringify(await f.channel.status()), /private-bot-token|private-context/);
});

test('Weixin QR IDC responses switch only QR polling and retain issued bot origin', async t => {
  const f = fixture(t, {unbound: true, qrResults: [{status: 'scaned_but_redirect', redirect_host: 'ilinkai-idc.weixin.qq.com'},
    {status: 'confirmed', bot_token: 'private-bot-token', ilink_bot_id: 'bot', ilink_user_id: 'owner'}]});
  await f.channel.request('weixin.login.start');
  await waitFor(() => f.channel.phase === 'awaiting_confirmation');
  assert.deepEqual(f.calls.filter(call => call.kind === 'poll-qr').map(call => call.origin),
    ['https://ilinkai.weixin.qq.com', 'https://ilinkai-idc.weixin.qq.com']);
  assert.equal(f.state().base_url, 'https://ilinkai-idc.weixin.qq.com');
});

test('Weixin cancelling local confirmation discards an unbound login credential', async t => {
  const f = fixture(t, {unbound: true, qrResults: [{status: 'confirmed', bot_token: 'private-bot-token',
    ilink_bot_id: 'bot', ilink_user_id: 'owner'}]});
  await f.channel.request('weixin.login.start');
  await waitFor(() => f.channel.phase === 'awaiting_confirmation');
  assert.equal(f.state().bound, false); assert.ok(f.state().token);
  await f.channel.request('weixin.login.cancel');
  assert.equal(f.state().token, null); assert.equal(f.channel.phase, 'unbound');
  assert.equal(f.calls.filter(call => call.kind === 'poll').length, 0);
});

test('Weixin unreadable migrated credentials degrade to login required without blocking product startup', async t => {
  const f = fixture(t);
  const rpc = f.product.rpc;
  f.product.rpc = (method, params) => method === 'channel.weixin.get'
    ? Promise.resolve({state: {...f.state(), token: null, context_token: null, credential_error: true}}) : rpc(method, params);
  await f.channel.start();
  assert.equal(f.channel.phase, 'login_required');
  assert.equal(f.calls.filter(call => call.kind === 'poll').length, 0);
  assert.match((await f.channel.status()).last_error, /凭据无法/);
});

test('Weixin existing unrelated bot binding never implicitly creates a local pairing', async t => {
  const f = fixture(t, {unbound: true, qrResults: [{status: 'binded_redirect'}]});
  await f.channel.request('weixin.login.start');
  await waitFor(() => f.channel.phase === 'error');
  assert.equal(f.state().token, null); assert.equal(f.state().bound, false); assert.equal(f.channel.login, null);
  assert.match((await f.channel.status()).last_error, /原连接|原绑定/);
});

test('Weixin cancelling a pending QR stops polling and removes the QR from status', async t => {
  const f = fixture(t, {unbound: true});
  await f.channel.request('weixin.login.start');
  assert.ok((await f.channel.status()).login);
  const signal = f.calls.find(call => call.kind === 'poll-qr').options.signal;
  await f.channel.request('weixin.login.cancel');
  await f.channel.loginTask;
  assert.equal(signal.aborted, true); assert.equal(f.channel.login, null);
  assert.equal((await f.channel.status()).login, undefined); assert.equal(f.channel.phase, 'unbound');
});

test('Weixin shutdown during login preparation cannot start a later network request or retain a QR attempt', async t => {
  const f = fixture(t, {unbound: true});
  const preparing = deferred(), continuePreparation = deferred();
  f.product.cancelWeixinCommands = async () => {preparing.resolve(); await continuePreparation.promise;};
  const attempt = f.channel.request('weixin.login.start');
  await preparing.promise;
  await f.channel.stop();
  continuePreparation.resolve();
  await attempt.catch(() => {});
  assert.equal(f.calls.filter(call => call.kind === 'qr').length, 0, 'shutdown prevents a late QR fetch');
  assert.equal(f.channel.login ?? null, null);
});

test('Weixin admits only paired direct user messages and deduplicates one model submission', async t => {
  const f = fixture(t);
  await f.channel.start();
  for (const extra of [{from_user_id: 'stranger'}, {group_id: 'group'}, {message_type: 2}, {to_user_id: 'another-bot'}, {message_state: 1}]) {
    await f.channel.receive(f.state(), inbound('ignored', 'ignored', extra));
  }
  assert.equal(f.events.size, 0);
  const message = inbound();
  await f.channel.receive(f.state(), message); await f.channel.receive(f.state(), message);
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 1);
  await waitFor(() => [...f.deliveries.values()].some(delivery => delivery.status === 'sent'));
  f.completions.get(message.message_id).resolve({state: 'completed', text: '实验仍在运行', ref: {native_session_id: 'native', turn_id: 'turn'}});
  await Promise.all([...f.channel.jobs]);
  assert.equal(f.events.get(message.message_id).state, 'completed');
  assert.equal(f.deliveries.size, 2);
  await f.channel.pump();
  const sent = f.calls.filter(call => call.kind === 'send');
  assert.equal(sent.length, 2); assert.equal(sent[0].options.text, WEIXIN_TEXT.received);
  assert.equal(sent[1].options.text, '实验仍在运行');
  assert.equal(sent[0].options.to, 'owner'); assert.equal(sent[0].options.contextToken, 'private-context');
  assert.ok([...f.deliveries.values()].every(delivery => delivery.status === 'sent'));
});

test('Weixin acknowledges an admitted slow turn before model completion, outside its transcript', async t => {
  const f = fixture(t);
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('slow-turn'));
  await waitFor(() => f.calls.some(call => call.kind === 'send'));
  assert.equal(f.events.get('slow-turn').state, 'submitted');
  const receipt = [...f.deliveries.values()][0];
  assert.match(receipt.key, /^received:/);
  assert.equal(receipt.command_id, undefined, 'receipt does not wait for the model command to complete');
  assert.equal(receipt.conversation_id, 'conversation-one');
  assert.equal(f.calls.find(call => call.kind === 'send').options.text, WEIXIN_TEXT.received);
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 1);
  assert.equal(f.calls.find(call => call.kind === 'submit').event.text, '实验进展怎么样？');
  f.completions.get('slow-turn').resolve({state: 'completed', text: '完成了', ref: {turn_id: 'slow'}});
  await Promise.all([...f.channel.jobs]);
  await f.channel.pump();
  assert.deepEqual(f.calls.filter(call => call.kind === 'send').map(call => call.options.text), [WEIXIN_TEXT.received, '完成了']);
});

test('Weixin slow receipt delivery does not block a model result and preserves send order', async t => {
  const pendingSend = deferred();
  const f = fixture(t, {sendText: ({text}) => text === WEIXIN_TEXT.received ? pendingSend.promise : {ret: 0}});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('slow-receipt'));
  await waitFor(() => f.calls.some(call => call.kind === 'send'));
  f.completions.get('slow-receipt').resolve({state: 'completed', text: '快速结果', ref: {turn_id: 'fast'}});
  await Promise.all([...f.channel.jobs]);
  assert.equal(f.events.get('slow-receipt').state, 'completed');
  assert.equal(f.deliveries.size, 2, 'result is persisted while receipt send is still pending');
  assert.equal(f.calls.filter(call => call.kind === 'send').length, 1);
  pendingSend.resolve({ret: 0});
  await f.channel.pump(); await f.channel.pump();
  assert.deepEqual(f.calls.filter(call => call.kind === 'send').map(call => call.options.text), [WEIXIN_TEXT.received, '快速结果']);
});

test('Weixin unknown receipt delivery never repeats the receipt or the model and does not suppress its result', async t => {
  const f = fixture(t, {sendText: ({text}) => {
    if (text === WEIXIN_TEXT.received) throw new WeixinApiError('lost receipt', {kind: 'network', deliveryState: 'unknown'});
    return {ret: 0};
  }});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('lost-receipt'));
  await waitFor(() => [...f.deliveries.values()].some(delivery => delivery.status === 'unknown'));
  f.completions.get('lost-receipt').resolve({state: 'completed', text: '最终结果', ref: {turn_id: 'done'}});
  await Promise.all([...f.channel.jobs]);
  await f.channel.pump(); await f.channel.pump();
  await f.channel.receive(f.state(), inbound('lost-receipt'));
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 1);
  assert.deepEqual(f.calls.filter(call => call.kind === 'send').map(call => call.options.text), [WEIXIN_TEXT.received, '最终结果']);
});

test('Weixin receipt storage failure cannot turn an accepted command into a failed or replayed input', async t => {
  const f = fixture(t);
  const rpc = f.product.rpc;
  f.product.rpc = async (method, params) => {
    if (method === 'channel.weixin.enqueue' && params.key.startsWith('received:')) throw Error('receipt storage failure');
    return rpc(method, params);
  };
  await f.channel.receive(f.state(), inbound('unrecorded-receipt'));
  assert.equal(f.events.get('unrecorded-receipt').state, 'submitted');
  f.completions.get('unrecorded-receipt').resolve({state: 'completed', text: '保留正确结果', ref: {turn_id: 'saved'}});
  await Promise.all([...f.channel.jobs]);
  assert.equal(f.events.get('unrecorded-receipt').state, 'completed');
  assert.deepEqual([...f.deliveries.values()].map(delivery => delivery.text), ['保留正确结果']);
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 1);
});

test('Weixin never acknowledges rejected or busy inputs as processing', async t => {
  for (const code of ['conversation_busy', 'weixin_scope_revoked']) {
    const f = fixture(t);
    f.product.sendWeixin = async () => {throw Object.assign(Error('not admitted'), {code});};
    await f.channel.receive(f.state(), inbound(`rejected-${code}`));
    assert.ok([...f.deliveries.values()].every(delivery => !delivery.key.startsWith('received:')));
    assert.equal(f.deliveries.size, 1, 'rejection has its own actionable reply');
  }
});

test('Weixin discards a queued processing notice once its turn has ended', async t => {
  const f = fixture(t);
  await f.channel.start();
  const pump = f.channel.pump.bind(f.channel);
  f.channel.pump = async () => {};
  await f.channel.receive(f.state(), inbound('fast-turn'));
  f.completions.get('fast-turn').resolve({state: 'completed', text: '直接送达结果', ref: {turn_id: 'fast'}});
  await Promise.all([...f.channel.jobs]);
  f.channel.pump = pump;
  await pump();
  assert.equal([...f.deliveries.values()].find(delivery => delivery.key.startsWith('received:')).status, 'cancelled');
  assert.deepEqual(f.calls.filter(call => call.kind === 'send').map(call => call.options.text), ['直接送达结果']);
});

test('Weixin restart cannot replay a persisted processing notice from an earlier process', async t => {
  const f = fixture(t);
  await f.channel.store('enqueue', {installation_id: 'installation', revision: 1,
    key: 'received:interrupted-command', conversation_id: 'conversation-one',
    text: WEIXIN_TEXT.received, context_token: 'private-context'});
  await f.channel.start();
  await f.channel.pump();
  assert.equal([...f.deliveries.values()][0].status, 'cancelled');
  assert.equal(f.calls.filter(call => call.kind === 'send').length, 0);
});

test('Weixin numbered session selection waits for the list to be delivered', async t => {
  const waiting = deferred();
  const f = fixture(t, {sendText: ({text}) => text.startsWith('最近会话') ? waiting.promise : {ret: 0}});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('list', '会话'));
  const sending = f.channel.pump();
  await waitFor(() => f.calls.some(call => call.kind === 'send'));
  await f.channel.receive(f.state(), inbound('early', '切换 2'));
  assert.equal(f.state().conversation_id, 'conversation-one');
  assert.ok([...f.deliveries.values()].some(delivery => /编号已失效/.test(delivery.text)));
  waiting.resolve({ret: 0});
  await sending;
  await f.channel.receive(f.state(), inbound('after-delivery', '切换 2'));
  assert.equal(f.state().conversation_id, 'conversation-two');
});

test('Weixin malformed or stale timestamps become rejected events without blocking later input', async t => {
  const f = fixture(t);
  for (const [index, create_time_ms] of [undefined, -1, 'bad-time', now - 11 * 60000, now + 61000].entries()) {
    await f.channel.receive(f.state(), inbound(`bad-${index}`, 'bad age', {create_time_ms}));
    assert.equal(f.events.get(`bad-${index}`).state, 'rejected');
  }
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 0);
  await f.channel.receive(f.state(), inbound('fresh'));
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 1);
});

test('Weixin conflicting duplicate content cannot block subsequent messages in the batch', async t => {
  const f = fixture(t);
  await f.channel.receive(f.state(), inbound('same-id', '/help'));
  await f.channel.receive(f.state(), inbound('same-id', 'conflicting new text'));
  await f.channel.receive(f.state(), inbound('next-id'));
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 1);
  assert.equal(f.calls.find(call => call.kind === 'submit').event.event_id, 'next-id');
});

test('Weixin startup reconciles a failed submission without conflicting with an existing control reply or replaying the model', async t => {
  const f = fixture(t);
  f.product.sendWeixin = async () => {f.calls.push({kind: 'failed-submit'}); throw Error('model unavailable');};
  await f.channel.receive(f.state(), inbound('failed-input'));
  assert.equal(f.events.get('failed-input').state, 'unknown');
  assert.equal(f.deliveries.size, 1);
  assert.equal([...f.deliveries.values()][0].text, WEIXIN_TEXT.unconfirmed, 'a submitted turn is never described as a resend');
  await f.channel.start();
  assert.equal(f.calls.filter(call => call.kind === 'failed-submit').length, 1, 'restart does not replay input');
  assert.ok(f.channel.connection);
});

test('Weixin sends a multipart reply sequentially under one delivery and never retries unknown suffixes', async t => {
  let attempt = 0;
  const f = fixture(t, {sendText: async ({text}) => {
    if (text === WEIXIN_TEXT.received) return {ret: 0};
    attempt++; if (attempt === 2) throw new WeixinApiError('fixture network failure', {code: 'network', kind: 'network', deliveryState: 'unknown'});
    return {ret: 0};
  }});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound());
  await waitFor(() => [...f.deliveries.values()].some(delivery => delivery.status === 'sent'));
  f.completions.get('message-one').resolve({state: 'completed', text: '中'.repeat(10000), ref: {turn_id: 'long'}});
  await Promise.all([...f.channel.jobs]);
  assert.equal([...f.deliveries.values()].filter(delivery => delivery.command_id).length, 1, 'multipart result is a single durable delivery');
  await f.channel.pump();
  const sent = f.calls.filter(call => call.kind === 'send' && call.options.text !== WEIXIN_TEXT.received);
  assert.equal(sent.length, 2);
  assert.match(sent[0].options.text, /^（1\/3）/); assert.match(sent[1].options.text, /^（2\/3）/);
  assert.notEqual(sent[0].options.clientId, sent[1].options.clientId);
  const delivery = [...f.deliveries.values()].find(delivery => delivery.command_id);
  assert.equal(delivery.status, 'unknown');
  assert.equal(delivery.receipt.accepted_parts, 1); assert.equal(delivery.receipt.failed_part, 1);
  await f.channel.pump(); assert.equal(f.calls.filter(call => call.kind === 'send').length, 3);
});

test('Weixin clipped reply marks the cut once across bounded and segmented delivery', async t => {
  const f = fixture(t);
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('clip-once'));
  await waitFor(() => [...f.deliveries.values()].some(delivery => delivery.status === 'sent'));
  f.completions.get('clip-once').resolve({state: 'completed', text: 'y'.repeat(15000), ref: {turn_id: 'clip'}});
  await Promise.all([...f.channel.jobs]);
  await f.channel.pump();
  const wire = f.calls.filter(call => call.kind === 'send' && call.options.text !== WEIXIN_TEXT.received)
    .map(call => call.options.text).join('');
  const marker = '…' + WEIXIN_TEXT.truncated;
  assert.equal(wire.split(marker).length - 1, 1, 'the clip point is marked exactly once');
  assert.match(wire, /微信只显示前 14000 字/);
});

test('Weixin rechecks source authorization before every outgoing segment', async t => {
  let alreadySent = false;
  const f = fixture(t, {sendText: async ({text}) => {if (text !== WEIXIN_TEXT.received) alreadySent = true; return {ret: 0};}});
  f.product.canDeliverWeixin = async () => !alreadySent;
  await f.channel.start();
  await f.channel.receive(f.state(), inbound());
  await waitFor(() => [...f.deliveries.values()].some(delivery => delivery.status === 'sent'));
  f.completions.get('message-one').resolve({state: 'completed', text: 'x'.repeat(8000), ref: {turn_id: 'revocable'}});
  await Promise.all([...f.channel.jobs]);
  await f.channel.pump();
  assert.equal(f.calls.filter(call => call.kind === 'send').length, 2, 'later segments stop when source permissions change');
  const delivery = [...f.deliveries.values()].find(delivery => delivery.command_id);
  assert.equal(delivery.status, 'cancelled'); assert.equal(delivery.receipt.accepted_parts, 1);
});

test('Weixin remote stop cancels the shared conversation without submitting a new model turn', async t => {
  const f = fixture(t);
  f.product.summary = () => ({status: 'running'});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('stop', '/stop'));
  assert.deepEqual(f.calls.filter(call => call.kind === 'cancel').map(call => call.id), ['conversation-one']);
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 0);
  await f.channel.pump();
  assert.deepEqual(f.calls.filter(call => call.kind === 'send').map(call => call.options.text), [WEIXIN_TEXT.stopped]);
});

test('Weixin /stop on an idle conversation reports the real state and never cancels', async t => {
  const f = fixture(t);
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('idle-stop', '停止回复'));
  assert.equal(f.calls.some(call => call.kind === 'cancel'), false, 'an idle conversation is not cancelled');
  await f.channel.pump();
  assert.deepEqual(f.calls.filter(call => call.kind === 'send').map(call => call.options.text), [WEIXIN_TEXT.notRunning]);
});

test('Weixin stopping an in-flight turn sends one notice and records the turn as unknown', async t => {
  const f = fixture(t);
  f.product.summary = () => ({status: 'running'});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('stopped-turn'));
  await waitFor(() => f.calls.some(call => call.kind === 'send'));
  await f.channel.receive(f.state(), inbound('stop-after-turn', '/stop'));
  await Promise.all([...f.channel.jobs]);
  await f.channel.pump();
  const texts = f.calls.filter(call => call.kind === 'send').map(call => call.options.text);
  assert.equal(texts.filter(text => text === WEIXIN_TEXT.stopped).length, 1);
  assert.equal(texts.includes(WEIXIN_TEXT.replyStopped), false, 'the cancelled turn does not repeat the stop');
  assert.equal(f.events.get('stopped-turn').state, 'unknown');
});

test('Weixin stopping a desktop turn does not swallow the next weixin turn stop notice', async t => {
  const f = fixture(t);
  f.product.summary = () => ({status: 'running'});
  // /stop cancelled the desktop turn, not the weixin turn submitted afterwards.
  f.product.cancel = async id => { f.calls.push({kind: 'cancel', id}); return {cancelled_command_ids: ['desktop-command']}; };
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('stop-desktop', '/stop'));
  assert.ok(f.calls.some(call => call.kind === 'cancel'));
  await f.channel.receive(f.state(), inbound('new-weixin-turn'));
  await waitFor(() => f.calls.some(call => call.kind === 'submit' && call.event.event_id === 'new-weixin-turn'));
  f.completions.get('new-weixin-turn').resolve({state: 'cancelled'});
  await Promise.all([...f.channel.jobs]);
  await f.channel.pump(); await f.channel.pump();
  const texts = f.calls.filter(call => call.kind === 'send').map(call => call.options.text);
  assert.equal(texts.filter(text => text === WEIXIN_TEXT.stopped).length, 1);
  assert.equal(texts.filter(text => text === WEIXIN_TEXT.replyStopped).length, 1,
    'the newer turn reports its own cancellation');
});

test('Weixin a cancelled turn reports once even when its finish settles late', async t => {
  let clock = now;
  const f = fixture(t, {clock: () => clock});
  f.product.summary = () => ({status: 'running'});
  f.product.cancel = async id => {
    f.calls.push({kind: 'cancel', id});
    const commandId = f.events.get('late-finish').command_id;
    clock += 5 * 60_000; // a late finish, far outside any short post-stop window
    f.completions.get('late-finish').resolve({state: 'cancelled'}); // races the stop result
    await new Promise(resolve => setTimeout(resolve, 10));
    return {cancelled_command_ids: [commandId]};
  };
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('late-finish'));
  await waitFor(() => f.calls.some(call => call.kind === 'submit'));
  await f.channel.receive(f.state(), inbound('stop-late', '/stop'));
  await Promise.all([...f.channel.jobs]);
  await f.channel.pump(); await f.channel.pump();
  const texts = f.calls.filter(call => call.kind === 'send').map(call => call.options.text);
  assert.equal(texts.filter(text => text === WEIXIN_TEXT.stopped).length, 1);
  assert.equal(texts.includes(WEIXIN_TEXT.replyStopped), false, 'the stop is not repeated');
  assert.equal(f.events.get('late-finish').state, 'unknown');
});

test('Weixin receipt distinguishes the assistant session from a direct conversation', async t => {
  const f = fixture(t, {mode: 'assistant'});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('assistant-receipt'));
  await waitFor(() => f.calls.some(call => call.kind === 'send'));
  assert.equal(f.calls.find(call => call.kind === 'send').options.text, WEIXIN_TEXT.receivedAssistant);
  f.completions.get('assistant-receipt').resolve({state: 'completed', text: '助理结果', ref: {turn_id: 'assistant'}});
  await Promise.all([...f.channel.jobs]);
});

test('Weixin media receipt states the item count and the whitelisted failure reason', async t => {
  const f = fixture(t);
  await f.channel.start();
  f.channel.media.prepare = async () => ({text: '微信附件 1：报告.pdf', materials: ['material-1']});
  const items = [{type: 4, file_item: {file_name: '报告.pdf', media: {encrypt_query_param: 'x'}}},
    {type: 1, text_item: {text: '看看这个'}}];
  await f.channel.receive(f.state(), inbound('media-receipt', '', {item_list: items}));
  await waitFor(() => f.calls.some(call => call.kind === 'send'));
  assert.equal(f.calls.find(call => call.kind === 'send').options.text, WEIXIN_TEXT.mediaReceived(2));
  f.completions.get('media-receipt').resolve({state: 'completed', text: '已保存附件', ref: {turn_id: 'media'}});
  await Promise.all([...f.channel.jobs]);
});

test('Weixin rejected media echoes the channel reason and marks the event rejected', async t => {
  const f = fixture(t);
  f.channel.media.prepare = async () => {throw Object.assign(Error('微信媒体超过 8 MiB 上限。'), {code: 'weixin_media_invalid'});};
  await f.channel.receive(f.state(), inbound('media-failure', '', {item_list: [{type: 4, file_item: {file_name: '大文件.zip', media: {encrypt_query_param: 'y'}}}]}));
  assert.equal(f.events.get('media-failure').state, 'rejected');
  const texts = [...f.deliveries.values()].map(delivery => delivery.text);
  assert.ok(texts.includes('微信媒体超过 8 MiB 上限。'));
  assert.equal(texts.includes(WEIXIN_TEXT.mediaFailedFallback), false);
});

test('Weixin media failures keep voice reasons and fall back to the attachment notice', async t => {
  for (const [id, code, message] of [
    ['voice-unsupported', 'weixin_voice_unsupported', '暂不支持此微信语音编码，请改用文字或其他语音格式。'],
    ['voice-decode', 'weixin_voice_decode', '微信语音解码失败，请改用文字或重新发送。'],
  ]) {
    const f = fixture(t);
    f.channel.media.prepare = async () => { throw Object.assign(Error(message), {code}); };
    await f.channel.receive(f.state(), inbound(id, '', {item_list: [{type: 4, file_item: {file_name: '语音.silk', media: {encrypt_query_param: 'v'}}}]}));
    assert.equal(f.events.get(id).state, 'rejected');
    const texts = [...f.deliveries.values()].map(delivery => delivery.text);
    assert.ok(texts.includes(message), `${code} keeps the channel reason`);
    assert.equal(texts.includes(WEIXIN_TEXT.notSubmitted), false);
    assert.equal(texts.includes(WEIXIN_TEXT.unconfirmed), false);
  }
  // An unrecognized media failure stays an attachment problem, never a model one.
  const f = fixture(t);
  f.channel.media.prepare = async () => { throw Object.assign(Error('unexpected provider failure'), {code: 'voice_provider_error'}); };
  await f.channel.receive(f.state(), inbound('media-unknown', '', {item_list: [{type: 4, file_item: {file_name: '语音.silk', media: {encrypt_query_param: 'u'}}}]}));
  assert.equal(f.events.get('media-unknown').state, 'rejected');
  const texts = [...f.deliveries.values()].map(delivery => delivery.text);
  assert.ok(texts.includes(WEIXIN_TEXT.mediaFailedFallback));
  assert.equal(texts.includes(WEIXIN_TEXT.notSubmitted), false);
  assert.equal(texts.includes(WEIXIN_TEXT.unconfirmed), false);
});

test('mediaFailureText echoes only this repository channel wording and otherwise falls back', () => {
  assert.equal(mediaFailureText({code: 'weixin_media_invalid', message: '微信媒体超过 8 MiB 上限。'}), '微信媒体超过 8 MiB 上限。');
  assert.equal(mediaFailureText({code: 'speech_unavailable', message: '尚未设置语音识别，请在电脑设置中配置。'}), '尚未设置语音识别，请在电脑设置中配置。');
  assert.equal(mediaFailureText({code: 'weixin_voice_unsupported', message: '暂不支持此微信语音编码，请改用文字或其他语音格式。'}),
    '暂不支持此微信语音编码，请改用文字或其他语音格式。');
  assert.equal(mediaFailureText({code: 'weixin_voice_decode', message: '微信语音解码失败，请改用文字或重新发送。'}),
    '微信语音解码失败，请改用文字或重新发送。');
  assert.equal(mediaFailureText({code: 'weixin_media_download', message: 'x'.repeat(81)}), WEIXIN_TEXT.mediaFailedFallback);
  assert.equal(mediaFailureText({code: 'weixin_media_invalid', message: '第一行\n第二行'}), WEIXIN_TEXT.mediaFailedFallback);
  assert.equal(mediaFailureText({code: 'network', message: 'unauthorized token at https://example.test'}), WEIXIN_TEXT.mediaFailedFallback);
  assert.equal(mediaFailureText(Error('boom')), WEIXIN_TEXT.mediaFailedFallback);
});

test('Weixin failure notice distinguishes "never reached the conversation" from "submitted, result unknown"', async t => {
  // The turn is stored as submitted only after this write; a storage failure
  // there proves the message never entered the conversation.
  const f = fixture(t), rpc = f.product.rpc;
  let failSubmitted = true;
  f.product.rpc = async (method, params) => {
    if (failSubmitted && method === 'channel.weixin.event' && params.state === 'submitted') {
      failSubmitted = false;
      throw Error('storage outage');
    }
    return rpc(method, params);
  };
  await f.channel.receive(f.state(), inbound('not-submitted'));
  assert.equal(f.events.get('not-submitted').state, 'rejected');
  assert.equal([...f.deliveries.values()][0].text, WEIXIN_TEXT.notSubmitted);
  assert.equal(f.calls.some(call => call.kind === 'submit'), false);
});

test('Weixin pre-admit model errors are never described as sent and are not replayed', async t => {
  const f = fixture(t);
  // The real models.requireOptions code and message: product.send rejects this
  // before command.admit, so no turn was ever submitted.
  f.product.sendWeixin = async () => {
    f.calls.push({kind: 'submit'});
    throw Object.assign(Error('请先为该用途在设置中配置模型、服务地址和凭据'), {code: 'model_not_configured'});
  };
  await f.channel.receive(f.state(), inbound('model-missing'));
  assert.equal(f.events.get('model-missing').state, 'rejected');
  const texts = [...f.deliveries.values()].map(delivery => delivery.text);
  assert.ok(texts.includes(WEIXIN_TEXT.notSubmitted));
  assert.equal(texts.includes(WEIXIN_TEXT.unconfirmed), false, 'a pre-admit rejection is not an unknown result');
  await f.channel.start();
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 1, 'restart does not replay the input');
});

test('Weixin credential expiry stops transport and does not retry rejected sends', async t => {
  const f = fixture(t, {sendText: async () => {throw new WeixinApiError('expired', {code: -14, kind: 'business', deliveryState: 'rejected'});}});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('help', '/help'));
  await f.channel.pump();
  assert.equal(f.channel.phase, 'login_required');
  assert.equal([...f.deliveries.values()][0].status, 'failed');
  assert.equal(f.calls.find(call => call.kind === 'poll').options.signal.aborted, true);
  await f.channel.pump(); assert.equal(f.calls.filter(call => call.kind === 'send').length, 1);
});

test('Weixin offline polling backs off and shutdown aborts the pending reconnect delay', async t => {
  const f = fixture(t, {updates: async () => {throw new WeixinApiError('offline', {kind: 'network', code: 'network'});}});
  await f.channel.start();
  await waitFor(() => f.channel.error && f.channel.phase === 'reconnecting');
  await f.channel.stop();
  assert.equal(f.calls.filter(call => call.kind === 'poll').length, 1);
  assert.equal(f.channel.connection, null);
});

test('Weixin shutdown aborts an in-flight send, records unknown delivery and does not repeat it', async t => {
  const f = fixture(t, {sendText: options => untilAbort(options.signal)});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('help', '/help'));
  const sending = f.channel.pump();
  await waitFor(() => f.calls.some(call => call.kind === 'send'));
  const signal = f.calls.find(call => call.kind === 'send').options.signal;
  await f.channel.stop(); await sending;
  assert.equal(signal.aborted, true);
  assert.equal([...f.deliveries.values()][0].status, 'unknown');
  await f.channel.pump(); assert.equal(f.calls.filter(call => call.kind === 'send').length, 1);
});

test('Weixin unbind cancels pending work and makes stale routes unusable; stop closes owned polls', async t => {
  const f = fixture(t);
  await f.channel.start();
  const route = f.state();
  await f.channel.receive(route, inbound());
  await f.channel.request('weixin.unbind', {expected_revision: route.revision});
  const sendsAtUnbind = f.calls.filter(call => call.kind === 'send').length;
  assert.equal(f.state().token, null); assert.equal(f.state().enabled, false);
  assert.equal(f.calls.find(call => call.kind === 'poll').options.signal.aborted, true);
  await assert.rejects(f.channel.authorize(route), {code: 'weixin_scope_revoked'});
  await f.channel.pump();
  assert.equal(f.calls.filter(call => call.kind === 'send').length, sendsAtUnbind);
  await f.channel.stop();
  assert.equal(f.channel.closed, true); assert.equal(f.channel.connection, null); assert.equal(f.channel.jobs.size, 0);
});

test('Weixin history reads canonical text in bounded earlier pages without a new model turn', async t => {
  const f = fixture(t);
  f.product.history = async id => {
    assert.equal(id, f.state().conversation_id);
    return {messages: [
      {role: 'system', seq: 0, text: 'hidden-system'},
      ...Array.from({length: 12}, (_, index) => ({seq: index + 1, role: index % 2 ? 'assistant' : 'user',
        text: '正文-' + (index + 1), reasoning: 'hidden-reasoning', content: [{type: 'tool_result', text: 'hidden-tool'}]})),
      {role: 'tool', seq: 13, text: 'hidden-tool'}], gaps: []};
  };
  await f.channel.receive(f.state(), inbound('history-latest', '查看历史'));
  const latest = [...f.deliveries.values()].at(-1).text;
  assert.match(latest, /正文-7\b/); assert.match(latest, /正文-12\b/);
  assert.doesNotMatch(latest, /正文-6\b|hidden-/);
  assert.match(latest, /更早记录：\/history 7/);
  await f.channel.receive(f.state(), inbound('history-older', '/history 7'));
  const older = [...f.deliveries.values()].at(-1).text;
  assert.match(older, /正文-1\b/); assert.match(older, /正文-6\b/);
  assert.doesNotMatch(older, /正文-7\b|hidden-/);
  assert.match(older, /已到当前可读取历史的开头/);
  assert.equal(f.calls.some(call => call.kind === 'submit'), false);
});

test('Weixin history clips long Unicode text, states gaps and validates the cursor', async t => {
  const f = fixture(t); let reads = 0;
  f.product.history = async () => {reads++; return {messages: [{seq: 1, role: 'assistant', text: '😀'.repeat(2000)}],
    gaps: [{reason: 'private-path-error'}], active_command_ids: ['active']};};
  await f.channel.receive(f.state(), inbound('history-long', '/history'));
  const text = [...f.deliveries.values()].at(-1).text;
  assert.equal(Array.from(text).filter(char => char === '😀').length, 1600);
  assert.match(text, /只显示前 1600 字/); assert.match(text, /并非完整记录/); assert.match(text, /仍在回复/);
  assert.doesNotMatch(text, /private-path-error|\uFFFD/);
  for (const value of ['-1', '1.5', '9007199254740992', '1 extra'])
    await f.channel.receive(f.state(), inbound('history-invalid-' + value, '/history ' + value));
  assert.equal(reads, 1);
  assert.match([...f.deliveries.values()].at(-1).text, /用法/);
});

test('Weixin history cannot send after the current route changes during the read', async t => {
  const f = fixture(t), read = deferred();
  let reading = false;
  f.product.history = () => {reading = true; return read.promise;};
  const operation = f.channel.receive(f.state(), inbound('history-stale', '/history'));
  await waitFor(() => reading);
  await f.channel.request('weixin.unbind', {expected_revision: f.state().revision});
  read.resolve({messages: [{seq: 1, role: 'user', text: 'stale-private-text'}], gaps: []});
  await operation;
  assert.equal(f.deliveries.size, 0);
});

test('Weixin Chinese menu is deterministic and requires no mobile webpage', async t => {
  const f = fixture(t);
  await f.channel.receive(f.state(), inbound('menu', '菜单'));
  const text = [...f.deliveries.values()].at(-1).text;
  assert.match(text, /电脑保持 Mirally 联网运行/);
  for (const command of ['/history', '/sessions']) assert.ok(text.includes(command));
  assert.doesNotMatch(text, /\/works/);
  assert.equal(f.calls.some(call => call.kind === 'submit'), false);
});

test('ordinary questions about work reach the selected conversation unchanged', async t => {
  const f = fixture(t);
  await f.channel.receive(f.state(), inbound('work-question', '我的工作怎么样了'));
  const submitted = f.calls.filter(call => call.kind === 'submit');
  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].event.text, '我的工作怎么样了');
});

test('Weixin can switch to a complete short legacy conversation ID without allowing short ambiguous prefixes', async t => {
  const f = fixture(t), rpc = f.product.rpc;
  f.product.rpc = (method, params) => method === 'internal.conversation.resolve'
    ? {conversations: params.selector === 'main' ? [{conversation_id: 'main', title: '主会话'}] : []}
    : rpc(method, params);
  await f.channel.receive(f.state(), inbound('use-too-short-prefix', '/use mai'));
  assert.equal(f.state().conversation_id, 'conversation-one');
  await f.channel.receive(f.state(), inbound('use-exact-main', '/use main'));
  assert.equal(f.state().conversation_id, 'main');
  assert.match([...f.deliveries.values()].at(-1).text, /已切换到/);
});
