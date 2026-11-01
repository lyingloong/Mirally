import {loadPlaywright} from './testing/dependencies.cjs';
/** Real retained UI with a channel protocol fixture; never contacts WeChat. */
import assert from 'node:assert/strict';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const {chromium} = loadPlaywright();
const runtime = JSON.parse(await readFile(process.argv[2], 'utf8'));
assert.equal(runtime.mode, 'test', 'Requires an isolated --test-mode host');
const output = resolve(process.argv[3] ?? join(root, '.artifacts/framework-weixin-browser'));
await mkdir(output, {recursive: true});
const browser = await chromium.launch({channel: 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 1280, height: 920}});
page.setDefaultTimeout(15000);
const errors = [], calls = [], checks = [];
page.on('pageerror', error => errors.push(error.message));
const test = id => page.getByTestId(id);
let status = {state: 'unbound', enabled: false, bound: false, installation_id: 'browser-fixture', revision: 1,
  outbox: {pending: 0, unknown: 0, failed: 0}};
let failNext = '', remoteControl = false;
await page.route('**/api/rpc', async route => {
  const data = route.request().postDataJSON();
  if (data.method === 'settings.get') {
    const response=await route.fetch(), json=await response.json();
    json.result.weixin_remote_control_enabled=remoteControl;
    return route.fulfill({response,json});
  }
  if (data.method === 'settings.update' && typeof data.params?.patch?.weixin_remote_control_enabled === 'boolean') {
    assert.equal(status.bound,true,'fixture still requires a real local binding step');
    remoteControl=data.params.patch.weixin_remote_control_enabled;
    return route.fulfill({json:{result:{weixin_remote_control_enabled:remoteControl}}});
  }
  if (data.method === 'conversation.list') return route.fulfill({json: {result: {conversations: [
    {conversation_id: 'fixture-existing', title: '已有工作会话'}, {id: 'fixture-legacy', title: '另一会话'},
  ]}}});
  if (!data.method.startsWith('weixin.')) return route.continue();
  calls.push(data);
  if (failNext === data.method) {
    failNext = '';
    return route.fulfill({status: 409, json: {error: {code: 'weixin_scope_revoked', message: 'secret-token raw remote response'}}});
  }
  const method = data.method, params = data.params || {};
  if (method === 'weixin.login.start') status = {...status, state: 'waiting_scan',
    login: {id: 'qr-fixture', qr_content: 'https://example.invalid/mirally-offline-ui-fixture', expires_at: Date.now() + 300000}};
  if (method === 'weixin.login.cancel') {delete status.login; status.state = status.bound ? 'disconnected' : 'unbound';}
  if (method === 'weixin.login.verify') {assert.equal(params.code, 'ABC123'); delete status.login; status.state = 'awaiting_confirmation'; status.peer_id = '本人微信（测试）';}
  if (['weixin.bind', 'weixin.select', 'weixin.disconnect', 'weixin.reconnect', 'weixin.unbind'].includes(method)) {
    assert.equal(params.expected_revision, status.revision); status.revision++;
  }
  if (method === 'weixin.bind' || method === 'weixin.select') {
    status = {...status, state: 'connected', enabled: true, bound: true,
      conversation_id: params.conversation_id || 'fixture-new', conversation_title: params.conversation_id ? '已有工作会话' : '微信新会话'};
    delete status.login;
  }
  if (method === 'weixin.disconnect') status = {...status, state: 'disconnected', enabled: false};
  if (method === 'weixin.reconnect') status = {...status, state: 'connected', enabled: true};
  if (method === 'weixin.unbind') status = {state: 'unbound', enabled: false, bound: false,
    installation_id: 'browser-fixture', revision: status.revision, outbox: {pending: 0, unknown: 0, failed: 0}};
  return route.fulfill({json: {result: status}});
});
async function ready() {await page.waitForFunction(() => document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase === 'connected');}
async function settled() {await test('mirally-weixin-page').waitFor(); await page.waitForFunction(() => document.querySelector('[data-testid="mirally-weixin-page"]')?.getAttribute('aria-busy') === 'false');}
async function refresh() {await test('mirally-weixin-refresh').click(); await settled();}
async function navigate(route) {await page.evaluate(route => document.querySelector('openclaw-app').context.navigate(route), route);}
try {
  await page.goto(runtime.url + '/settings/channels#token=' + runtime.token); await ready(); await settled();
  await page.locator('a[href="/settings/channels"]').waitFor();
  assert.match(await test('mirally-weixin-page').innerText(), /尚未连接/);
  assert.ok(await page.locator('mirally-channels-page .settings-section').count());
  assert.ok(!(await page.locator('#control-ui-main').innerText()).includes('OpenClaw'));
  checks.push('actual Mirally settings navigation and shared design language');

  const entryText = await test('mirally-weixin-page').innerText();
  assert.match(entryText, /电脑需联网并保持 Mirally 运行/);
  const help=test('mirally-weixin-help'),web=test('mirally-mobile-options');
  assert.equal(await help.getAttribute('open'),null);assert.equal(await web.getAttribute('open'),null);
  assert.equal(await help.locator('code').first().isVisible(),false);
  await help.locator('summary').click();
  for (const command of ['/works', '/work-review', '/work-logs', '/work-notify']) assert.ok((await help.innerText()).includes(command));
  await help.locator('summary').click();
  await web.locator('summary').click();await web.locator('mirally-mobile-settings').waitFor({state:'visible'});await web.locator('summary').click();
  checks.push('Weixin setup remains primary; help and optional web controls expand independently');

  await test('mirally-weixin-login').evaluate(button => {button.click(); button.click();});
  await test('mirally-weixin-qr').waitFor(); await settled();
  assert.equal(calls.filter(call => call.method === 'weixin.login.start').length, 1);
  const qr = page.locator('wa-qr-code');
  await qr.locator('canvas').waitFor();
  assert.equal(await qr.evaluate(element => element.value), status.login.qr_content);
  const pixels = await qr.locator('canvas').evaluate(canvas => {
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let dark = 0, light = 0; for (let i = 0; i < data.length; i += 4) {if (data[i] < 32 && data[i + 3] > 0) dark++; if (data[i] > 220 && data[i + 3] > 0) light++;}
    return {dark, light};
  });
  assert.ok(pixels.dark > 100 && pixels.light > 100);
  assert.equal(await test('mirally-weixin-qr').locator('img').count(), 0);
  await page.screenshot({path: join(output, 'weixin-scan.png')});
  await test('mirally-weixin-login-cancel').click(); await settled();
  await test('mirally-weixin-login').waitFor();
  checks.push('client-side visible QR, duplicate click guarded, cancel login');

  await test('mirally-weixin-login').click(); await test('mirally-weixin-qr').waitFor();
  status = {...status, state: 'need_verifycode', login: {...status.login, needs_verify_code: true}};
  await refresh(); await test('mirally-weixin-code').fill('ABC123'); await test('mirally-weixin-verify').click();
  await test('mirally-weixin-bind').waitFor(); await settled();
  assert.equal(calls.filter(call => call.method === 'weixin.bind').length, 0, 'scan confirmation must not bind implicitly');
  await test('mirally-weixin-conversation').selectOption('fixture-existing');
  await page.screenshot({path: join(output, 'weixin-confirm.png')});
  await test('mirally-weixin-bind').click(); await test('mirally-weixin-unbind').waitFor(); await settled();
  assert.equal(status.conversation_id, 'fixture-existing');
  assert.equal(await test('mirally-weixin-conversation').inputValue(), 'fixture-existing');
  assert.equal(await test('mirally-weixin-code').count(), 0);
  await help.locator('summary').click();
  assert.match(await help.innerText(), /\/history/);assert.match(await help.innerText(), /菜单/);
  await help.locator('summary').click();
  const remote=test('mirally-weixin-remote-control').getByRole('switch');
  await test('mirally-weixin-remote-control').locator('wa-switch').click();await settled();assert.equal(await remote.isChecked(),true);
  await remote.focus();await page.keyboard.press('Space');await settled();assert.equal(await remote.isChecked(),false);
  checks.push('verification code, explicit local binding, existing conversation identity');

  await test('mirally-weixin-conversation').selectOption(''); await test('mirally-weixin-select').click(); await settled();
  assert.equal(status.conversation_id, 'fixture-new');
  assert.equal(await test('mirally-weixin-conversation').inputValue(), 'fixture-new');
  await test('mirally-weixin-disconnect').click(); await settled(); await test('mirally-weixin-reconnect').waitFor();
  await test('mirally-weixin-reconnect').click(); await settled(); await test('mirally-weixin-disconnect').waitFor();
  checks.push('new conversation selection, pause and reconnect');

  status.outbox = {pending: 1, unknown: 2, failed: 1}; status.last_error = 'private-token remote-body';
  await refresh();
  assert.match(await test('mirally-weixin-page').innerText(), /为避免重复发送/);
  assert.doesNotMatch(await test('mirally-weixin-page').innerText(), /private-token|remote-body/);
  failNext = 'weixin.disconnect'; await test('mirally-weixin-disconnect').click();
  await test('mirally-weixin-error').waitFor();
  assert.doesNotMatch(await test('mirally-weixin-page').innerText(), /secret-token|raw remote/);
  await page.waitForTimeout(2300); assert.ok(await test('mirally-weixin-error').isVisible());
  await refresh(); assert.equal(await test('mirally-weixin-error').count(), 0);
  await page.screenshot({path: join(output, 'weixin-connected.png')});
  checks.push('uncertain delivery summary, secret-free errors, mutation failure survives polling');

  const beforePoll = calls.filter(call => call.method === 'weixin.status').length;
  await page.waitForTimeout(2300);
  assert.ok(calls.filter(call => call.method === 'weixin.status').length > beforePoll);
  await page.evaluate(() => {Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'));});
  const hiddenCount = calls.length; await page.waitForTimeout(2300); assert.equal(calls.length, hiddenCount);
  await page.evaluate(() => {Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'));});
  await settled();
  checks.push('visible status polling and hidden-document timer cancellation');

  await test('mirally-weixin-unbind').click();
  assert.equal(calls.filter(call => call.method === 'weixin.unbind').length, 0);
  await page.getByRole('group', {name: '确认解除微信绑定'}).getByRole('button', {name: '取消', exact: true}).click();
  await test('mirally-weixin-unbind').click(); await test('mirally-weixin-unbind-confirm').click(); await settled();
  await test('mirally-weixin-login').waitFor(); assert.equal(status.bound, false);
  checks.push('local unbind confirmation and retained conversation explanation');

  await page.setViewportSize({width: 480, height: 820}); await refresh();
  await test('mirally-weixin-login').click(); await test('mirally-weixin-qr').waitFor(); await settled();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({path: join(output, 'weixin-narrow.png')});
  await navigate('profile'); await page.getByTestId('mirally-settings-profile').waitFor();
  assert.equal(await page.locator('mirally-channels-page').count(), 0);
  const detachedCount = calls.length; await page.waitForTimeout(2300); assert.equal(calls.length, detachedCount);
  checks.push('narrow settings layout and route-detach polling cleanup');
  assert.deepEqual(errors, []);
  await writeFile(join(output, 'report.json'), JSON.stringify({mode: 'protocol-fixture', realWechatCalls: 0, checks, pageErrors: errors}, null, 2) + '\n');
  console.log(`PASS ${checks.length} WeChat UI groups (actual browser; protocol fixture; no real WeChat requests)`);
} catch (error) {
  console.error('Page errors:', errors); console.error((await page.locator('body').innerText()).slice(-3500));
  await page.screenshot({path: join(output, 'failure.png')}).catch(() => {}); throw error;
} finally {await browser.close();}
