/** Real native DSH questions and plan review in the retained UI, local model only. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {readFile,mkdtemp,rm,realpath} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {join,dirname,basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {setTimeout as delay} from 'node:timers/promises';
const root=fileURLToPath(new URL('../',import.meta.url));
const require=createRequire(new URL('../kernel/dsh/packages/mirally/mirally-product/package.json',import.meta.url));
const {chromium}=require('playwright-core');
const home=await mkdtemp(join(tmpdir(),'mirally-native-ui-'));
let child,browser,runtime,logs='',turn=0;
const requests=[];
const server=createServer(async(req,res)=>{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);const input=JSON.parse(Buffer.concat(chunks));requests.push(input);
  const n=++turn;
  const operation=n===1?['ask_user_question',{questions:[{id:'choice',question:'选择本地验证方案',options:[{label:'方案 A',description:'使用隔离测试目录。'},{label:'方案 B'}]}]}]
    :n===3?['exit_plan_mode',{plan:'# 本地验证计划\n\n确认选择后继续本地验证，不操作用户文件。'}]:null;
  const delta=operation?{tool_calls:[{index:0,id:`ui-${n}`,type:'function',function:{name:operation[0],arguments:JSON.stringify(operation[1])}}]}:{content:'NATIVE_INTERACTION_DONE'};
  res.writeHead(200,{'content-type':'text/event-stream'});
  for(const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:operation?'tool_calls':'stop'}],usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
  res.end('data: [DONE]\n\n');
});
async function until(check,label,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const value=await check();if(value)return value;await delay(50);}throw Error(`timeout ${label}: ${logs}`);}
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
try {
  child=spawn(process.execPath,[join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',join(root,'.venv/Scripts/python.exe')],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',c=>logs+=c);child.stderr.on('data',c=>logs+=c);
  runtime=await until(async()=>{if(child.exitCode!==null)throw Error(logs);try{const r=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return r.pid===child.pid&&r;}catch{return false;}},'Host');
  const api=async(path,body)=>{const r=await fetch(runtime.url+path,{method:'POST',headers:{authorization:'Bearer '+runtime.token,'content-type':'application/json'},body:JSON.stringify(body)});const data=await r.json();assert.ok(r.ok,JSON.stringify(data));return data.result??data;};
  const rpc=(method,params={})=>api('/api/rpc',{method,params});
  await rpc('settings.update',{patch:{model:{provider:'deepseek',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_format:'openai-chat',api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  const first=await api('/api/sessions',{title:'原生问题验证'});
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(20000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{const url=new URL(route.request().url());return url.hostname==='127.0.0.1'?route.continue():route.abort();});
  await page.goto(runtime.url+'/chat/main/'+encodeURIComponent(first.id)+'#token='+runtime.token);
  await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
  const card=page.locator('openclaw-chat-pane.chat-pane-cache__pane--active mirally-native-interactions');
  const plan=card.locator('input[type="checkbox"]').first();await plan.waitFor();
  await api(`/api/sessions/${first.id}/messages`,{commandId:'question',text:'请先询问我选择。'});
  await card.getByText('选择本地验证方案',{exact:true}).waitFor();
  await card.getByLabel('方案 A',{exact:false}).check();
  await card.getByRole('button',{name:'提交回答'}).click();
  await until(async()=>(await rpc('command.list',{conversation_id:first.id,command_id:'question'})).commands.some(c=>c.state==='completed'),'answer completes native turn');
  assert.match(JSON.stringify(requests[1].messages),/方案 A/);
  assert.ok(requests[1].messages.some(m=>m.role==='tool'&&m.tool_call_id==='ui-1'));
  await plan.check();
  await until(async()=>{const v=await rpc('interaction.plan.get',{conversation_id:first.id});return v.active||v.pending;},'plan toggle');
  await api(`/api/sessions/${first.id}/messages`,{commandId:'plan',text:'请展示完整计划供我确认。'});
  await card.getByText('审核计划',{exact:true}).waitFor();
  await card.getByLabel('Approve',{exact:false}).check();
  await card.getByRole('button',{name:'提交回答'}).click();
  await until(async()=>(await rpc('command.list',{conversation_id:first.id,command_id:'plan'})).commands.some(c=>c.state==='completed'),'plan completes native turn');
  assert.equal((await rpc('interaction.plan.get',{conversation_id:first.id})).active,false);
  assert.ok(requests.at(-1).messages.some(m=>m.role==='tool'&&m.tool_call_id==='ui-3'));
  await card.getByRole('button',{name:'压缩上下文',exact:true}).click();
  await card.locator('[role="status"]').waitFor();
  await page.locator('openclaw-chat-pane.chat-pane-cache__pane--active .chat-header-session-menu__trigger').click();
  const forkItem=page.locator('openclaw-chat-pane.chat-pane-cache__pane--active openclaw-chat-header-session-menu wa-dropdown-item[value="fork"]');
  await forkItem.waitFor();assert.equal(await forkItem.getAttribute('disabled'),null,'native fork is available in retained menu');
  await forkItem.click();
  await page.waitForFunction(key=>{const pane=document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active');return pane?.state?.sessionKey&&pane.state.sessionKey!==key;},'agent:main:'+first.id);
  const forkKey=await page.evaluate(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active').state.sessionKey);
  const roster=await rpc('conversation.list');
  assert.ok(roster.conversations.some(c=>c.conversation_id===forkKey.replace(/^agent:main:/,'')&&c.fork?.parent_conversation_id===first.id),'menu creates a real linked native fork');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,checks:['native question answered in actual UI','answer reaches actual model tool continuation','native plan review and approval','manual native compact UI','native fork from retained conversation menu'],external_requests:0}));
} finally {
  await browser?.close();
  if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
  server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally-native-ui-'));await rm(canonical,{recursive:true,force:true});
}
