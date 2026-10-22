import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, readFile, rm, realpath} from 'node:fs/promises';
import {join, dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {normalizeExtension, redactExtension} from '../src/configuration.mjs';
import {runConfiguredScript} from '../src/script.mjs';

async function fixture(t, code = 'console.log(JSON.stringify(process.argv.slice(2)))') {
  const parent = await realpath(tmpdir());
  const root = await mkdtemp(join(parent, 'mirally-生态 空格-'));
  t.after(async () => {
    assert.equal(dirname(root), parent);
    await rm(root, {recursive: true, force: true});
  });
  const skill = join(root, 'skills', 'utility');
  await mkdir(skill, {recursive: true});
  await writeFile(join(skill, 'SKILL.md'), '---\nname: utility\ndescription: A local fixture skill\n---\nUse the configured helper.\n');
  await writeFile(join(skill, 'helper.mjs'), code);
  const options = {root, home: join(root, 'home'), python: 'unused-python'};
  const config = {id: 'utility', type: 'bundle', version: '1.0.0', config: {
    skills: ['skills'], scripts: [{id: 'helper', path: 'skills/utility/helper.mjs', interpreter: 'node', timeoutMs: 3000}],
  }};
  return {root, options, config, script: (await normalizeExtension(config, options)).scripts.get('helper')};
}

const execution = (f, extra = {}) => ({python: f.options.python, authorize: async () => {}, assertActive() {}, ...extra});

test('configured scripts preserve literal arguments in a Chinese and space-containing directory', async t => {
  const f = await fixture(t);
  const args = ['中文 空格', '$(not-a-shell)', '; touch should-not-run'];
  const result = await runConfiguredScript(f.script, args, execution(f));
  assert.equal(result.outcome, 'completed');
  assert.deepEqual(JSON.parse(result.stdout), args);
  assert.equal(result.script_sha256, f.script.sha256);
});

test('authorization denial cannot start the script', async t => {
  const f = await fixture(t, 'require("node:fs").writeFileSync("executed", "bad")');
  await assert.rejects(runConfiguredScript(f.script, [], execution(f, {authorize: async () => { throw new Error('confirmation required'); }})), /confirmation required/);
  await assert.rejects(readFile(join(f.root, 'skills/utility/executed')), {code: 'ENOENT'});
});

test('changed allowlisted code is rejected until it is reviewed and configured again', async t => {
  const f = await fixture(t);
  await writeFile(f.script.path, 'console.log("new code")');
  await assert.rejects(runConfiguredScript(f.script, [], execution(f)), /script changed/);
});

test('cancellation during authorization cannot launch the delayed invocation', async t => {
  const f = await fixture(t);
  const controller = new AbortController();
  await assert.rejects(runConfiguredScript(f.script, [], execution(f, {
    signal: controller.signal, authorize: async () => { controller.abort(new Error('stopped before spawn')); },
  })), /stopped before spawn/);
});

test('script output limit stops the process and reports an unknown external outcome', async t => {
  const f = await fixture(t, 'setInterval(() => process.stdout.write("界".repeat(2000)), 5)');
  const result = await runConfiguredScript({...f.script, maxOutputBytes: 1024}, [], execution(f));
  assert.equal(result.status, 'output_limit');
  assert.equal(result.outcome, 'unknown');
  assert.equal(result.truncated, true);
  // UTF-8 decoding can replace the last partial code point; retained raw bytes were bounded.
  assert.ok(Buffer.byteLength(result.stdout, 'utf8') <= 1026);
});

test('timeout and cancellation wait for the spawned process to close', async t => {
  const f = await fixture(t, 'console.log("started"); setInterval(() => {}, 1000)');
  const timed = await runConfiguredScript({...f.script, timeoutMs: 150}, [], execution(f));
  assert.equal(timed.status, 'timeout');
  assert.equal(timed.outcome, 'unknown');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('user stopped')), 150);
  try {
    const cancelled = await runConfiguredScript(f.script, [], execution(f, {signal: controller.signal}));
    assert.equal(cancelled.status, 'cancelled');
    assert.equal(cancelled.outcome, 'unknown');
  } finally { clearTimeout(timer); }
});

test('configuration rejects future APIs, shell interpreters and scripts outside Skill roots', async t => {
  const f = await fixture(t);
  await assert.rejects(normalizeExtension({...f.config, config: {...f.config.config, apiVersion: 2}}, f.options), /API version/);
  await assert.rejects(normalizeExtension({...f.config, config: {...f.config.config,
    scripts: [{id: 'shell', path: 'skills/utility/helper.mjs', interpreter: 'powershell'}],
  }}, f.options), /python or node/);
  await writeFile(join(f.root, 'outside.mjs'), 'console.log("outside")');
  await assert.rejects(normalizeExtension({...f.config, config: {...f.config.config,
    scripts: [{id: 'outside', path: 'outside.mjs', interpreter: 'node'}],
  }}, f.options), /inside an explicitly configured Skill/);
});

test('MCP credential presence is visible without exposing values', async t => {
  const f = await fixture(t);
  const raw = {...f.config, config: {mcp: [{serverName: 'docs', transport: 'streamable-http',
    url: 'https://example.invalid/mcp', headers: {Authorization: 'secret-header'}, env: {TOKEN: 'secret-env'}}]}};
  const output = JSON.stringify(redactExtension(raw));
  assert.equal(output.includes('secret-header'), false);
  assert.equal(output.includes('secret-env'), false);
  assert.match(output, /Authorization/);
  const mcp = await normalizeExtension({id: 'docs', type: 'mcp', config: raw.config.mcp[0]}, f.options);
  assert.equal(mcp.mcp[0].readOnly, false);
});
