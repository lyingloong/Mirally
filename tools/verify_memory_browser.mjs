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
const output = path.join(root, '.artifacts/memory-browser-verification');
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
let browser,page;
const errors=[],findings=[];
try{
  browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1280,height:1000},timezoneId:'Asia/Shanghai'});
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await context.routeWebSocket('**',socket=>socket.close());
  await context.addInitScript(()=>{
    window.__memoryRequests=[];window.__noSummaries=false;window.__holdMemory=false;
    const rows=[
      {id:'model-summary',title:'Activity synthesis',kind:'episodic',status:'active',provenance:'model_inference',content:'周三讨论设计，随后整理需求。',metadata:{derived_from_memory_id:'rule-parent',source_retention_status:'partially_archived'},source_observation_ids:['synthetic-evidence']},
      {id:'user-fact',title:'用户已确认的偏好',kind:'semantic',status:'active',provenance:'user_confirmed',content:'<img src=x onerror=window.__unsafe=1>喜欢简洁界面。'},
      {id:'rule-parent',title:'Observed desktop context',kind:'episodic',status:'active',provenance:'observed',content:'Desktop evidence observed ... Application: wemeet.exe；Window title: 设计会议。',metadata:{source_retention_status:'archived'},source_observation_ids:['synthetic-evidence']},
      {id:'candidate',title:'Unconfirmed Activity inference',kind:'semantic',status:'candidate',provenance:'model_inference',content:'可能偏好简洁呈现，待用户核对。'}
    ].map(row=>({...row,observed_start_utc_ms:Date.parse('2026-10-15T01:00:00Z'),observed_end_utc_ms:Date.parse('2026-10-15T01:05:00Z')}));
    window.chrome ||= {};window.chrome.webview={addEventListener(){},removeEventListener(){},postMessage(message){
      if(message.type!=='mirally-activity' || !['memory-search','memory-read'].includes(message.action))return;
      window.__memoryRequests.push(message);let result;
      if(message.action==='memory-search'){
        let selected=rows.filter(row=>(!window.__noSummaries || row.provenance==='observed') && (!message.provenances || message.provenances.includes(row.provenance)) && (!message.kinds || message.kinds.includes(row.kind)) && (message.include_candidates || row.status!=='candidate') && (!message.query || (row.title+row.content).includes(message.query)));
        const offset=message.offset||0;result={status:'ready',memories:selected.slice(offset,offset+1),next_offset:offset+1<selected.length?offset+1:null,truncated:offset+1<selected.length};
      }else{
        const row=rows.find(row=>row.id===message.memory_id);
        result={status:'ready',memory:row,text:message.from_line>1?'后续正文：已保存的详细安排。':(row?.content || '')+'\n<script>unsafe()</script>',from_line:message.from_line,line_count:message.from_line>1?1:100,truncated:message.from_line===1};
      }
      const response={requestId:message.requestId,result};
      if(window.__holdMemory){window.__heldMemory=response;return;}
      setTimeout(()=>window.__mirallyActivity.receive(response),0);
    }};
  });
  await context.addInitScript(brand);
  page=await context.newPage();page.on('pageerror',error=>errors.push(error.stack || String(error)));
  await page.goto(origin);await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.navigate);
  await page.evaluate(()=>{
    const app=document.querySelector('openclaw-app'),ctx=app.context;ctx.gateway.stop();
    const agents={defaultId:'main',mainKey:'main',agents:[{id:'main',name:'Mirally'},{id:'other',name:'Other'}]};
    const client={request:async(method)=>method==='agents.list'?agents:method==='agents.files.list'?{files:[]}:method==='memory.search'?{results:[]}:method==='doctor.memory.status'?Promise.reject(Error('Synthetic memory engine offline')):method==='memory.status'?{agentId:'main',available:false}:method==='plugins.list'?{plugins:[],mutationAllowed:false}:{}};
    Object.assign(ctx.gateway.snapshot,{phase:'connected',client,lastError:null,offlineStable:false,hello:{auth:{role:'operator',scopes:['operator.admin']},features:{methods:[]}}});
    Object.assign(ctx.runtimeConfig.state,{client,connected:true,configSnapshot:{config:{agents:{defaults:{model:{primary:'fixture/model'}},list:[{id:'main',default:true},{id:'other'}]}},hash:'fixture'},configForm:{agents:{defaults:{},list:[{id:'main',default:true},{id:'other'}]}},configSchema:{type:'object',properties:{}},configUiHints:{},configLoading:false});
    ctx.runtimeConfig.ensureLoaded=async()=>{};ctx.runtimeConfig.ensureSchemaLoaded=async()=>{};
    ctx.agents.state.agentsList=agents;ctx.agents.state.agentsLoading=false;ctx.agents.state.agentsError=null;
    ctx.settingsAgentSelection.set('main');app.requestUpdate();
  });
  await page.evaluate(()=>document.querySelector('openclaw-app').context.navigate('memory',{pathname:'/settings/memory/memories',search:''}));
  const panel=page.locator('#mirally-memory-browser'),field=name=>panel.locator(`[data-memory-filter="${name}"]`),slot=name=>panel.locator(`[data-memory-slot="${name}"]`),action=name=>panel.locator(`[data-memory-action="${name}"]`);
  await panel.waitFor();await page.waitForFunction(()=>document.querySelector('#mirally-memory-browser [data-memory-slot="notice"]')?.textContent.includes('条记忆'));
  assert.ok(await page.locator('openclaw-memory-settings').count());
  await page.locator('openclaw-memory-memories').waitFor();
  assert.equal(await panel.evaluate(node=>node.parentElement.classList.contains('memory-page__panel')),true);
  assert.equal(await page.evaluate(()=>{
    const heading=document.querySelector('openclaw-memory-settings .content-header'),card=document.querySelector('#mirally-memory-browser');
    return !!heading && !!(heading.compareDocumentPosition(card)&Node.DOCUMENT_POSITION_FOLLOWING);
  }),true);
  assert.match(await slot('list').innerText(),/活动记忆总结.*模型活动总结/s);
  assert.equal(await field('source').inputValue(),'all');
  assert.equal(await page.evaluate(()=>Object.hasOwn(window.__memoryRequests[0],'provenances')),false);
  findings.push('native primary Memory /settings/memory/memories contains owner local activity memories and retains native memory search');
  await panel.locator('.mirally-memory__result').click();await slot('body').filter({hasText:'周三'}).waitFor();
  assert.match(await slot('explanation').innerText(),/已保存不等于用户确认/);assert.match(await slot('time').innerText(),/09:00.*本地时间/);
  assert.match(await slot('source').innerText(),/部分原始证据已清理.*synthetic-evidence/);
  await action('detail-more').click();await slot('body').filter({hasText:'后续正文'}).waitFor();
  await slot('trace').locator('button').click();await slot('explanation').filter({hasText:'不是模型总结'}).waitFor();
  assert.match(await slot('meta').innerText(),/规则观测记录/);assert.match(await slot('source').innerText(),/原始证据已清理/);
  assert.equal(await panel.locator('img,script').count(),0);assert.equal(await page.evaluate(()=>window.__unsafe),undefined);
  findings.push('detail displays provenance, local observed time, paginated complete text and a read-only parent trace with retention labels');
  await action('more').click();await slot('list').filter({hasText:'用户确认'}).waitFor();
  await action('more').click();await slot('list').filter({hasText:'规则观测记录'}).waitFor();
  findings.push('all sources are visible by default, including earlier rule observations reached through ordinary pagination');
  await field('source').selectOption('all');await field('kind').selectOption('semantic');await field('candidates').check();await field('start').fill('2026-10-15');await field('end').fill('2026-10-15');
  await panel.locator('button[type="submit"]').click();await slot('notice').filter({hasText:'条记忆'}).waitFor();
  const query=await page.evaluate(()=>window.__memoryRequests.filter(row=>row.action==='memory-search').at(-1));
  assert.deepEqual(query.kinds,['semantic']);assert.equal(query.include_candidates,true);assert.equal(Object.hasOwn(query,'provenances'),false);
  assert.equal(query.start_utc_ms,Date.parse('2026-10-14T16:00:00Z'));assert.equal(query.end_utc_ms,Date.parse('2026-10-15T16:00:00Z')-1);
  findings.push('server-side source, kind, review scope and local-day boundaries are sent before bounded result pagination');
  await field('kind').selectOption('');await field('source').selectOption('summaries');await field('start').fill('');await field('end').fill('');
  await page.evaluate(()=>{window.__noSummaries=true;});await action('refresh').click();await slot('notice').filter({hasText:'不表示没有历史记录'}).waitFor();
  await action('observed').click();await slot('list').filter({hasText:'桌面活动观测'}).waitFor();
  assert.match(await slot('list').innerText(),/规则观测记录/);await page.evaluate(()=>{window.__noSummaries=false;});
  findings.push('empty model summary filters explicitly preserve the existence of historical records and offer rule observations');
  for(const path of ['/settings/memory','/settings/agents/main/memory']){
    await page.evaluate(path=>document.querySelector('openclaw-app').context.navigate(path==='/settings/memory'?'memory':'agents',{pathname:path,search:''}),path);
    await page.locator(path==='/settings/memory'?'openclaw-memory-settings':'openclaw-agent-memory-panel').waitFor();
    await panel.waitFor();await page.waitForFunction(()=>!!document.querySelector('#mirally-memory-browser .mirally-memory__result'));
    assert.equal(await panel.count(),1);
  }
  findings.push('native Memory overview and Agents main Memory both mount one activity contribution without replacing original controls');
  await page.evaluate(()=>document.querySelector('openclaw-app').context.navigate('memory',{pathname:'/settings/memory/memories',search:''}));
  await panel.waitFor();await page.evaluate(()=>document.querySelector('openclaw-app').context.settingsAgentSelection.set('other'));
  await panel.waitFor({state:'detached'});
  const count=await page.evaluate(()=>window.__memoryRequests.length);await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>window.__memoryRequests.length),count);
  await page.evaluate(()=>document.querySelector('openclaw-app').context.settingsAgentSelection.set('main'));
  await panel.waitFor();await page.waitForFunction(()=>!!document.querySelector('#mirally-memory-browser .mirally-memory__result'));
  findings.push('switching the native selected agent away from the owner removes local private memory and switching back reloads it');
  for(const [width,height,theme] of [[1280,900,'light'],[1280,900,'dark'],[480,900,'light']]){
    await page.setViewportSize({width,height});await page.evaluate(theme=>{document.documentElement.setAttribute('data-theme',theme);document.documentElement.setAttribute('data-theme-mode',theme);},theme);
    await page.locator('openclaw-memory-settings .content-header').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,`memory-${width}-${theme}.png`),fullPage:false});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    for(const name of ['query','source','kind','start','end']){const bounds=await field(name).boundingBox();assert.ok(bounds && bounds.x>=0 && bounds.x+bounds.width<=width);}
  }
  findings.push('light, dark and narrow native Memory layouts keep source/date controls within the viewport');
  assert.deepEqual(errors,[]);await fs.writeFile(path.join(output,'verification.json'),JSON.stringify({findings,errors,synthetic:true},null,2));
  console.log(JSON.stringify({passed:findings.length,findings,output:path.relative(root,output)}));
}catch(error){
  if(page){await page.screenshot({path:path.join(output,'failed.png'),fullPage:true}).catch(()=>{});await fs.writeFile(path.join(output,'failed.json'),JSON.stringify({error:String(error),errors,url:page.url(),body:await page.locator('body').innerText().catch(()=>''),memory:await page.evaluate(()=>[...document.querySelectorAll('openclaw-memory-settings,openclaw-memory-memories,openclaw-agent-memory-panel')].map(node=>({tag:node.localName,agent:node.agentId,selected:node.selectedAgentId,routeData:node.routeData,activeTab:node.activeTab?.()}))).catch(()=>[])},null,2));}throw error;
}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
