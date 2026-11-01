/** Real owned Host/PiAI/HTTP stream; only the external provider is a local fixture. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {readFileSync, existsSync} from 'node:fs';
import {mkdtemp, readFile, realpath, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, basename, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {stripTypeScriptTypes} from 'node:module';
import {setTimeout as delay} from 'node:timers/promises';

const root = fileURLToPath(new URL('../', import.meta.url));
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source, {mode: 'transform'})).toString('base64');
const reads = moduleUrl(readFileSync(join(root, 'frontend/control-ui/ui/src/app/mirally-read-models.ts'), 'utf8'));
const reminders = moduleUrl(readFileSync(join(root, 'frontend/control-ui/ui/src/app/desktop-reminders.ts'), 'utf8'));
const {MirallyBrowserClient} = await import(moduleUrl(readFileSync(join(root, 'frontend/control-ui/ui/src/app/mirally-client.ts'), 'utf8')
  .replace(/(["'])\.\/mirally-read-models\.(?:ts|js)\1/g, () => JSON.stringify(reads))
  .replace(/(["'])\.\/desktop-reminders\.(?:ts|js)\1/g, () => JSON.stringify(reminders))));

async function until(check, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {const value = await check(); if (value) return value; await delay(15);}
  throw new Error('Timed out: ' + label);
}

test('native reasoning reaches live original-client thinking and persisted history without joining answer text', {timeout: 60000}, async t => {
  const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!existsSync(python) || !existsSync(join(root, 'kernel/dsh/packages/core/agent/lib/index.js'))) {
    t.skip('Prepare the owned kernel and repository Python environment'); return;
  }
  const requests = [], server = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    requests.push({path: req.url, body: JSON.parse(Buffer.concat(chunks).toString('utf8'))});
    res.writeHead(200, {'content-type': 'text/event-stream'});
    const frames = [
      {id: 'thinking-fixture', choices: [{index: 0, delta: {role: 'assistant', reasoning_content: '核对原始条件。'}, finish_reason: null}]},
      {id: 'thinking-fixture', choices: [{index: 0, delta: {content: '验证结果是 42。'}, finish_reason: null}]},
      {id: 'thinking-fixture', choices: [{index: 0, delta: {}, finish_reason: 'stop'}], usage: {prompt_tokens: 8, completion_tokens: 8, total_tokens: 16}},
    ];
    for (const frame of frames) {res.write(`data: ${JSON.stringify(frame)}\n\n`); await delay(20);}
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));});
  const home = await mkdtemp(join(tmpdir(), 'mirally reasoning fixture '));
  let child, logs = '', client;
  t.after(async () => {
    client?.stop();
    if (child?.exitCode === null) {
      child.stdin.end();
      try {await Promise.race([once(child, 'exit'), delay(7000).then(() => {throw new Error('cleanup timeout');})]);}
      catch {child.kill(); await Promise.race([once(child, 'exit'), delay(3000)]);}
    }
    const canonical = await realpath(home), temporary = await realpath(tmpdir());
    assert.equal(dirname(canonical), temporary); assert.ok(basename(canonical).startsWith('mirally reasoning fixture '));
    await rm(canonical, {recursive: true, force: true});
  });
  child = spawn(process.execPath, [join(root, 'kernel/dsh/packages/mirally/mirally-product/start.mjs'), '--root', root, '--home', home, '--python', python],
    {cwd: root, windowsHide: true, env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}, stdio: ['pipe', 'pipe', 'pipe']});
  child.stdout.on('data', bytes => {logs += bytes;}); child.stderr.on('data', bytes => {logs += bytes;});
  const runtime = await until(async () => {try{return JSON.parse(await readFile(join(home, 'runtime.json'), 'utf8'));}catch{return false;}}, 'native Host startup: ' + logs);
  const nativeFetch = globalThis.fetch, oldGlobals = new Map(['window', 'document', 'location', 'fetch'].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  t.after(() => {for (const [name, value] of oldGlobals) {if (value) Object.defineProperty(globalThis, name, value); else delete globalThis[name];}});
  Object.assign(globalThis, {window: {dispatchEvent() {}}, document: {getElementById: () => ({})}, location: {origin: runtime.url, hash: ''},
    fetch: (input, options) => nativeFetch(new URL(input, runtime.url), options)});
  const request = async (path, body) => {
    const reply = await nativeFetch(runtime.url + path, {method: 'POST', headers: {'content-type': 'application/json', authorization: 'Bearer ' + runtime.token}, body: JSON.stringify(body)});
    const value = await reply.json(); assert.ok(reply.ok, JSON.stringify(value)); return value;
  };
  await request('/api/rpc', {method: 'settings.update', params: {patch: {model: {provider: 'deepseek', api_format: 'openai-chat',
    base_url: `http://127.0.0.1:${server.address().port}/v1`, api_key: 'isolated-fixture-key', model_id: 'deepseek-fixture',
    reasoning_effort: 'high', max_tokens: 1024, context_window: 128000}}}});
  const session = await request('/api/sessions', {title: '推理呈现验证'}), key = 'agent:main:' + session.id;
  const received = [], liveThinking = [];
  client = new MirallyBrowserClient({token: runtime.token, onEvent(event) {
    received.push(event);
    liveThinking.push(...client.getReasoningStream(key, 'reason-fixture-run').map(message => message.content[0].thinking));
  }});
  client.start(); await until(() => client.connected, 'original-client connection');
  await client.request('chat.send', {sessionKey: key, message: '请验证这个固定样例。', idempotencyKey: 'reason-fixture-run'});
  await until(() => received.some(event => event.event === 'chat' && event.payload.state === 'final'), 'reasoning model completion: ' + logs);
  assert.ok(liveThinking.includes('核对原始条件。'), 'Native reasoning must be visible before terminal history replaces it');
  const final = received.find(event => event.event === 'chat' && event.payload.state === 'final').payload.message;
  assert.deepEqual(final.content, [{type: 'thinking', thinking: '核对原始条件。'}, {type: 'text', text: '验证结果是 42。'}]);
  assert.equal(client.getReasoningStream(key, 'reason-fixture-run').length, 0);
  const fresh = new MirallyBrowserClient({token: runtime.token});
  const restored = await fresh.request('chat.history', {sessionKey: key});
  fresh.stop();
  assert.deepEqual(restored.messages.find(message => message.role === 'assistant').content, final.content);
  assert.ok(received.filter(event => event.payload.state === 'delta').every(event => !event.payload.message?.content?.some(block => block.type === 'text' && block.text.includes('原始条件'))));
  assert.equal(requests.length, 1); assert.equal(requests[0].path, '/v1/chat/completions');
  assert.deepEqual(requests[0].body.thinking, {type: 'enabled'});
  await request('/api/shutdown', {});
  await until(() => child.exitCode !== null, 'native Host shutdown');
});
