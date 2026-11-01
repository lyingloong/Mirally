import {loadPlaywright} from './testing/dependencies.cjs';
/** Actual retained sidebar/header menus; pass a disposable --test-mode runtime.json. */
import assert from 'node:assert/strict';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const {chromium} = loadPlaywright();
const runtime=JSON.parse(await readFile(process.argv[2],'utf8'));
assert.equal(runtime.mode,'test','Conversation organization acceptance requires an isolated test Host');
const output=resolve(process.argv[3]??join(root,'.artifacts/session-organization'));
await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(15000);
const errors=[],checks=[];page.on('pageerror',error=>errors.push(error.message));
const ready=()=>page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
const active='openclaw-chat-pane.chat-pane-cache__pane--active';
async function api(path,body,method='GET') {
  const response=await page.request.fetch(runtime.url+path,{method,headers:{authorization:'Bearer '+runtime.token},...(body===undefined?{}:{data:body})});
  assert.ok(response.ok(),`${method} ${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}
async function until(check,label) {
  const deadline=Date.now()+15000;
  while(Date.now()<deadline){const value=await check();if(value)return value;await delay(30);}
  throw new Error('Timed out: '+label);
}
let key,id;
const row=()=>page.locator(`openclaw-app-sidebar .sidebar-recent-session[data-session-key="${key}"]`);
const metadata=async()=> (await api('/api/sessions?archived=all')).sessions.find(value=>value.id===id);
async function selectSession(){await page.evaluate(key=>document.querySelector('openclaw-app-shell').selectChatSession(key,'main'),key);await page.locator(active+' textarea').waitFor();}
async function sidebarAction(value) {
  await row().click({button:'right'});
  const item=page.locator(`openclaw-session-menu wa-dropdown-item[value="${value}"]`);
  await item.waitFor();assert.equal(await item.getAttribute('disabled'),null,`${value} must be enabled`);await item.click();
}
async function headerAction(value) {
  await page.locator(active+' .chat-header-session-menu__trigger').click();
  const item=page.locator(active+` openclaw-chat-header-session-menu wa-dropdown-item[value="${value}"]`);
  await item.waitFor();assert.equal(await item.getAttribute('disabled'),null,`${value} must be enabled`);await item.click();
}
async function submitRename(label) {
  const input=page.locator('openclaw-modal-dialog input[name=value]');await input.waitFor();await input.fill(label);await input.press('Enter');
  await input.waitFor({state:'detached'});
}
async function statusFilter(value) {
  await page.locator('openclaw-app-sidebar button.sidebar-session-sort').first().click();
  await page.locator(`wa-dropdown.sidebar-session-sort-menu wa-dropdown-item[value="status:${value}"]`).click();
}
try {
  await page.goto(runtime.url+'/new#token='+runtime.token);await ready();
  const created=await page.evaluate(()=>window.__mirallyHost.client.request('sessions.create',{displayName:'会话组织验收',idempotencyKey:crypto.randomUUID()}));
  key=created.key;id=key.replace(/^agent:main:/,'');await selectSession();await row().waitFor();
  const original=await metadata();assert.equal(original.metadata_version,1);
  await sidebarAction('rename');await submitRename('侧栏改名后的会话');
  await until(async()=> (await metadata()).title==='侧栏改名后的会话','sidebar rename');
  await row().filter({hasText:'侧栏改名后的会话'}).waitFor();checks.push('original sidebar rename and refreshed title');

  await headerAction('rename');
  const inlineRename=page.locator(active+' input.chat-pane__session-title-input');
  await inlineRename.waitFor();await inlineRename.fill('标题菜单改名后的会话');await inlineRename.press('Enter');await inlineRename.waitFor({state:'detached'});
  await until(async()=> (await metadata()).title==='标题菜单改名后的会话','header rename');
  await sidebarAction('toggle-pin');await until(async()=> (await metadata()).pinned,'pin metadata');
  await page.waitForFunction(key=>document.querySelector(`.sidebar-recent-session[data-session-key="${key}"]`)?.classList.contains('session-row-host--pinned'),key);
  checks.push('original header rename and sidebar pin presentation');
  await page.reload();await ready();await row().filter({hasText:'标题菜单改名后的会话'}).waitFor();
  assert.equal((await metadata()).session_id,original.session_id);checks.push('reload preserves title, pin and native conversation');

  await selectSession();await sidebarAction('rename');
  const draft=page.locator('openclaw-modal-dialog input[name=value]');await draft.fill('不能覆盖并发修改');
  const beforeConflict=await metadata();
  await api('/api/sessions/'+id,{expected_version:beforeConflict.metadata_version,patch:{title:'另一客户端确认的标题'}},'PATCH');
  await draft.press('Enter');await page.locator('openclaw-modal-dialog [role=alert]').waitFor();
  assert.equal(await draft.inputValue(),'不能覆盖并发修改');assert.equal((await metadata()).title,'另一客户端确认的标题');
  await page.keyboard.press('Escape');await draft.waitFor({state:'detached'});checks.push('stale rename is visibly rejected and retains draft');

  await sidebarAction('toggle-archived');await until(async()=> (await metadata()).archived,'archive');
  await row().waitFor({state:'detached'});
  await page.locator('.app-toast__action').click();await until(async()=> !(await metadata()).archived,'archive undo');
  await row().waitFor();assert.equal((await metadata()).pinned,true);checks.push('archive leaves active roster; Undo uses new metadata version');

  await sidebarAction('toggle-archived');await until(async()=> (await metadata()).archived,'second archive');
  await row().waitFor({state:'detached'});await statusFilter('archived');await row().waitFor();
  await page.screenshot({path:join(output,'archived-menu.png')});
  await sidebarAction('toggle-archived');await until(async()=> !(await metadata()).archived,'restore from archived roster');
  await row().waitFor({state:'detached'});await statusFilter('active');await row().waitFor();checks.push('archived roster and original restore menu');

  await sidebarAction('toggle-pin');await until(async()=> !(await metadata()).pinned,'unpin');
  await selectSession();
  assert.equal(await page.locator(active+' .chat-controls__permission-picker').count(),0,'Metadata patch cannot enable unsupported permissions');
  await page.reload();await ready();await row().waitFor();
  const final=await metadata();assert.equal(final.archived,false);assert.equal(final.pinned,false);assert.equal(final.session_id,original.session_id);
  assert.deepEqual(errors,[]);checks.push('unpin, final reload, stable identity and no page errors');
  await page.screenshot({path:join(output,'session-organization.png')});
  const result={passed:true,key,metadata:final,checks,errors};await writeFile(join(output,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
} catch(error) {
  await page.screenshot({path:join(output,'failure.png')}).catch(()=>{});
  await writeFile(join(output,'result.json'),JSON.stringify({passed:false,key,checks,errors,error:String(error)},null,2)+'\n');
  console.error((await page.locator('body').innerText()).slice(-4000));throw error;
} finally {await browser.close();}
