/** Test dependencies are owned here, independently of the product runtime. */
const {createRequire} = require('node:module');
const {isAbsolute, resolve} = require('node:path');
const {fileURLToPath} = require('node:url');

const testRequire = createRequire(resolve(__dirname, 'package.json'));
const root = resolve(__dirname, '../..');
const preparation = 'Run npm --prefix tools/testing ci --ignore-scripts --no-audit --no-fund from the repository root.';

function loadDependency(specifier) {
  try {
    return testRequire(specifier);
  } catch (cause) {
    if (cause.code !== 'MODULE_NOT_FOUND') throw cause;
    throw Object.assign(new Error(`Cannot load test dependency ${specifier}. ${preparation}`, {cause}), {
      code: 'mirally_test_dependency_missing',
    });
  }
}

function loadPlaywright() {
  let specifier = process.env.MIRALLY_PLAYWRIGHT?.trim() || 'playwright-core';
  if (specifier.startsWith('file:')) specifier = fileURLToPath(specifier);
  else if (!isAbsolute(specifier) && /^\.{1,2}[\\/]/.test(specifier)) specifier = resolve(root, specifier);
  return loadDependency(specifier);
}

function loadLinkedom() {
  return loadDependency('linkedom');
}

module.exports = {loadPlaywright, loadLinkedom};
