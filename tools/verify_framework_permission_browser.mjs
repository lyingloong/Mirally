/** Headless UI and real product host, using only an isolated temporary home. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile, mkdtemp, rm, unlink, realpath} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve, dirname, join, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const home=await mkdtemp(join(tmpdir(),'mirally-permission-ui-'));
const require=createRequire(join(root,'integrations/openclaw/package.json'));
const {chromium}=require(process.env.MIRALLY_PLAYWRIGHT??'playwright-core');
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
  const toggle=()=>pane().locator('mirally-action-approvals input[type="checkbox"]');
  async function open(id){await page.goto(runtime.url+'/chat/main/'+encodeURIComponent(id)+'#token='+runtime.token);await ready();await page.waitForFunction(key=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active')?.state?.sessionKey===key,'agent:main:'+id);await toggle().waitFor();await page.waitForFunction(()=>{const e=document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active mirally-action-approvals input');return e&&!e.disabled;});}
  await open(first.id);assert.equal(await toggle().isChecked(),false);await toggle().check();
  await page.waitForFunction(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active mirally-action-approvals input')?.checked===true);
  assert.equal((await rpc('action.permissions.get',{conversation_id:first.id})).session_auto_approve,true);
  await open(second.id);assert.equal(await toggle().isChecked(),false);
  await open(first.id);assert.equal(await toggle().isChecked(),true);
  await page.goto(runtime.url+'/settings/general#token='+runtime.token);await ready();
  const mode=page.getByTestId('mirally-action-approval-mode');await mode.waitFor();
  await page.waitForFunction(()=>{const e=document.querySelector('[data-testid="mirally-action-approval-mode"]');return e&&!e.disabled;});
  assert.equal(await mode.inputValue(),'ask');await mode.selectOption('trusted');
  await page.waitForFunction(()=>document.querySelector('[data-testid="mirally-action-approval-mode"]')?.value==='trusted');
  for(let i=0;i<100&&(await rpc('settings.get')).action_approval_mode!=='trusted';i++)await delay(100);
  assert.equal((await rpc('settings.get')).action_approval_mode,'trusted');
  await open(second.id);assert.equal(await toggle().isChecked(),false);
  assert.match(await pane().locator('mirally-action-approvals').innerText(),/全局持续信任已开启，电脑端动作自动批准/);
  assert.match(await pane().locator('mirally-action-approvals').innerText(),/关闭本开关不会关闭全局信任/);
  await stop();await start();await open(first.id);
  assert.equal(await toggle().isChecked(),false,'session grant should expire after host restart');
  assert.match(await pane().locator('mirally-action-approvals').innerText(),/全局持续信任已开启/);
  assert.equal((await rpc('settings.get')).action_approval_mode,'trusted','global setting persists');
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:['session toggle and cross-session isolation','global settings and chat status','restart clears session grant and retains global trust'],external_requests:0}));
}finally{
  await browser?.close();await stop();
  const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally-permission-ui-'));
  await rm(canonical,{recursive:true,force:true});
}
