/** Real settings UI + normal product Host + loopback-only model endpoint. No user profile or external API. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createServer} from 'node:http';
import {readFile, mkdir, mkdtemp, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const output=resolve(process.argv[2]??join(root,'.artifacts/framework-model-settings'));await mkdir(output,{recursive:true});
const home=await mkdtemp(join(output,'isolated-home-'));
const require=createRequire(join(root,'integrations/openclaw/package.json'));
const {chromium}=require(process.env.MIRALLY_PLAYWRIGHT??'playwright-core');
const calls=[], findings=[];let failGeneration=false,failDiscovery=false;
const fixture=createServer(async(req,res)=>{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const body=Buffer.concat(chunks).toString('utf8');
  calls.push({path:req.url,auth:req.headers.authorization,body:body?JSON.parse(body):null});
  if(req.url==='/v1/models'){
    if(failDiscovery){res.writeHead(401,{'content-type':'application/json'});return res.end('{"error":"fixture auth failure"}');}
    res.setHeader('content-type','application/json');return res.end(JSON.stringify({data:[
      {id:'fixture-vision',input_modalities:['text','image'],context_window:1000000,max_output_tokens:8192,effort:{supported_levels:['low','high','max'],default_level:'high'}},
      {id:'fixture-text',input_modalities:['text'],context_window:700000,max_output_tokens:1024,effort:{supported_levels:['low','high'],default_level:'high'}},
    ]}));
  }
  if(req.url==='/v1/chat/completions'){
    if(failGeneration){res.writeHead(503,{'content-type':'application/json'});return res.end('{"error":{"message":"Fixture provider temporarily unavailable"}}');}
    const parsed=JSON.parse(body);res.writeHead(200,{'content-type':'text/event-stream'});
    const event=(delta,finish_reason=null)=>({id:'fixture-reply',object:'chat.completion.chunk',created:1,model:parsed.model,choices:[{index:0,delta,finish_reason}]});
    res.write('data: '+JSON.stringify(event({role:'assistant',content:'Mirally connection OK'}))+'\n\n');
    res.write('data: '+JSON.stringify({...event({},'stop'),usage:{prompt_tokens:8,completion_tokens:4,total_tokens:12}})+'\n\n');
    return res.end('data: [DONE]\n\n');
  }
  res.writeHead(404);res.end();
});
await new Promise(done=>fixture.listen(0,'127.0.0.1',done));
const base=`http://127.0.0.1:${fixture.address().port}/v1`;
const python=join(root,'.venv/Scripts/python.exe');
const child=spawn(python,['-m','mirally','--headless','--home',home],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1'}});
let log='';child.stdout.on('data',chunk=>log+=chunk);child.stderr.on('data',chunk=>log+=chunk);
let runtime,browser;
const delay=ms=>new Promise(done=>setTimeout(done,ms));
try{
  for(let i=0;i<400;i++){
    try{runtime=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));if(runtime.url)break;}catch{}
    if(child.exitCode!==null)throw Error('Isolated Host exited: '+log);
    await delay(150);
  }
  assert.ok(runtime?.url,'isolated Host ready');
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:1000}});page.setDefaultTimeout(20000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const test=id=>page.getByTestId('mirally-settings-'+id);
  const ready=()=>page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected' && document.querySelector('[data-testid="mirally-settings-model-id"]') && document.querySelector('[data-testid="mirally-settings-model"]')?.getAttribute('aria-busy')==='false');
  const settings=()=>page.evaluate(()=>window.__mirallyLegacyHost.rpc('settings.get'));
  const save=async()=>{await test('model-save').click();await test('notice').filter({hasText:'模型连接已保存'}).waitFor();await ready();};
  await page.goto(runtime.url+'/settings/model-providers#token='+runtime.token);await ready();
  const initial=await settings();assert.equal(await test('model-reasoning').inputValue(),'off');
  await test('model-preset').selectOption('deepseek');
  assert.equal(await test('model-id').inputValue(),'deepseek-flash');assert.equal(await test('model-format').inputValue(),'openai-responses');
  assert.deepEqual((await settings()).model,initial.model,'preset does not silently save');
  assert.equal(await test('model-discover').isDisabled(),true);
  await test('model-base-url').fill(base);await test('model-id').fill('fixture-vision');await test('model-format').selectOption('openai-chat');
  await test('model-key').fill('disposable-fixture-key');await test('model-context-window').fill('1000000');await test('model-reasoning').selectOption('low');
  await save();assert.equal(await test('model-key').inputValue(),'');
  assert.equal((await settings()).model.reasoning_effort,'low');assert.equal(JSON.stringify(await settings()).includes('disposable-fixture-key'),false);
  findings.push('Preset remains unsaved until submit; real settings owner stores explicit effort/context without returning key.');
  await test('model-discover').click();await test('model-catalog').waitFor();await ready();
  assert.match(await page.locator('mirally-settings-page').innerText(),/1,000,000/);
  await test('model-context-window').fill('128000');await save();
  assert.equal(await test('model-catalog').count(),0,'saving invalidates cached capability information');
  await test('model-discover').click();await test('model-catalog').waitFor();await ready();
  await test('model-adopt').click();assert.equal(await test('model-context-window').inputValue(),'1000000');
  assert.equal(await test('model-test').isDisabled(),true);await save();
  await test('model-discover').click();await test('model-catalog').waitFor();await ready();
  await test('model-catalog').selectOption('fixture-text');assert.equal(await test('model-id').inputValue(),'fixture-text');
  assert.equal(await test('model-context-window').inputValue(),'700000');assert.equal(await test('model-max-tokens').inputValue(),'1024');
  assert.equal(await page.locator('[data-testid="mirally-settings-model-images"] wa-switch').evaluate(element=>element.checked),false);
  assert.equal(await test('model-test').isDisabled(),true);await save();
  assert.equal((await settings()).model.image_enabled,false);findings.push('Real model listing adopts reported modalities, context/output limits and preserves explicit save.');
  await test('model-test').click();await test('model-test-result').filter({hasText:'Mirally connection OK'}).waitFor();await ready();
  assert.equal(calls.find(call=>call.body)?.auth,'Bearer disposable-fixture-key');
  assert.equal(calls.find(call=>call.body)?.body.reasoning_effort,'low');
  assert.equal(calls.find(call=>call.body)?.body.max_tokens??calls.find(call=>call.body)?.body.max_completion_tokens,512);
  await page.screenshot({path:join(output,'model-connection.png'),fullPage:true});
  await test('model-discover').click();await test('model-catalog').waitFor();await ready();
  assert.equal(await test('model-test-result').count(),1,'discovery does not clear a still-current text test');
  await test('refresh').click();await ready();
  assert.equal(await test('model-catalog').count(),0,'refresh invalidates cached models from the previous saved connection');
  assert.equal(await test('model-test-result').count(),0,'refresh invalidates previous text test results');
  findings.push('Saving or refreshing authoritative settings clears old model catalogs and test results; fresh discovery remains usable until then.');
  const requestCount=calls.filter(call=>call.body).length;
  failGeneration=true;await test('model-test').click();await test('error').waitFor();await ready();
  assert.ok(calls.filter(call=>call.body).length>requestCount,'failure reached the actual provider');
  assert.ok((await test('error').innerText()).trim(),'provider failure is visible');
  assert.equal(await test('model-test-result').count(),0,'a failed request must not retain a previous successful result');failGeneration=false;
  failDiscovery=true;await test('model-discover').click();await test('error').filter({hasText:'HTTP 401'}).waitFor();await ready();failDiscovery=false;
  findings.push('Text test reaches real product/LLM HTTP transport; provider failure is visible, previous success clears; listing auth failure is actionable.');
  for(const effort of ['default','off','low']){
    await test('model-reasoning').selectOption(effort);await save();await page.reload();await ready();
    assert.equal(await test('model-reasoning').inputValue(),effort);assert.equal((await settings()).model.reasoning_effort,effort);
  }
  await test('connection-new').click();
  await test('connection-name').fill('独立后台连接');await test('model-base-url').fill(base);
  await test('model-id').fill('fixture-text');await test('model-key').fill('second-fixture-key');await save();
  const connectionId=await test('connection-select').inputValue();assert.notEqual(connectionId,'default');
  assert.equal((await settings()).model_connections[connectionId].name,'独立后台连接');
  assert.equal(JSON.stringify(await settings()).includes('second-fixture-key'),false);
  await test('route-background').selectOption(connectionId);await test('notice').filter({hasText:'用途已保存'}).waitFor();await ready();
  assert.equal((await settings()).model_routes.background,connectionId);assert.equal(await test('connection-delete').isDisabled(),true);
  await test('model-test').click();await test('model-test-result').waitFor();await ready();
  assert.equal(calls.filter(call=>call.body).at(-1).auth,'Bearer second-fixture-key','named probe uses that connection key');
  await test('route-background').selectOption('default');await test('notice').filter({hasText:'用途已保存'}).waitFor();await ready();
  await test('connection-delete').click();await test('connection-delete-confirm').click();await test('notice').filter({hasText:'连接已删除'}).waitFor();await ready();
  assert.equal((await settings()).model_connections[connectionId],undefined);assert.equal(await test('connection-select').inputValue(),'default');
  findings.push('Named connections save independent keys, assign background purpose, probe the selected connection, refuse deletion while assigned and delete after explicit confirmation.');
  await page.setViewportSize({width:480,height:900});await page.screenshot({path:join(output,'model-connection-narrow.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false,'narrow page fits viewport');
  assert.deepEqual(errors,[]);
  findings.push('Off/default/low persist distinctly across reload; narrow layout fits; no browser page errors.');
  await writeFile(join(output,'result.json'),JSON.stringify({passed:true,findings,requests:calls.length,external_requests:0},null,2));
  console.log(JSON.stringify({passed:true,findings,output},null,2));
}finally{
  await browser?.close();
  if(runtime?.url)try{await fetch(runtime.url+'/api/shutdown',{method:'POST',headers:{authorization:'Bearer '+runtime.token,'content-type':'application/json'},body:'{}'});}catch{}
  for(let i=0;i<200&&child.exitCode===null;i++)await delay(100);
  if(child.exitCode===null)child.kill();
  await writeFile(join(output,'launcher.log'),log);
  fixture.closeAllConnections();await new Promise(done=>fixture.close(done));
}
