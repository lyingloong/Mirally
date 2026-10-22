import {test} from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {mkdtempSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {DesktopTasks} from '../src/desktop.mjs';

async function until(check) { for (let i = 0; i < 100; i++) { const value = check(); if (value) return value; await delay(10); } throw new Error('condition timed out'); }
function fixture(t) {
  const home = mkdtempSync(join(tmpdir(), 'mirally desktop 中文 '));
  t.after(() => rmSync(home, {recursive: true, force: true}));
  mkdirSync(join(home, 'UFO/ufo'), {recursive: true});
  const calls = [], commands = new Set(); let allowed = true; let child; const sent = [];
  const product = {root: home, home, url: 'http://127.0.0.1:1', token: 'fixture', config: {python: 'fixture-python'},
    ctx: {logger: {warn() {}}}, broadcast() {}, authorizations: {run: (_scope, fn) => fn()},
    models: {requireOptions: () => ({model: 'fixture'})}, conversation: async id => ({conversation_id: id, session_id: 'native'}),
    ensureNative: async conversation => ({conversation}), guard: async () => { if (!allowed) throw new Error('revoked'); },
    rpc: async (method, params) => { calls.push({method, params}); if (method === 'internal.settings.get') return {model: {image_enabled: true}};
      if (method === 'command.admit') { const should_dispatch = !commands.has(params.command_id); commands.add(params.command_id); return {should_dispatch}; } return {}; }};
  const tasks = new DesktopTasks(product, {spawnWorker: () => {
    child = Object.assign(new EventEmitter(), {stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null});
    let buffer = ''; child.stdin.on('data', data => { buffer += data; let pos; while ((pos = buffer.indexOf('\n')) >= 0) { sent.push(JSON.parse(buffer.slice(0, pos))); buffer = buffer.slice(pos + 1); } });
    return child;
  }});
  const frame = value => child.stdout.write(JSON.stringify(value) + '\n');
  const exit = () => { child.exitCode = 0; child.emit('exit', 0, null); };
  const start = () => tasks.start({conversation_id: 'conversation', command_id: 'cmd', text: 'Explicit fixture task', confirmed: true});
  return {tasks, calls, sent, start, frame, exit, revoke: () => { allowed = false; }};
}

test('requires separate confirmation and does not start on an ordinary request', async t => {
  const f = fixture(t);
  await assert.rejects(f.tasks.start({conversation_id: 'c', command_id: 'cmd', text: 'x', confirmed: false}), /confirmed/);
  assert.equal(f.sent.length, 0);
});
test('desktop question/refusal and completed receipt survive a new transport', async t => {
  const f = fixture(t); await f.start(); await until(() => f.sent.length);
  f.frame({type: 'request', id: 'q', operation: 'confirm', text: 'Send this message?'});
  await until(() => f.tasks.list().actions[0]?.question);
  await f.tasks.respond({id: f.tasks.list().actions[0].id, request_id: 'q', confirmed: false});
  assert.equal(f.sent.at(-1).value, false);
  f.frame({type: 'result', result: {status: 'completed', message: 'Finished'}});
  await until(() => f.tasks.list().actions[0]?.state === 'completed'); f.exit(); await f.tasks.dispose();
  assert.equal(new DesktopTasks(f.tasks.product).list().actions[0].state, 'completed');
  assert.equal((await f.start()).replay, true);
});
test('source withdrawal denies the next observation and every action boundary', async t => {
  const f = fixture(t); await f.start(); await until(() => f.sent.length); f.revoke();
  f.frame({type: 'request', id: 'observation', operation: 'authorize_observation'});
  await until(() => f.sent.some(frame => frame.id === 'observation'));
  assert.equal(f.sent.at(-1).ok, false); f.exit(); await f.tasks.dispose();
  assert.equal(f.tasks.list().actions[0].state, 'unknown');
});
test('cancel followed by unreceipted worker exit remains unknown', async t => {
  const f = fixture(t); await f.start(); await until(() => f.sent.length);
  f.tasks.cancel(f.tasks.list().actions[0].id); assert.equal(f.sent.at(-1).type, 'cancel');
  f.exit(); await f.tasks.dispose(); assert.equal(f.tasks.list().actions[0].state, 'unknown');
  assert.equal(f.calls.filter(c => c.method === 'command.ack').at(-1).params.state, 'unknown');
});
test('concurrent starts cannot admit two desktop workers', async t => {
  const f = fixture(t);
  const results = await Promise.allSettled([f.start(), f.tasks.start({conversation_id: 'another', command_id: 'other', text: 'Another confirmed task', confirmed: true})]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  await until(() => f.sent.length); f.exit(); await f.tasks.dispose();
});
test('retrying the same active command returns its receipt', async t => {
  const f = fixture(t); await f.start(); await until(() => f.sent.length);
  assert.equal((await f.start()).replay, true);
  assert.equal(f.tasks.jobs.size, 1); f.exit(); await f.tasks.dispose();
});
