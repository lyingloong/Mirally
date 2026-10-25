import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {ProductModels} from '../kernel/dsh/packages/mirally/mirally-product/src/models.mjs';
import {BlockAssembler, createUserMessage, createToolResultMessage} from '../kernel/dsh/packages/llm/llm/lib/index.js';

// Only the provider endpoint is a fixture. ProductModels.sync validates real
// profiles, and the owned PiAiAdapter dispatches/parses each actual HTTP stream.
async function provider(t, frames) {
  const requests = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const row = {path: request.url, auth: request.headers.authorization,
      body: JSON.parse(Buffer.concat(chunks).toString('utf8'))};
    requests.push(row);
    response.writeHead(200, {'content-type': 'text/event-stream'});
    for (const frame of frames(row, requests.length)) {
      response.write(`data: ${typeof frame === 'string' ? frame : JSON.stringify(frame)}\n\n`);
    }
    response.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));});
  return {url: `http://127.0.0.1:${server.address().port}/gateway/v1`, requests};
}

function chatFrames({tool = false} = {}) {
  return [
    {id: 'chat_fixture', choices: [{index: 0, delta: {role: 'assistant', reasoning_content: 'Check the requested value.'}, finish_reason: null}]},
    {id: 'chat_fixture', choices: [{index: 0, delta: tool
      ? {tool_calls: [{index: 0, id: 'call_fixture', type: 'function', function: {name: 'lookup', arguments: '{"key":"sample"}'}}]}
      : {content: 'Mirally local route OK'}, finish_reason: null}]},
    {id: 'chat_fixture', choices: [{index: 0, delta: {}, finish_reason: tool ? 'tool_calls' : 'stop'}],
      usage: {prompt_tokens: 8, completion_tokens: 5, total_tokens: 13}},
    '[DONE]',
  ];
}

function responsesFrames() {
  const item = {id: 'msg_fixture', type: 'message', role: 'assistant', status: 'completed',
    content: [{type: 'output_text', text: 'Mirally local route OK', annotations: []}]};
  return [
    {type: 'response.created', response: {id: 'resp_fixture', status: 'in_progress', output: []}},
    {type: 'response.output_item.added', output_index: 0, item: {...item, status: 'in_progress', content: []}},
    {type: 'response.output_item.done', output_index: 0, item},
    {type: 'response.completed', response: {id: 'resp_fixture', status: 'completed', output: [item],
      usage: {input_tokens: 8, output_tokens: 5, total_tokens: 13}}},
  ];
}

function productModels(t) {
  let registrations = 0, disposals = 0;
  const owner = new ProductModels({get: () => undefined, llm: {registerAdapter(routes, adapter) {
    assert.deepEqual(routes, ['mirally']);
    assert.ok(adapter);
    registrations++;
    return () => {disposals++;};
  }}}, {testMode: false});
  t.after(() => {
    owner.dispose();
    assert.equal(registrations, 1, 'Profile changes must keep one owned adapter registration');
    assert.equal(disposals, 1);
    assert.equal(owner.keys.size, 0);
  });
  return owner;
}

const user = () => createUserMessage({source: {kind: 'user'}, content: [{type: 'text', text: 'Reply with the connection result.'}]});
async function generate(owner, extra = {}) {
  const options = owner.requireOptions(), assembler = new BlockAssembler();
  for await (const chunk of owner.adapter.stream({...options, messages: [user()], signal: AbortSignal.timeout(5000), ...extra})) {
    assembler.push(chunk);
  }
  const message = assembler.message({provider: options.provider, model: options.model,
    ...(assembler.replayState === undefined ? {} : {replayState: assembler.replayState})});
  return {message, finish: assembler.finish};
}

const settings = (url, format, extra) => ({model: {provider: 'deepseek', base_url: url,
  api_format: format, model_id: 'deepseek-fixture', api_key: 'isolated-provider-key',
  max_tokens: 1024, context_window: 128000, temperature: 0.6, ...extra}});

const efforts = [
  {label: 'off overrides legacy true', input: {reasoning_effort: 'off', reasoning_enabled: true}, wire: 'off'},
  {label: 'low overrides legacy false', input: {reasoning_effort: 'low', reasoning_enabled: false}, wire: 'low'},
  {label: 'high', input: {reasoning_effort: 'high'}, wire: 'high'},
  {label: 'max', input: {reasoning_effort: 'max'}, wire: 'max'},
  {label: 'medium maps to supported DeepSeek high', input: {reasoning_effort: 'medium'}, wire: 'high'},
  {label: 'service default overrides legacy false', input: {reasoning_effort: 'default', reasoning_enabled: false}, wire: 'default'},
  {label: 'service default overrides legacy true', input: {reasoning_effort: 'default', reasoning_enabled: true}, wire: 'default'},
  {label: 'legacy false remains explicit off', input: {reasoning_enabled: false}, wire: 'off'},
  {label: 'legacy true maps to high', input: {reasoning_enabled: true}, wire: 'high'},
];

test('ProductModels DeepSeek Chat settings reach the actual PiAI HTTP request', async t => {
  const endpoint = await provider(t, () => chatFrames());
  const owner = productModels(t);
  for (const effort of efforts) await t.test(effort.label, async () => {
    owner.sync(settings(endpoint.url, 'openai-chat', effort.input));
    const result = await generate(owner);
    assert.deepEqual(result.finish, {kind: 'stop'});
    const request = endpoint.requests.at(-1);
    assert.equal(request.path, '/gateway/v1/chat/completions');
    assert.equal(request.auth, 'Bearer isolated-provider-key');
    assert.equal(request.body.model, 'deepseek-fixture');
    if (effort.wire === 'off') {
      assert.deepEqual(request.body.thinking, {type: 'disabled'});
      assert.equal(Object.hasOwn(request.body, 'reasoning_effort'), false);
    } else if (effort.wire === 'default') {
      assert.equal(Object.hasOwn(request.body, 'thinking'), false, 'Service default must not silently disable thinking');
      assert.equal(Object.hasOwn(request.body, 'reasoning_effort'), false);
    } else {
      assert.deepEqual(request.body.thinking, {type: 'enabled'});
      assert.equal(request.body.reasoning_effort, effort.wire);
    }
    assert.equal(result.message.content.find(block => block.type === 'text')?.text, 'Mirally local route OK');
    assert.equal(result.message.content.find(block => block.type === 'reasoning')?.text, 'Check the requested value.');
  });
  assert.equal(endpoint.requests.length, efforts.length);
});

test('ProductModels DeepSeek Responses settings reach the actual PiAI HTTP request', async t => {
  const endpoint = await provider(t, responsesFrames);
  const owner = productModels(t);
  for (const effort of efforts) await t.test(effort.label, async () => {
    owner.sync(settings(endpoint.url, 'openai-responses', effort.input));
    const result = await generate(owner);
    assert.deepEqual(result.finish, {kind: 'stop'});
    const request = endpoint.requests.at(-1);
    assert.equal(request.path, '/gateway/v1/responses');
    assert.equal(request.auth, 'Bearer isolated-provider-key');
    assert.equal(request.body.model, 'deepseek-fixture');
    if (effort.wire === 'default') {
      assert.equal(Object.hasOwn(request.body, 'reasoning'), false, 'Service default must not silently send effort:none');
    } else {
      assert.equal(request.body.reasoning?.effort, effort.wire === 'off' ? 'none' : effort.wire);
    }
    assert.equal(Object.hasOwn(request.body, 'thinking'), false);
    assert.equal(result.message.content.find(block => block.type === 'text')?.text, 'Mirally local route OK');
  });
  assert.equal(endpoint.requests.length, efforts.length);
});

test('DeepSeek Chat replays saved reasoning_content through a native tool continuation', async t => {
  const endpoint = await provider(t, (_row, count) => chatFrames({tool: count === 1}));
  const owner = productModels(t);
  owner.sync(settings(endpoint.url, 'openai-chat', {reasoning_effort: 'high'}));
  const tools = [{name: 'lookup', description: 'Read the requested value.',
    parameters: {type: 'object', properties: {key: {type: 'string'}}, required: ['key']}}];
  const initial = user(), first = await generate(owner, {messages: [initial], tools});
  assert.deepEqual(first.finish, {kind: 'tool-calls'});
  const call = first.message.content.find(block => block.type === 'tool-call');
  assert.equal(call.name, 'lookup');
  const history = JSON.parse(JSON.stringify([initial, first.message,
    createToolResultMessage({callId: call.id, content: [{type: 'text', text: 'Found the sample value.'}], isError: false})]));
  const second = await generate(owner, {messages: history, tools});
  assert.deepEqual(second.finish, {kind: 'stop'});
  const assistant = endpoint.requests[1].body.messages.find(message => message.role === 'assistant');
  assert.equal(assistant.reasoning_content, 'Check the requested value.');
  assert.equal(assistant.tool_calls[0].function.name, 'lookup');
  assert.equal(endpoint.requests[1].body.messages.find(message => message.role === 'tool').tool_call_id, assistant.tool_calls[0].id);
  assert.deepEqual(endpoint.requests[1].body.thinking, {type: 'enabled'});
  assert.equal(endpoint.requests[1].body.reasoning_effort, 'high');
});
