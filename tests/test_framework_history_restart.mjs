/** Native materials/tools survive fresh clients and a cold Host, across generations. */
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

const root = fileURLToPath(new URL('../', import.meta.url)), png = readFileSync(join(root, 'mirally/assets/mirally-128.png')).toString('base64');
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source, {mode: 'transform', sourceUrl: 'mirally-history-test.ts'})).toString('base64');
const reads = moduleUrl(readFileSync(join(root, 'frontend/control-ui/ui/src/app/mirally-read-models.ts'), 'utf8'));
const {MirallyBrowserClient} = await import(moduleUrl(readFileSync(join(root, 'frontend/control-ui/ui/src/app/mirally-client.ts'), 'utf8')
  .replace(/(["'])\.\/mirally-read-models\.(?:ts|js)\1/g, () => JSON.stringify(reads))));
async function until(check, label, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {const result = await check(); if (result) return result; await delay(15);}
  throw new Error('Timed out: ' + label);
}

test('real native attachment bytes and tool receipts restore after refresh, rotation and cold restart', {timeout: 60000}, async t => {
  const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!existsSync(python) || !existsSync(join(root, 'kernel/dsh/packages/core/agent/lib/index.js'))) {t.skip('Prepare the repository Python and kernel build'); return;}
  const requests = [], server = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const input = JSON.parse(Buffer.concat(chunks).toString('utf8')); requests.push(input);
    const finish = input.messages.at(-1)?.role === 'tool';
    const delta = finish ? {content: '已读取真实提醒结果。'} : {reasoning_content: '先查看提醒来源。',
      tool_calls: [{index: 0, id: 'deliberately-reused-provider-id', type: 'function', function: {name: 'mirally_reminders', arguments: '{}'}}]};
    res.writeHead(200, {'content-type': 'text/event-stream'});
    for (const frame of [{id: 'native-history', choices: [{index: 0, delta, finish_reason: null}]},
      {id: 'native-history', choices: [{index: 0, delta: {}, finish_reason: finish ? 'stop' : 'tool_calls'}], usage: {prompt_tokens: 10, completion_tokens: 10, total_tokens: 20}}])
      res.write(`data: ${JSON.stringify(frame)}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));});
  const home = await mkdtemp(join(tmpdir(), 'mirally history restart ')); let child, runtime, logs = '';
  const nativeFetch = globalThis.fetch, clients = [], oldGlobals = new Map(['window', 'document', 'location', 'fetch'].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  t.after(async () => {
    clients.forEach(client => client.stop());
    if (child?.exitCode === null) {child.stdin.end(); try {await Promise.race([once(child, 'exit'), delay(7000).then(() => {throw new Error('cleanup timeout');})]);} catch {child.kill(); await Promise.race([once(child, 'exit'), delay(3000)]);}}
    for (const [name, value] of oldGlobals) {if (value) Object.defineProperty(globalThis, name, value); else delete globalThis[name];}
    const canonical = await realpath(home); assert.equal(dirname(canonical), await realpath(tmpdir())); assert.ok(basename(canonical).startsWith('mirally history restart '));
    await rm(canonical, {recursive: true, force: true});
  });
  const start = async () => {
    child = spawn(process.execPath, [join(root, 'kernel/dsh/packages/mirally/mirally-product/start.mjs'), '--root', root, '--home', home, '--python', python],
      {cwd: root, windowsHide: true, env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}, stdio: ['pipe', 'pipe', 'pipe']});
    child.stdout.on('data', bytes => {logs += bytes;}); child.stderr.on('data', bytes => {logs += bytes;});
    runtime = await until(async () => {try {const value = JSON.parse(await readFile(join(home, 'runtime.json'), 'utf8')); return value.pid === child.pid && value;} catch {return false;}}, 'native startup');
    Object.assign(globalThis, {window: {dispatchEvent() {}}, document: {getElementById: () => ({})}, location: {origin: runtime.url, hash: ''},
      fetch: (input, options) => nativeFetch(new URL(input, runtime.url), options)});
  };
  const api = async (path, value, expected = 200) => {
    const response = await nativeFetch(new URL(path,runtime.url), {method: value === undefined ? 'GET' : 'POST', headers: {authorization: 'Bearer ' + runtime.token, 'content-type': 'application/json'}, ...(value !== undefined ? {body: JSON.stringify(value)} : {})});
    assert.equal(response.status, expected, await response.clone().text()); return response;
  };
  const rpc = async (method, params) => (await (await api('/api/rpc', {method, params})).json()).result;
  const fresh = () => {const client = new MirallyBrowserClient({token: runtime.token}); clients.push(client); return client;};
  await start();
  await rpc('settings.update', {patch: {model: {provider: 'deepseek', api_format: 'openai-chat', base_url: `http://127.0.0.1:${server.address().port}/v1`,
    api_key: 'isolated-provider-key', model_id: 'deepseek-fixture', image_enabled: true, reasoning_effort: 'high', max_tokens: 1024}}});
  const session = await (await api('/api/sessions', {title: '历史材料验收'}, 201)).json(), key = 'agent:main:' + session.id;
  const foreign = await (await api('/api/sessions', {title: '无材料的另一会话'}, 201)).json();
  const sent = [], client = new MirallyBrowserClient({token: runtime.token, onEvent: event => sent.push(event)}); clients.push(client);
  client.start(); await until(() => client.connected, 'client connected');
  await client.request('chat.send', {sessionKey: key, message: '请用工具列出提醒，并查看我提供的材料。', idempotencyKey: 'history-run-one', attachments: [
    {fileName: '原始图像.png', mimeType: 'image/png', content: png}, {fileName: '原始资料.txt', mimeType: 'text/plain', content: Buffer.from('原始资料内容').toString('base64')}]});
  await until(() => sent.some(event => event.payload.state === 'final' && event.payload.runId === 'history-run-one'), 'first native tool completion: ' + logs);
  let history = await fresh().request('chat.history', {sessionKey: key});
  const user = history.messages.find(row => row.role === 'user'), tool = history.messages.find(row => row.role === 'toolResult');
  assert.equal(user.content.filter(block => block.type === 'image').length, 1); assert.equal(user.content.filter(block => block.type === 'attachment').length, 1);
  assert.equal(tool.content.length, 2); assert.equal(tool.content[0].name, 'mirally_reminders'); assert.match(tool.content[1].text, /commitments/);
  const liveToolIds = new Set(sent.filter(event => event.event === 'agent').map(event => event.payload.data.toolCallId));
  assert.ok(liveToolIds.has(tool.toolCallId), 'Durable and live tool cards share the same exact native invocation identity');
  const imageUrl = user.content.find(block => block.type === 'image').url, fileUrl = user.content.find(block => block.type === 'attachment').attachment.url;
  assert.equal((await (await api(fileUrl)).arrayBuffer()).byteLength, Buffer.byteLength('原始资料内容'));
  assert.ok((await (await api(imageUrl)).arrayBuffer()).byteLength > 0);
  await api(imageUrl.replace(encodeURIComponent(session.id), encodeURIComponent(foreign.id)), undefined, 404);
  const anonymous = await nativeFetch(imageUrl); assert.equal(anonymous.status, 401);
  const firstUserId = user.id, firstToolId = tool.id;
  await api(`/api/sessions/${session.id}/cancel`, {});
  await client.request('chat.send', {sessionKey: key, message: '第二代继续用工具查看提醒。', idempotencyKey: 'history-run-two'});
  await until(() => sent.some(event => event.payload.state === 'final' && event.payload.runId === 'history-run-two'), 'second native generation');
  history = await fresh().request('chat.history', {sessionKey: key});
  const tools = history.messages.filter(row => row.role === 'toolResult');
  assert.equal(tools.length, 2); assert.notEqual(tools[0].toolCallId, tools[1].toolCallId);
  const stable = row => ({id: row.id, content: row.content.map(block=>block.type==='image'
    ? {...block,url:new URL(block.url).pathname} : block.type==='attachment'
      ? {...block,attachment:{...block.attachment,url:new URL(block.attachment.url).pathname}} : block),
    owner: row.__openclaw.runId, native: row.native_session_id});
  const before = history.messages.map(stable);
  clients.forEach(value => value.stop());
  await api('/api/shutdown', {}); await until(() => child.exitCode !== null, 'first Host exits');
  await start();
  const restored = await fresh().request('chat.history', {sessionKey: key});
  assert.deepEqual(restored.messages.map(stable), before);
  assert.equal(restored.messages.filter(row => row.id === firstUserId).length, 1); assert.equal(restored.messages.filter(row => row.id === firstToolId).length, 1);
  assert.equal(restored.messages.flatMap(row => row.content).some(block => block.type === 'thinking' && block.thinking === '先查看提醒来源。'), true);
  const restoredUser=restored.messages.find(row=>row.id===firstUserId);
  const restoredImage=restoredUser.content.find(block=>block.type==='image').url;
  const restoredFile=restoredUser.content.find(block=>block.type==='attachment').attachment.url;
  assert.equal(new URL(restoredImage).origin,runtime.url,'Cold startup uses the new Host address');
  assert.equal((await (await api(restoredFile)).arrayBuffer()).byteLength, Buffer.byteLength('原始资料内容'));
  assert.ok((await (await api(restoredImage)).arrayBuffer()).byteLength > 0);
  assert.equal(requests.length, 4, 'Two turns use one native tool/reply chain each; history reads do not call the model');
  await api('/api/shutdown', {}); await until(() => child.exitCode !== null, 'restarted Host exits');
});
