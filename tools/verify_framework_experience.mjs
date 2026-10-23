/** Retained Mirally surfaces against a real isolated host; no desktop capture. */
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(join(root, 'integrations/openclaw/package.json'));
const {chromium} = require(process.env.MIRALLY_PLAYWRIGHT ?? 'playwright-core');
const runtime = JSON.parse(await readFile(process.argv[2], 'utf8'));
assert.equal(runtime.mode, 'test');
const output = resolve(process.argv[3] ?? join(root, '.artifacts/framework-experience'));
await mkdir(output, {recursive: true});
const browser = await chromium.launch({channel: 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 1280, height: 900}});
const errors = []; page.on('pageerror', error => errors.push(error.message));
async function newChat() { const old = await page.evaluate(() => __mirallyFramework.state.current?.id); await page.locator('#new-chat').click(); await page.waitForFunction(old => __mirallyFramework.state.current?.id && __mirallyFramework.state.current.id !== old && !__mirallyFramework.state.historyLoading, old); }
try {
  await page.goto(runtime.url + '/#token=' + runtime.token);
  await page.locator('#connection.ready').waitFor();
  await newChat();
  const original = await page.evaluate(() => __mirallyFramework.state.current.id);
  await page.locator('#message').fill('保留在对话里的中文草稿');
  await page.locator('#file-input').setInputFiles([{name: '中文材料.txt', mimeType: 'text/plain', buffer: Buffer.from('真实材料：迁移之前的产品体验。')}]);
  await page.waitForFunction(() => __mirallyFramework.state.attachments[0]?.readingState === 'ready');
  assert.equal(await page.locator('#attachments').getByText('中文材料.txt', {exact: false}).count(), 1);
  const material = await page.evaluate(() => __mirallyFramework.state.attachments[0].id);
  await newChat();
  assert.equal(await page.locator('#message').inputValue(), '');
  assert.equal(await page.locator('#attachments').textContent(), '');
  const rejected = await page.evaluate(async id => {
    const r = await fetch(`/api/sessions/${__mirallyFramework.state.current.id}/messages`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({commandId: crypto.randomUUID(), text: 'cross session', materials: [id]})}); return r.status;
  }, material);
  assert.equal(rejected, 500);
  await page.evaluate(id => __mirallyFramework.selectSession(id), original);
  assert.equal(await page.locator('#message').inputValue(), '保留在对话里的中文草稿');
  await page.locator('nav [data-page=work]').click();
  await page.locator('.mirally-work:not([hidden])').waitFor();
  assert.equal(await page.locator('.mirally-work__cards [data-workflow]').count(), 10);
  assert.equal(await page.locator('[data-output]').count(), 4);
  await page.locator('[data-workflow=read]').click();
  await page.locator('[data-work=goal]').fill('只整理已提供的材料');
  await page.locator('[data-output=reply]').click();
  await page.screenshot({path: join(output, 'work.png')});
  await page.locator('[data-work=start]').click();
  await page.waitForFunction(() => __mirallyFramework.state.messages.some(x => x.role === 'assistant') && !__mirallyFramework.state.busy);
  assert.equal(await page.locator('#message').inputValue(), '保留在对话里的中文草稿');
  assert.equal(await page.evaluate(() => __mirallyFramework.state.attachments.length), 0);
  await page.evaluate(() => __mirallyWork.close());
  await page.locator('#continue-work').click();
  assert.match(await page.locator('#message').inputValue(), /请接着/);
  for (const form of ['rail', 'badge', 'pet']) {
    await page.evaluate(async form => { __mirallyCompanion.setForm(form); await __mirallyCompanion.dock(); }, form);
    await page.waitForFunction(form => document.documentElement.dataset.mirallyMode === 'companion' && document.querySelector('.mirally-companion').dataset.form === form, form);
    await page.screenshot({path: join(output, `${form}.png`)});
    await page.evaluate(() => __mirallyCompanion.workspace());
  }
  await page.evaluate(() => { __mirallyCompanion.setPet('fox'); __mirallyCompanion.setForm('pet'); });
  await page.waitForFunction(() => __mirallyFramework.state.uiPreferences['mirally.companion.pet'] === 'fox');
  await page.reload(); await page.locator('#connection.ready').waitFor();
  assert.equal(await page.evaluate(() => __mirallyFramework.state.uiPreferences['mirally.companion.pet']), 'fox');
  assert.equal(await page.evaluate(() => __mirallyFramework.state.uiPreferences['mirally.companion.form']), 'pet');
  await page.locator('nav [data-page=activity]').click();
  await page.getByText('活动范围、采集控制与原始记录', {exact: true}).click();
  assert.equal(await page.locator('#fx-capture-dialog').count(), 1);
  await page.screenshot({path: join(output, 'activity.png')});
  await page.locator('nav [data-page=memory]').click();
  await page.getByText('按来源与类型浏览记忆 / 查看候选和完整正文', {exact: true}).click();
  await page.screenshot({path: join(output, 'memory.png')});
  assert.deepEqual(errors, []);
  console.log('PASS retained experience: 10 workflows / 4 outputs, real material upload and native send, conversation drafts/ownership, editable continuation, 3 companion forms, fox preference/reload, advanced Activity/Memory UI');
} catch (error) {
  console.error('UI errors:', errors); console.error('Notice:', await page.locator('#notice').textContent());
  await page.screenshot({path: join(output, 'failure.png')}); throw error;
} finally { await browser.close(); }
