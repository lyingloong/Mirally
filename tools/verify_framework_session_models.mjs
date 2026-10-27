/** Real model picker and product owner in an isolated profile; no external API. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile, mkdir, mkdtemp, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const output=resolve(process.argv[2]??join(root,'.artifacts/framework-session-models'));await mkdir(output,{recursive:true});
const home=await mkdtemp(join(output,'isolated-home-'));
const require=createRequire(join(root,'integrations/openclaw/package.json'));
const {chromium}=require(process.env.MIRALLY_PLAYWRIGHT??'playwright-core');
const child=spawn(join(root,'.venv/Scripts/python.exe'),['-m','mirally','--headless','--home',home],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1'}});
let log='',runtime,browser;child.stdout.on('data',chunk=>log+=chunk);child.stderr.on('data',chunk=>log+=chunk);
const delay=ms=>new Promise(done=>setTimeout(done,ms));
try {
  for(let i=0;i<400;i++) {try{runtime=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));if(runtime.url)break;}catch{} if(child.exitCode!==null)throw Error('Host exited: '+log);await delay(150);}
  assert.equal(new URL(runtime.url).hostname,'127.0.0.1');
  async function call(path,body) {const response=await fetch(runtime.url+path,{method:'POST',headers:{authorization:'Bearer '+runtime.token,'content-type':'application/json'},body:JSON.stringify(body)});const json=await response.json();if(!response.ok)throw Error(JSON.stringify(json));return json.result??json;}
  const rpc=(method,params={})=>call('/api/rpc',{method,params});
  const session=async id=>{const response=await fetch(runtime.url+'/api/sessions',{headers:{authorization:'Bearer '+runtime.token}});const data=await response.json();return data.sessions.find(item=>item.id===id);};
  await rpc('settings.update',{patch:{model:{base_url:'http://127.0.0.1:12345/v1',model_id:'fixture-alpha'}}});
  await rpc('model.catalog.save',{connection_id:'default',model:{model_id:'fixture-beta'}});
  const second=(await rpc('model.connection.save',{name:'第二连接',model:{base_url:'http://127.0.0.1:12346/v1',model_id:'fixture-gamma'}})).connection_id;
  const firstSession=await call('/api/sessions',{title:'模型验收一',requestId:'selector-one'});
  const secondSession=await call('/api/sessions',{title:'模型验收二',requestId:'selector-two'});
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(20000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const pane=()=>page.locator('openclaw-chat-pane.chat-pane-cache__pane--active');
  const trigger=()=>pane().locator('[data-chat-model-select]');
  async function open(id) {
    await page.goto(runtime.url+'/chat/main/'+encodeURIComponent(id)+'#token='+runtime.token);
    await page.waitForFunction(key=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active')?.state?.sessionKey===key,'agent:main:'+id);
    await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
    await trigger().waitFor();
  }
  async function select(provider,model) {
    await page.waitForFunction(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active [data-chat-model-select]')?.getAttribute('aria-disabled')==='false');
    await trigger().click();
    const group=pane().locator(`[data-chat-model-provider-group="${provider}"]`);
    await group.locator('[data-chat-model-provider-toggle]').click();
    await group.locator(`[data-chat-model-option="${provider}/${model}"]`).click();
    await page.waitForFunction(expected=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active [data-chat-model-select]')?.getAttribute('data-chat-select-value')===expected,`${provider}/${model}`);
  }
  await open(firstSession.id);await select('mirally-default','fixture-beta');
  assert.equal((await session(firstSession.id)).model_selection.model_id,'fixture-beta');
  await open(secondSession.id);await select('mirally-'+second,'fixture-gamma');
  assert.deepEqual((await session(secondSession.id)).model_selection,{connection_id:second,model_id:'fixture-gamma'});
  await page.reload();await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
  await page.waitForFunction(expected=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active [data-chat-model-select]')?.getAttribute('data-chat-select-value')===expected,`mirally-${second}/fixture-gamma`);
  await open(firstSession.id);
  await page.waitForFunction(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active [data-chat-model-select]')?.getAttribute('data-chat-select-value')==='mirally-default/fixture-beta');
  assert.equal((await session(secondSession.id)).model_selection.model_id,'fixture-gamma');
  await page.screenshot({path:join(output,'conversation-model.png')});
  assert.deepEqual(errors,[]);
  await writeFile(join(output,'result.json'),JSON.stringify({passed:true,external_requests:0},null,2));
  console.log(JSON.stringify({passed:true,output}));
} finally {
  await browser?.close();
  if(runtime?.url)try{await fetch(runtime.url+'/api/shutdown',{method:'POST',headers:{authorization:'Bearer '+runtime.token,'content-type':'application/json'},body:'{}'});}catch{}
  for(let i=0;i<200&&child.exitCode===null;i++)await delay(100);
  if(child.exitCode===null)child.kill();
  await writeFile(join(output,'launcher.log'),log);
}
