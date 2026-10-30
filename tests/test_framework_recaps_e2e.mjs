/** Real DSH transcript and product owner, with an isolated local model fixture. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,realpath,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {basename,dirname,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=fileURLToPath(new URL('../',import.meta.url));
async function until(check,label,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const value=await check();if(value)return value;await delay(80);}throw Error(`timeout: ${label}`);}

test('real transcript recap, title, stale status and manual rename', {timeout:120000}, async t=>{
  const home=await mkdtemp(join(tmpdir(),'mirally recap 中文 ')); let child,logs='';const requests=[];
  const server=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const input=JSON.parse(Buffer.concat(chunks).toString());requests.push(input);
    const recap=JSON.stringify(input.messages).includes('概括对话主题与目前进展');
    const content=recap?JSON.stringify({title:'实验计划',summary:'讨论了实验和数据分析。'}):'已了解实验计划。';
    res.writeHead(200,{'content-type':'text/event-stream'});
    for(const frame of [{id:'fixture',choices:[{index:0,delta:{content},finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:20,completion_tokens:10,total_tokens:30}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally recap 中文 '));await rm(canonical,{recursive:true,force:true});
  });
  const preload=join(home,'fixture-model.mjs'),models=pathToFileURL(join(root,'kernel/dsh/packages/mirally/mirally-product/src/models.mjs')).href;
  await writeFile(preload,`import {ProductModels} from ${JSON.stringify(models)};\nconst sync=ProductModels.prototype.sync;\nconst requireOptions=ProductModels.prototype.requireOptions;\nProductModels.prototype.sync=function(s){if(this.testMode&&!this.fixture){this.registration?.();this.fixture=new ProductModels(this.ctx,{testMode:false});}if(this.fixture){const value=sync.call(this.fixture,s);this.options=value;return value;}return sync.call(this,s);};\nProductModels.prototype.requireOptions=function(...a){return this.fixture?requireOptions.call(this.fixture,...a):requireOptions.call(this,...a);};\n`);
  child=spawn(process.execPath,['--import',pathToFileURL(preload).href,join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',join(root,'.venv/Scripts/python.exe'),'--test-mode'],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',c=>{logs+=c;});child.stderr.on('data',c=>{logs+=c;});
  const runtime=await until(async()=>{if(child.exitCode!==null)throw Error(logs);try{const r=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return r.pid===child.pid&&r;}catch{return false;}},'host start');
  const api=async(path,value,expected=200,method)=>{const response=await fetch(runtime.url+path,{method:method??(value===undefined?'GET':'POST'),headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  const complete=async(id,command)=>until(async()=>(await rpc('command.list',{conversation_id:id,command_id:command})).commands.some(c=>c.state==='completed'),'completion '+command);
  await rpc('settings.update',{patch:{model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  const session=await api('/api/sessions',{},201);
  await api(`/api/sessions/${session.id}/messages`,{commandId:'first',text:'帮我规划一个数据分析实验。'},202);
  await complete(session.id,'first');
  const result=await api(`/api/sessions/${session.id}/recap`,{});
  assert.equal(result.status,'ready');assert.equal(result.recap.text,'讨论了实验和数据分析。');
  const sessionView=async()=>(await api('/api/sessions')).sessions.find(s=>s.id===session.id);
  let view=await sessionView();assert.equal(view.title,'实验计划');assert.equal(view.recap.stale,false);
  await api(`/api/sessions/${session.id}/messages`,{commandId:'second',text:'继续完善实验。'},202);
  await complete(session.id,'second');view=await sessionView();assert.equal(view.recap.stale,true);
  await api(`/api/sessions/${session.id}`,{expected_version:view.metadata_version,patch:{title:'我的实验'}},200,'PATCH');
  const refreshed=await api(`/api/sessions/${session.id}/recap`,{});assert.equal(refreshed.status,'ready');
  view=await sessionView();assert.equal(view.title,'我的实验');assert.equal(view.recap.stale,false);
  assert.ok(requests.some(item=>JSON.stringify(item.messages).includes('帮我规划一个数据分析实验')));
});
