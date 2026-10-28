import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdtemp,readFile,realpath,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=fileURLToPath(new URL('../',import.meta.url));
async function until(check,label,timeout=20000){const end=Date.now()+timeout;while(Date.now()<end){const value=await check();if(value)return value;await delay(30);}throw Error(`timeout: ${label}`);}
test('stopped conversation continues with prior user context through the real model wire', {timeout:60000}, async t=>{
  const python=join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  if(!existsSync(python)||!existsSync(join(root,'kernel/dsh/packages/core/agent/lib/index.js'))){t.skip('prepare repository and kernel');return;}
  const home=await mkdtemp(join(tmpdir(),'mirally continuation 中文 ')); let child,logs='', firstRequest,continued;
  const server=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const input=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if(!firstRequest){firstRequest=input;res.writeHead(200,{'content-type':'text/event-stream'});res.write('data: '+JSON.stringify({id:'fixture',choices:[{index:0,delta:{content:'Working'},finish_reason:null}]})+'\n\n');return;}
    continued=input;
    res.writeHead(200,{'content-type':'text/event-stream'});
    for(const frame of [{id:'fixture',choices:[{index:0,delta:{content:'继续处理。'},finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally continuation 中文 '));await rm(canonical,{recursive:true,force:true});
  });
  child=spawn(process.execPath,[join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',python],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',chunk=>{logs+=chunk;});child.stderr.on('data',chunk=>{logs+=chunk;});
  const runtime=await until(async()=>{if(child.exitCode!==null)throw Error(logs);try{const r=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return r.pid===child.pid&&r;}catch{return false;}},'host');
  const api=async(path,value,expected=200)=>{const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  await rpc('settings.update',{patch:{model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  await rpc('preference.set',{key:'test_context',value:'PERSONAL_CONTEXT_SENTINEL'});
  const session=await api('/api/sessions',{title:'continuation'},201);
  await api(`/api/sessions/${session.id}/messages`,{commandId:'first',text:'实验任务 EXPERIMENT_SENTINEL，请先检查配置。'},202);
  await until(()=>firstRequest,'first model request');
  assert.match(JSON.stringify(firstRequest.messages),/PERSONAL_CONTEXT_SENTINEL/);
  await api(`/api/sessions/${session.id}/cancel`,{});
  await api(`/api/sessions/${session.id}/messages`,{commandId:'second',text:'继续'},202);
  await until(()=>continued,'continuation model request');
  assert.match(JSON.stringify(continued.messages),/EXPERIMENT_SENTINEL/);
  await until(async()=>(await rpc('command.list',{conversation_id:session.id,command_id:'second'})).commands.some(c=>c.state==='completed'),'second complete');
  const history=await api(`/api/sessions/${session.id}/history`);
  assert.equal(history.messages.filter(message=>message.role==='user'&&message.text.includes('EXPERIMENT_SENTINEL')).length,1);
  await rpc('source.set',{source_id:'preference:user/local:test_context',enabled:false});
  continued=undefined;
  await api(`/api/sessions/${session.id}/messages`,{commandId:'third',text:'新的问题'},202);
  await until(()=>continued,'model request after source withdrawal');
  assert.doesNotMatch(JSON.stringify(continued.messages),/PERSONAL_CONTEXT_SENTINEL|EXPERIMENT_SENTINEL/);
});
