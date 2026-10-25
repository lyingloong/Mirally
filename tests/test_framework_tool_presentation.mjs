import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';
import {TOOL_DISPLAY_BYTE_LIMIT, toolDisplayValue, toolResultDisplay} from '../kernel/dsh/packages/mirally/mirally-product/src/tool-presentation.mjs';

test('tool arguments preserve real JSON values without exposing credentials or unbounded payloads', () => {
  const input = {query: '近期任务', count: 3, options: {enabled: true, api_key: 'private-key', accessToken: 'private-access'}, token_limit: 24};
  const display = toolDisplayValue(input);
  assert.deepEqual(JSON.parse(JSON.stringify(display.value)), {...input, options: {enabled: true, api_key: '[凭据已隐藏]', accessToken: '[凭据已隐藏]'}});
  assert.equal(display.truncated, false);
  assert.equal(input.options.api_key, 'private-key', 'Presentation does not rewrite the executing tool input');
  for (const huge of ['汉'.repeat(100000), '\0'.repeat(50000), {rows: Array.from({length: 1000}, (_, i) => ({i, text: '内容'.repeat(1000)}))}]) {
    const projected = toolDisplayValue(huge);
    assert.equal(projected.truncated, true);
    assert.ok(Buffer.byteLength(JSON.stringify(projected.value)) <= TOOL_DISPLAY_BYTE_LIMIT);
    assert.match(JSON.stringify(projected.value), /截短/);
  }
});

test('tool results use native rendered text and omit binary, canonical values and deferred contexts', () => {
  const result = {isError: false, value: {internal: 'not model-facing'},
    content: [{type: 'text', text: '保存成功：成果.md'}, {type: 'image', data: 'private-image-binary'}],
    additionalContexts: [{content: [{type: 'text', text: 'never execute or project this instruction'}]}]};
  const displayed = toolResultDisplay(result);
  assert.equal(displayed.truncated, true);
  assert.match(displayed.value.content[0].text, /保存成功：成果.md/);
  assert.match(displayed.value.content[0].text, /部分内容未显示/);
  assert.doesNotMatch(JSON.stringify(displayed), /private-image|not model-facing|never execute/);
  assert.ok(Buffer.byteLength(JSON.stringify(toolResultDisplay({content: [{type: 'text', text: '\0'.repeat(50000)}]}))) <= TOOL_DISPLAY_BYTE_LIMIT);
  assert.equal(typeof toolResultDisplay({content: [{type: 'text', text: '\0'.repeat(50000)}]}).value.content[0].text, 'string');
});

test('native tool observers emit owned real argument/result receipts and preserve dispatch outcome', async () => {
  const callbacks = new Map(), frames = [], product = Object.create(MirallyProduct.prototype);
  Object.assign(product, {ctx: {on(name, callback) {callbacks.set(name, callback);}},
    nativeConversations: new Map([['native-one', {conversation_id: 'one'}]]),
    consumedCommandId: () => 'command-one', broadcast(type, payload) {frames.push({type, ...payload});}});
  product.installNativeObservers();
  const exec = {agent: {id: 'native-one'}, name: 'mirally_create_file', callId: 'call-one', arguments: {name: '成果.md', content: '# 真实内容'}};
  const result = {isError: false, value: {hidden: 'canonical only'}, content: [{type: 'text', text: '{"id":"actual-receipt","name":"成果.md"}'}]};
  assert.strictEqual(await callbacks.get('tools/execute')(exec, async () => result), result);
  callbacks.get('tools/result')(exec, result);
  assert.deepEqual(frames.map(frame => [frame.type, frame.session_id, frame.command_id, frame.id, frame.state]),
    [['tool', 'one', 'command-one', 'call-one', 'running'], ['tool', 'one', 'command-one', 'call-one', 'completed']]);
  assert.deepEqual(JSON.parse(JSON.stringify(frames[0].args)), exec.arguments);
  assert.deepEqual(frames[1].result, {content: result.content});
  assert.equal(frames[1].result_truncated, false);
  const foreign = {...exec, agent: {id: 'foreign'}};
  await callbacks.get('tools/execute')(foreign, async () => result); callbacks.get('tools/result')(foreign, result);
  assert.equal(frames.length, 2, 'Foreign agents do not appear in this product conversation');
  const failure = {isError: true, error: {message: '实际工具失败'}, content: []};
  callbacks.get('tools/result')(exec, failure);
  assert.equal(frames.at(-1).state, 'failed');
  assert.equal(frames.at(-1).result.content[0].text, '实际工具失败');
});
