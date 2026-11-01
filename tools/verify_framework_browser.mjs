import {loadPlaywright} from './testing/dependencies.cjs';
/** Real retained UI acceptance against an isolated test-mode Mirally Host. */
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const {chromium} = loadPlaywright();
const runtime = JSON.parse(await readFile(process.argv[2], 'utf8'));
assert.equal(runtime.mode, 'test', 'Requires an explicitly isolated --test-mode host');
const output = resolve(process.argv[3] ?? join(root, '.artifacts/framework-browser'));
await mkdir(output, {recursive:true});
const browser = await chromium.launch({channel:'msedge', headless:true});
const page = await browser.newPage({viewport:{width:1280,height:900}});
page.setDefaultTimeout(15000);
const errors=[]; page.on('pageerror', error=>errors.push(error.message));
const active = 'openclaw-chat-pane.chat-pane-cache__pane--active';
async function ready() { await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase === 'connected'); }
async function newChat() { await page.evaluate(()=>window.__mirallyFramework.showPage('chat')); await page.locator('.new-session-page__message').waitFor({state:'visible'}); }
async function idle() { await page.waitForFunction(()=>{const p=document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active'); return p?.state?.chatMessages?.some(m=>m.role==='assistant') && !p.runActivity?.working;}); }
try {
  // Save the Mirally-owned form through the same real product transport.
  await page.goto(runtime.url+'/settings/model-providers#token='+runtime.token); await ready();
  await page.getByTestId('mirally-settings-model-id').waitFor();
  await page.getByTestId('mirally-settings-model-base-url').fill('http://127.0.0.1:1/v1');
  await page.getByTestId('mirally-settings-model-id').fill('offline-test');
  await page.getByTestId('mirally-settings-model-key').fill('isolated-fixture-secret');
  await page.getByTestId('mirally-settings-model-save').click();
  await page.getByTestId('mirally-settings-notice').filter({hasText:'模型连接已保存'}).waitFor();
  assert.equal(await page.getByTestId('mirally-settings-model-key').inputValue(), '');
  await page.screenshot({path:join(output,'mirally-settings.png')});
  await newChat();
  assert.equal(await page.locator('[data-chat-permission-select]').count(),0,'new conversations do not advertise unsupported host permission modes');
  const draft = '原版 **粗体** 与代码\n```js\nconst hello = 1;\n```';
  await page.locator('.new-session-page__message').fill(draft);
  await page.locator('.new-session-page__start-submit').click(); await idle();
  const pane=page.locator(active);
  assert.equal(await pane.locator('[data-chat-permission-select]').count(),0,'existing conversations do not advertise unsupported host permission modes');
  assert.equal(await pane.locator('.chat-group.user').count(),1,'optimistic user reconciles with native history');
  assert.equal(await pane.locator('.chat-group.assistant').count(),1);
  assert.ok(await pane.locator('.chat-group.assistant strong').count());
  assert.ok(await pane.locator('.chat-group.assistant pre code').count());
  await page.evaluate(()=>{window.__copiedCode=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>window.__copiedCode.push(value)}});});
  await pane.locator('.chat-group.assistant pre').first().hover();
  await pane.locator('.chat-group.assistant .code-block-copy').first().click();
  await page.waitForFunction(()=>window.__copiedCode.some(value=>value.includes('const hello = 1;')));

  const url=page.url();
  await page.screenshot({path:join(output,'original-chat.png')});
  await page.reload(); await ready(); await idle();
  assert.equal(await pane.locator('.chat-group.user').count(),1,'refresh does not duplicate user message');
  await pane.locator('textarea[aria-label="聊天输入框"]').fill('第二轮原版输入');
  await pane.locator('.chat-send-btn--send').click();
  await page.waitForFunction(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active')?.state?.chatMessages?.filter(m=>m.role==='assistant').length===2);
  await idle();
  assert.equal(await pane.locator('.chat-group.user').count(),2);
  // Original file controls: two simultaneous reads, remove one, attachments-only first turn.
  await newChat();
  const first=page.locator('openclaw-new-session-page');
  await first.locator('input.agent-chat__file-input').setInputFiles([
    {name:'移除材料.txt',mimeType:'text/plain',buffer:Buffer.from('不应发送')},
    {name:'中文材料.txt',mimeType:'text/plain',buffer:Buffer.from('验收材料只保留这一份')},
  ]);
  await first.locator('.chat-attachment-thumb').nth(1).waitFor();
  await first.locator('.chat-attachment-thumb').filter({hasText:'移除材料.txt'}).locator('button').click();
  assert.equal(await first.locator('.chat-attachment-thumb').count(),1);
  await first.locator('.new-session-page__start-submit').click(); await idle();
  assert.ok((await pane.innerText()).includes('验收材料只保留这一份'));
  assert.equal(await pane.locator('.chat-group.user').count(),1);
  assert.ok(!(await pane.innerText()).includes('不应发送'));
  // Switching uses the original app shell, retaining its own unsent draft.
  await pane.locator('textarea[aria-label="聊天输入框"]').fill('未发送草稿');
  const attachmentUrl=page.url();
  await page.goto(url); await ready(); await idle();
  assert.equal(await pane.locator('textarea[aria-label="聊天输入框"]').inputValue(),'');
  await page.goto(attachmentUrl); await ready(); await idle();
  assert.equal(await pane.locator('textarea[aria-label="聊天输入框"]').inputValue(),'未发送草稿');
  await page.setViewportSize({width:480,height:820});
  await page.screenshot({path:join(output,'original-narrow-chat.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  console.log('PASS retained original UI: actual settings save/secret clearing, unsupported permission modes absent, streaming and Markdown/code/copy, native receipt reconciliation, refresh/2 turns, attachment batch/removal/attachments-only send, session drafts and narrow layout');
} catch(error) {
  console.error('Page errors:', errors); console.error((await page.locator('body').innerText()).slice(-2500));
  await page.screenshot({path:join(output,'failure.png')}).catch(()=>{}); throw error;
} finally {await browser.close();}
