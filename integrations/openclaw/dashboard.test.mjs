import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync, readdirSync, readFileSync, realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {pathToFileURL} from 'node:url';
import {runDashboard} from './dashboard.mjs';

function fixture(t, {version = '2026.9.6', source, extra} = {}) {
  const directory = mkdtempSync(join(tmpdir(), 'mirally-dashboard-'));
  t.after(() => {
    const resolved = realpathSync(directory);
    assert.equal(dirname(resolved), realpathSync(tmpdir()));
    assert.match(basename(resolved), /^mirally-dashboard-/);
    rmSync(resolved, {recursive: true, force: true});
  });
  writeFileSync(join(directory, 'package.json'), JSON.stringify({version}));
  mkdirSync(join(directory, 'dist'));
  if (source) writeFileSync(join(directory, 'dist/dashboard-abcdefgh.mjs'), source);
  if (extra) writeFileSync(join(directory, 'dist/dashboard-ijklmnop.mjs'), extra);
  return pathToFileURL(directory + '/');
}

test('unsupported versions and ambiguous or missing entries request full CLI fallback without output', async t => {
  for (const options of [{version: '2026.10.1'}, {}, {source: 'export const dashboardCommand=()=>{};', extra: 'export const dashboardCommand=()=>{};'}, {source: 'export const changedExport=()=>{};'}]) {
    const output = [];
    assert.equal(await runDashboard(fixture(t, options), value => output.push(value)), 78);
    assert.deepEqual(output, []);
  }
});

test('uses the native JSON path and passes its private authentication result unchanged', async t => {
  const root = fixture(t, {source: `export async function dashboardCommand(runtime, options) {
    if (options.json !== true) throw Error('Non-JSON path may open browser or clipboard');
    runtime.log(JSON.stringify({ok:true,browserUrl:'https://example.test/#private-once',browserBootstrapExpiresAtMs:123}));
  }`});
  const output = [];
  assert.equal(await runDashboard(root, value => output.push(value)), 0);
  assert.deepEqual(output, [{ok:true,browserUrl:'https://example.test/#private-once',browserBootstrapExpiresAtMs:123}]);
});

test('preserves native structured failure but never echoes a thrown diagnostic', async t => {
  const output = [];
  const failure = fixture(t, {source: `export async function dashboardCommand(runtime) {
    runtime.log(JSON.stringify({ok:false,reason:'Gateway is unavailable.'}));runtime.exit(1);
  }`});
  assert.equal(await runDashboard(failure, value => output.push(value)), 1);
  assert.deepEqual(output.pop(), {ok:false,reason:'Gateway is unavailable.'});
  const thrown = fixture(t, {source: `export async function dashboardCommand() {throw Error('private-auth-token');}`});
  assert.equal(await runDashboard(thrown, value => output.push(value)), 1);
  assert.equal(JSON.stringify(output).includes('private-auth-token'), false);
});

test('installed pinned native dashboard exposes the expected entry without calling or replacing it', async () => {
  const root = new URL('./node_modules/openclaw/', import.meta.url);
  assert.equal(JSON.parse(readFileSync(new URL('package.json', root), 'utf8')).version, '2026.9.6');
  const dist = new URL('dist/', root);
  const entries = readdirSync(dist).filter(name => /^dashboard-[A-Za-z0-9_-]{8}\.mjs$/.test(name));
  assert.equal(entries.length, 1);
  assert.equal(typeof (await import(new URL(entries[0], dist))).dashboardCommand, 'function');
});
