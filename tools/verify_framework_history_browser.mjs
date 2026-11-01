import {loadPlaywright} from './testing/dependencies.cjs';
/** Real retained UI history acceptance; use a configured isolated normal Host. */
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const {chromium} = loadPlaywright();
const runtime = JSON.parse(await readFile(process.argv[2], 'utf8'));
assert.equal(runtime.mode, 'normal', 'Real native tool history requires a configured isolated normal Host');
assert.ok(process.argv.includes('--live'), 'Provider calls require explicit --live and an isolated profile');
const output = resolve(process.argv[3] && !process.argv[3].startsWith('--') ? process.argv[3] : join(root, '.artifacts/history-browser'));
await mkdir(output, {recursive: true});
const browser = await chromium.launch({channel: 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 1280, height: 900}}); page.setDefaultTimeout(20000);
const errors = []; page.on('pageerror', error => errors.push(error.message));
const active = 'openclaw-chat-pane.chat-pane-cache__pane--active';
const ready = async target => target.waitForFunction(() => document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase === 'connected');
async function verify(target) {
  await ready(target);
  await target.waitForFunction(active => {
    const pane = document.querySelector(active);
    return pane?.state?.chatMessages?.some(row => row.role === 'assistant') && !pane.runActivity?.working
      && !!pane.querySelector('.chat-tool-msg-collapse--manual');
  }, active, {timeout: 20000});
  const pane = target.locator(active), image = pane.locator('.chat-group.user img.chat-message-image');
  await image.waitFor();
  await target.waitForFunction(active => {
    const img = document.querySelector(active)?.querySelector('.chat-group.user img.chat-message-image');
    return img?.complete && img.naturalWidth > 0;
  }, active);
  assert.equal(await image.count(), 1, 'One native image receipt renders once');
  assert.equal(await pane.locator('.chat-group.user').count(), 1, 'The submitted material turn remains one native user receipt');
  assert.ok((await pane.innerText()).includes('历史资料.txt'));
  const tool = pane.locator('.chat-tool-msg-collapse--manual').last(); await tool.waitFor();
  if (!await tool.locator('.chat-tool-msg-body').isVisible()) await tool.locator('button.chat-tool-msg-summary').click();
  await tool.locator('.chat-tool-msg-body').waitFor();
  assert.ok((await tool.locator('.chat-tool-msg-body').innerText()).includes('[]'), 'Real native empty reminders result survives the original tool card');
  assert.equal(await pane.locator('.chat-tool-msg-collapse--manual').count(),1,'One native tool invocation renders once');
  const model = await target.evaluate(async active => {
    const state = document.querySelector(active).state;
    const history=await window.__mirallyHost.client.request('chat.history',{sessionKey:state.sessionKey});
    return {users: history.messages.filter(row => row.role === 'user').map(row => row.id),
      tools: history.messages.filter(row => row.role === 'toolResult').map(row => ({id: row.id, content: row.content})),
      materials: history.messages.filter(row => row.role === 'user').flatMap(row => row.content).filter(block => ['image', 'attachment'].includes(block.type))};
  }, active);
  assert.equal(model.tools.length, 1, 'Durable tool and live echoes do not duplicate a native invocation');
  for (const block of model.materials) {
    const url = block.type === 'image' ? block.url : block.attachment.url;
    const response = await target.request.get(new URL(url, runtime.url).href);
    assert.equal(response.status(), 200); assert.ok((await response.body()).length > 0);
  }
  return model;
}
try {
  await page.goto(runtime.url + '/new#token=' + runtime.token); await ready(page);
  await page.evaluate(() => {
    const context = document.querySelector('openclaw-app').context;
    return context.gateway.snapshot.client.request('sessions.create', {displayName: 'Isolated material and tool history', idempotencyKey: crypto.randomUUID()})
      .then(result => document.querySelector('openclaw-app-shell').selectChatSession(result.key, 'main'));
  });
  const pane = page.locator(active); await pane.locator('textarea[aria-label="聊天输入框"]').waitFor();
  await pane.locator('input.agent-chat__file-input').setInputFiles([
    {name: '历史图片.png', mimeType: 'image/png', buffer: await readFile(join(root, 'mirally/assets/mirally-128.png'))},
    {name: '历史资料.txt', mimeType: 'text/plain', buffer: Buffer.from('这是只用于历史呈现验收的材料。')},
  ]);
  await page.waitForFunction(active => document.querySelector(active)?.state?.chatAttachments?.length === 2, active);
  await pane.locator('textarea[aria-label="聊天输入框"]').fill('Use the mirally_reminders tool to list my reminders. This isolated test profile has no reminders. Briefly report the real tool result. The attached image and text are test materials; no other action is requested.');
  await pane.locator('.chat-send-btn--send').click();
  const initial = await verify(page), url = page.url();
  await page.screenshot({path: join(output, 'live-history.png')});
  await page.reload(); assert.deepEqual(await verify(page), initial);
  await page.screenshot({path: join(output, 'refreshed-history.png')});
  const fresh = await browser.newContext(), newPage = await fresh.newPage(); newPage.on('pageerror', error => errors.push(error.message));
  await newPage.goto(url.split('#')[0] + '#token=' + runtime.token);
  assert.deepEqual(await verify(newPage), initial);
  await newPage.screenshot({path: join(output, 'fresh-client-history.png')}); await fresh.close();
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({passed: true, checks: ['actual image pixels and file bytes', 'actual tool result', 'reload retains stable receipts', 'fresh browser client retains history', 'no page errors']}, null, 2));
} catch (error) {
  console.error('History state:', await page.evaluate(active=>{const p=document.querySelector(active);return {roles:p?.state?.chatMessages?.map(row=>row.role),working:p?.runActivity?.working};},active));
  console.error('Page errors:', errors); console.error((await page.locator('body').innerText()).slice(-4000));
  await page.screenshot({path: join(output, 'failure.png')}).catch(() => {}); throw error;
} finally {await browser.close();}
