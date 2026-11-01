import {loadPlaywright} from './testing/dependencies.cjs';
/** Headless UI and real product host, using only an isolated temporary home. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile, mkdtemp, rm, unlink, realpath, mkdir} from 'node:fs/promises';
import {resolve, dirname, join, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const home=await mkdtemp(join(tmpdir(),'mirally-permission-ui-'));
const {chromium} = loadPlaywright();
const delay=ms=>new Promise(done=>setTimeout(done,ms));
let child,runtime,browser,log='';
async function start(){
  try{await unlink(join(home,'runtime.json'));}catch(error){if(error.code!=='ENOENT')throw error;}
  child=spawn(join(root,'.venv/Scripts/python.exe'),['-m','mirally','--headless','--test-mode','--home',home],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1'}});
  child.stdout.on('data',v=>log+=v);child.stderr.on('data',v=>log+=v);
  for(let i=0;i<400;i++){try{runtime=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));if(runtime.url)break;}catch{}if(child.exitCode!==null)throw Error('Host exited: '+log);await delay(150);}
  assert.ok(runtime?.url,'host did not start');assert.equal(new URL(runtime.url).hostname,'127.0.0.1');
}
async function stop(){
  if(runtime?.url)try{await fetch(runtime.url+'/api/shutdown',{method:'POST',headers:{authorization:'Bearer '+runtime.token,'content-type':'application/json'},body:'{}'});}catch{}
  for(let i=0;i<200&&child?.exitCode===null;i++)await delay(100);
  if(child?.exitCode===null){child.kill();for(let i=0;i<50&&child.exitCode===null;i++)await delay(100);}
  runtime=null;
}
async function call(path,body){const response=await fetch(runtime.url+path,{method:'POST',headers:{authorization:'Bearer '+runtime.token,'content-type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(JSON.stringify(data));return data.result??data;}
const rpc=(method,params={})=>call('/api/rpc',{method,params});
try{
  await start();
  const first=await call('/api/sessions',{title:'权限验证一',requestId:'permissions-one'});
  const second=await call('/api/sessions',{title:'权限验证二',requestId:'permissions-two'});
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(20000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{const url=new URL(route.request().url());return url.hostname==='127.0.0.1'?route.continue():route.abort();});
  const ready=()=>page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
  const pane=()=>page.locator('openclaw-chat-pane.chat-pane-cache__pane--active');
  const options=()=>pane().locator('mirally-chat-options');
  const trigger=()=>options().getByRole('button',{name:/会话模式与权限/});
  const item=value=>options().locator(`wa-dropdown-item[value="${value}"]`);
  async function open(id){await page.goto(runtime.url+'/chat/main/'+encodeURIComponent(id)+'#token='+runtime.token);await ready();await page.waitForFunction(key=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active')?.state?.sessionKey===key,'agent:main:'+id);await trigger().waitFor();await page.waitForFunction(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active mirally-chat-options')?.permission!==null);}
  async function choose(value){await trigger().click();await item(value).click();await page.waitForFunction(()=>!document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active mirally-chat-options')?.busy);}
  await open(first.id);
  const welcomeButton=()=>pane().locator('.agent-chat__welcome--setup > button');
  await welcomeButton().waitFor();
  assert.equal(await pane().locator('.agent-chat__disabled-banner[data-model-setup]').isVisible(),false,'empty welcome owns the only visible model setup action');
  await welcomeButton().click();await page.getByTestId('mirally-settings-model-save').waitFor();
  await open(first.id);assert.match(await trigger().innerText(),/逐次确认/);await choose('session');
  await page.waitForFunction(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active mirally-chat-options')?.permission?.session_auto_approve===true);
  assert.equal((await rpc('action.permissions.get',{conversation_id:first.id})).session_auto_approve,true);
  await open(second.id);assert.match(await trigger().innerText(),/逐次确认/);
  await open(first.id);assert.match(await trigger().innerText(),/自动批准/);
  // Open settings from the actual permission menu, not a fabricated navigation path.
  await choose('settings');await ready();
  const mode=page.getByTestId('mirally-action-approval-mode');await mode.waitFor();
  await page.waitForFunction(()=>{const e=document.querySelector('[data-testid="mirally-action-approval-mode"]');return e&&!e.disabled;});
  assert.equal(await mode.inputValue(),'ask');await mode.selectOption('trusted');
  await page.waitForFunction(()=>document.querySelector('[data-testid="mirally-action-approval-mode"]')?.value==='trusted');
  for(let i=0;i<100&&(await rpc('settings.get')).action_approval_mode!=='trusted';i++)await delay(100);
  assert.equal((await rpc('settings.get')).action_approval_mode,'trusted');
  if(process.env.MIRALLY_UI_CAPTURE_DIR){await mkdir(process.env.MIRALLY_UI_CAPTURE_DIR,{recursive:true});await page.screenshot({path:join(process.env.MIRALLY_UI_CAPTURE_DIR,'general-settings.png'),fullPage:true});}
  await open(second.id);assert.equal((await rpc('action.permissions.get',{conversation_id:second.id})).session_auto_approve,false);
  assert.match(await trigger().innerText(),/自动批准/);
  await trigger().click();await options().getByText('自动批准 · 全局设置',{exact:true}).waitFor();
  assert.equal(await item('ask').count(),0,'no ineffective session-off option under global trust');
  assert.equal(await item('session').count(),0,'no redundant session grant under global trust');
  // The existing popup must be keyboard dismissible and return focus to its trigger.
  await page.keyboard.press('Escape');await trigger().focus();await page.keyboard.press('Enter');
  await options().getByText('自动批准 · 全局设置',{exact:true}).waitFor();await page.keyboard.press('Escape');
  assert.equal(await trigger().evaluate(e=>e===document.activeElement),true);
  assert.equal(await pane().locator('.mirally-session-permission').count(),0);
  const captureDir=process.env.MIRALLY_UI_CAPTURE_DIR;
  if(captureDir){await mkdir(captureDir,{recursive:true});await page.screenshot({path:join(captureDir,'chat-wide.png'),fullPage:true});}
  await page.setViewportSize({width:390,height:844});
  await trigger().click();await options().getByText('自动批准 · 全局设置',{exact:true}).waitFor();
  const bounds=await options().locator('wa-dropdown').evaluate(e=>{const r=e.shadowRoot.querySelector('[part="menu"]').getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:innerWidth,height:innerHeight};});
  assert.ok(bounds.left>=0&&bounds.right<=bounds.width+1&&bounds.top>=0&&bounds.bottom<=bounds.height+1,JSON.stringify(bounds));
  if(captureDir)await page.screenshot({path:join(captureDir,'chat-narrow-menu.png'),fullPage:true});
  await page.keyboard.press('Escape');
  const overflow=await pane().evaluate(e=>e.scrollWidth>e.clientWidth+1);assert.equal(overflow,false,'narrow chat must not horizontally overflow');
  await page.setViewportSize({width:1280,height:900});
  await stop();await start();await open(first.id);
  assert.equal((await rpc('action.permissions.get',{conversation_id:first.id})).session_auto_approve,false,'session grant should expire after host restart');
  assert.match(await trigger().innerText(),/自动批准/);
  assert.equal((await rpc('settings.get')).action_approval_mode,'trusted','global setting persists');
  // Return to per-action mode: the visible state must follow the actual global policy.
  await choose('settings');await mode.selectOption('ask');
  for(let i=0;i<100&&(await rpc('settings.get')).action_approval_mode!=='ask';i++)await delay(100);
  await open(first.id);assert.match(await trigger().innerText(),/逐次确认/);
  await choose('session');await choose('ask');assert.match(await trigger().innerText(),/逐次确认/);
  // A nonempty conversation still needs its recovery action when no model is configured.
  await call(`/api/sessions/${first.id}/messages`,{text:'本地界面恢复验证',commandId:'model-setup-history-fixture'});
  await page.waitForFunction(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active')?.state?.chatMessages?.some(m=>m.role==='user'));
  assert.equal(await welcomeButton().count(),0);
  await pane().locator('.agent-chat__disabled-banner[data-model-setup]').waitFor({state:'visible'});
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:['session choices and cross-session isolation','global effective state and real settings navigation','keyboard and narrow popup bounds','restart clears session grant and retains global trust','disable global and session trust','single working model setup action for empty chat; history keeps recovery'],external_requests:0}));
}finally{
  await browser?.close();await stop();
  const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally-permission-ui-'));
  await rm(canonical,{recursive:true,force:true});
}
