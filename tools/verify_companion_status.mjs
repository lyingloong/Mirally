/** Exercise task/progress projection with isolated native-shaped state. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(path.join(root,'integrations/openclaw/package.json'));
const {chromium}=require('playwright-core');
const script=await fs.readFile(path.join(root,'mirally/assets/control-ui-companion.js'),'utf8');
const office=await fs.readFile(path.join(root,'mirally/assets/control-ui-office.js'),'utf8');
const css=await fs.readFile(path.join(root,'mirally/assets/control-ui-companion.css'),'utf8');
const svg='<svg viewBox="0 0 120 150"><circle cx="60" cy="70" r="40" fill="teal"/></svg>';
let origin,browser;
const errors=[],findings=[];
const server=http.createServer((request,response)=>{
  if(request.url==='/companion.css'){response.setHeader('Content-Type','text/css');response.end(css);return;}
  if(request.url==='/companion.js'){response.setHeader('Content-Type','text/javascript; charset=utf-8');response.end(script.replaceAll('__MIRALLY_ORIGIN__',JSON.stringify(origin)).replaceAll('__MIRALLY_MARK__',JSON.stringify('data:image/svg+xml,'+encodeURIComponent(svg))).replaceAll('__MIRALLY_PETS__',JSON.stringify({otter:svg,fox:svg})));return;}
  if(request.url==='/office.js'){response.setHeader('Content-Type','text/javascript; charset=utf-8');response.end(office.replaceAll('__MIRALLY_ORIGIN__',JSON.stringify(origin)));return;}
  response.setHeader('Content-Type','text/html; charset=utf-8');response.end(`<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/companion.css"></head><body><openclaw-app></openclaw-app><openclaw-chat-pane class="chat-pane-cache__pane--active"></openclaw-chat-pane><script>
    window.chrome=window.chrome||{};window.chrome.webview={postMessage:()=>{}};
    document.querySelector('openclaw-app').context={};
    window.fixturePane=document.querySelector('openclaw-chat-pane');fixturePane.paneId='fixture';fixturePane.paneTitle='第一轮会话名';
    fixturePane.state={sessionKey:'session-a',connected:true,chatMessage:'',chatMessages:[{role:'user',content:'第一轮旧请求。请分析旧资料。'},{role:'assistant',content:'第一轮已经完成。',stopReason:'stop'}],handleSendChat:()=>{},handleChatDraftChange:value=>fixturePane.state.chatMessage=value};
  </script><script src="/office.js"></script><script src="/companion.js"></script></body></html>`);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
try{
  browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await context.routeWebSocket('**',socket=>socket.close());
  const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
  await page.goto(origin);await page.waitForFunction(()=>window.__mirallyCompanion);
  await page.evaluate(()=>window.__mirallyCompanion.dock());
  const flush=()=>page.evaluate(async()=>{window.__mirallyCompanion.refresh();await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
  const read=()=>page.evaluate(()=>Object.fromEntries(['title','rail-title','rail-current','rail-previous','activity','rail-state'].map(slot=>[slot,document.querySelector('[data-slot="'+slot+'"]').textContent])));
  await flush();assert.equal((await read())['rail-title'],'第一轮旧请求');
  await page.evaluate(()=>fixturePane.state.chatMessage='这条草稿还没发送');await flush();
  assert.equal((await read())['rail-title'],'第一轮旧请求');
  await page.evaluate(()=>{
    const s=fixturePane.state;s.chatMessages.push({role:'user',content:'现在检查新的预算报告。不要继续旧任务。'});s.chatSending=true;s.chatRunId='run-new';
    s.observerDigest={runId:'run-old',revision:99,headline:'旧资料处理中'};
    s.sessionsResult={sessions:[{key:s.sessionKey,hasActiveRun:true,activeRunIds:['run-new'],observerDigest:{runId:'run-new',revision:1,headline:'准备新报告的资料'}}]};
    s.toolStreamOrder=['old'];s.toolStreamById={old:{runId:'run-old',sessionKey:s.sessionKey,name:'read',resultReceived:true,activity:[{title:'旧任务工具',phase:'end'}]}};
  });await flush();
  let current=await read();assert.equal(current['rail-title'],'现在检查新的预算报告');assert.equal(current['rail-current'],'准备新报告的资料');
  assert.equal(current.activity,'');assert.equal(current['rail-previous'],'');
  assert.equal(await page.evaluate(()=>fixturePane.paneTitle),'第一轮会话名');
  findings.push('new sent request replaces the first conversation title; unsent draft and previous-run tools do not become the task');
  await page.evaluate(()=>{
    const s=fixturePane.state;s.toolStreamOrder.push('new','foreign');
    s.toolStreamById.new={runId:'run-new',sessionKey:s.sessionKey,name:'read',activity:[{progressText:'正在读取新报告文档',phase:'start'}]};
    s.toolStreamById.foreign={runId:'run-new',sessionKey:'other-session',name:'exec',activity:[{title:'另一会话的工具'}]};
  });await flush();current=await read();assert.equal(current['rail-current'],'正在读取新报告文档');
  assert.match(current.activity,/正在读取新报告文档/);assert.doesNotMatch(current.activity,/旧任务|另一会话/);
  findings.push('live progress uses the matching session and run; a stale higher-revision digest cannot replace current progress');
  await page.evaluate(()=>fixturePane.state.chatLoading=true);await flush();
  current=await read();assert.equal(current.title,'现在检查新的预算报告');assert.equal(current['rail-state'],'工作中');assert.equal(current['rail-current'],'正在读取新报告文档');
  assert.equal(await page.locator('.mirally-companion').getAttribute('data-state'),'working');
  await page.evaluate(()=>fixturePane.state.chatLoading=false);await flush();
  assert.equal((await read())['rail-title'],'现在检查新的预算报告');
  findings.push('same-session active history refresh preserves the sent task and working progress with the real reply tracker');
  await page.evaluate(()=>{fixturePane.state.sessionKey='session-b';fixturePane.paneTitle='另一个会话名';fixturePane.state.chatLoading=true;});await flush();
  current=await read();assert.equal(current.title,'正在读取会话');assert.equal(current['rail-state'],'读取中');assert.equal(current.activity,'');assert.equal(current['rail-previous'],'');
  await page.evaluate(()=>{
    const s=fixturePane.state;s.chatLoading=false;s.chatMessages=[{role:'user',content:'处理第二个会话的请求'}];s.chatRunId='run-b';
    s.observerDigest={runId:'run-b',sessionKey:'session-a',revision:100,headline:'错误会话摘要'};
    s.chatRunStartup={runId:'run-new',sessionKey:'session-a',phase:'starting_model',message:'旧回合启动信息'};
  });await flush();current=await read();assert.equal(current.title,'处理第二个会话的请求');assert.equal(current['rail-current'],'正在思考下一步');assert.equal(current.activity,'');
  findings.push('session loading does not project retained history, foreign-session digests or stale run startup messages');
  await page.evaluate(()=>{fixturePane.state.chatSending=false;fixturePane.state.chatRunId=null;fixturePane.state.chatMessage='';fixturePane.runActivity={working:false,completion:{phase:'done',sessionKey:'session-b',runId:'run-b'}};fixturePane.state.chatMessages.push({role:'assistant',content:'第二个会话完成',stopReason:'stop'});});await flush();
  current=await read();assert.equal(current.title,'处理第二个会话的请求');assert.equal(current['rail-current'],'这一轮已结束');assert.equal(current.activity,'');
  assert.equal(await page.locator('.mirally-companion').getAttribute('data-view'),'reply');
  await page.evaluate(()=>document.querySelector('[data-action="notice-close"]').click());await flush();
  assert.equal(await page.locator('.mirally-companion').getAttribute('data-view'),'compact');
  for(let refresh=0;refresh<2;refresh++){
    await page.evaluate(()=>fixturePane.state.chatLoading=true);await flush();
    assert.equal((await read()).title,'处理第二个会话的请求');
    await page.evaluate(()=>fixturePane.state.chatLoading=false);await flush();
    assert.equal(await page.locator('.mirally-companion').getAttribute('data-view'),'compact','completed history refresh must not reannounce a dismissed reply');
  }
  findings.push('real office reply tracker emits one completion bubble; repeated same-session refresh does not reannounce it');
  await page.evaluate(()=>{fixturePane.state.chatMessages=[{role:'user',content:[{type:'image',data:'synthetic'}]}];});await flush();
  assert.equal((await read()).title,'处理本轮附带的材料');
  findings.push('completion preserves the latest task label; attachment-only turns do not fall back to an unrelated old conversation name');
  assert.deepEqual(errors,[]);
  const output=path.join(root,'.artifacts/companion-status-verification');await fs.mkdir(output,{recursive:true});
  await fs.writeFile(path.join(output,'results.json'),JSON.stringify({findings,errors},null,2));
  console.log(JSON.stringify({findings,errors},null,2));
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
