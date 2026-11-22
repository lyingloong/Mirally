/** Desktop observations and the default DSH conversation. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const methods = ['desktop.start', 'desktop.list', 'desktop.respond', 'desktop.cancel'];
async function until(check, label, timeout = 18000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await check(); if (value) return value; await delay(20); }
  throw new Error('Timed out: ' + label);
}

test('desktop task RPCs cannot admit a product command', async () => {
  const product = Object.create(MirallyProduct.prototype);
  product.rpc = () => { throw new Error('No side effect may be reached'); };
  product.serial = () => { throw new Error('No desktop queue may be reached'); };
  for (const method of methods) {
    await assert.rejects(product.publicRpc(method, {confirmed: true, conversation_id: 'c', command_id: 'desktop-command'}),
      error => error.code === 'method_not_allowed' && error.status === 403);
  }
});

test('product tools expose observations and scoped semantic actions', () => {
  const product = Object.create(MirallyProduct.prototype), tools = [];
  product.ctx = {tools: {register: definition => tools.push(definition)}};
  product.installProductTools();
  assert.ok(tools.some(tool => tool.name === 'mirally_context'));
  assert.ok(tools.some(tool => tool.name === 'mirally_create_file'));
  const desktopTools = tools.filter(tool => tool.name.startsWith('mirally_desktop_')).map(tool => tool.name).sort();
  assert.deepEqual(desktopTools, ['mirally_desktop_action', 'mirally_desktop_foreground', 'mirally_desktop_screenshot',
    'mirally_desktop_snapshot', 'mirally_desktop_windows']);
});

test('real default Host converses while desktop task RPCs are unavailable', {timeout: 45000}, async t => {
  const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!existsSync(python) || !existsSync(join(root, 'kernel/dsh/packages/core/agent/lib/index.js'))) {
    t.skip('Prepare the repository .venv and build the owned kernel first'); return;
  }
  const home = await mkdtemp(join(tmpdir(), 'mirally desktop boundary '));
  let child, logs = '';
  const close = async () => {
    if (child?.exitCode === null) {
      child.stdin.end();
      await Promise.race([once(child, 'exit'), delay(10000, undefined, {ref: false}).then(() => {throw new Error('Host shutdown timed out: ' + logs);})]);
      assert.equal(child.exitCode, 0, logs);
    }
  };
  t.after(async () => {
    try { await close(); } finally {
      if (child?.exitCode === null) { child.kill(); await once(child, 'exit'); }
      await rm(home, {recursive: true, force: true});
    }
  });
  child = spawn(process.execPath, [join(root, 'kernel/dsh/packages/mirally/mirally-product/start.mjs'),
    '--root', root, '--home', home, '--python', python, '--test-mode'], {cwd: root,
    env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'},
    windowsHide: true, stdio: ['pipe', 'pipe', 'pipe']});
  child.stdout.on('data', chunk => {logs += chunk;}); child.stderr.on('data', chunk => {logs += chunk;});
  const runtime = await until(() => {
    if (child.exitCode !== null) throw new Error('Default Host failed: ' + logs);
    try { return JSON.parse(readFileSync(join(home, 'runtime.json'), 'utf8')); } catch {}
  }, 'real Host startup');
  const call = async (path, input) => {
    const response = await fetch(new URL(path, runtime.url), {method: input === undefined ? 'GET' : 'POST',
      headers: {Authorization: 'Bearer ' + runtime.token, 'Content-Type': 'application/json'},
      ...(input === undefined ? {} : {body: JSON.stringify(input)})});
    return {status: response.status, value: await response.json()};
  };
  assert.equal((await call('/api/health')).value.ready, true);
  assert.equal(existsSync(join(home, 'actions')), false);
  for (const method of methods) {
    const result = await call('/api/rpc', {method, params: {confirmed: true, conversation_id: 'c', command_id: 'desktop-command'}});
    assert.equal(result.status, 403); assert.equal(result.value.error.code, 'method_not_allowed');
  }
  const retired = await call('/internal/v1/chat/completions', {messages: [{role: 'user', content: 'retired worker route'}]});
  assert.equal(retired.status, 404);
  const session = (await call('/api/sessions', {title: '桌面能力边界'})).value;
  const sent = await call(`/api/sessions/${session.id}/messages`, {commandId: 'ordinary-chat', text: '继续协作'});
  assert.equal(sent.status, 202);
  const history = await until(async () => {
    const result = (await call(`/api/sessions/${session.id}/history`)).value;
    return result.messages?.some(message => message.role === 'assistant') && result;
  }, 'real native reply');
  assert.match(history.messages.find(message => message.role === 'assistant').text, /继续协作/);
  assert.equal(existsSync(join(home, 'actions')), false);
  await close();
});
