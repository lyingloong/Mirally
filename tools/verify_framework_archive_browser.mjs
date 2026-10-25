/** Isolated real UI + product owner; no external provider or user data. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,mkdir,mkdtemp,writeFile,unlink} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const output=resolve(process.argv[2]??join(root,'.artifacts/framework-archive-browser'));
await mkdir(output,{recursive:true});
const home=await mkdtemp(join(output,'新的 home ')),source=join(output,'旧档案.jsonl');
const bytes=Buffer.from(Array.from({length:35},(_,i)=>JSON.stringify({type:'message',message:{role:i%2?'assistant':'user',content:[{type:'text',text:`记录 ${i} <img src=x onerror="window.archiveExecuted=true">`}]}})).join('\n'));
await mkdir(join(home,'framework'),{recursive:true});await writeFile(source,bytes);
await writeFile(join(home,'framework/legacy-history.json'),JSON.stringify([{session_id:'原有会话',path:source,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),readonly:true}]));
const require=createRequire(join(root,'integrations/openclaw/package.json'));
const {chromium}=require(process.env.MIRALLY_PLAYWRIGHT??'playwright-core');
const child=spawn(join(root,'.venv/Scripts/python.exe'),['-m','mirally','--headless','--test-mode','--home',home],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1'}});
let log='',runtime,browser;child.stdout.on('data',v=>log+=v);child.stderr.on('data',v=>log+=v);
const delay=ms=>new Promise(done=>setTimeout(done,ms));
const checks=[];
try{
  for(let i=0;i<400;i++){
    try{runtime=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));if(runtime.url)break;}catch{}
    if(child.exitCode!==null)throw Error(log);await delay(100);
  }
  assert.ok(runtime?.url,'real Host ready');
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:1000}});page.setDefaultTimeout(20000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const ready=()=>page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
  const rpc=(method,params={})=>page.evaluate(({method,params})=>window.__mirallyLegacyHost.rpc(method,params),{method,params});
  await page.goto(runtime.url+'/settings/about#token='+runtime.token);await ready();
  const select=page.getByTestId('mirally-legacy-select');await select.waitFor();
  const entry=(await rpc('legacy.history')).sessions[0];
  await select.selectOption(entry.id);await page.getByTestId('mirally-legacy-more').waitFor();
  assert.equal(await page.getByTestId('mirally-legacy-messages').locator('article').count(),30);
  assert.equal(await page.evaluate(()=>window.archiveExecuted),undefined);
  assert.equal(await page.getByTestId('mirally-legacy-messages').locator('img').count(),0);
  await page.getByTestId('mirally-legacy-more').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-testid="mirally-legacy-messages"] article').length===35);
  assert.equal(await page.getByTestId('mirally-legacy-more').count(),0);
  await page.getByTestId('mirally-legacy-archive').click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="mirally-legacy-select"]')?.textContent.includes('已归档'));
  assert.deepEqual(await readFile(source),bytes,'original remains untouched');await unlink(source);
  await page.reload();await ready();await page.getByTestId('mirally-legacy-select').selectOption(entry.id);
  await page.getByTestId('mirally-legacy-messages').waitFor();
  assert.match(await page.getByTestId('mirally-legacy-messages').innerText(),/记录 0/);
  assert.equal((await rpc('conversation.list')).conversations.length,0,'archive cannot create native conversations');
  await page.screenshot({path:join(output,'archived-records.png'),fullPage:true});
  checks.push('Real index, pagination, literal text, copy, original removal, reload, no transcript conversion.');

  const granted=join(output,'授权 文件夹');await mkdir(granted,{recursive:true});
  await page.goto(runtime.url+'/settings/general');await ready();
  await page.waitForFunction(()=>document.querySelector('[data-testid="mirally-capabilities-save"]')?.disabled===false);
  const config=await rpc('capabilities.get');assert.equal(config.web_enabled,false);assert.equal(config.workspace_write_enabled,false);
  await page.getByTestId('mirally-capabilities-roots').fill(granted);
  await page.getByTestId('mirally-capabilities-write').locator('wa-switch').click();
  await page.getByTestId('mirally-capabilities-web').locator('wa-switch').click();
  await page.getByTestId('mirally-capabilities-save').click();await page.getByTestId('mirally-capabilities-notice').waitFor();
  const saved=await rpc('capabilities.get');assert.equal(saved.web_enabled,true);assert.equal(saved.workspace_write_enabled,true);assert.deepEqual(saved.read_roots,[granted]);
  const roots=(await rpc('workspace.roots')).roots;assert.equal(roots.length,2);assert.equal(roots[1].writable,false);
  await page.reload();await ready();await page.waitForFunction(()=>document.querySelector('[data-testid="mirally-capabilities-save"]')?.disabled===false);
  assert.equal(await page.getByTestId('mirally-capabilities-roots').inputValue(),granted);
  assert.equal(await page.getByTestId('mirally-capabilities-web').locator('wa-switch').evaluate(e=>e.checked),true);
  await page.getByTestId('mirally-capabilities-web').locator('wa-switch').click();await page.getByTestId('mirally-capabilities-save').click();await page.getByTestId('mirally-capabilities-notice').waitFor();
  assert.equal((await rpc('capabilities.get')).web_enabled,false);
  await page.setViewportSize({width:480,height:900});await page.screenshot({path:join(output,'capabilities-narrow.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);checks.push('Actual settings save/reload grants, independent toggles, readonly roots, narrow layout; no network or page errors.');
  await writeFile(join(output,'result.json'),JSON.stringify({passed:true,checks,external_requests:0},null,2));
  console.log(JSON.stringify({passed:true,checks,output},null,2));
}finally{
  await browser?.close();
  if(runtime?.url)try{await fetch(runtime.url+'/api/shutdown',{method:'POST',headers:{authorization:'Bearer '+runtime.token,'content-type':'application/json'},body:'{}'});}catch{}
  for(let i=0;i<200&&child.exitCode===null;i++)await delay(100);
  if(child.exitCode===null)child.kill();await writeFile(join(output,'launcher.log'),log);
}
