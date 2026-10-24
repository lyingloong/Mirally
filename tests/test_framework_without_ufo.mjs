/** The default product works with UFO imports forbidden and exposes no executor. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
import {mkdtemp, readFile, readdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {delimiter, join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
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

test('retired desktop RPCs cannot reach an executor or admit a product command', async () => {
  const product = Object.create(MirallyProduct.prototype);
  product.rpc = () => { throw new Error('No side effect may be reached'); };
  product.serial = () => { throw new Error('No desktop queue may be reached'); };
  for (const method of methods) {
    await assert.rejects(product.publicRpc(method, {confirmed: true, conversation_id: 'c', command_id: 'ufo-command'}),
      error => error.code === 'method_not_allowed' && error.status === 403);
  }
});

test('product model tools expose owned collaboration capabilities without a desktop executor', () => {
  const product = Object.create(MirallyProduct.prototype), tools = [];
  product.ctx = {tools: {register: definition => tools.push(definition)}};
  product.installProductTools();
  assert.ok(tools.some(tool => tool.name === 'mirally_context'));
  assert.ok(tools.some(tool => tool.name === 'mirally_create_file'));
  assert.equal(tools.some(tool => /ufo|desktop/i.test(tool.name + ' ' + tool.description)), false);
});

test('real default Host starts, converses and shuts down while UFO imports are forbidden', {timeout: 45000}, async t => {
  const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!existsSync(python) || !existsSync(join(root, 'kernel/dsh/packages/core/agent/lib/index.js'))) {
    t.skip('Prepare the repository .venv and build the owned kernel first'); return;
  }
  const home = await mkdtemp(join(tmpdir(), 'mirally 无UFO host '));
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
  // Fail if the transport ever tries to recover the retired executor, including
  // a swallowed dynamic import. Python workers inherit the matching import fence.
  const preload = join(home, 'forbid-ufo.mjs');
  await writeFile(preload, `import {registerHooks} from 'node:module';
import {appendFileSync} from 'node:fs';
registerHooks({resolve(specifier, context, next) {
  if (/(?:^|[/\\\\])(?:desktop\\.mjs|ufo)(?:$|[/\\\\])/i.test(specifier)) {
    appendFileSync(${JSON.stringify(join(home, 'blocked-node.jsonl'))}, JSON.stringify(specifier) + '\\n');
    throw new Error('UFO is not part of the default framework');
  }
  return next(specifier, context);
}});`);
  await writeFile(join(home, 'sitecustomize.py'), `import atexit, importlib.abc, json, os, sys
from pathlib import Path
audit_home = Path(${JSON.stringify(home)})
class NoUfo(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'ufo' or fullname.startswith(('ufo.', 'mirally.runtime.ufo')):
            with (audit_home / 'blocked-python.jsonl').open('a', encoding='utf-8') as output:
                output.write(json.dumps(fullname) + '\\n')
            raise ModuleNotFoundError('UFO is not part of the default framework')
sys.meta_path.insert(0, NoUfo())
@atexit.register
def check_imports():
    loaded = [name for name in sys.modules if name == 'ufo' or name.startswith(('ufo.', 'mirally.runtime.ufo'))]
    (audit_home / ('python-imports-' + str(os.getpid()) + '.json')).write_text(json.dumps(loaded), encoding='utf-8')
`);
  child = spawn(process.execPath, ['--import', pathToFileURL(preload).href, join(root, 'kernel/dsh/packages/mirally/mirally-product/start.mjs'),
    '--root', root, '--home', home, '--python', python, '--test-mode'], {cwd: root,
    env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PYTHONPATH: [home, process.env.PYTHONPATH].filter(Boolean).join(delimiter)},
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
    const result = await call('/api/rpc', {method, params: {confirmed: true, conversation_id: 'c', command_id: 'ufo-command'}});
    assert.equal(result.status, 403); assert.equal(result.value.error.code, 'method_not_allowed');
  }
  const retired = await call('/internal/v1/chat/completions', {messages: [{role: 'user', content: 'retired worker route'}]});
  assert.equal(retired.status, 404);
  const presentation = await fetch(new URL('/mirally-presentation.js', runtime.url));
  assert.doesNotMatch(await presentation.text(), /__mirallyUfoInput|mirally-ufo-input/);
  const session = (await call('/api/sessions', {title: '不接入UFO'})).value;
  const sent = await call(`/api/sessions/${session.id}/messages`, {commandId: 'ordinary-chat', text: '继续协作'});
  assert.equal(sent.status, 202);
  const history = await until(async () => {
    const result = (await call(`/api/sessions/${session.id}/history`)).value;
    return result.messages?.some(message => message.role === 'assistant') && result;
  }, 'real native reply');
  assert.match(history.messages.find(message => message.role === 'assistant').text, /继续协作/);
  assert.equal(existsSync(join(home, 'actions')), false);
  assert.equal(existsSync(join(home, 'blocked-node.jsonl')), false);
  assert.equal(existsSync(join(home, 'blocked-python.jsonl')), false);
  await close();
  const audits = (await readdir(home)).filter(name => name.startsWith('python-imports-'));
  assert.ok(audits.length > 0, 'The real Python sidecar must have run its import fence');
  for (const name of audits) assert.deepEqual(JSON.parse(await readFile(join(home, name), 'utf8')), []);
});
