import {loadPlaywright} from './testing/dependencies.cjs';
/** Read-only browser acceptance; called by verify_framework.py on its disposable loopback-provider Host. */
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const {chromium} = loadPlaywright();
const runtime=JSON.parse(await readFile(process.argv[2],'utf8'));
assert.equal(new URL(runtime.url).hostname,'127.0.0.1');
const id=process.argv[3], artifact=process.argv[4]; assert.ok(id&&artifact);
const output=join(root,'.artifacts/framework-files');await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(15000);
const errors=[];page.on('pageerror',error=>errors.push(error.stack || error.message));
try {
 await page.goto(runtime.url+'/chat/main/'+encodeURIComponent(id)+'#token='+runtime.token);
 await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway.snapshot.phase==='connected');
 const pane=page.locator('openclaw-chat-pane.chat-pane-cache__pane--active');
 await pane.locator('.chat-header-session-menu__trigger').click();
 const files=pane.locator('wa-dropdown-item[value="quick:panels:session-files"]');
 await files.locator('..').hover();await files.click();
 const panel=page.locator('openclaw-chat-files-panel');await panel.waitFor();
 await panel.getByText('迁移成果.md',{exact:false}).first().click();
 await page.waitForFunction(()=>document.querySelector('openclaw-chat-files-panel')?.textContent.includes('实际交付'));
 await page.screenshot({path:join(output,'original-files.png')});
 const receipts=await page.evaluate(async({id,artifact})=>{
   const c=document.querySelector('openclaw-app').context.gateway.snapshot.client;
   const list=await c.request('artifacts.list',{sessionKey:'agent:main:'+id});
   const download=await c.request('artifacts.download',{artifactId:artifact,sessionKey:'agent:main:'+id});
   const url=download.url??download.downloadUrl;
   if(!url)throw new Error('No actual download URL');
   const response=await fetch(url);return {list,download,text:await response.text(),status:response.status};
 },{id,artifact});
 assert.equal(receipts.status,200);assert.match(receipts.text,/实际交付/);
 assert.equal(receipts.download.encoding,'base64');
 assert.match(Buffer.from(receipts.download.data,'base64').toString('utf8'),/实际交付/);
 await panel.evaluate(node=>node.onSelect(null));
 const artifactGroup=panel.locator('details').last();
 if(await artifactGroup.getAttribute('open')===null) await artifactGroup.locator('summary').click();
 await artifactGroup.getByText('迁移成果.md',{exact:false}).first().click();
 await page.waitForFunction(()=>document.querySelector('openclaw-chat-files-panel')?.textContent.includes('实际交付'));
 assert.ok(!(await panel.innerText()).includes('Open artifact'));
 await page.screenshot({path:join(output,'original-artifact-preview.png')});
 // Work output authorization must survive the native durable outbox, not a
 // temporary conversation flag. Fail the first HTTP send, then reload/retry.
 const retryKey=await page.evaluate(async()=>{const c=document.querySelector('openclaw-app').context.gateway.snapshot.client;const r=await c.request('sessions.create',{displayName:'文件授权重试验收',idempotencyKey:crypto.randomUUID()});document.querySelector('openclaw-app-shell').selectChatSession(r.key,'main');return r.key;});
 await page.waitForFunction(key=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active')?.state?.sessionKey===key,retryKey);
 await pane.locator('textarea[aria-label="聊天输入框"]').waitFor();
 const sends=[];
 await page.route('**/api/sessions/*/messages',async route=>{sends.push(route.request().postDataJSON());if(sends.length===1)await route.abort('connectionfailed');else await route.continue();});
 await page.evaluate(()=>__mirallyWork.open());
 const work=page.locator('.mirally-work:not([hidden])');await work.waitFor();
 await work.locator('[data-workflow=custom]').click();
 await work.locator('[data-work=goal]').fill('create deliverable');
 await work.locator('[data-output=document]').click();
 await work.locator('[data-work=start]').click();
 await page.waitForFunction(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active')?.state?.chatQueue?.some(x=>x.mirallyAllowFileWrite===true));
 assert.equal(sends[0]?.allow_file_write,true);
 await page.reload();
 await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway.snapshot.phase==='connected');
 await page.waitForTimeout(500);
 const retry=pane.locator('.chat-send-status__retry').first();
 await retry.waitFor({state:'visible',timeout:3000}).catch(()=>{});
 if(await retry.isVisible())await retry.click();
 let delivered=false;
 for(let n=0;n<100;n++){
   delivered=await page.evaluate(async key=>{const p=document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active');if(p?.state?.sessionKey!==key)return false;const id=__mirallyHost.conversationIdForSession(key);return (await __mirallyHost.request('/api/artifacts?conversation_id='+encodeURIComponent(id))).artifacts.length>0;},retryKey);
   if(delivered)break;await page.waitForTimeout(200);
 }
 assert.equal(delivered,true,'Retried Work must create an actual artifact');
 assert.ok(sends.length>=2);assert.equal(sends[1].commandId,sends[0].commandId);
 assert.equal(sends[1].allow_file_write,true);
 await page.screenshot({path:join(output,'original-work-retry.png')});
 // Both accepted local hostnames must install the same presentation scripts.
 await page.goto(runtime.url.replace('127.0.0.1','localhost')+'/new#token='+runtime.token);
 await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway.snapshot.phase==='connected'&&!!window.__mirallyWork&&!!window.__mirallyCompanion);

 assert.deepEqual(errors,[]);
 console.log('PASS original Files/Work UI: real tool artifacts, file and artifact previews/download, durable file permission after failed send/reload/retry, localhost presentation');
}catch(error){console.error((await page.locator('body').innerText()).slice(-3000));await page.screenshot({path:join(output,'failure.png')}).catch(()=>{});throw error;}finally{await browser.close();}
