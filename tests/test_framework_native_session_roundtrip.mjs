/** Real DSH sessions retain tool and image history across stop and process restart. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdtemp, readFile, realpath, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root = fileURLToPath(new URL('../', import.meta.url));
async function until(check, label, timeout = 25000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {const value = await check(); if (value) return value; await delay(25);}
  throw Error(`timeout: ${label}`);
}

test('same native DSH session keeps complete available messages after stop and cold resume, but excludes revoked generations', {timeout: 100000}, async t => {
  const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!existsSync(python) || !existsSync(join(root, 'kernel/dsh/packages/core/agent/lib/index.js'))) {t.skip('prepare repository and kernel'); return;}
  const requests = [];
  const server = createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const input = JSON.parse(Buffer.concat(chunks).toString('utf8')); requests.push(input);
    const n = requests.length;
    res.writeHead(200, {'content-type': 'text/event-stream'});
    if (n === 3) {
      res.write('data: ' + JSON.stringify({id: 'waiting', choices: [{index: 0, delta: {content: 'Interrupted work'}, finish_reason: null}]}) + '\n\n');
      return;
    }
    const tool = n === 1;
    const delta = tool ? {tool_calls: [{index: 0, id: 'native-tool', type: 'function', function: {name: 'mirally_reminders', arguments: '{}'}}]}
      : {content: n === 2 ? 'TOOL_DERIVED_ASSISTANT_SENTINEL' : '继续处理。'};
    for (const frame of [{id: 'native-session', choices: [{index: 0, delta, finish_reason: null}]},
      {id: 'native-session', choices: [{index: 0, delta: {}, finish_reason: tool ? 'tool_calls' : 'stop'}], usage: {prompt_tokens: 100, completion_tokens: 30, total_tokens: 130}}])
      res.write(`data: ${JSON.stringify(frame)}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const home = await mkdtemp(join(tmpdir(), 'mirally native sessions 中文 ')); let child, runtime, logs = '';
  t.after(async () => {
    if (child?.exitCode === null) {child.stdin.end(); try {await Promise.race([once(child, 'exit'), delay(7000).then(() => {throw Error('cleanup timeout');})]);} catch {child.kill(); await Promise.race([once(child, 'exit'), delay(3000)]);}}
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    const canonical = await realpath(home); assert.equal(dirname(canonical), await realpath(tmpdir())); assert.ok(basename(canonical).startsWith('mirally native sessions 中文 '));
    await rm(canonical, {recursive: true, force: true});
  });
  const start = async () => {
    child = spawn(process.execPath, [join(root, 'kernel/dsh/packages/mirally/mirally-product/start.mjs'), '--root', root, '--home', home, '--python', python],
      {cwd: root, windowsHide: true, env: {...process.env, PYTHONUTF8: '1'}, stdio: ['pipe', 'pipe', 'pipe']});
    child.stdout.on('data', chunk => {logs += chunk;}); child.stderr.on('data', chunk => {logs += chunk;});
    runtime = await until(async () => {if (child.exitCode !== null) throw Error(logs); try {const value = JSON.parse(await readFile(join(home, 'runtime.json'), 'utf8')); return value.pid === child.pid && value;} catch {return false;}}, 'host: ' + logs);
  };
  const api = async (path, value, expected = 200) => {
    const response = await fetch(runtime.url + path, {method: value === undefined ? 'GET' : 'POST', headers: {authorization: `Bearer ${runtime.token}`, 'content-type': 'application/json'}, ...(value === undefined ? {} : {body: JSON.stringify(value)})});
    assert.equal(response.status, expected, await response.clone().text()); return response.json();
  };
  const rpc = async (method, params = {}) => (await api('/api/rpc', {method, params})).result;
  const complete = command => until(async () => (await rpc('command.list', {conversation_id: session.id, command_id: command})).commands.some(item => item.state === 'completed'), `complete ${command}: ${logs}`);
  const send = (commandId, text, more = {}) => api(`/api/sessions/${session.id}/messages`, {commandId, text, ...more}, 202);
  const summary = async () => (await api('/api/sessions')).sessions.find(item => item.id === session.id);
  await start();
  await rpc('settings.update', {patch: {model: {provider: 'deepseek', api_format: 'openai-chat', base_url: `http://127.0.0.1:${server.address().port}/v1`, api_key: 'fixture', model_id: 'fixture', image_enabled: true, reasoning_effort: 'off', max_tokens: 1024}}});
  await rpc('preference.set', {key: 'session-test', value: 'PERSONAL_CONTEXT_SENTINEL'});
  await rpc('commitment.create', {title: 'TOOL_RECEIPT_SENTINEL', due_at_utc_ms: Date.now() + 3600000});
  const session = await api('/api/sessions', {title: 'Native complete session'}, 201);
  await rpc('action.permissions.set', {conversation_id: session.id, enabled: true});
  const material = await api('/api/materials', {conversation_id: session.id, name: '原始图.png', media_type: 'image/png', data: (await readFile(join(root, 'mirally/assets/mirally-128.png'))).toString('base64')}, 201);
  await send('first', 'USER_CONTEXT_SENTINEL，请用提醒工具检查。', {materials: [material.id]});
  await complete('first');
  const original = await summary();
  await send('waiting', '等待中，直到我叫你停止。');
  await until(() => requests.length === 3, 'waiting request');
  await api(`/api/sessions/${session.id}/cancel`, {commandId: 'waiting'});
  const stopped = await summary();
  assert.equal(stopped.frozen, false); assert.equal(stopped.session_id, original.session_id); assert.equal(stopped.generation, original.generation);
  assert.equal((await rpc('action.permissions.get', {conversation_id: session.id})).session_auto_approve, true);
  const continued = await send('continue', '继续'); await complete('continue');
  assert.equal(continued.native_session_id, original.session_id);
  const validate = request => {
    const text = JSON.stringify(request.messages);
    assert.match(text, /USER_CONTEXT_SENTINEL/); assert.match(text, /TOOL_DERIVED_ASSISTANT_SENTINEL/);
    assert.ok(request.messages.some(message => message.role === 'tool' && JSON.stringify(message.content).includes('TOOL_RECEIPT_SENTINEL')));
    assert.ok(request.messages.some(message => message.role === 'user' && Array.isArray(message.content) && message.content.some(block => block.type === 'image_url')));
    assert.doesNotMatch(text, /conversation_continuation/);
  };
  validate(requests[3]);
  const history = await api(`/api/sessions/${session.id}/history`);
  assert.equal(history.messages.filter(message => message.role === 'user' && message.text.includes('USER_CONTEXT_SENTINEL')).length, 1);
  assert.equal((await rpc('conversation.history_refs', {conversation_id: session.id})).generations.length, 1);
  await api('/api/shutdown', {}); await until(() => child.exitCode !== null, 'first host exits');
  const beforeRestart = requests.length; await start();
  await delay(150); assert.equal(requests.length, beforeRestart, 'startup does not replay work');
  const resumed = await send('restart', '重启后继续检查。'); await complete('restart');
  assert.equal(resumed.native_session_id, original.session_id); validate(requests[4]);
  const fork=await api(`/api/sessions/${session.id}/fork`,{requestId:'stable-fork'},201);
  assert.notEqual(fork.session_id,original.session_id);
  assert.equal(fork.parent_conversation_id,session.id);
  assert.equal((await api(`/api/sessions/${session.id}/fork`,{requestId:'stable-fork'},201)).id,fork.id);
  const forkedHistory=await api(`/api/sessions/${fork.id}/history?events=1`);
  assert.ok(forkedHistory.messages.some(m=>m.role==='toolResult'));
  assert.ok(forkedHistory.messages.some(m=>m.role==='assistant'&&m.text.includes('TOOL_DERIVED_ASSISTANT_SENTINEL')));
  assert.ok(forkedHistory.events.some(e=>e.type==='tool/result'));
  await api(`/api/sessions/${fork.id}/messages`,{commandId:'fork-reply',text:'在分叉中继续。'},202);
  await until(async()=>(await rpc('command.list',{conversation_id:fork.id,command_id:'fork-reply'})).commands.some(c=>c.state==='completed'),'fork model complete');
  validate(requests.at(-1));
  await rpc('source.set', {source_id: 'preference:user/local:session-test', enabled: false});
  assert.equal((await rpc('conversation.history_refs',{conversation_id:fork.id})).generations[0].frozen,true,'fork inherits source withdrawal');
  const clean = await send('clean', '新的问题'); await complete('clean');
  assert.notEqual(clean.native_session_id, original.session_id);
  assert.doesNotMatch(JSON.stringify(requests.at(-1).messages), /PERSONAL_CONTEXT_SENTINEL|USER_CONTEXT_SENTINEL|TOOL_DERIVED_ASSISTANT_SENTINEL/);
});
