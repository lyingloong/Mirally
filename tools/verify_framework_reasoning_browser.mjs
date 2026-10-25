/** Actual thinking/tool presentation; production model use requires an explicit --live flag. */
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(join(root,'integrations/openclaw/package.json'));
const {chromium}=require(process.env.MIRALLY_PLAYWRIGHT??'playwright-core');
const runtime=JSON.parse(await readFile(process.argv[2],'utf8'));
assert.ok(runtime.mode==='test'||process.argv.includes('--live'),'Normal model requests require --live; use only an explicitly isolated profile.');
const output=resolve(process.argv[3]&&!process.argv[3].startsWith('--')?process.argv[3]:join(root,'.artifacts/reasoning-browser'));
await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(20000);
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const active='openclaw-chat-pane.chat-pane-cache__pane--active';
async function ready(){await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');}
async function idle(count){await page.waitForFunction(({active,count})=>{const p=document.querySelector(active);return p?.state?.chatMessages?.filter(x=>x.role==='assistant').length>=count&&!p.runActivity?.working;},{active,count},{timeout:60000});}
async function showReasoning(){
  const menu=page.locator(active+' openclaw-chat-header-session-menu');
  await menu.locator('button[slot=trigger]').click();
  const option=menu.locator('wa-dropdown-item[value="view:reasoning"]');
  if(!await option.evaluate(item=>item.checked)){
    await menu.locator('wa-dropdown-item').filter({has:page.locator('wa-dropdown-item[value="view:reasoning"]')}).hover();
    await option.click();
  }
  await page.keyboard.press('Escape');
}
try{
  await page.goto(runtime.url+'/new#token='+runtime.token);await ready();
  const session=await page.evaluate(async()=>{const c=document.querySelector('openclaw-app').context;const r=await c.gateway.snapshot.client.request('sessions.create',{displayName:'Isolated reasoning presentation check',idempotencyKey:crypto.randomUUID()});document.querySelector('openclaw-app-shell').selectChatSession(r.key,'main');return r.key;});
  await page.locator(active+' textarea[aria-label="聊天输入框"]').waitFor();
  await showReasoning();
  await page.evaluate(active=>{
    window.__reasoningPaints=[];
    const inspect=()=>{const p=document.querySelector(active);if(p?.runActivity?.working&&p.querySelector('.chat-thinking')?.textContent.trim())window.__reasoningPaints.push(p.querySelector('.chat-thinking').textContent);};
    const observer=new MutationObserver(inspect);observer.observe(document.querySelector(active),{subtree:true,childList:true,characterData:true});window.__reasoningPaintObserver=observer;
  },active);
  const pane=page.locator(active), input=pane.locator('textarea[aria-label="聊天输入框"]');
  await input.fill('Compute 137 times 29. Check it by breaking 29 into 30 minus 1, then reply with just the final integer.');
  await pane.locator('.chat-send-btn--send').click();await idle(1);
  await pane.locator('.chat-thinking').first().waitFor();
  assert.ok((await pane.locator('.chat-group.assistant').innerText()).includes('3973'));
  const livePaints=await page.evaluate(()=>window.__reasoningPaints.length);
  assert.ok(livePaints>0,'thinking must actually paint during the native stream');
  await page.screenshot({path:join(output,'actual-reasoning.png')});
  await page.reload();await ready();await idle(1);
  await pane.locator('.chat-thinking').first().waitFor();
  assert.equal(await pane.locator('.chat-group.user').count(),1,'reload retains a single native user turn');
  assert.equal(await pane.locator('.chat-group.assistant').count(),1,'transient reasoning is not persisted as a duplicate assistant');
  await input.fill('Use the mirally_reminders tool to list my reminders. This is an isolated test profile. Report its result briefly.');
  await pane.locator('.chat-send-btn--send').click();await idle(2);
  const tool=pane.locator('.chat-tool-msg-collapse--manual').last();
  await tool.waitFor();
  await tool.locator('button.chat-tool-msg-summary').click();
  await tool.locator('.chat-tool-msg-body').waitFor();
  assert.ok((await tool.locator('.chat-tool-msg-body').innerText()).includes('[]'),'expanded native tool result contains the actual empty reminders list');
  await page.screenshot({path:join(output,'actual-tool-result.png')});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,session,live_thinking_paints:livePaints,checks:['actual thinking stream paints','native thinking survives reload without duplicate turns','actual tool result renders','no page errors']},null,2));
}catch(error){console.error('Page errors:',errors);console.error((await page.locator('body').innerText()).slice(-3000));await page.screenshot({path:join(output,'failure.png')}).catch(()=>{});throw error;}
finally{await browser.close();}
