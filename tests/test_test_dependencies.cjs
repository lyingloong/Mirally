const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const {mkdtempSync, realpathSync, rmSync, writeFileSync} = require('node:fs');
const {tmpdir} = require('node:os');
const {basename, dirname, join, resolve} = require('node:path');
const {pathToFileURL} = require('node:url');
const dependencies = require('../tools/testing/dependencies.cjs');

const root = resolve(__dirname, '..');
const loader = resolve(root, 'tools/testing/dependencies.cjs');
const playwright = resolve(root, 'tools/testing/node_modules/playwright-core');

function runFromAnotherDirectory(script, override = '') {
  return spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    cwd: tmpdir(), encoding: 'utf8', windowsHide: true, timeout: 20_000,
    env: {...process.env, MIRALLY_PLAYWRIGHT: override},
  });
}

test('standalone test tools provide the actual browser and DOM APIs', () => {
  const {parseHTML} = dependencies.loadLinkedom();
  assert.equal(parseHTML('<p id="value">fixture</p>').document.querySelector('#value').textContent, 'fixture');
  const previous = process.env.MIRALLY_PLAYWRIGHT;
  try {
    delete process.env.MIRALLY_PLAYWRIGHT;
    assert.equal(typeof dependencies.loadPlaywright().chromium.launch, 'function');
  } finally {
    if (previous === undefined) delete process.env.MIRALLY_PLAYWRIGHT;
    else process.env.MIRALLY_PLAYWRIGHT = previous;
  }
});

const esmScript = `import {loadPlaywright, loadLinkedom} from ${JSON.stringify(pathToFileURL(loader).href)};
if (typeof loadPlaywright().chromium.launch !== 'function' || typeof loadLinkedom().parseHTML !== 'function') process.exit(2);`;

test('ESM acceptance tools resolve dependencies independently of their working directory', () => {
  const result = runFromAnotherDirectory(esmScript);
  assert.equal(result.status, 0, result.stderr);
});

for (const [label, override] of [
  ['absolute path', playwright],
  ['repository relative path', './tools/testing/node_modules/playwright-core'],
  ['file URL', pathToFileURL(playwright).href],
]) {
  test(`MIRALLY_PLAYWRIGHT accepts an explicit ${label} from another working directory`, () => {
    const result = runFromAnotherDirectory(esmScript, override);
    assert.equal(result.status, 0, result.stderr);
  });
}

test('an invalid explicit Playwright location fails with preparation guidance instead of falling back', () => {
  const result = runFromAnotherDirectory(`import {loadPlaywright} from ${JSON.stringify(pathToFileURL(loader).href)};
try { loadPlaywright(); process.exit(2); }
catch (error) { if (error.code !== 'mirally_test_dependency_missing' || !error.message.includes('npm --prefix tools/testing ci')) throw error; }`, './tools/testing/missing-playwright-for-loader-test');
  assert.equal(result.status, 0, result.stderr);
});

test('an explicit external module in a path containing spaces and Chinese is actually selected', t => {
  const directory = mkdtempSync(join(tmpdir(), 'mirally testing 中文 '));
  t.after(() => {
    const canonical = realpathSync(directory);
    assert.equal(dirname(canonical), realpathSync(tmpdir()));
    assert.ok(basename(canonical).startsWith('mirally testing 中文 '));
    rmSync(canonical, {recursive: true, force: true});
  });
  const modulePath = join(directory, 'browser fixture.cjs');
  writeFileSync(modulePath, 'module.exports = {chromium: {marker: "external fixture"}};\n');
  const script = `import {loadPlaywright} from ${JSON.stringify(pathToFileURL(loader).href)};
if (loadPlaywright().chromium.marker !== 'external fixture') process.exit(2);`;
  for (const override of [modulePath, pathToFileURL(modulePath).href]) {
    const result = runFromAnotherDirectory(script, override);
    assert.equal(result.status, 0, result.stderr);
  }
});
