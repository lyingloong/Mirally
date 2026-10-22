/** Pinned native transcript/file preview, synthetic session RPC; no Gateway/API. */
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(path.join(root,'integrations/openclaw/package.json'));
const {chromium}=require('playwright-core');
const assets=path.join(root,'integrations/openclaw/node_modules/openclaw/dist/control-ui');
const output=path.join(root,'.artifacts/file-entry-verification');
const mime={'.js':'text/javascript','.css':'text/css','.html':'text/html','.json':'application/json','.png':'image/png','.svg':'image/svg+xml','.woff2':'font/woff2'};
await fs.mkdir(output,{recursive:true});
const server=http.createServer(async(request,response)=>{
  const pathname=decodeURIComponent(new URL(request.url,'http://localhost').pathname);
  let target=path.resolve(assets,'.'+pathname);
  if(target!==assets&&!target.startsWith(assets+path.sep)){response.writeHead(403).end();return;}
  try{if(!(await fs.stat(target)).isFile())target=path.join(assets,'index.html');}catch{target=path.join(assets,'index.html');}
  try{response.setHeader('Content-Type',mime[path.extname(target)]||'application/octet-stream');response.end(await fs.readFile(target));}catch{response.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
const brand=execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8','-c',
  'import sys; from mirally.control_ui_brand import brand_script; print(brand_script(sys.argv[1]))',origin],{cwd:root,encoding:'utf8',maxBuffer:2*1024*1024});
let nativePaneFile;
for(const name of await fs.readdir(path.join(assets,'assets'))){
  if(name.startsWith('control-ui-boot-shared-')&&name.endsWith('.js')&&
    (await fs.readFile(path.join(assets,'assets',name),'utf8')).includes('customElements.define(`openclaw-chat-pane`')){nativePaneFile=name;break;}
}
assert.ok(nativePaneFile,'Pinned native chat pane module must exist');
let browser,page;
const errors=[],findings=[];
const relative='memory\\notice-综合测评材料提交-润色版.md';
const spaced='memory\\中文 文件夹\\润色 版本.md';
const canonical=relative.replaceAll('\\','/'),canonicalSpaced=spaced.replaceAll('\\','/');
try{
  browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1000},timezoneId:'Asia/Shanghai'});
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await context.routeWebSocket('**',socket=>socket.close());
  await context.addInitScript(()=>{window.chrome||={};window.chrome.webview={addEventListener(){},removeEventListener(){},postMessage(){}};});
  await context.addInitScript(brand);
  page=await context.newPage();page.on('pageerror',error=>errors.push(error.stack||String(error)));
  page.setDefaultTimeout(12000);
  await page.goto(origin);await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.navigate);
  await page.evaluate(async({relative,spaced,nativePaneFile})=>{
    const app=document.querySelector('openclaw-app'),ctx=app.context;ctx.gateway.stop();
    const first='agent:main:dashboard:file-entry-a',second='agent:main:dashboard:file-entry-b';
    const body='已生成润色版：`'+relative+'`。\n\n另有空格路径：`'+spaced+'`。\n\n既有链接：[已有文件](memory/existing.md)。\n\n```text\n'+relative+'\n```';
    const messages=()=>[{role:'user',content:[{type:'text',text:'用户引用 `'+relative+'`'}],timestamp:Date.now()-1000},{role:'assistant',content:[{type:'text',text:body}],timestamp:Date.now()}];
    window.__fileRequests=[];window.__fileHolds=[];window.__holdFile=false;window.__nativeRequests=[];
    const sessions=[first,second].map((key,index)=>({key,sessionId:`fixture-${index}`,agentId:'main',label:`File preview ${index}`,updatedAt:Date.now(),permissionMode:'read-only'}));
    const agents={defaultId:'main',mainKey:'main',agents:[{id:'main',name:'Mirally',workspace:'fixture-workspace',model:{primary:'fixture/model'}}]};
    const client={connectionGeneration:1,recoveryScopeReady:true,recoveryScope:'file-fixture',request:async(method,params={})=>{
      window.__nativeRequests.push({method,params});
      if(method==='sessions.files.get'){
        window.__fileRequests.push({method,params:structuredClone(params)});
        const result={sessionKey:params.sessionKey,root:'fixture-workspace',file:{name:params.path.split(/[\\/]/).at(-1),path:params.path,workspacePath:params.path,previewKind:'text',contentEncoding:'utf8',mimeType:'text/markdown',content:`# 综合测评材料提交\n\n${params.sessionKey===first?'会话 A 的润色内容':'会话 B 的不同内容'}\n\n中文与空格路径预览。`}};
        if(window.__holdFile)return new Promise(resolve=>window.__fileHolds.push(()=>resolve(result)));
        return result;
      }
      if(method==='agents.list')return agents;
      if(method==='sessions.list')return {sessions,defaults:{}};
      if(method==='sessions.resolve')return {ok:true,key:params.key||first,session:sessions.find(row=>row.key===(params.key||first))||sessions[0]};
      if(method==='chat.history'||method==='chat.startup')return {sessionKey:params.sessionKey,sessionId:'fixture-history',sessionInfo:{sessionId:'fixture-history',hasActiveRun:false},messages:messages(),hasMore:false,totalMessages:2};
      if(method==='agents.files.list')return {files:[]};
      if(method==='sessions.files.list')return {sessionKey:params.sessionKey,root:'fixture-workspace',files:[],artifacts:[],attachments:[]};
      if(method==='models.list')return {models:[]};
      if(method==='plugins.list')return {plugins:[],mutationAllowed:false};
      return {};
    }};
    Object.assign(ctx.gateway.snapshot,{phase:'connected',client,lastError:null,offlineStable:false,hello:{auth:{role:'operator',scopes:['operator.admin']},features:{methods:['sessions.files.get','sessions.files.list','chat.history','sessions.list']}}});
    const config={agents:{defaults:{model:{primary:'fixture/model'}},list:[{id:'main',default:true}]}};
    Object.assign(ctx.runtimeConfig.state,{client,connected:true,configSnapshot:{config,hash:'fixture'},configForm:structuredClone(config),configSchema:{type:'object',properties:{}},configUiHints:{},configLoading:false});
    ctx.runtimeConfig.ensureLoaded=async()=>{};ctx.runtimeConfig.ensureSchemaLoaded=async()=>{};
    Object.assign(ctx.agents.state,{agentsList:agents,agentsLoading:false,agentsError:null});
    // Instantiate the installed native pane; only its RPC transport is stubbed.
    (await import('/assets/'+nativePaneFile)).t();
    const pane=document.createElement('openclaw-chat-pane');
    pane.id='file-entry-fixture';
    pane.style.cssText='display:block;position:fixed;inset:0;z-index:10;';
    Object.assign(pane,{context:ctx,paneId:'file-entry-fixture',presentationId:'file-entry-fixture',sessionKey:first,presented:true,visuallyPresented:true,active:true});
    app.style.display='none';document.body.append(pane);window.__filePane=pane;
    await pane.updateComplete;
    window.__renderFileFixture=async(key=first)=>{
      const state=pane.state;if(!state)throw Error('Native pane state not initialized');
      Object.assign(state,{client,connected:true,sessionKey:key,assistantAgentId:'main',hello:ctx.gateway.snapshot.hello,chatLoading:false,chatSending:false,chatMessages:messages(),sessionsResult:{sessions,defaults:{}},chatToolMessages:[],chatStream:null,chatRunId:null});
      state.requestUpdate();await pane.updateComplete;window.__mirallyFiles?.sync();
    };
    await window.__renderFileFixture();
  },{relative,spaced,nativePaneFile});
  await page.waitForTimeout(300);
  // Native dock hosts normally supply this containing-block height.
  await page.addStyleTag({content:'#file-entry-fixture > .sidebar-region {height:100%;min-height:0;}'});
  await page.evaluate(()=>window.__renderFileFixture());
  const pane=page.locator('#file-entry-fixture');
  const link=pane.locator('a[data-mirally-file-link]').filter({hasText:relative});
  await link.waitFor();
  assert.equal(await link.getAttribute('data-file-path'),canonical);
  assert.equal(await pane.locator('.chat-group.assistant a[data-mirally-file-link]').count(),2);
  assert.equal(await pane.locator('pre a[data-mirally-file-link],.chat-group.user a[data-mirally-file-link]').count(),0);
  assert.equal(await pane.locator('a[data-file-path="memory/existing.md"]').count(),1);
  assert.equal(await pane.locator('a a').count(),0);
  findings.push('native assistant Markdown renderer gains Windows path entries; code blocks, user messages and existing Markdown links remain intact');
  await fs.writeFile(path.join(output,'layout.json'),JSON.stringify(await link.evaluate(node=>{const rows=[];for(let e=node;e;e=e.parentElement){const s=getComputedStyle(e),r=e.getBoundingClientRect();rows.push({tag:e.localName,class:e.className,display:s.display,visibility:s.visibility,overflow:s.overflow,position:s.position,rect:{x:r.x,y:r.y,width:r.width,height:r.height}});}return rows;}),null,2));
  await link.click();
  await page.locator('.sidebar-file-view').filter({hasText:'会话 A 的润色内容'}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.__fileRequests.at(-1).params),{sessionKey:'agent:main:dashboard:file-entry-a',path:canonical,agentId:'main'});
  findings.push('native onOpenWorkspaceFile invokes sessions.files.get for the current session and renders its native file preview');
  await pane.locator('a[data-mirally-file-link]').filter({hasText:spaced}).click();
  await page.waitForFunction(spaced=>window.__fileRequests.at(-1)?.params.path===spaced,canonicalSpaced);
  findings.push('Chinese names and embedded spaces are passed intact to the native session-scoped file API');
  await link.focus();await link.press('Enter');
  await page.waitForFunction(path=>window.__fileRequests.at(-1)?.params.path===path,canonical);
  const beforeSpace=await page.evaluate(()=>window.__fileRequests.length);
  await link.focus();await link.press('Space');
  await page.waitForFunction(count=>window.__fileRequests.length>count,beforeSpace);
  findings.push('native delegated keyboard handlers open enhanced entries with Enter and Space');
  await page.locator('.sidebar-file-view__action').evaluateAll(nodes=>{window.__revealButtons=nodes.map(n=>({text:n.textContent,aria:n.getAttribute('aria-label')}));});
  await fs.writeFile(path.join(output,'preview-actions.json'),JSON.stringify(await page.evaluate(()=>window.__revealButtons),null,2));
  await page.screenshot({path:path.join(output,'file-preview.png'),fullPage:false});
  await pane.getByRole('button',{name:'在文件中显示',exact:true}).click();
  await page.waitForFunction(()=>window.__filePane.state.sessionWorkspaceState?.activeId?.startsWith('file:'));
  const reveal=await page.evaluate(()=>{const s=window.__filePane.state;return {layout:s.sidebarLayout,workspace:{activeId:s.sessionWorkspaceState.activeId,browserPath:s.sessionWorkspaceState.browserPath,collapsed:s.sessionWorkspaceState.collapsed}};});
  assert.deepEqual(reveal.workspace,{activeId:'file:'+canonical,browserPath:'memory',collapsed:false});
  await fs.writeFile(path.join(output,'show-in-files.json'),JSON.stringify(reveal,null,2));
  findings.push('native Show in Files action selects the session workspace file, expands its browser and opens the parent directory');
  await link.click();
  await page.setViewportSize({width:480,height:800});
  await page.waitForTimeout(150);
  await link.scrollIntoViewIfNeeded();
  const narrow=await link.boundingBox();
  assert.ok(narrow&&narrow.width>0&&narrow.x>=0&&narrow.x+narrow.width<=480&&narrow.y>=0&&narrow.y+narrow.height<=800,'file entry fits narrow viewport');
  await page.screenshot({path:path.join(output,'file-preview-narrow.png'),fullPage:false});
  findings.push('Chinese file entry remains visible within the 480px viewport');
  await page.setViewportSize({width:1440,height:1000});
  // A pending read from A must not publish into B after a session switch.
  await page.evaluate(()=>{window.__holdFile=true;});await link.click();
  await page.waitForFunction(()=>window.__fileHolds.length>0);
  await page.evaluate(async()=>{const pane=window.__filePane;pane.setPaneSessionKey('agent:main:dashboard:file-entry-b');await window.__renderFileFixture('agent:main:dashboard:file-entry-b');window.__holdFile=false;});
  await pane.locator('a[data-mirally-file-link]').filter({hasText:relative}).click();
  await page.locator('.sidebar-file-view').filter({hasText:'会话 B 的不同内容'}).waitFor();
  await page.evaluate(()=>window.__fileHolds.splice(0).forEach(resolve=>resolve()));
  await page.waitForTimeout(100);
  assert.equal(await page.locator('.sidebar-file-view').filter({hasText:'会话 A 的润色内容'}).count(),0);
  assert.equal(await page.evaluate(()=>window.__fileRequests.at(-1).params.sessionKey),'agent:main:dashboard:file-entry-b');
  findings.push('late file reads from the previous session cannot overwrite the current session preview');
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(output,'verification.json'),JSON.stringify({findings,errors,synthetic:true,nativeRenderer:true,nativePreview:true,requests:await page.evaluate(()=>window.__fileRequests)},null,2));
  console.log(JSON.stringify({passed:findings.length,findings,output:path.relative(root,output)}));
}catch(error){
  if(page){await page.screenshot({path:path.join(output,'failed.png'),fullPage:true}).catch(()=>{});await fs.writeFile(path.join(output,'failed.json'),JSON.stringify({error:String(error),errors,body:await page.locator('body').innerText().catch(()=>''),requests:await page.evaluate(()=>window.__fileRequests).catch(()=>[]),nativeRequests:await page.evaluate(()=>window.__nativeRequests).catch(()=>[]),pane:await page.evaluate(()=>{const p=window.__filePane,s=p?.state;return {html:p?.innerHTML,state:s&&Object.fromEntries(['connected','sessionKey','chatMessages','chatLoading','chatError','lastError','sidebarContent','sidebarOpen','sessionFilePreviews'].map(k=>[k,s[k]]))};}).catch(()=>null)},null,2));}throw error;
}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
