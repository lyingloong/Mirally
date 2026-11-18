/**
 * Verify the built desktop settings and phone web UI against a disposable test-mode Host.
 * node tools/verify_framework_mobile_browser.mjs <test-mode runtime.json> [output-directory]
 */
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdir, readFile} from 'node:fs/promises';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadPlaywright} from './testing/dependencies.cjs';

const runtimePath = process.argv[2];
assert.ok(runtimePath, 'Pass the runtime.json of a disposable --test-mode Host.');
const runtime = JSON.parse(await readFile(runtimePath, 'utf8'));
assert.equal(runtime.mode, 'test', 'Requires a disposable --test-mode Host.');
assert.ok(typeof runtime.token === 'string' && runtime.token, 'Missing test Host token.');

function localUrl(value) {
  const url = new URL(value);
  assert.ok(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
    && !url.username && !url.password, 'Requires a loopback HTTP address.');
  return url;
}

const host = localUrl(runtime.url);
async function desktopRequest(path, body) {
  const response = await fetch(new URL(path, host), {
    method: body === undefined ? 'GET' : 'POST',
    headers: {Authorization: 'Bearer ' + runtime.token, 'Content-Type': 'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    redirect: 'error',
  });
  assert.ok(response.ok, path + ' returned HTTP ' + response.status);
  const value = await response.json();
  assert.ok(!value.error, path + ' returned a product error.');
  return value.result ?? value;
}
assert.equal((await desktopRequest('/api/health')).mode, 'test', 'Host did not confirm test mode.');

const tag = randomUUID();
const root = fileURLToPath(new URL('../', import.meta.url));
const output = resolve(process.argv[3] ?? join(root, 'output/tmp/verify-framework-mobile-browser', tag));
await mkdir(output, {recursive: true});
const {chromium} = loadPlaywright();
const browser = await chromium.launch({channel: 'msedge', headless: true});
const checks = [];
const errors = [];
const allowedOrigins = new Set([host.origin]);
const pass = label => { checks.push(label); console.log('PASS', label); };

try {
  const desktopContext = await browser.newContext({viewport: {width: 1280, height: 940}, serviceWorkers: 'block'});
  const phoneContext = await browser.newContext({
    viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, serviceWorkers: 'block',
  });
  for (const context of [desktopContext, phoneContext]) {
    await context.route('**/*', route => allowedOrigins.has(new URL(route.request().url()).origin)
      ? route.continue() : route.abort());
  }
  const desktop = await desktopContext.newPage();
  const phone = await phoneContext.newPage();
  for (const page of [desktop, phone]) {
    page.setDefaultTimeout(20_000);
    page.on('pageerror', error => errors.push(error.message));
  }

  const initialTitle = '手机会话验收 ' + tag;
  const desktopText = '电脑发送的隔离验收消息';
  const phoneText = '手机发送的隔离验收消息';
  const session = await desktopRequest('/api/sessions', {title: initialTitle});
  assert.ok(session.id, 'Could not create a real conversation.');
  const sessionPath = '/api/sessions/' + encodeURIComponent(session.id);
  await desktopRequest(sessionPath + '/messages', {commandId: randomUUID(), text: desktopText});

  async function openDesktopPhoneSettings() {
    await desktop.goto(host.origin + '/settings/channels#token=' + encodeURIComponent(runtime.token));
    const details = desktop.getByTestId('mirally-mobile-options');
    await details.locator('summary').click();
    return details;
  }

  const settings = await openDesktopPhoneSettings();
  const enabled = settings.getByRole('checkbox', {name: '启用手机网页'});
  await enabled.waitFor();
  await enabled.check();
  await settings.getByTestId('mobile-pair-create').click();
  const pairingLink = settings.getByRole('link', {name: '打开手机页面（本机预览）'});
  await pairingLink.waitFor();
  const pairUrl = localUrl(await pairingLink.getAttribute('href'));
  const code = new URLSearchParams(pairUrl.hash.slice(1)).get('pair');
  assert.ok(code, 'Desktop settings did not issue a pairing code.');
  allowedOrigins.add(pairUrl.origin);
  const deviceName = '手机网页验收 ' + tag.slice(0, 8);
  await phone.goto(pairUrl.href);
  await phone.getByLabel('设备名称').fill(deviceName);
  await phone.getByRole('button', {name: '配对并进入', exact: true}).click();
  const nav = phone.getByRole('navigation', {name: '主要导航'});
  await nav.waitFor();
  assert.equal(new URL(phone.url()).hash, '');
  const reused = await phone.request.post(pairUrl.origin + '/api/mobile/pair', {data: {code, device_name: deviceName}, maxRedirects: 0});
  assert.equal(reused.status(), 403);
  assert.equal((await reused.json()).error.code, 'pairing_invalid');
  pass('Desktop settings creates a one-use phone pairing and the paired page clears its URL fragment.');

  await phone.getByTestId('mobile-conversation').filter({hasText: initialTitle}).click();
  const transcript = phone.getByLabel('会话记录');
  await transcript.locator('[data-role="user"]').filter({hasText: desktopText}).getByLabel('消息来自电脑').waitFor();
  await transcript.locator('[data-role="assistant"]').filter({hasText: desktopText}).waitFor();
  const composer = phone.getByRole('textbox', {name: '消息内容'});
  await composer.fill(phoneText);
  await phone.getByRole('button', {name: '发送', exact: true}).click();
  await transcript.locator('[data-role="assistant"]').filter({hasText: phoneText}).waitFor();
  await phone.getByRole('button', {name: '停止回复', exact: true}).waitFor({state: 'hidden'});

  const renamed = '手机网页会话改名 ' + tag;
  await phone.getByText('管理会话', {exact: true}).click();
  await phone.getByRole('button', {name: '重命名', exact: true}).click();
  await phone.getByRole('textbox', {name: '会话名称'}).fill(renamed);
  await phone.getByRole('button', {name: '保存名称', exact: true}).click();
  await phone.getByRole('heading', {name: renamed, exact: true}).waitFor();
  const conversation = async () => {
    const {sessions} = await desktopRequest('/api/sessions?archived=all');
    return sessions.find(item => item.id === session.id);
  };
  assert.equal((await conversation()).title, renamed);
  await phone.getByRole('button', {name: '归档会话', exact: true}).click();
  await phone.getByRole('button', {name: '恢复会话', exact: true}).waitFor();
  await composer.waitFor({state: 'hidden'});
  assert.equal((await conversation()).archived, true);
  await phone.getByRole('button', {name: '恢复会话', exact: true}).click();
  await composer.waitFor();
  assert.equal((await conversation()).archived, false);

  await desktop.goto(host.origin + '/chat/main/' + encodeURIComponent(session.id) + '#token=' + encodeURIComponent(runtime.token));
  const desktopMessage = desktop.locator('.chat-group.user').filter({hasText: phoneText});
  await desktopMessage.locator('.mirally-message-origin').getByText('手机网页', {exact: true}).waitFor();
  pass('Desktop and phone share real history, one reply, conversation metadata, and input origin.');

  async function expectNarrowLayout() {
    for (const width of [320, 390, 736]) {
      await phone.setViewportSize({width, height: 844});
      const bounds = await phone.evaluate(() => ({
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
      }));
      assert.ok(Math.abs(bounds.client - width) <= 1 && bounds.scroll <= bounds.client + 1,
        JSON.stringify({width, ...bounds}));
    }
  }
  await expectNarrowLayout();

  await nav.getByRole('button', {name: '设置', exact: true}).click();
  await phone.getByRole('combobox', {name: '保存到'}).selectOption('device');
  await phone.getByRole('combobox', {name: '会话排序'}).selectOption('pinned');
  await phone.getByText('显示偏好已保存。', {exact: true}).waitFor();
  const inboxPreference = phone.locator('.mirally-mobile-nav-setting').filter({hasText: '消息'});
  await inboxPreference.getByRole('checkbox').uncheck();
  await nav.getByRole('button', {name: '更多', exact: true}).click();
  await phone.getByRole('group', {name: '更多入口'}).getByRole('button', {name: '消息', exact: true}).click();
  await phone.getByRole('heading', {name: '消息', exact: true, level: 1}).waitFor();
  await phone.reload();
  await phone.getByRole('heading', {name: '会话', exact: true, level: 1}).waitFor();
  await nav.getByRole('button', {name: '设置', exact: true}).click();
  await phone.getByRole('heading', {name: '设置', exact: true, level: 1}).waitFor();
  await phone.waitForFunction(() => [...document.querySelectorAll('label')].some(label =>
    label.textContent.includes('会话排序') && label.querySelector('select')?.value === 'pinned'));
  assert.equal(await nav.getByRole('button', {name: '消息', exact: true}).count(), 0);
  await expectNarrowLayout();
  await phone.setViewportSize({width: 390, height: 844});
  await phone.emulateMedia({colorScheme: 'dark'});
  await phone.screenshot({path: join(output, 'phone-settings-dark.png'), fullPage: true});
  pass('Per-device preferences survive reload, hidden navigation still opens, and phone layouts fit 320, 390 and 736px.');

  const managedSettings = await openDesktopPhoneSettings();
  const device = managedSettings.locator('.mirally-mobile-device').filter({hasText: deviceName});
  await device.getByRole('button', {name: '撤销连接', exact: true}).click();
  await device.getByRole('button', {name: '确认撤销', exact: true}).click();
  await device.getByText('已撤销', {exact: false}).waitFor();
  await phone.reload();
  await phone.getByRole('alert').filter({hasText: '手机连接已失效'}).waitFor();
  await phone.getByLabel('配对码').waitFor();
  assert.equal(await nav.count(), 0);
  pass('Revoking the device in desktop settings returns its phone to pairing.');
  assert.deepEqual(errors, [], 'Browser page errors');
  console.log(JSON.stringify({passed: checks.length, checks, output}));
} finally {
  await browser.close();
}
