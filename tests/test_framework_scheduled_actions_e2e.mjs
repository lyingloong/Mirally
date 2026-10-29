/** Isolated real DSH host, model fixture, and sidecar; no external API. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,realpath,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {basename,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=fileURLToPath(new URL('../',import.meta.url));
async function until(check,label,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const value=await check();if(value)return value;await delay(50);}throw Error(`timeout: ${label}`);}

test('scheduled action uses the original session and cannot bypass a native action approval', {timeout:120000},async t=>{
  const home=await mkdtemp(join(tmpdir(),'mirally schedule 中文 '));let child,logs='';const requests=[];
  const server=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const input=JSON.parse(Buffer.concat(chunks));requests.push(input);
    const user=input.messages.findLast(message=>message.role==='user' && JSON.stringify(message.content).includes('SCHEDULE_FIXTURE'));
    const tail=input.messages.slice(input.messages.lastIndexOf(user)+1),done=tail.some(message=>message.role==='tool');
    const content=JSON.stringify(user?.content),fired=content.includes('定时任务'),trusted=content.includes('SCHEDULE_FIXTURE_TRUSTED');let delta,reason;
    if(!done){const name=fired?'write':'mirally_schedule_action';
      const args=fired?{file_path:trusted?'scheduled-allowed.txt':'scheduled-denied.txt',content:trusted?'ALLOWED':'must not write'}
        :{title:trusted?'测试可信执行':'测试定时执行',prompt:trusted?'SCHEDULE_FIXTURE_TRUSTED 执行测试':'SCHEDULE_FIXTURE 执行测试',after_seconds:3};
      delta={tool_calls:[{index:0,id:fired?'scheduled-write':'scheduled-create',type:'function',function:{name,arguments:JSON.stringify(args)}}]};reason='tool_calls';
    }else{delta={content:fired?'已处理执行请求。':'定时任务已安排。'};reason='stop';}
    res.writeHead(200,{'content-type':'text/event-stream'});
    for(const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:reason}],usage:{prompt_tokens:20,completion_tokens:10,total_tokens:30}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally schedule 中文 '));await rm(canonical,{recursive:true,force:true});
  });
  child=spawn(process.execPath,[join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',join(root,'.venv/Scripts/python.exe')],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',c=>{logs+=c;});child.stderr.on('data',c=>{logs+=c;});
  const runtime=await until(async()=>{if(child.exitCode!==null)throw Error(logs);try{const r=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return r.pid===child.pid&&r;}catch{return false;}},'host start');
  const api=async(path,value,expected=200)=>{const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  await rpc('settings.update',{patch:{action_approval_mode:'ask',model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  await rpc('calendar.settings.update',{time_zone:'Asia/Shanghai'});
  const session=await api('/api/sessions',{title:'定时执行'},201);
  await api(`/api/sessions/${session.id}/messages`,{commandId:'setup-task',text:'SCHEDULE_FIXTURE 三秒后执行测试。'},202);
  await until(async()=>(await rpc('command.list',{conversation_id:session.id,command_id:'setup-task'})).commands.some(c=>c.state==='completed'),'schedule creation');
  const pending=await until(async()=>{const {approvals}=await rpc('action.approvals.list',{conversation_id:session.id});return approvals[0];},'scheduled action approval');
  assert.match(pending.title,/DSH.*write/);assert.match(pending.command_id,/^schedule-/);
  await assert.rejects(readFile(join(home,'workspace','scheduled-denied.txt')),{code:'ENOENT'});
  await rpc('action.approvals.decide',{conversation_id:session.id,approval_id:pending.id,allow:false});
  await until(async()=>(await rpc('command.list',{conversation_id:session.id,command_id:pending.command_id})).commands.some(c=>c.state==='completed'),'scheduled turn completed');
  const resultNotice=await until(async()=>(await rpc('inbox.list')).items.find(item=>item.title.includes('测试定时执行')),'scheduled result notification');
  assert.equal(resultNotice.conversation_id,session.id);assert.match(resultNotice.body,/查看结果/);
  await assert.rejects(readFile(join(home,'workspace','scheduled-denied.txt')),{code:'ENOENT'});
  const history=await api(`/api/sessions/${session.id}/history`);
  const scheduled=history.messages.filter(message=>message.role==='user'&&message.text.includes('定时任务「测试定时执行」'));
  assert.equal(scheduled.length,1);assert.equal(scheduled[0].input_origin,'schedule');
  assert.ok(requests.some(input=>input.messages.some(message=>JSON.stringify(message.content).includes('定时任务「测试定时执行」'))));
  await rpc('settings.update',{patch:{action_approval_mode:'trusted'}});
  await api(`/api/sessions/${session.id}/messages`,{commandId:'setup-trusted',text:'SCHEDULE_FIXTURE_TRUSTED 三秒后执行测试。'},202);
  await until(async()=>(await rpc('command.list',{conversation_id:session.id,command_id:'setup-trusted'})).commands.some(c=>c.state==='completed'),'trusted schedule creation');
  await until(async()=>{try{return (await readFile(join(home,'workspace','scheduled-allowed.txt'),'utf8'))==='ALLOWED';}catch{return false;}},'trusted scheduled action');
  const trustedHistory=await api(`/api/sessions/${session.id}/history`);
  assert.ok(trustedHistory.messages.some(message=>message.role==='user'&&message.text.includes('定时任务「测试可信执行」')&&message.input_origin==='schedule'));
  assert.equal((await rpc('action.approvals.list',{conversation_id:session.id})).approvals.length,0);
});
