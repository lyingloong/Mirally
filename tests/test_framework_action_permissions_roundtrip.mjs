/** Real DSH/Host with a local model and an isolated, disposable home. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdtemp, readFile, realpath, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=fileURLToPath(new URL('../',import.meta.url));
async function until(check,label,timeout=30000) {
  const end=Date.now()+timeout;
  while(Date.now()<end) {const value=await check(); if(value) return value; await delay(40);}
  throw new Error(`Timed out: ${label}`);
}

test('real host preserves global trust but not session grant across restart', {timeout:180000}, async t=>{
  if(process.platform!=='win32') return t.skip('Windows PowerShell capability');
  const python=join(root,'.venv/Scripts/python.exe');
  if(!existsSync(python)||!existsSync(join(root,'kernel/dsh/packages/core/agent/lib/index.js'))) return t.skip('Prepare repository Python and kernel build');
  const home=await mkdtemp(join(tmpdir(),'mirally permission host 中文 '));
  let child, runtime, logs='', errors=[];
  const server=createServer(async(req,res)=>{
    try {
      const chunks=[]; for await(const chunk of req) chunks.push(chunk);
      const input=JSON.parse(Buffer.concat(chunks).toString('utf8'));
      const user=[...input.messages].reverse().find(message=>message.role==='user' && /^probe-\d+$/.test(message.content));
      assert.ok(user,'missing probe user message');
      const name=user.content, marker=join(home,`${name}.txt`).replaceAll("'","''");
      const done=input.messages.at(-1).role==='tool';
      const delta=done?{content:'完成'}:{tool_calls:[{index:0,id:`call-${name}`,type:'function',function:{name:'mirally_run_command',arguments:JSON.stringify({command:`Set-Content -LiteralPath '${marker}' -Value done`,cwd:home,timeout_seconds:30})}}]};
      res.writeHead(200,{'content-type':'text/event-stream'});
      for(const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:done?'stop':'tool_calls'}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}}]) res.write(`data: ${JSON.stringify(frame)}\n\n`);
      res.end('data: [DONE]\n\n');
    } catch(error) {errors.push(error);res.writeHead(500);res.end('fixture error');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  async function stop() {
    if(child?.exitCode===null) {child.stdin.end(); try {await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]);} catch {child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
  }
  t.after(async()=>{
    await stop(); server.closeAllConnections(); await new Promise(resolve=>server.close(resolve));
    const canonical=await realpath(home); assert.equal(dirname(canonical),await realpath(tmpdir())); assert.ok(basename(canonical).startsWith('mirally permission host 中文 '));
    await rm(canonical,{recursive:true,force:true});
  });
  async function start() {
    child=spawn(process.execPath,[join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',python],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
    child.stdout.on('data',data=>{logs+=data;}); child.stderr.on('data',data=>{logs+=data;});
    runtime=await until(async()=>{try{const value=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return value.pid===child.pid&&value;}catch{return false;}},'startup '+logs);
  }
  await start();
  const api=async(path,value,expected=200)=>{const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  await rpc('settings.update',{patch:{model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture-key',model_id:'fixture-model',reasoning_effort:'off',max_tokens:1024}}});
  const first=await api('/api/sessions',{title:'授权会话'},201), other=await api('/api/sessions',{title:'其他会话'},201);
  const send=async(id,n)=>api(`/api/sessions/${id}/messages`,{commandId:`command-${n}`,text:`probe-${n}`},202);
  const approval=async(id,n)=>until(async()=>{const list=await rpc('action.approvals.list',{conversation_id:id}); return list.approvals.find(item=>item.command_id===`command-${n}`);},`approval ${n} ${logs}`);
  const completed=async(id,n)=>until(async()=>{const list=await rpc('command.list',{conversation_id:id,command_id:`command-${n}`});return list.commands.find(item=>['completed','cancelled'].includes(item.state));},`completion ${n} ${logs}`);
  async function denied(id,n) {await send(id,n);const item=await approval(id,n);assert.equal(existsSync(join(home,`probe-${n}.txt`)),false);await rpc('action.approvals.decide',{approval_id:item.id,conversation_id:id,allow:false});await completed(id,n);assert.equal(existsSync(join(home,`probe-${n}.txt`)),false);}
  async function automatic(id,n) {await send(id,n);await completed(id,n);assert.equal((await readFile(join(home,`probe-${n}.txt`),'utf8')).trim(),'done');assert.deepEqual((await rpc('action.approvals.list',{conversation_id:id})).approvals,[]);}
  await denied(first.id,1);
  await rpc('action.permissions.set',{conversation_id:first.id,enabled:true});
  await automatic(first.id,2);
  await denied(other.id,3);
  await rpc('action.permissions.set',{conversation_id:first.id,enabled:false});
  await denied(first.id,4);
  await rpc('action.permissions.set',{conversation_id:first.id,enabled:true});
  await rpc('settings.update',{patch:{action_approval_mode:'trusted'}});
  await automatic(other.id,5);
  await stop(); await start();
  assert.equal((await rpc('settings.get')).action_approval_mode,'trusted');
  assert.equal((await rpc('action.permissions.get',{conversation_id:first.id})).session_auto_approve,false);
  await automatic(other.id,6);
  await rpc('settings.update',{patch:{action_approval_mode:'ask'}});
  await denied(first.id,7);
  assert.deepEqual(errors,[]);
});
