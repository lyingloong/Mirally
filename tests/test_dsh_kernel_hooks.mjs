import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync,
  realpathSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const installer = readFileSync(fileURLToPath(new URL('../kernel/dsh/scripts/install-lefthook.mjs', import.meta.url)), 'utf8');
const fixturePrefix = 'mirally-dsh-hooks-';

function write(path, content) {
  mkdirSync(dirname(path), {recursive: true});
  writeFileSync(path, content);
}

function fixture(t) {
  const temporaryRoot = realpathSync(tmpdir());
  const container = mkdtempSync(join(temporaryRoot, fixturePrefix));
  t.after(() => {
    const canonical = realpathSync(container);
    assert.equal(dirname(canonical), temporaryRoot);
    assert.ok(basename(canonical).startsWith(fixturePrefix));
    rmSync(canonical, {recursive: true, force: true});
  });
  const env = {...process.env};
  for (const name of Object.keys(env)) {
    if (/^GIT_/i.test(name) || /^DSH_(?:TEST_|LEFTHOOK_)/i.test(name)) delete env[name];
  }
  const globalConfig = join(container, 'global.gitconfig');
  write(globalConfig, '');
  Object.assign(env, {CI: 'false', GITHUB_ACTIONS: 'false',
    GIT_CONFIG_GLOBAL: globalConfig, GIT_CONFIG_NOSYSTEM: '1',
    GIT_CEILING_DIRECTORIES: container});
  return {container, env};
}

function git(f, cwd, ...args) {
  const result = spawnSync('git', args, {cwd, env: f.env, encoding: 'utf8', timeout: 15_000});
  assert.equal(result.status, 0, result.error?.message ?? result.stderr);
  return result.stdout.trim();
}

function initializeRepository(f, directory) {
  const template = join(f.container, 'empty-git-template');
  mkdirSync(template, {recursive: true});
  mkdirSync(directory, {recursive: true});
  git(f, directory, 'init', `--template=${template}`, directory);
}

function installFixture(directory) {
  const path = join(directory, 'scripts', 'install-lefthook.mjs');
  write(path, installer);
  write(join(directory, 'node_modules', 'lefthook', 'package.json'), JSON.stringify({
    name: 'lefthook', version: '0.0.0-fixture', bin: {lefthook: 'bin/lefthook'},
  }));
  return path;
}

function installFakeBinary(directory) {
  const stub = join(directory, 'fake-lefthook.cjs');
  write(stub, "require('node:fs').writeFileSync(require('node:path').join(__dirname, '.hook-invoked'), JSON.stringify(process.argv.slice(2)));\n");
  const binary = join(directory, 'node_modules', '.bin', process.platform === 'win32' ? 'lefthook.cmd' : 'lefthook');
  if (process.platform === 'win32') {
    write(binary, `@echo off\r\n"${process.execPath}" "${stub}" %*\r\n`);
  } else {
    write(binary, `#!/bin/sh\nexec "${process.execPath}" "${stub}" "$@"\n`);
    chmodSync(binary, 0o755);
  }
}

function runInstaller(f, path, cwd) {
  const result = spawnSync(process.execPath, [path], {cwd, env: f.env, encoding: 'utf8', timeout: 15_000});
  assert.equal(result.status, 0, result.error?.message ?? result.stderr);
}

function treeBytes(directory) {
  const result = {};
  function visit(current, relative = '') {
    for (const entry of readdirSync(current, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const key = relative ? `${relative}/${entry.name}` : entry.name;
      const path = join(current, entry.name);
      if (entry.isDirectory()) {
        result[key] = null;
        visit(path, key);
      } else {
        result[key] = readFileSync(path).toString('base64');
      }
    }
  }
  visit(directory);
  return result;
}

test('vendored kernel installation leaves the containing Git repository untouched', t => {
  const f = fixture(t);
  const outer = join(f.container, '产品 空格 checkout');
  initializeRepository(f, outer);
  installFakeBinary(outer);
  const kernel = join(outer, 'kernel', 'dsh');
  const path = installFixture(kernel);
  const gitDirectory = join(outer, '.git');
  const before = treeBytes(gitDirectory);
  runInstaller(f, path, kernel);
  assert.deepEqual(treeBytes(gitDirectory), before);
  assert.equal(existsSync(join(outer, '.hook-invoked')), false);
  assert.equal(existsSync(join(gitDirectory, 'dsh-hooks')), false);
  assert.equal(existsSync(join(gitDirectory, 'dsh-lefthook-install.lock')), false);
});

test('an unpacked kernel outside Git exits without creating repository state', t => {
  const f = fixture(t);
  const directory = join(f.container, 'unpacked source');
  const path = installFixture(directory);
  installFakeBinary(directory);
  const before = treeBytes(directory);
  runInstaller(f, path, directory);
  assert.deepEqual(treeBytes(directory), before);
});

test('a standalone kernel checkout still installs its own worktree hooks', t => {
  const f = fixture(t);
  const directory = join(f.container, '独立 kernel checkout');
  initializeRepository(f, directory);
  const path = installFixture(directory);
  installFakeBinary(directory);
  runInstaller(f, path, directory);
  assert.deepEqual(JSON.parse(readFileSync(join(directory, '.hook-invoked'), 'utf8')), ['install', '--force']);
  const hooks = git(f, directory, 'config', '--worktree', '--get', 'core.hooksPath');
  assert.equal(realpathSync.native(hooks), realpathSync.native(join(directory, '.git', 'dsh-hooks')));
  assert.equal(git(f, directory, 'config', '--local', '--get', 'extensions.worktreeConfig'), 'true');
  assert.equal(existsSync(join(directory, '.git', 'dsh-hooks', '.dsh-lefthook-owned')), true);
  assert.equal(existsSync(join(directory, '.git', 'dsh-lefthook-install.lock')), false);
});
