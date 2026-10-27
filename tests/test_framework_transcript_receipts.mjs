/** Native-log receipts are projected into the retained UI's real identity contract. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {createHash} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {MirallyProduct, projectMessages} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

const asModule = source => 'data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source, {mode: 'transform'})).toString('base64');
const coercion = asModule(readFileSync(new URL('../frontend/control-ui/packages/normalization-core/src/record-coerce.ts', import.meta.url), 'utf8'));
const identitySource = readFileSync(new URL('../frontend/control-ui/packages/gateway-client/src/session-projection-message-identity.ts', import.meta.url), 'utf8')
  .replaceAll('"@openclaw/normalization-core/record-coerce"', JSON.stringify(coercion));
const {readSessionMessageIdentity, readSessionMessageSequence} = await import(asModule(identitySource));
const mergeSource = readFileSync(new URL('../frontend/control-ui/ui/src/pages/chat/history-merge.ts', import.meta.url), 'utf8');
const receiptStart = mergeSource.indexOf('function isInitialSubmissionReceipt('), receiptEnd = mergeSource.indexOf('\nfunction readChatSubmissionBatch', receiptStart);
assert.ok(receiptStart >= 0 && receiptEnd > receiptStart);
const {isInitialSubmissionReceipt} = await import(asModule('export ' + mergeSource.slice(receiptStart, receiptEnd)));
const user = (id, text, kind = 'user') => ({id, role: 'user', content: [{type: 'text', text}], source: {kind}});
const event = (seq, type, data) => ({seq, time: 1700000000000 + seq, type, data});
const command = (id, message, session = 'native-one') => ({command_id: id, runtime_request_id: message, session_id: session, state: 'completed'});

test('native reasoning is preserved separately from visible answers, including reasoning-only steps', () => {
  const human = user('ask', '求解'), source = [event(0, 'user/message', human),
    event(1, 'assistant/message', {turn: 1, message: {id: 'reason-first', content: [{type: 'reasoning', text: '先核对已知条件。'}]}}),
    event(2, 'assistant/message', {turn: 1, message: {id: 'answer', content: [
      {type: 'reasoning', text: '再验证结果。'}, {type: 'text', text: '结果为 42。'},
      {type: 'tool-call', id: 'not-reasoning', name: 'unknown', arguments: '{}'}]}}),
    event(3, 'turn/end', {turn: 1, reason: {kind: 'completed'}})];
  const before = JSON.stringify(source), rows = projectMessages(source, [command('real-run', 'ask')]);
  assert.equal(rows[1].text, '');
  assert.deepEqual(rows[1].content, [{type: 'thinking', thinking: '先核对已知条件。'}]);
  assert.equal(rows[2].text, '结果为 42。');
  assert.deepEqual(rows[2].content, [{type: 'thinking', thinking: '再验证结果。'}, {type: 'text', text: '结果为 42。'}]);
  assert.equal(rows[1].__openclaw.runTerminal, undefined); assert.equal(rows[2].__openclaw.runTerminal, true);
  assert.deepEqual(rows.slice(1).map(row => row.__openclaw.runId), ['real-run', 'real-run']);
  assert.equal(JSON.stringify(source), before, 'Displaying reasoning cannot alter native replay content');
});

test('queued human rows have one stable receipt and assistant ownership follows consumed input', () => {
  const a = user('native-user-a', '第一条需求'), b = user('native-user-b', '忙中补充'), injected = user('context', '内部上下文', 'runtime-context');
  const events = [event(0, 'agent/inbox/spliced', {inserted: [a]}), event(1, 'turn/start', {turn: 1}),
    event(2, 'user/message', a), event(3, 'user/message', injected), event(4, 'agent/inbox/spliced', {inserted: [b, injected]}),
    event(5, 'assistant/message', {turn: 1, message: {id: 'reply-a', content: [{type: 'text', text: '第一条的回复'}]}}),
    event(6, 'turn/end', {turn: 1, reason: {kind: 'completed'}}), event(7, 'user/message', b),
    event(8, 'assistant/message', {turn: 2, message: {id: 'reply-b', content: [{type: 'text', text: '补充的回复'}]}}),
    event(9, 'turn/end', {turn: 2, reason: {kind: 'completed'}})];
  const before = JSON.stringify(events), rows = projectMessages(events, [command('run-a', a.id), command('run-b', b.id)]);
  assert.deepEqual(rows.map(row => row.id), [a.id, b.id, 'reply-a', 'reply-b']);
  assert.deepEqual(rows.map(row => row.command_id), ['run-a', 'run-b', 'run-a', 'run-b']);
  assert.equal(rows[0].native_seq, 0); assert.equal(rows[0].seq, 1); assert.equal(rows[0].timestamp, events[0].time);
  assert.equal(rows[1].native_seq, 4); assert.equal(rows[1].seq, 5, 'Consumption must not replace the first durable admission receipt');
  assert.equal(rows[2].__openclaw.runTerminal, true);
  assert.equal(JSON.stringify(events), before, 'Projection cannot rewrite the native transcript');
  const persisted = readSessionMessageIdentity(rows[0]);
  const optimistic = readSessionMessageIdentity({role: 'user', content: [{type: 'text', text: '第一条需求'}], __openclaw: {idempotencyKey: 'run-a:user'}});
  assert.equal(persisted.sendId, optimistic.sendId); assert.equal(persisted.runId, 'run-a');
  assert.equal(readSessionMessageSequence(rows[0]), 1);
  assert.equal(isInitialSubmissionReceipt({kind: 'initial', pendingRunId: 'run-a'}, persisted), true);
  assert.equal(isInitialSubmissionReceipt({kind: 'initial', pendingRunId: 'another-run'}, persisted), false);
});

test('history maps commands beyond page 200 and keeps sequence ranges across native generations', async () => {
  const product = Object.create(MirallyProduct.prototype), calls = [];
  const commands = Array.from({length: 205}, (_, index) => command('run-' + index, 'user-' + index, index === 204 ? 'native-two' : 'native-one'));
  const oldEvents = [event(0, 'user/message', user('user-0', '旧代需求')), event(1, 'assistant/message', {turn: 1, message: {id: 'old-reply', content: [{type: 'text', text: '旧代回复'}]}}), event(2, 'turn/end', {turn: 1, reason: {kind: 'completed'}})];
  const newEvents = [event(0, 'agent/inbox/spliced', {inserted: [user('user-204', '新代需求')]}), event(1, 'user/message', user('user-204', '新代需求'))];
  Object.assign(product, {handles: new Map([['native-one', {agent: {session: {snapshotEvents: () => oldEvents}}}], ['native-two', {agent: {session: {snapshotEvents: () => newEvents}}}]]),
    async rpc(method, params) {
      calls.push({method, params});
      if (method === 'conversation.history_refs') return {generations: [{generation: 1, session_id: 'native-one'}, {generation: 2, session_id: 'native-two'}]};
      if (method === 'command.list') return {commands: commands.slice(params.offset, params.offset + params.limit)};
      throw new Error('Unexpected RPC ' + method);
    }});
  const history = await product.history('one', true);
  assert.deepEqual(calls.filter(call => call.method === 'command.list').map(call => call.params.offset), [0, 200]);
  assert.deepEqual(history.messages.map(row => row.seq), [1, 2, 4]);
  assert.equal(history.messages[2].command_id, 'run-204'); assert.equal(history.messages[2].native_seq, 0);
  assert.equal(history.messages[2].native_session_id, 'native-two'); assert.equal(history.messages[2].generation, 2);
  assert.equal(await product.nativeSequenceOffset('one', 'native-two'), oldEvents.length);
  assert.deepEqual(history.events.map(row => row.seq), [0, 1, 2, 0, 1]);
});

test('send and replay acknowledge the same positive native user receipt and command-owned completion', async () => {
  const product = Object.create(MirallyProduct.prototype), events = [], broadcasts = [];
  product.commandPolicies = new Map();
  let finish;
  const idle = new Promise(resolve => {finish = resolve;}), conversation = {conversation_id: 'one', session_id: 'native-one', generation: 1, frozen: false};
  const handle = {agent: {status: 'idle', session: {snapshotEvents: () => events}, followup(message) {events.push(event(0, 'agent/inbox/spliced', {inserted: [message]}));}, whenIdle: () => idle}};
  const messageId = 'mirally-' + createHash('sha256').update('one:run-a').digest('hex');
  let admitted = false;
  Object.assign(product, {handles: new Map([['native-one', handle]]), activeCommands: new Map(), requestPolicies: new Map(), nativeErrors: new Map(), contexts: new Map(),
    serial: (_id, operation) => operation(), materials: {content: async () => []}, models: {requireOptions() {}}, async refreshModels() {}, async guard() {},
    conversation: async () => conversation, ensureNative: async () => ({conversation, handle}),
    ctx: {sessions: {async flush() {}}}, broadcast: (type, payload) => broadcasts.push({type, ...payload}),
    async rpc(method) {
      if (method === 'command.list') return {commands: []};
      if (method === 'command.admit') { const replay = admitted; admitted = true; return {...command('run-a', messageId), should_dispatch: !replay}; }
      if (method === 'command.ack') return {};
      if (method === 'context.read') return {};
      if (method === 'internal.conversation.autotitle') return {changed: false};
      throw new Error('Unexpected RPC ' + method);
    }});
  const first = await product.send('one', {commandId: 'run-a', text: '首轮', input_origin: 'weixin'});
  assert.equal(first.message_seq, 1); assert.equal(first.command_id, 'run-a');
  const repeated = await product.send('one', {commandId: 'run-a', text: '首轮'});
  assert.equal(repeated.replay, true); assert.equal(repeated.message_seq, first.message_seq); assert.equal(events.length, 1);
  const row = projectMessages(events, [command('run-a', messageId)])[0];
  assert.equal(row.input_origin, 'desktop', 'request body cannot impersonate a remote channel');
  assert.equal(readSessionMessageSequence(row), first.message_seq);
  finish(); for (let index = 0; index < 100 && !broadcasts.some(item => item.type === 'done'); index++) await delay(1);
  assert.equal(broadcasts.find(item => item.type === 'done').command_id, 'run-a');
});

test('source projection preserves native channel receipts only on human messages and never guesses old sources', () => {
  const users = ['desktop', 'weixin', 'mobile', undefined, 'unknown'].map((origin, index) => ({
    ...user('u' + index, 'text'), source: {kind: 'user', ...(origin ? {mirally_input_origin: origin} : {})},
  }));
  const events = users.map((message, index) => event(index, 'user/message', message));
  events.push(event(5, 'assistant/message', {turn: 1, message: {id: 'reply', source: {mirally_input_origin: 'weixin'}, content: [{type:'text',text:'answer'}]}}));
  const rows = projectMessages(JSON.parse(JSON.stringify(events)));
  assert.deepEqual(rows.map(row => row.input_origin), ['desktop', 'weixin', 'mobile', undefined, undefined, undefined]);
});

test('old WeChat origin requires matching channel receipt, native session, identity and content', () => {
  const conversationId = 'one', commandId = 'weixin-abc', nativeSessionId = 'native';
  const id = 'mirally-' + createHash('sha256').update(`${conversationId}:${commandId}`).digest('hex');
  const message = user(id, '旧微信消息'), events = [event(0,'user/message',message)];
  const commands = [command(commandId,id,nativeSessionId)];
  const receipt = {command_id:commandId,runtime_request_id:id,session_id:nativeSessionId,
    content_hash:createHash('sha256').update('旧微信消息').digest('hex')};
  const project = receipts => projectMessages(events,commands,0,{conversationId,nativeSessionId,weixinReceipts:receipts})[0].input_origin;
  assert.equal(project([receipt]),'weixin');
  assert.equal(project([]),undefined);
  for (const key of ['command_id','runtime_request_id','session_id','content_hash']) assert.equal(project([{...receipt,[key]:'wrong'}]),undefined);
  message.source.mirally_input_origin = 'desktop';
  assert.equal(project([receipt]),'desktop');
});

test('trusted WeChat and paired phone entry points assign their own origin', async () => {
  const product = Object.create(MirallyProduct.prototype), recorded = [];
  const conversation = {conversation_id: 'one', session_id: 'native-one', generation: 1};
  const handle = {agent: {status: 'idle', session: {snapshotEvents: () => recorded},
    followup(message) {recorded.push(event(recorded.length, 'agent/inbox/spliced', {inserted: [message]}));}, whenIdle: () => new Promise(() => {})}};
  Object.assign(product, {weixin: {authorize: async () => {}}, mobile: {authorize: () => true}, commandPolicies: new Map(),
    activeCommands: new Map(), requestPolicies: new Map(), nativeErrors: new Map(), contexts: new Map(),
    serial: (_id, operation) => operation(), materials: {content: async () => []}, models: {requireOptions() {}},
    refreshModels: async () => {}, guard: async () => {}, conversation: async () => conversation,
    ensureNative: async () => ({conversation, handle}), ctx: {sessions: {flush: async () => {}}}, broadcast() {},
    rpc: async method => method === 'command.admit' ? {should_dispatch: true} : method === 'command.list' ? {commands: []} : {}});
  await product.sendWeixin({conversation_id:'one', command_id:'weixin-test', text:'wechat', installation_id:'fixture', revision:1});
  await product.sendMobile({conversation_id:'one', command_id:'phone', text:'mobile', input_origin:'weixin'}, {id:'device'});
  assert.deepEqual(projectMessages(recorded).map(row => row.input_origin), ['weixin', 'mobile']);
});

test('automatic title follows a durable receipt and reconciliation uses its recorded text', async () => {
  const product = Object.create(MirallyProduct.prototype), events = [], calls = [], broadcasts = [];
  const conversation = {conversation_id:'one', session_id:'native-one', generation:1};
  const handle = {agent:{status:'idle', session:{snapshotEvents:()=>events},
    followup(message) {events.push(event(0,'agent/inbox/spliced',{inserted:[message]}));}, whenIdle:()=>new Promise(()=>{})}};
  let admitted = false;
  Object.assign(product, {commandPolicies:new Map(), activeCommands:new Map(), requestPolicies:new Map(), nativeErrors:new Map(), contexts:new Map(),
    serial:(_id, operation)=>operation(), materials:{content:async()=>[]}, models:{requireOptions(){}}, refreshModels:async()=>{}, guard:async()=>{},
    conversation:async()=>conversation, ensureNative:async()=>({conversation,handle}), ctx:{sessions:{flush:async()=>calls.push('flush')}},
    broadcast:(type,payload)=>broadcasts.push({type,...payload}), rpc:async(method,params)=>{
      calls.push({method,params});
      if(method==='command.list') return {commands:[]};
      if(method==='command.admit') {const replay=admitted;admitted=true;return {should_dispatch:!replay,needs_reconciliation:replay};}
      if(method==='internal.conversation.autotitle') return {changed:true,conversation:{metadata_version:2}};
      return {};
    }});
  await product.send('one',{commandId:'first',text:'真实首条'});
  assert.ok(calls.findIndex(item=>item.method==='command.ack' && item.params.state==='applied') < calls.findIndex(item=>item.method==='internal.conversation.autotitle'));
  assert.equal(calls.find(item=>item.method==='internal.conversation.autotitle').params.text,'真实首条');
  assert.equal(broadcasts.find(item=>item.type==='session-metadata').metadata_version,2);
  await product.send('one',{commandId:'first',text:'重试伪造内容'});
  assert.equal(calls.filter(item=>item.method==='internal.conversation.autotitle').at(-1).params.text,'真实首条');
});

test('stream attempts retain consumed command ownership while another human input is queued', () => {
  const product = Object.create(MirallyProduct.prototype), observers = new Map(), broadcasts = [];
  const a = user('user-a', 'A'), b = user('user-b', 'B'), events = [event(0, 'user/message', a)];
  const agent = {id: 'native-one', session: {snapshotEvents: () => events}};
  Object.assign(product, {nativeMessageCommands: new Map([[a.id, 'run-a'], [b.id, 'run-b']]), nativeErrors: new Map(),
    nativeConversations: new Map([[agent.id, {conversation_id: 'one'}]]),
    ctx: {on: (name, callback) => observers.set(name, callback)}, broadcast: (type, payload) => broadcasts.push({type, ...payload})});
  product.installNativeObservers();
  const stream = observers.get('agent/assistant-stream');
  stream({agent, frame: {type: 'start', attemptId: 'attempt-a'}});
  events.push(event(1, 'agent/inbox/spliced', {inserted: [b]}));
  stream({agent, frame: {type: 'chunk', attemptId: 'attempt-a', chunk: {type: 'reasoning-delta', text: 'A推理'}}});
  stream({agent, frame: {type: 'chunk', attemptId: 'attempt-a', chunk: {type: 'text-delta', text: 'A回复'}}});
  // Even if consumption advances before a delayed old stream boundary, that
  // boundary retains the command captured by its native attempt start.
  events.push(event(2, 'user/message', b));
  stream({agent, frame: {type: 'end', attemptId: 'attempt-a', outcome: {kind: 'committed', seq: 3}}});
  stream({agent, frame: {type: 'start', attemptId: 'attempt-b'}});
  stream({agent, frame: {type: 'chunk', attemptId: 'attempt-b', chunk: {type: 'text-delta', text: 'B回复'}}});
  assert.deepEqual(broadcasts.map(item => item.command_id), ['run-a', 'run-a', 'run-a', 'run-a', 'run-b', 'run-b']);
  assert.equal(broadcasts[1].type, 'reasoning'); assert.equal(broadcasts[1].text, 'A推理');
  assert.equal(broadcasts[3].native_seq, 3); assert.equal(broadcasts[3].attempt_id, 'attempt-a');
});

test('in-flight history comes only from a live owned command, never unknown or persisted old work', async () => {
  const product = Object.create(MirallyProduct.prototype);
  const generation = {generation: 1, session_id: 'native-one', frozen: false};
  const record = {...command('live-run', 'live-input'), state: 'applied', created_at: 1700000000000};
  const agent = {status: 'running', session: {snapshotEvents: () => []}};
  Object.assign(product, {handles: new Map([['native-one', {agent}]]), activeCommands: new Map([['native-one', 'live-run']]),
    async rpc(method) {
      if (method === 'conversation.history_refs') return {generations: [generation]};
      if (method === 'command.list') return {commands: [record]};
      throw new Error('Unexpected RPC ' + method);
    }});
  const live = await product.history('one');
  assert.deepEqual(live.active_command_ids, ['live-run']);
  assert.equal(live.in_flight.command_id, 'live-run'); assert.equal(live.in_flight.started_at, record.created_at);
  record.state = 'unknown';
  assert.equal((await product.history('one')).in_flight, undefined);
  record.state = 'applied'; generation.frozen = true;
  assert.deepEqual((await product.history('one')).active_command_ids, []);
  generation.frozen = false; agent.status = 'idle';
  assert.equal((await product.history('one')).in_flight, undefined);
  agent.status = 'running'; product.handles.clear();
  assert.equal((await product.history('one')).in_flight, undefined);
  const summary = product.summary({conversation_id: 'one', session_id: 'native-one', generation: 1, frozen: false});
  assert.equal(summary.status, 'idle'); assert.deepEqual(summary.active_command_ids, []);
});
