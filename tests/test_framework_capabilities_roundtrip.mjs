/** The unmocked normal Host/PiAI/SQLite/tool loop, with an offline HTTP model fixture. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdtemp, mkdir, readFile, writeFile, realpath, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root = fileURLToPath(new URL('../', import.meta.url));
async function until(check, label, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {const result = await check(); if (result) return result; await delay(20);}
  throw new Error('Timed out: ' + label);
}

test('normal Host and PiAI execute actual granted read/hash/write tools, deny a later unconfirmed write and persist receipts', {timeout: 60000}, async t => {
  const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!existsSync(python) || !existsSync(join(root, 'kernel/dsh/packages/core/agent/lib/index.js'))) {t.skip('Prepare repository Python and kernel build'); return;}
  const requests = [], providerErrors = [];
  const server = createServer(async (req, res) => {
    try {
      const chunks = []; for await (const chunk of req) chunks.push(chunk);
      const input = JSON.parse(Buffer.concat(chunks).toString('utf8')), step = requests.length; requests.push(input);
      let call, content;
      if (step === 0) call = {name: 'mirally_read_file', arguments: JSON.stringify({root_id: 'workspace', path: '资料/source.txt'})};
      else if (step === 1) {
        const read = JSON.parse(input.messages.at(-1).content);
        assert.equal(read.text, '用户资料原文'); assert.match(read.sha256, /^[a-f0-9]{64}$/);
        call = {name: 'mirally_write_workspace_file', arguments: JSON.stringify({path: '资料/source.txt', content: '用户批准后的修改', expected_sha256: read.sha256})};
      } else if (step === 3) call = {name: 'mirally_write_workspace_file', arguments: JSON.stringify({path: 'unconfirmed.txt', content: '这个命令未批准保存'})};
      else content = step === 2 ? '已根据真实读取和保存回执完成。' : '本次没有文件授权，未保存文件。';
      const delta = call ? {tool_calls: [{index: 0, id: 'repeat-native-file-call', type: 'function', function: call}]} : {content};
      res.writeHead(200, {'content-type': 'text/event-stream'});
      for (const frame of [{id: 'file-fixture', choices: [{index: 0, delta, finish_reason: null}]},
        {id: 'file-fixture', choices: [{index: 0, delta: {}, finish_reason: call ? 'tool_calls' : 'stop'}], usage: {prompt_tokens: 10, completion_tokens: 10, total_tokens: 20}}])
        res.write(`data: ${JSON.stringify(frame)}\n\n`);
      res.end('data: [DONE]\n\n');
    } catch (error) {providerErrors.push(error); res.writeHead(500); res.end('Fixture assertion failed');}
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));});
  const home = await mkdtemp(join(tmpdir(), 'mirally basic capabilities 中文 '));
  let child, runtime, logs = '';
  t.after(async () => {
    if (child?.exitCode === null) {child.stdin.end(); try {await Promise.race([once(child, 'exit'), delay(7000).then(() => {throw new Error('cleanup timeout');})]);} catch {child.kill(); await Promise.race([once(child, 'exit'), delay(3000)]);}}
    const canonical = await realpath(home); assert.equal(dirname(canonical), await realpath(tmpdir())); assert.ok(basename(canonical).startsWith('mirally basic capabilities 中文 '));
    await rm(canonical, {recursive: true, force: true});
  });
  await mkdir(join(home, 'workspace/资料'), {recursive: true}); await writeFile(join(home, 'workspace/资料/source.txt'), '用户资料原文');
  const start = async () => {
    child = spawn(process.execPath, [join(root, 'kernel/dsh/packages/mirally/mirally-product/start.mjs'), '--root', root, '--home', home, '--python', python],
      {cwd: root, windowsHide: true, env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}, stdio: ['pipe', 'pipe', 'pipe']});
    child.stdout.on('data', bytes => {logs += bytes;}); child.stderr.on('data', bytes => {logs += bytes;});
    runtime = await until(async () => {try {const value = JSON.parse(await readFile(join(home, 'runtime.json'), 'utf8')); return value.pid === child.pid && value;} catch {return false;}}, 'normal startup ' + logs);
    assert.equal(runtime.mode, 'normal', 'This exercises actual PiAI rather than the explicit test adapter');
  };
  const api = async (path, value, expected = 200) => {
    const response = await fetch(runtime.url + path, {method: value === undefined ? 'GET' : 'POST', headers: {authorization: 'Bearer ' + runtime.token, 'content-type': 'application/json'}, ...(value === undefined ? {} : {body: JSON.stringify(value)})});
    assert.equal(response.status, expected, await response.clone().text()); return response.json();
  };
  const rpc = async (method, params = {}) => (await api('/api/rpc', {method, params})).result;
  await start();
  await rpc('settings.update', {patch: {model: {provider: 'deepseek', api_format: 'openai-chat', base_url: `http://127.0.0.1:${server.address().port}/v1`,
    api_key: 'offline-fixture-key', model_id: 'file-fixture', reasoning_effort: 'off', max_tokens: 1024}}});
  await rpc('capabilities.configure', {expected_revision: 1, patch: {workspace_write_enabled: true}});
  const session = await api('/api/sessions', {title: '文件能力真实回环'}, 201);
  const send = (commandId, allow_file_write) => api(`/api/sessions/${session.id}/messages`, {commandId, text: '按照本轮明确授权读写工作区资料。', allow_file_write}, 202);
  const completed = async commandId => until(async () => (await rpc('command.list', {conversation_id: session.id, command_id: commandId})).commands.some(row => row.state === 'completed'), 'native file chain ' + logs);
  await send('granted-command', true); await completed('granted-command');
  assert.deepEqual(providerErrors, []);
  assert.equal(await readFile(join(home, 'workspace/资料/source.txt'), 'utf8'), '用户批准后的修改');
  const declared = requests[0].tools.map(tool => tool.function.name);
  for (const name of ['mirally_workspace_roots', 'mirally_list_files', 'mirally_read_file', 'mirally_search_files', 'mirally_write_workspace_file', 'mirally_web_search', 'mirally_web_fetch']) assert.ok(declared.includes(name), name);
  let history = await api(`/api/sessions/${session.id}/history`);
  const tools = history.messages.filter(row => row.role === 'toolResult');
  assert.equal(tools.length, 2); assert.equal(tools[0].content[0].name, 'mirally_read_file'); assert.equal(tools[1].content[0].name, 'mirally_write_workspace_file');
  assert.equal(tools.every(row => row.content[1].isError === false), true);
  assert.notEqual(tools[0].toolCallId, tools[1].toolCallId, 'Repeated provider IDs do not collapse independent native invocations');
  await send('unconfirmed-command', false); await completed('unconfirmed-command');
  assert.deepEqual(providerErrors, []);
  assert.equal(existsSync(join(home, 'workspace/unconfirmed.txt')), false);
  history = await api(`/api/sessions/${session.id}/history`);
  const denied = history.messages.filter(row => row.role === 'toolResult').at(-1);
  assert.equal(denied.content[1].isError, true); assert.match(denied.content[1].text, /明确选择保存文件/);
  const before = history.messages.map(row => ({id: row.id, content: row.content}));
  await api('/api/shutdown', {}); await until(() => child.exitCode !== null, 'normal shutdown');
  await start();
  assert.equal((await rpc('capabilities.get')).workspace_write_enabled, true);
  assert.deepEqual((await api(`/api/sessions/${session.id}/history`)).messages.map(row => ({id: row.id, content: row.content})), before);
  assert.equal(requests.length, 5, 'History and cold startup never rerun tools or call the model');
  await api('/api/shutdown', {}); await until(() => child.exitCode !== null, 'cold restart shutdown');
});
