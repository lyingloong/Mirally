import {test} from 'node:test';
import assert from 'node:assert/strict';
import {projectMessages, nativeUserAttachments, toolPresentationId} from '../kernel/dsh/packages/mirally/mirally-product/src/history-presentation.mjs';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

const hash = 'a'.repeat(64), otherHash = 'b'.repeat(64);
const image = {type: 'image', attachment: {attachmentId: 'sha256:' + hash, mediaType: 'image/png', bytes: 3, width: 1, height: 1, name: '画面.png'}};
const file = {type: 'file', attachment: {attachmentId: 'sha256:' + otherHash, bytes: 4, name: '资料.txt'}};
const user = (id, text, attachments = []) => ({id, source: {kind: 'user'}, content: [{type: 'text', text}, ...attachments]});
const event = (seq, type, data, extra = {}) => ({seq, type, time: 1000 + seq, data, ...extra});
const command = (id, input) => ({command_id: id, runtime_request_id: input});
const scope = {conversationId: 'owned conversation', nativeSessionId: 'native-one'};

test('durable queued user materials retain one receipt and real owned image/file links', () => {
  const input = user('input', '查看这些材料', [image, file]), log = [event(0, 'agent/inbox/spliced', {inserted: [input]}), event(1, 'user/message', input)];
  const before = JSON.stringify(log), rows = projectMessages(log, [command('run', 'input')], 0, scope);
  assert.equal(rows.length, 1); assert.equal(rows[0].seq, 1); assert.equal(rows[0].text, '查看这些材料');
  assert.equal(rows[0].content[1].type, 'image'); assert.equal(rows[0].content[1].fileName, '画面.png');
  assert.equal(rows[0].content[1].url, `/api/sessions/owned%20conversation/attachments/image/${hash}`);
  assert.equal(rows[0].content[2].type, 'attachment'); assert.equal(rows[0].content[2].attachment.label, '资料.txt');
  assert.equal(rows[0].__openclaw.idempotencyKey, 'run:user'); assert.equal(JSON.stringify(log), before);
  const internal = {...input, id: 'internal', source: {kind: 'runtime-context'}};
  assert.deepEqual(projectMessages([event(0, 'user/message', internal)], [], 0, scope), []);
  assert.deepEqual(nativeUserAttachments([event(0, 'user/message', internal)]), []);
});

test('historical tools use native call sequence identities, settle from actual results and preserve consumed owners', () => {
  const log = [event(0, 'user/message', user('one', '第一轮')),
    event(1, 'tool/call', {turn: 1, step: 1, callId: 'reused', name: 'mirally_reminders', arguments: '{}'}),
    event(2, 'agent/inbox/spliced', {inserted: [user('two', '排队补充')]}),
    event(3, 'user/message', user('two', '排队补充')),
    event(4, 'tool/result', {turn: 1, step: 1, message: {toolCallId: 'reused', isError: false, content: [{type: 'text', text: '{"commitments":[]}'}]}}, {sourceEventSeqs: [1]}),
    event(5, 'tool/call', {turn: 2, step: 1, callId: 'reused', name: 'mirally_context', arguments: '{"query":"第二轮"}'}),
    event(6, 'tool/result', {turn: 2, step: 1, message: {toolCallId: 'reused', isError: true, content: [{type: 'text', text: '真实失败'}]}}, {sourceEventSeqs: [5]}),
    event(7, 'tool/call', {turn: 2, step: 2, callId: 'cancelled', name: 'read', arguments: '{"path":"material.txt"}'})];
  const rows = projectMessages(log, [command('run-one', 'one'), command('run-two', 'two')], 0, scope), tools = rows.filter(row => row.role === 'toolResult');
  assert.equal(tools.length, 3); assert.deepEqual(tools.map(row => row.command_id), ['run-one', 'run-two', 'run-two']);
  assert.deepEqual(tools.map(row => row.id), [1, 5, 7].map(seq => toolPresentationId('native-one', seq)));
  assert.deepEqual(JSON.parse(JSON.stringify(tools[1].content[0].arguments)), {query: '第二轮'}); assert.equal(tools[1].content[1].isError, true);
  assert.equal(tools[2].content.length, 1, 'An interrupted call without a result is not a completed operation');
  assert.equal(tools.some(row => row.__openclawToolStreamLive), false, 'Historical calls never spin as a live operation');
  const moved = projectMessages(log, [command('run-one', 'one'), command('run-two', 'two')], 100, {...scope, nativeSessionId: 'native-two'});
  assert.equal(moved[1].seq, 102); assert.notEqual(moved[1].id, tools[0].id, 'Generations may reuse raw provider call IDs and native sequence values');
});

test('a result loaded before its call coalesces by native causal receipt and result-only recovery remains explicit', () => {
  const call = event(1, 'tool/call', {turn: 1, step: 1, callId: 'same', name: 'read', arguments: '{malformed'});
  const result = event(2, 'tool/result', {turn: 1, step: 1, message: {toolCallId: 'same', isError: false, content: []}}, {sourceEventSeqs: [1]});
  const rows = projectMessages([result, call], [], 0, scope);
  assert.equal(rows.length, 1); assert.equal(rows[0].content.length, 2); assert.equal(rows[0].content[0].arguments, '{malformed');
  const orphan = projectMessages([result], [], 0, scope)[0];
  assert.equal(orphan.content.length, 1); assert.equal(orphan.content[0].type, 'toolresult');
  const late = projectMessages([event(0, 'user/message', user('one', '旧任务')),
    event(1, 'assistant/message', {turn: 1, message: {content: [{type: 'text', text: '旧回复'}]}}),
    event(2, 'user/message', user('two', '新任务')), {...result, seq: 3}], [command('old', 'one'), command('new', 'two')], 0, scope);
  assert.equal(late.at(-1).command_id, 'old', 'An orphan late result never borrows the newest consumed command');
  const unproven = projectMessages([event(0, 'user/message', user('two', '新任务')), result], [command('new', 'two')], 0, scope);
  assert.equal(unproven.at(-1).command_id, undefined, 'An unproven orphan result keeps no fabricated command owner');
});

test('history attachment reads require a native user receipt in the exact conversation and verified store bytes', async () => {
  let reads = 0;
  const product = Object.create(MirallyProduct.prototype);
  Object.assign(product, {async history(id) {return {events: id === 'owned' ? [event(0, 'user/message', user('one', '材料', [image, file]))] : []};},
    ctx: {get() {return {async readImage(ref) {reads++; assert.equal(ref, image.attachment); return {data: Buffer.from('png')};},
      async *readFileStream(ref) {reads++; assert.equal(ref, file.attachment); yield Buffer.from('text');}};}}});
  assert.equal((await product.readConversationAttachment('owned', 'image', hash)).data.toString(), 'png');
  assert.equal((await product.readConversationAttachment('owned', 'file', otherHash)).data.toString(), 'text');
  await assert.rejects(product.readConversationAttachment('foreign', 'image', hash), error => error.status === 404);
  await assert.rejects(product.readConversationAttachment('owned', 'image', otherHash), error => error.status === 404);
  await assert.rejects(product.readConversationAttachment('owned', 'file', '../outside'), /Invalid attachment/);
  assert.equal(reads, 2, 'Unowned or path-like references must never reach filesystem-backed storage');
});

test('live tool IDs and owners are pinned to the native call receipt across queued user input', async () => {
  const callbacks = new Map(), frames = [], native = [event(0, 'tool/call', {callId: 'raw', name: 'read'})];
  let current = 'old-run';
  const product = Object.create(MirallyProduct.prototype);
  Object.assign(product, {ctx: {on: (name, callback) => callbacks.set(name, callback)}, nativeConversations: new Map([['native-one', {conversation_id: 'owned'}]]),
    consumedCommandId: () => current, broadcast: (type, frame) => frames.push(frame)});
  product.installNativeObservers();
  const exec = {agent: {id: 'native-one', session: {snapshotEvents: () => native}}, callId: 'raw', name: 'read', arguments: {}, token: Symbol('execution')};
  await callbacks.get('tools/execute')(exec, async () => ({})); current = 'new-run';
  callbacks.get('tools/result')(exec, {isError: false, content: []});
  assert.equal(frames[0].id, toolPresentationId('native-one', 0)); assert.equal(frames[1].id, frames[0].id);
  assert.deepEqual(frames.map(frame => frame.command_id), ['old-run', 'old-run']);
});
