/** Channel orchestration against API/product doubles; durable SQL/wire tests live separately. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WeixinChannel, weixinTextParts} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin.mjs';
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

function fixture(t, {unbound = false, qrResults = [], sendText, updates} = {}) {
  let state = {installation_id: unbound ? null : 'installation', revision: 1, bound: !unbound, enabled: !unbound,
    peer_id: unbound ? null : 'owner', bot_id: unbound ? null : 'bot', token: unbound ? null : 'private-bot-token',
    base_url: 'https://ilinkai.weixin.qq.com', conversation_id: unbound ? null : 'conversation-one', cursor: '', context_token: null};
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
      calls.push({kind: 'cancel', id}); for (const completion of completions.values()) completion.resolve({state: 'cancelled'});
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
  const channel = new WeixinChannel(product, {apiFactory, now: () => now});
  t.after(async () => {await channel.stop();});
  return {channel, product, calls, events, deliveries, completions, state: snapshots};
}

function inbound(id = 'message-one', text = '实验进展怎么样？', extra = {}) {
  return {message_id: id, message_type: 1, message_state: 2, from_user_id: 'owner', to_user_id: 'bot',
    context_token: 'private-context', create_time_ms: now, item_list: [{type: 1, text_item: {text}}], ...extra};
}

test('Weixin text segmentation preserves Unicode, bounds wire payload and identifies clipped replies', () => {
  const text = '😀中'.repeat(4000);
  const parts = weixinTextParts(text);
  assert.equal(parts.length, 3);
  assert.equal(parts.map(part => part.replace(/^\[\d+\/\d+\] /, '')).join(''), text);
  for (const part of parts) {
    assert.ok(Buffer.byteLength(part) < 16384);
    assert.doesNotMatch(part, /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u);
  }
  assert.match(weixinTextParts('x'.repeat(15000)).at(-1), /桌面查看完整内容/);
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
  f.completions.get(message.message_id).resolve({state: 'completed', text: '实验仍在运行', ref: {native_session_id: 'native', turn_id: 'turn'}});
  await Promise.all([...f.channel.jobs]);
  assert.equal(f.events.get(message.message_id).state, 'completed');
  assert.equal(f.deliveries.size, 1);
  await f.channel.pump();
  const sent = f.calls.filter(call => call.kind === 'send');
  assert.equal(sent.length, 1); assert.equal(sent[0].options.text, '实验仍在运行');
  assert.equal(sent[0].options.to, 'owner'); assert.equal(sent[0].options.contextToken, 'private-context');
  assert.equal([...f.deliveries.values()][0].status, 'sent');
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
  await f.channel.start();
  assert.equal(f.calls.filter(call => call.kind === 'failed-submit').length, 1, 'restart does not replay input');
  assert.ok(f.channel.connection);
});

test('Weixin sends a multipart reply sequentially under one receipt and never retries unknown suffixes', async t => {
  let attempt = 0;
  const f = fixture(t, {sendText: async () => {
    attempt++; if (attempt === 2) throw new WeixinApiError('fixture network failure', {code: 'network', kind: 'network', deliveryState: 'unknown'});
    return {ret: 0};
  }});
  await f.channel.start();
  await f.channel.receive(f.state(), inbound());
  f.completions.get('message-one').resolve({state: 'completed', text: '中'.repeat(10000), ref: {turn_id: 'long'}});
  await Promise.all([...f.channel.jobs]);
  assert.equal(f.deliveries.size, 1, 'multipart text is a single durable delivery');
  await f.channel.pump();
  const sent = f.calls.filter(call => call.kind === 'send');
  assert.equal(sent.length, 2);
  assert.match(sent[0].options.text, /^\[1\/3\]/); assert.match(sent[1].options.text, /^\[2\/3\]/);
  assert.notEqual(sent[0].options.clientId, sent[1].options.clientId);
  const delivery = [...f.deliveries.values()][0];
  assert.equal(delivery.status, 'unknown');
  assert.equal(delivery.receipt.accepted_parts, 1); assert.equal(delivery.receipt.failed_part, 1);
  await f.channel.pump(); assert.equal(f.calls.filter(call => call.kind === 'send').length, 2);
});

test('Weixin rechecks source authorization before every outgoing segment', async t => {
  let alreadySent = false;
  const f = fixture(t, {sendText: async () => {alreadySent = true; return {ret: 0};}});
  f.product.canDeliverWeixin = async () => !alreadySent;
  await f.channel.start();
  await f.channel.receive(f.state(), inbound());
  f.completions.get('message-one').resolve({state: 'completed', text: 'x'.repeat(8000), ref: {turn_id: 'revocable'}});
  await Promise.all([...f.channel.jobs]);
  await f.channel.pump();
  assert.equal(f.calls.filter(call => call.kind === 'send').length, 1, 'later segments stop when source permissions change');
  const delivery = [...f.deliveries.values()][0];
  assert.equal(delivery.status, 'cancelled'); assert.equal(delivery.receipt.accepted_parts, 1);
});

test('Weixin remote stop cancels the shared conversation without submitting a new model turn', async t => {
  const f = fixture(t);
  await f.channel.start();
  await f.channel.receive(f.state(), inbound('stop', '/stop'));
  assert.deepEqual(f.calls.filter(call => call.kind === 'cancel').map(call => call.id), ['conversation-one']);
  assert.equal(f.calls.filter(call => call.kind === 'submit').length, 0);
  await f.channel.pump();
  assert.match(f.calls.find(call => call.kind === 'send').options.text, /已停止/);
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
  assert.equal(f.state().token, null); assert.equal(f.state().enabled, false);
  assert.equal(f.calls.find(call => call.kind === 'poll').options.signal.aborted, true);
  await assert.rejects(f.channel.authorize(route), {code: 'weixin_scope_revoked'});
  assert.equal(f.calls.filter(call => call.kind === 'send').length, 0);
  await f.channel.stop();
  assert.equal(f.channel.closed, true); assert.equal(f.channel.connection, null); assert.equal(f.channel.jobs.size, 0);
});
