/** Presentation preferences persist independently of conversations and notify
 * existing clients through the same authenticated product event stream. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

test('capture exclusion accepts booleans, preserves other preferences and broadcasts only persisted changes', async t => {
  const home = await mkdtemp(join(tmpdir(), 'mirally capture 截图 '));
  const product = Object.create(MirallyProduct.prototype);
  Object.assign(product, {home, ready: true, token: 'capture-test-token', cookieName: 'capture-test-cookie', cookieToken: 'unused', clients: new Set()});
  const server = createServer((request, response) => void product.route(request, response).catch(error => {
    response.writeHead(error.status ?? 500); response.end(JSON.stringify({error: {code: error.code, message: error.message}}));
  }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  product.server = server; product.url = `http://127.0.0.1:${server.address().port}`;
  t.after(async () => {server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(home, {recursive: true, force: true});});
  const call = async input => {
    const response = await fetch(product.url + '/api/ui-preferences', {method: input ? 'POST' : 'GET',
      headers: {Authorization: 'Bearer capture-test-token', 'Content-Type': 'application/json'}, ...(input ? {body: JSON.stringify(input)} : {})});
    return {status: response.status, body: await response.json()};
  };
  const received = [];
  product.clients.add({write: value => received.push(JSON.parse(value.slice(6)))});
  assert.deepEqual((await call()).body.preferences, {});
  await call({key: 'mirally.window.closeBehavior', value: 'exit'});
  const key = 'mirally.companion.hideFromCapture';
  for (const value of [false, true, false]) {
    const result = await call({key, value});
    assert.equal(result.status, 200); assert.equal(result.body.preferences[key], value);
    assert.equal(result.body.preferences['mirally.window.closeBehavior'], 'exit');
    assert.equal(JSON.parse(await readFile(join(home, 'ui-preferences.json'), 'utf8'))[key], value);
    assert.deepEqual(received.at(-1), {type: 'ui-preference', key, value});
  }
  const count = received.length;
  for (const value of ['true', 'false', 0, 1, null, {}, []]) assert.equal((await call({key, value})).status, 400);
  assert.equal(received.length, count);
  assert.equal((await call()).body.preferences[key], false);
  assert.equal((await fetch(product.url + '/api/ui-preferences', {method: 'POST', body: JSON.stringify({key, value: true})})).status, 401);
  assert.equal((await call()).body.preferences[key], false);
});
