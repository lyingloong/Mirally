/** Real DSH compaction + pruning against a local OpenAI protocol fixture. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdtemp, mkdir, readFile, writeFile, realpath, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root = fileURLToPath(new URL('../', import.meta.url));
async function until(check, label, timeout=25000) {
  const end=Date.now()+timeout;
  while(Date.now()<end) { const result=await check(); if(result) return result; await delay(40); }
  throw Error(`timeout: ${label}`);
}
function stream(res, content, usage, tools) {
  res.writeHead(200, {'content-type':'text/event-stream'});
  const delta=tools ? {tool_calls:tools.map((tool,index)=>({index,id:`tool-${index}`,type:'function',function:{name:tool.name,arguments:JSON.stringify(tool.arguments)}}))} : {content};
  res.write(`data: ${JSON.stringify({id:'fixture',choices:[{index:0,delta,finish_reason:null}]})}\n\n`);
  res.write(`data: ${JSON.stringify({id:'fixture',choices:[{index:0,delta:{},finish_reason:tools?'tool_calls':'stop'}],usage:{prompt_tokens:usage,completion_tokens:100,total_tokens:usage+100}})}\n\n`);
  res.end('data: [DONE]\n\n');
}

test('native base prunes tool results and checkpoints a smaller configured model through actual requests', {timeout:90000}, async t => {
  const python=join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  if(!existsSync(python)||!existsSync(join(root,'kernel/dsh/packages/core/agent/lib/index.js'))) { t.skip('prepare repository and kernel'); return; }
  const home=await mkdtemp(join(tmpdir(),'mirally native compaction 中文 '));
  const fixture=join(home,'workspace','large.txt');
  await mkdir(dirname(fixture),{recursive:true});
  const lines=Array.from({length:180},(_,i)=>`${String(i).padStart(3,'0')} ${i===85?'MIDDLE_ONLY_SENTINEL ':''}${'sample evidence '.repeat(14)}`);
  await writeFile(fixture,lines.join('\n'));
  let child, logs='', reads=0, chats=0, summaries=0;
  const requests=[];
  const server=createServer(async(req,res)=>{
    const chunks=[]; for await(const chunk of req) chunks.push(chunk);
    const input=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    requests.push(input);
    const text=JSON.stringify(input.messages);
    if(text.includes('You are now acting as a compaction engine')) {
      summaries++;
      assert.match(text,/ORIGINAL_GOAL_SENTINEL|COMPACTED_GOAL_SENTINEL/);
      return stream(res,'## Primary Request and Intent\n- COMPACTED_GOAL_SENTINEL: keep the original evidence review task and continue the experiment.\n## Next Step\n- Continue the requested work.',500);
    }
    if(!reads++) return stream(res,'',29000,[{name:'read',arguments:{file_path:fixture}}]);
    chats++;
    // Provider-reported prompt usage is deliberately above the 32k route's
    // pressure threshold after the third request. The actual native meter,
    // pruning, summary transaction and model requests all remain unmodified.
    stream(res,`${'analysis evidence '.repeat(230)} RESPONSE_${chats}`,chats>=3?29000:9000+chats*4000);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    if(child?.exitCode===null) {
      child.stdin.end();
      try { await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('shutdown timeout');})]); }
      catch { child.kill(); await Promise.race([once(child,'exit'),delay(3000)]); }
    }
    server.closeAllConnections(); await new Promise(resolve=>server.close(resolve));
    const canonical=await realpath(home); assert.equal(dirname(canonical),await realpath(tmpdir()));
    assert.ok(basename(canonical).startsWith('mirally native compaction 中文 ')); await rm(canonical,{recursive:true,force:true});
  });
  child=spawn(process.execPath,[join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',python],
    {cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',chunk=>{logs+=chunk;}); child.stderr.on('data',chunk=>{logs+=chunk;});
  const runtime=await until(async()=>{ if(child.exitCode!==null) throw Error(logs); try {const r=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return r.pid===child.pid&&r;}catch{return false;}},'host');
  const api=async(path,value,expected=200)=>{
    const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});
    assert.equal(response.status,expected,await response.clone().text()); return response.json();
  };
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  await rpc('settings.update',{patch:{model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture',model_id:'fixture-32k',reasoning_effort:'off',context_window:32768,max_tokens:1024}}});
  const session=await api('/api/sessions',{title:'native compaction'},201);
  for(let index=0;index<5;index++) {
    const commandId=`turn-${index}`;
    const text=index===0?'ORIGINAL_GOAL_SENTINEL Review the evidence in the supplied file.':`Continue preserving the original task, segment ${index}. ${'retain research detail '.repeat(350)}`;
    await api(`/api/sessions/${session.id}/messages`,{commandId,text},202);
    await until(async()=>{const records=(await rpc('command.list',{conversation_id:session.id,command_id:commandId})).commands;
      const done=records.find(c=>['completed','failed'].includes(c.state)); if(done?.state==='failed') throw Error(`${JSON.stringify(done)}\n${logs}`); return done;},`turn ${index}`);
  }
  const toolRequest=requests.find(request=>request.messages.some(message=>message.role==='tool'));
  assert.ok(toolRequest,'actual native read result reached next request');
  const tool=toolRequest.messages.find(message=>message.role==='tool');
  assert.match(JSON.stringify(tool),/tool result middle pruned/);
  assert.doesNotMatch(JSON.stringify(tool),/MIDDLE_ONLY_SENTINEL/);
  assert.ok(summaries>0,`native compaction must run\n${logs}`);
  assert.ok(requests.some(input=>JSON.stringify(input.messages).includes('<compacted-summary>')&&JSON.stringify(input.messages).includes('COMPACTED_GOAL_SENTINEL')),'checkpoint reached the actual subsequent model request');
  const history=await api(`/api/sessions/${session.id}/history?events=1`);
  assert.ok(history.events.some(event=>event.type==='compaction/prune'));
  assert.ok(history.events.some(event=>event.type==='compaction/end'));
  assert.ok(!logs.includes('headroomTokens')&&!logs.includes('TargetPressureConfigError'),logs);
});
