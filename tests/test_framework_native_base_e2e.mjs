import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,realpath,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=fileURLToPath(new URL('../',import.meta.url));
async function until(check,label,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const value=await check();if(value)return value;await delay(35);}throw Error(`timeout: ${label}`);}

test('standard DSH composition executes native file, shell and delegated model work', {timeout:90000}, async t=>{
  const home=await mkdtemp(join(tmpdir(),'mirally native base 中文 '));let child,logs='',step=0,childRequests=0,askIssued=false;
  const requests=[];
  const actions=[['write',{file_path:'native.txt',content:'before'}],['read',{file_path:'native.txt'}],
    ['edit',{file_path:'native.txt',old_string:'before',new_string:'after'}],
    [process.platform==='win32'?'pwsh':'bash',{description:'Print isolated fixture sentinel',command:process.platform==='win32'?"Write-Output 'NATIVE_SHELL_OK'":"printf NATIVE_SHELL_OK",timeoutMs:10000}],
    ['subagent',{description:'Check independent model task',prompt:'CHILD_TASK_SENTINEL. Reply CHILD_MODEL_OK.'}]];
  const server=createServer(async(req,res)=>{
    const chunks=[];for await(const c of req)chunks.push(c);const input=JSON.parse(Buffer.concat(chunks));requests.push(input);
    const isChild=input.messages.some(m=>m.role==='user'&&typeof m.content==='string'&&m.content.startsWith('CHILD_TASK_SENTINEL'));
    let delta,finish;
    if(isChild){childRequests++;delta={content:'CHILD_MODEL_OK'};finish='stop';}
    else if(input.messages.some(m=>m.role==='user'&&m.content==='ASK_NATIVE')&&!askIssued){
      askIssued=true;delta={tool_calls:[{index:0,id:'denied-native',type:'function',function:{name:'write',arguments:JSON.stringify({file_path:'denied.txt',content:'must not write'})}}]};finish='tool_calls';
    }
    else if(step<actions.length){const [name,args]=actions[step++];delta={tool_calls:[{index:0,id:`native-${step}`,type:'function',function:{name,arguments:JSON.stringify(args)}}]};finish='tool_calls';}
    else {delta={content:'NATIVE_DONE'};finish='stop';}
    res.writeHead(200,{'content-type':'text/event-stream'});
    for(const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:finish}],usage:{prompt_tokens:100,completion_tokens:10,total_tokens:110}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally native base 中文 '));await rm(canonical,{recursive:true,force:true});
  });
  child=spawn(process.execPath,[join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python')],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',c=>{logs+=c;});child.stderr.on('data',c=>{logs+=c;});
  const runtime=await until(async()=>{if(child.exitCode!==null)throw Error(logs);try{const r=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return r.pid===child.pid&&r;}catch{return false;}},'native Host '+logs);
  const api=async(path,value,expected=200)=>{const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  await rpc('settings.update',{patch:{action_approval_mode:'trusted',model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  const session=await api('/api/sessions',{title:'native'},201);
  const native=await api(`/api/sessions/${session.id}/native`);
  for(const tool of ['read','write','edit','grep','glob','subagent','subagent_fork','todo_write','ask_user_question','exit_plan_mode',process.platform==='win32'?'pwsh':'bash'])assert.ok(native.tools.includes(tool),`native ${tool} registered`);
  assert.ok(native.commands.some(c=>c.name==='compact'));
  await api(`/api/sessions/${session.id}/messages`,{commandId:'native-task',text:'Execute native tools in this isolated fixture workspace.'},202);
  await until(async()=>(await rpc('command.list',{conversation_id:session.id,command_id:'native-task'})).commands.some(c=>c.state==='completed'),'native command complete');
  await until(()=>childRequests>0,'native child model request');
  assert.equal(await readFile(join(home,'workspace','native.txt'),'utf8'),'after');
  const toolMessages=requests.flatMap(r=>r.messages.filter(m=>m.role==='tool'));
  assert.match(JSON.stringify(toolMessages),/NATIVE_SHELL_OK/);
  assert.ok(childRequests>=1,'native spawned child used real model provider');
  assert.ok((await api(`/api/sessions/${session.id}/native`)).children.length>=1,'native child catalog persisted');
  const plan=await api(`/api/sessions/${session.id}/native/commands`,{line:'/plan on'});
  assert.equal(plan.result.kind,'success');
  const events=(await api(`/api/sessions/${session.id}/history?events=1`)).events;
  assert.ok(events.some(e=>e.type==='tool/call'&&e.data.name==='edit'));
  await rpc('settings.update',{patch:{action_approval_mode:'ask'}});
  await api(`/api/sessions/${session.id}/messages`,{commandId:'ask-native',text:'ASK_NATIVE'},202);
  const pending=await until(async()=>{const {approvals}=await rpc('action.approvals.list',{conversation_id:session.id});return approvals[0];},'native per-action approval');
  assert.match(pending.title,/DSH.*write/);
  await assert.rejects(readFile(join(home,'workspace','denied.txt')),{code:'ENOENT'});
  await rpc('action.approvals.decide',{conversation_id:session.id,approval_id:pending.id,allow:false});
  await until(async()=>(await rpc('command.list',{conversation_id:session.id,command_id:'ask-native'})).commands.some(c=>c.state==='completed'),'denied native tool settles');
  await assert.rejects(readFile(join(home,'workspace','denied.txt')),{code:'ENOENT'});
});
