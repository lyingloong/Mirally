/** Original Mirally work/companion/settings surfaces on a real isolated Host. */
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(join(root,'integrations/openclaw/package.json'));
const {chromium}=require(process.env.MIRALLY_PLAYWRIGHT ?? 'playwright-core');
const runtime=JSON.parse(await readFile(process.argv[2],'utf8')); assert.equal(runtime.mode,'test');
const output=resolve(process.argv[3]??join(root,'.artifacts/framework-experience')); await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}}); page.setDefaultTimeout(15000);
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const active='openclaw-chat-pane.chat-pane-cache__pane--active';
async function ready(){await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');}
try {
  await page.goto(runtime.url+'/new#token='+runtime.token);await ready();
  const visitor=page.locator('mirally-composer-companion');
  await visitor.locator('[data-mirally-mascot=otter]').waitFor();
  assert.equal(await page.locator('openclaw-lobster-pet').count(),0,'new-session visitor uses the Mirally companion');
  assert.ok(await visitor.locator('img').evaluate(image=>image.complete&&image.naturalWidth>0),'the actual companion resource loads');
  await page.screenshot({path:join(output,'composer-companion.png')});
  // Native client factory only; no shadow session, fake DOM or fixture state.
  await page.evaluate(async()=>{const ctx=document.querySelector('openclaw-app').context;const r=await ctx.gateway.snapshot.client.request('sessions.create',{displayName:'原版体验验收',idempotencyKey:crypto.randomUUID()});document.querySelector('openclaw-app-shell').selectChatSession(r.key,'main');});
  await page.locator(active+' textarea[aria-label="聊天输入框"]').waitFor();
  const pane=page.locator(active), composer=pane.locator('textarea[aria-label="聊天输入框"]');
  await composer.fill('保留在聊天里的中文草稿');
  await pane.locator('input.agent-chat__file-input').setInputFiles([{name:'工作材料.txt',mimeType:'text/plain',buffer:Buffer.from('真实工作材料：保留原版体验')}]);
  await pane.locator('.chat-attachment-thumb').waitFor();
  await page.evaluate(()=>__mirallyWork.open());
  const work=page.locator('.mirally-work:not([hidden])');await work.waitFor();
  assert.equal(await work.locator('.mirally-work__cards [data-workflow]').count(),10);
  assert.equal(await work.locator('[data-output]').count(),4);
  await work.locator('[data-workflow=read]').click();
  await work.locator('[data-work=goal]').fill('只整理已提供材料');
  await work.locator('[data-output=reply]').click();
  await work.locator('[data-work=start]').click();
  await page.waitForFunction(()=>{const p=document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active');return p?.state?.chatMessages?.some(x=>x.role==='assistant')&&!p.runActivity?.working;});
  assert.equal(await composer.inputValue(),'保留在聊天里的中文草稿');
  assert.equal(await pane.locator('.chat-attachment-thumb').count(),0);
  await page.screenshot({path:join(output,'original-work.png')});
  await page.evaluate(()=>__mirallyWork.close());
  for(const form of ['rail','badge','pet']){
    await page.evaluate(async form=>{__mirallyCompanion.setForm(form);await __mirallyCompanion.dock();},form);
    await page.waitForFunction(form=>document.documentElement.dataset.mirallyMode==='companion'&&document.querySelector('.mirally-companion').dataset.form===form,form);
    await page.screenshot({path:join(output,form+'.png')});
    await page.evaluate(()=>__mirallyCompanion.workspace());
    await page.waitForFunction(()=>document.documentElement.dataset.mirallyMode!=='companion');
  }
  await page.evaluate(()=>{__mirallyCompanion.setPet('fox');__mirallyCompanion.setForm('pet');});
  await page.waitForFunction(()=>__mirallyLegacyHost.getUiPreference('mirally.companion.pet')==='fox');
  await page.reload();await ready();
  assert.equal(await page.evaluate(()=>__mirallyLegacyHost.getUiPreference('mirally.companion.pet')),'fox');
  assert.equal(await page.evaluate(()=>__mirallyLegacyHost.getUiPreference('mirally.companion.form')),'pet');
  await page.evaluate(()=>__mirallyFramework.showPage('activity'));
  await page.locator('.mirally-activity').waitFor();
  await page.waitForFunction(()=>{const e=document.querySelector('.mirally-activity [data-status=collector]');return e&&e.textContent!=='—';});
  await page.screenshot({path:join(output,'original-activity.png')});
  assert.equal(await page.locator('.mirally-activity [data-action=set-enabled]').getAttribute('aria-checked'),'false');
  await page.evaluate(()=>__mirallyFramework.showPage('memory'));
  await page.locator('#mirally-memory-browser').waitFor();
  await page.waitForFunction(()=>!document.querySelector('#mirally-memory-browser [data-memory-slot=notice]')?.textContent.includes('正在读取'));
  await page.screenshot({path:join(output,'original-memory.png')});
  await page.evaluate(()=>document.querySelector('openclaw-app').context.navigate('appearance',{search:''}));
  await page.locator('wa-radio[value=dark]').click();
  await page.waitForFunction(()=>document.querySelector('openclaw-app').context.theme.resolvedMode==='dark');
  await page.screenshot({path:join(output,'original-dark-appearance.png')});
  await page.reload();await ready();
  assert.equal(await page.evaluate(()=>document.querySelector('openclaw-app').context.theme.resolvedMode),'dark');
  await page.locator('wa-radio[value=light]').click();
  await page.waitForFunction(()=>document.querySelector('openclaw-app').context.theme.resolvedMode==='light');
  assert.deepEqual(errors,[]);
  console.log('PASS retained Mirally surfaces: actual otter composer visitor, original 10 workflows/4 outputs, real materials and native task, chat draft preservation, 3 companion forms, server-owned pet preference/reload, actual Activity/Memory pages without capture, original appearance controls and dark-mode reload');
}catch(error){console.error('Page errors:',errors);console.error((await page.locator('body').innerText()).slice(-2000));await page.screenshot({path:join(output,'failure.png')}).catch(()=>{});throw error;}finally{await browser.close();}
