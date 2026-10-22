/** Exercise the pinned Control UI with synthetic model configuration and no Gateway. */
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'integrations/openclaw/package.json'));
const {chromium} = require('playwright-core');
const assets = path.join(root, 'integrations/openclaw/node_modules/openclaw/dist/control-ui');
const output = path.join(root, '.artifacts/model-settings-verification');
const mime = {'.js':'text/javascript', '.css':'text/css', '.html':'text/html', '.json':'application/json',
  '.png':'image/png', '.svg':'image/svg+xml', '.woff2':'font/woff2'};
await fs.mkdir(output, {recursive:true});
const server = http.createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  let target = path.resolve(assets, '.' + pathname);
  if (target !== assets && !target.startsWith(assets + path.sep)) {response.writeHead(403).end(); return;}
  try {
    if (!(await fs.stat(target)).isFile()) target = path.join(assets, 'index.html');
  } catch {target = path.join(assets, 'index.html');}
  try {response.setHeader('Content-Type', mime[path.extname(target)] || 'application/octet-stream'); response.end(await fs.readFile(target));}
  catch {response.writeHead(404).end();}
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const brand = execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-X', 'utf8', '-c',
  'import sys; from mirally.control_ui_brand import brand_script; print(brand_script(sys.argv[1]))', origin],
  {cwd:root, encoding:'utf8', maxBuffer:2*1024*1024});
let browser, page;
const errors = [], findings = [];
try {
  browser = await chromium.launch({channel:'msedge', headless:true});
  const context = await browser.newContext({viewport:{width:1280,height:900}});
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  await context.routeWebSocket('**', ws => ws.close());


  await context.addInitScript(() => {
    window.__memoryModelFixture={enabled:true,model:'',effective_model:'mirally/old-flash',max_output_tokens:1500,canSave:true,max_calls_per_hour:24,max_tokens_per_day:60000};
    window.__memoryModelWrites=[];
    window.chrome ||= {};window.chrome.webview = {addEventListener(){},removeEventListener(){},postMessage(message){
      let result;
      if(message.action==='model-settings'){
        result=structuredClone(window.__memoryModelFixture);
        const config=document.querySelector('openclaw-app')?.context?.runtimeConfig?.state?.configSnapshot?.config;
        result.effective_model=result.model || config?.agents?.defaults?.model?.primary || '';
      }
      else if(message.action==='set-semantic-model'){
        const edits={};
        if(Object.hasOwn(message,'model')){edits.model=message.model;window.__memoryModelFixture.model=message.model;}
        if(Object.hasOwn(message,'maxOutputTokens')){edits.maxOutputTokens=message.maxOutputTokens;window.__memoryModelFixture.max_output_tokens=message.maxOutputTokens;}
        window.__memoryModelWrites.push(edits);result={saved:true,applied:true};
      } else return;
      setTimeout(()=>window.__mirallyActivity.receive({requestId:message.requestId,result}),0);
    }};
  });
  await context.addInitScript(brand);
  page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(origin);
  await page.waitForFunction(() => document.querySelector('openclaw-app')?.context?.navigate);
  await page.evaluate(() => {
    const app = document.querySelector('openclaw-app');
    app.context.gateway.stop();
    const gatewayClient={request:async(method)=>method==='models.list'?{models:[]}:method==='auth.profiles.list'?{profiles:[]}:method==='models.auth.list'?{profiles:[],providers:[]}:method==='models.status'?{providers:[],profiles:[],defaults:{primary:'mirally/old-flash',fallbacks:[]}}:{}};
    Object.assign(app.context.gateway.snapshot, {phase:'connected',client:gatewayClient,lastError:null,offlineStable:false});
    const config = {
      models:{mode:'merge',providers:{mirally:{baseUrl:'https://fixture.invalid/v1',api:'openai-completions',apiKey:'synthetic-existing-key',models:[
        {id:'old-flash',name:'Existing Flash',contextWindow:32768,maxTokens:4096,input:['text'],cost:{input:0.1,output:0.2}},
        {id:'vision',name:'Vision',input:['text','image'],contextWindow:65536,maxTokens:8192}
      ]},other:{baseUrl:'https://other.invalid/v1',apiKey:'synthetic-other-key',api:'openai-completions',models:[{id:'other-model',name:'Other'}]}}},
      agents:{defaults:{model:{primary:'mirally/old-flash',fallbacks:['other/other-model']},workspace:'synthetic-workspace'}},
      plugins:{entries:{fixture:{enabled:true,config:{preserve:'fixture-private-setting'}}}}
    };
    const state = {client:{request:async () => ({})},connected:true,configSnapshot:{config:structuredClone(config),hash:'synthetic-hash-0'},
      configForm:structuredClone(config),configFormOriginal:structuredClone(config),configFormDirty:false,configAutoSaveStatus:'idle',configRecoveryError:null,
      lastError:null,configLoading:false};
    const listeners = new Set();
    window.__modelFixture = {initial:structuredClone(config),patches:[],refreshFail:false,notify(){for(const listener of listeners)listener(state);app.requestUpdate();}};
    const merge = (target,source) => {for(const [key,value] of Object.entries(source)){if(value&&typeof value==='object'&&!Array.isArray(value)){target[key]??={};merge(target[key],value);}else target[key]=structuredClone(value);}};
    app.context.runtimeConfig = {state,canPatch:true,canSet:true,canApply:true,
      subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);},async ensureLoaded(){},async ensureSchemaLoaded(){},
      async refresh(options){state.lastError=null;if(window.__modelFixture.refreshFail==='superseded')return;if(window.__modelFixture.refreshFail==='state'){state.lastError='Synthetic refresh unavailable';window.__modelFixture.notify();return;}if(window.__modelFixture.refreshFail)throw Error('Synthetic refresh unavailable');options?.beforeApplySnapshot?.();window.__modelFixture.notify();},
      async patchFromSnapshot(build){const result=build(structuredClone(state.configSnapshot.config));return result.options?this.patch(result.options):false;},
      async patch(options){
        if(options.canDispatch && !options.canDispatch())return false;
        const raw = typeof options.raw === 'string'?JSON.parse(options.raw):options.raw;
        window.__modelFixture.patches.push({raw:structuredClone(raw),replacePaths:options.replacePaths,note:options.note});
        merge(state.configSnapshot.config,raw);
        state.configSnapshot.hash='synthetic-hash-'+window.__modelFixture.patches.length;
        state.configForm=structuredClone(state.configSnapshot.config);
        state.configFormOriginal=structuredClone(state.configSnapshot.config);
        window.__modelFixture.notify();return true;
      }};
    app.requestUpdate();
  });


  await page.evaluate(() => document.querySelector('openclaw-app').context.navigate('model-providers', {search:''}));
  const panel = page.locator('#mirally-model-services');
  const field = name => panel.locator('[data-field="'+name+'"]');
  const notice = panel.locator('[data-slot="notice"]');
  await panel.waitFor();
  await field('modelId').waitFor();
  await page.waitForFunction(() => document.querySelector('#mirally-model-services [data-field="modelId"]')?.value === 'old-flash');
  assert.equal(await page.locator('openclaw-model-providers-page').count(),1);
  assert.equal(await field('apiKey').inputValue(),'');
  assert.equal(await field('supportsImages').isChecked(),false);
  await field('supportsReasoning').check();
  await field('thinkingFormat').selectOption('qwen');
  await field('maxTokens').fill('8192');
  await field('thinkingDefault').selectOption('off');
  await field('utilityModel').fill('other/other-model');
  await field('baseUrl').fill('https://relay.invalid/v1');
  await field('apiKey').fill('synthetic-replacement-key');
  await field('modelId').fill('qwen/qwen3.8-flash');
  await field('supportsImages').check();
  await field('makeDefault').check();
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'模型服务已保存。'}).waitFor();
  let fixture = await page.evaluate(() => ({config:document.querySelector('openclaw-app').context.runtimeConfig.state.configSnapshot.config,patches:window.__modelFixture.patches,initial:window.__modelFixture.initial}));
  assert.equal(fixture.patches.length,1);
  assert.equal(fixture.patches[0].raw.models.providers.mirally.auth,'api-key');
  assert.deepEqual(fixture.patches[0].replacePaths,['models.providers.mirally.models']);
  assert.equal(fixture.config.models.providers.mirally.models[0].id,'qwen/qwen3.8-flash');
  assert.equal(fixture.config.models.providers.mirally.models[0].contextWindow,32768);
  assert.equal(fixture.config.models.providers.mirally.models[0].reasoning,true);
  assert.equal(fixture.config.models.providers.mirally.models[0].compat.thinkingFormat,'qwen');
  assert.equal(fixture.config.models.providers.mirally.models[0].maxTokens,8192);
  assert.equal(fixture.config.agents.defaults.thinkingDefault,'off');
  assert.equal(fixture.config.agents.defaults.utilityModel,'other/other-model');
  assert.deepEqual(fixture.config.models.providers.mirally.models[0].input,['text','image']);
  assert.equal(await field('supportsImages').isChecked(),true);
  assert.deepEqual(fixture.config.models.providers.mirally.models[1],fixture.initial.models.providers.mirally.models[1]);
  assert.deepEqual(fixture.config.models.providers.other,fixture.initial.models.providers.other);
  assert.deepEqual(fixture.config.plugins,fixture.initial.plugins);
  assert.equal(fixture.config.agents.defaults.model.primary,'mirally/qwen/qwen3.8-flash');
  assert.deepEqual(fixture.config.agents.defaults.model.fallbacks,['other/other-model']);
  assert.equal(await field('modelId').inputValue(),'qwen/qwen3.8-flash');
  assert.equal(await field('apiKey').inputValue(),'');
  findings.push('full model ID, relay URL/key and default saved once with metadata, fallback and unrelated configuration preserved');
  assert.equal(await field('supportsReasoning').isChecked(),true);
  assert.equal(await field('thinkingDefault').inputValue(),'off');
  assert.match(await panel.innerText(),/不是任务的思考开关/);
  findings.push('reasoning capability, qwen request dialect and model output limit save independently from off task thinking and utility route');
  const memoryField=name=>panel.locator(`[data-memory-field="${name}"]`);
  await page.waitForFunction(()=>!document.querySelector('[data-action="save-memory"]').disabled);
  assert.match(await panel.locator('[data-slot="memory-notice"]').innerText(),/mirally\/qwen\/qwen3.8-flash/);
  await memoryField('model').fill('other/other-model');await memoryField('maxOutputTokens').fill('2048');
  await panel.locator('[data-action="save-memory"]').click();
  await panel.locator('[data-slot="memory-notice"]').filter({hasText:'保存并应用'}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.__memoryModelWrites),[{model:'other/other-model',maxOutputTokens:2048}]);
  assert.equal(await page.evaluate(()=>window.__memoryModelFixture.max_calls_per_hour),24);
  assert.equal(await page.evaluate(()=>window.__memoryModelFixture.max_tokens_per_day),60000);
  assert.equal(await page.evaluate(()=>window.__modelFixture.patches.length),1);
  findings.push('activity memory model and task output limit save through the fixed bridge without changing enabled state, budgets or native provider config');
  await page.evaluate(()=>{window.__memoryModelFixture.model='other/external-model';});
  await memoryField('maxOutputTokens').fill('3072');await panel.locator('[data-action="save-memory"]').click();
  await panel.locator('[data-slot="memory-notice"]').filter({hasText:'保存并应用'}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.__memoryModelWrites.at(-1)),{maxOutputTokens:3072});
  assert.equal(await memoryField('model').inputValue(),'other/external-model');
  await page.evaluate(()=>{window.__memoryModelFixture.max_output_tokens=4096;});
  await memoryField('model').fill('');await panel.locator('[data-action="save-memory"]').click();
  await panel.locator('[data-slot="memory-notice"]').filter({hasText:'保存并应用'}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.__memoryModelWrites.at(-1)),{model:''});
  assert.equal(await memoryField('maxOutputTokens').inputValue(),'4096');
  await panel.locator('[data-action="save-memory"]').click();await panel.locator('[data-slot="memory-notice"]').filter({hasText:'没有待保存'}).waitFor();
  assert.equal(await page.evaluate(()=>window.__memoryModelWrites.length),3);
  findings.push('memory output-only and model-only saves preserve external changes, refresh untouched fields and skip no-op writes');
  await field('baseUrl').fill('https://relay-2.invalid/v1');
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'模型服务已保存。'}).waitFor();
  fixture=await page.evaluate(() => window.__modelFixture.patches);
  assert.equal(fixture.length,2);
  assert.equal(Object.hasOwn(fixture[1].raw.models.providers.mirally,'apiKey'),false);
  assert.deepEqual(fixture[1].raw.models.providers.mirally.models[0].input,['text','image']);
  findings.push('blank key omitted from patch');
  const beforeEndpointSave = await page.evaluate(() => window.__modelFixture.patches.length);
  await field('api').selectOption('openai-responses');
  await field('baseUrl').fill('https://relay.invalid/api/gpt/v1/responses');
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'https://relay.invalid/api/gpt/v1'}).waitFor();
  assert.equal(await page.evaluate(() => window.__modelFixture.patches.length),beforeEndpointSave);
  assert.match(await notice.innerText(), /基础地址|Base URL/i);
  await field('baseUrl').fill('https://relay.invalid/api/gpt/v1');
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'模型服务已保存。'}).waitFor();
  const endpointPatch = await page.evaluate(() => window.__modelFixture.patches.at(-1));
  assert.equal(endpointPatch.raw.models.providers.mirally.baseUrl,'https://relay.invalid/api/gpt/v1');
  assert.equal(endpointPatch.raw.models.providers.mirally.api,'openai-responses');
  assert.equal(await field('api').inputValue(),'openai-responses');
  findings.push('Responses operation endpoint rejected before writing; suggested API base preserves proxy prefix and saved format');
  await field('model').selectOption('vision');
  assert.equal(await field('supportsImages').isChecked(),true);
  await field('supportsImages').uncheck();
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'模型服务已保存。'}).waitFor();
  assert.equal(await field('supportsImages').isChecked(),false);
  assert.deepEqual(await page.evaluate(() => window.__modelFixture.patches.at(-1).raw.models.providers.mirally.models[1].input),['text']);
  await field('provider').selectOption('other');
  assert.equal(await field('supportsImages').isChecked(),false);
  await field('baseUrl').fill('https://other-updated.invalid/v1');
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'模型服务已保存。'}).waitFor();
  assert.equal(await page.evaluate(() => Object.hasOwn(window.__modelFixture.patches.at(-1).raw.models.providers.other.models[0],'input')),false);
  findings.push('existing image capability reads back, explicit disable saves text-only, connection-only edit preserves omitted capability');
  await field('provider').selectOption('__new__');
  await field('providerId').fill('relay');
  assert.equal(await field('supportsImages').isChecked(),false);
  await field('baseUrl').fill('https://new-relay.invalid/v1');
  await field('apiKey').fill('synthetic-new-key');
  await field('modelId').fill('qwen/qwen3.8-flash');
  await field('supportsImages').check();
  await field('makeDefault').check();
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'模型服务已保存。'}).waitFor();
  assert.equal(await field('provider').inputValue(),'relay');
  assert.equal(await field('supportsImages').isChecked(),true);
  assert.deepEqual(await page.evaluate(() => window.__modelFixture.patches.at(-1).raw.models.providers.relay.models[0].input),['text','image']);
  findings.push('new provider created and read back');
  await page.evaluate(() => {window.__modelFixture.refreshFail=true;});
  await field('baseUrl').fill('https://refresh-fixture.invalid/v1');
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'模型服务已保存，但刷新未成功'}).waitFor();
  assert.equal(await panel.locator('[data-action="save"]').isDisabled(),true);
  await page.evaluate(() => {window.__modelFixture.refreshFail=false;});
  await panel.locator('[data-action="reload"]').click();
  await page.waitForFunction(() => !document.querySelector('#mirally-model-services [data-action="save"]').disabled);
  await page.evaluate(() => {window.__modelFixture.refreshFail='state';});
  await field('baseUrl').fill('https://state-refresh-fixture.invalid/v1');
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'模型服务已保存，但刷新未成功'}).waitFor();
  await page.evaluate(() => {window.__modelFixture.refreshFail=false;});
  await panel.locator('[data-action="reload"]').click();
  await page.waitForFunction(() => !document.querySelector('#mirally-model-services [data-action="save"]').disabled);
  await page.evaluate(() => {window.__modelFixture.refreshFail='superseded';});
  await field('baseUrl').fill('https://superseded-refresh-fixture.invalid/v1');
  await panel.locator('[data-action="save"]').click();
  await notice.filter({hasText:'模型服务已保存，但刷新未成功'}).waitFor();
  assert.equal(await panel.locator('[data-action="save"]').isDisabled(),true);
  await page.evaluate(() => {window.__modelFixture.refreshFail=false;});
  await panel.locator('[data-action="reload"]').click();
  await page.waitForFunction(() => !document.querySelector('#mirally-model-services [data-action="save"]').disabled);
  findings.push('acknowledged save distinguished from thrown, state-based and silently superseded refresh failures');
  await field('apiKey').fill('synthetic-unsaved-secret');
  await page.evaluate(() => {const runtime=document.querySelector('openclaw-app').context.runtimeConfig;runtime.state.client={request:async()=>({})};window.__modelFixture.notify();});
  await page.waitForFunction(() => document.querySelector('#mirally-model-services [data-field="apiKey"]')?.value==='');
  assert.equal(await panel.count(),1);
  await field('apiKey').fill('synthetic-navigation-secret');
  await page.evaluate(() => document.querySelector('openclaw-app').context.navigate('appearance',{search:''}));
  await page.waitForFunction(() => !document.querySelector('#mirally-model-services'));
  await page.evaluate(() => document.querySelector('openclaw-app').context.navigate('model-providers',{search:''}));
  await panel.waitFor();
  await page.waitForFunction(() => document.querySelector('#mirally-model-services [data-field="apiKey"]')?.value==='');
  assert.equal(await panel.count(),1);
  assert.equal(await page.evaluate(() => [localStorage,sessionStorage].some(storage => Object.keys(storage).some(key => /synthetic-(?:replacement-key|new-key|unsaved-secret|navigation-secret)/.test(storage.getItem(key)||'')))),false);
  assert.equal(await page.locator('body').innerText().then(text=>text.includes('synthetic-new-key')),false);
  findings.push('navigation/client change erase secret drafts; no duplicate contribution or persisted key drafts');
  for (const [width,height,theme] of [[1280,900,'light'],[1280,900,'dark'],[720,900,'light'],[480,1100,'light']]) {
    await page.setViewportSize({width,height});
    await page.evaluate(theme => {document.documentElement.setAttribute('data-theme',theme);document.documentElement.setAttribute('data-theme-mode',theme);},theme);
    await page.evaluate(() => {for(const node of document.querySelectorAll('*')) if(node.scrollTop) node.scrollTop=0;});
    // Allow the native theme transition to finish before recording visual evidence.
    await page.waitForTimeout(300);
    await page.screenshot({path:path.join(output,'models-'+width+'-'+theme+'.png'),fullPage:false});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false);
    const bounds=await panel.boundingBox();assert.ok(bounds.width<=width && bounds.width>200);
    await field('thinkingDefault').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,'model-tasks-'+width+'-'+theme+'.png'),fullPage:false});
    await memoryField('maxOutputTokens').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,'model-memory-'+width+'-'+theme+'.png'),fullPage:false});
    for(const control of [field('thinkingDefault'),field('utilityModel'),memoryField('model'),memoryField('maxOutputTokens')]) {
      const rect=await control.boundingBox();assert.ok(rect && rect.x>=0 && rect.x+rect.width<=width);
    }
  }
  findings.push('light/dark/narrow viewport without horizontal overflow');
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(output,'verification.json'),JSON.stringify({findings,errors,synthetic:true},null,2));
  console.log(JSON.stringify({passed:findings.length,findings,output:path.relative(root,output)}));

} catch (error) {
  if(page){await page.screenshot({path:path.join(output,'failed.png'),fullPage:true}).catch(()=>{});await fs.writeFile(path.join(output,'failed.json'),JSON.stringify({error:String(error),errors,url:page.url(),body:await page.locator('body').innerText().catch(()=>''),context:await page.evaluate(()=>Object.keys(document.querySelector('openclaw-app')?.context||{})).catch(()=>[])},null,2));}
  throw error;
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
}
