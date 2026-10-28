/** Real DSH, product sidecar and Weixin wire, isolated local model and iLink fixtures. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdtemp, readFile, realpath, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=fileURLToPath(new URL('../',import.meta.url));
async function until(fn,label,timeout=25000) {const end=Date.now()+timeout; while(Date.now()<end){const v=await fn();if(v)return v;await delay(70);}throw Error(`timeout: ${label}`);}

test('real session experiment is readable and controllable over bound Weixin', {timeout:150000}, async t=>{
  if(process.platform!=='win32'){t.skip('Windows PowerShell');return;}
  const python=join(root,'.venv/Scripts/python.exe');
  if(!existsSync(python)||!existsSync(join(root,'kernel/dsh/packages/core/agent/lib/index.js'))){t.skip('requires built kernel');return;}
  const home=await mkdtemp(join(tmpdir(),'mirally weixin e2e 中文 '));
  const queue=[], replies=[], modelErrors=[], requests=[]; let child, logs='', jobId, remoteHintSeen=false;
  const fixture=createServer(async(req,res)=>{
    try{
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      const body=Buffer.concat(chunks).toString(), path=req.url;
      if(path.startsWith('/ilink/')){
        res.setHeader('content-type','application/json');
        if(path.startsWith('/ilink/bot/get_bot_qrcode')) return res.end(JSON.stringify({qrcode:'fixture',qrcode_img_content:'fixture-scan'}));
        if(path.startsWith('/ilink/bot/get_qrcode_status')) return res.end(JSON.stringify({status:'confirmed',bot_token:'fixture-token',ilink_bot_id:'bot',ilink_user_id:'owner'}));
        if(path==='/ilink/bot/sendmessage'){replies.push(JSON.parse(body).msg.item_list[0].text_item.text);return res.end(JSON.stringify({ret:0,message_id:String(replies.length)}));}
        if(path==='/ilink/bot/getupdates'){await delay(100);return res.end(JSON.stringify({ret:0,msgs:queue.splice(0),get_updates_buf:'cursor'}));}
        throw Error('unknown iLink endpoint '+path);
      }
      const input=JSON.parse(body), last=input.messages.at(-1), lastUser=[...input.messages].reverse().find(m=>m.role==='user' && ['桌面启动实验','微信查询进度','微信停止实验','微信启动新命令','微信询问方案'].some(v=>JSON.stringify(m.content).includes(v)));
      const prompt=JSON.stringify(lastUser?.content), suffix=input.messages.slice(input.messages.lastIndexOf(lastUser)+1);
      const toolResults=suffix.filter(m=>m.role==='tool');
      requests.push({prompt,last:last?.role,toolResults});
      const turn=prompt.includes('桌面启动实验')?'start':prompt.includes('微信查询进度')?'progress':prompt.includes('微信停止实验')?'stop':prompt.includes('微信启动新命令')?'remote_start':prompt.includes('微信询问方案')?'question':null;
      if(turn==='remote_start' && JSON.stringify(input.messages).includes('Remote commands, isolated browser actions and desktop observation are available')) remoteHintSeen=true;
      if(!turn) throw Error('unexpected user prompt '+prompt);
      let tool, args;
      if(!toolResults.length){
        if(turn==='start'){tool='mirally_run_command';args={command:'Write-Output "EXPERIMENT_PROGRESS_73"; Start-Sleep -Seconds 90',cwd:home,background:true};}
        if(turn==='progress'){tool='mirally_job_list';args={};}
        if(turn==='stop'){tool='mirally_stop_job';args={job_id:jobId};}
        if(turn==='remote_start'){tool='mirally_run_command';args={command:`Set-Content -LiteralPath '${join(home,'remote-marker.txt').replaceAll("'","''")}' -Value yes`,cwd:home};}
        if(turn==='question'){tool='ask_user_question';args={questions:[{id:'mode',question:'请选择远程方案',options:[{label:'方案 A'},{label:'方案 B'}]}]};}
      }else if(turn==='progress'&&toolResults.length===1){
        const found=JSON.stringify(toolResults[0]).match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i); if(!found)throw Error('no job id in '+JSON.stringify(toolResults));
        jobId=found[0];tool='mirally_job_status';args={job_id:jobId};
      }
      if(turn==='start'&&toolResults.length){const found=JSON.stringify(toolResults[0]).match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i); if(found)jobId=found[0];}
      const delta=tool?{tool_calls:[{index:0,id:`call-${turn}-${toolResults.length}`,type:'function',function:{name:tool,arguments:JSON.stringify(args)}}]}:{content:`实验结果：${JSON.stringify(toolResults.at(-1)?.content??'无结果')}`};
      res.writeHead(200,{'content-type':'text/event-stream'});
      for(const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:tool?'tool_calls':'stop'}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
      res.end('data: [DONE]\n\n');
    }catch(e){modelErrors.push(e);res.writeHead(500);res.end('fixture error');}
  });
  await new Promise(resolve=>fixture.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
    fixture.closeAllConnections();await new Promise(resolve=>fixture.close(resolve));
    const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally weixin e2e 中文 '));await rm(canonical,{recursive:true,force:true});
  });
  // test-mode is needed for loopback iLink. Replace its deterministic text-only model
  // with the real configured DSH model adapter only within this isolated child process.
  const preload=join(home,'fixture-model.mjs'), models=pathToFileURL(join(root,'kernel/dsh/packages/mirally/mirally-product/src/models.mjs')).href;
  await writeFile(preload,`import {ProductModels} from ${JSON.stringify(models)};\nconst sync=ProductModels.prototype.sync;\nconst requireOptions=ProductModels.prototype.requireOptions;\nProductModels.prototype.sync=function(s){if(this.testMode&&!this.fixture){this.registration?.();this.fixture=new ProductModels(this.ctx,{testMode:false});}if(this.fixture){const value=sync.call(this.fixture,s);this.options=value;return value;}return sync.call(this,s);};\nProductModels.prototype.requireOptions=function(...a){return this.fixture?requireOptions.call(this.fixture,...a):requireOptions.call(this,...a);};\n`);
  child=spawn(process.execPath,['--import',pathToFileURL(preload).href,join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',python,'--test-mode'],
    {cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',MIRALLY_TEST_WEIXIN_URL:`http://127.0.0.1:${fixture.address().port}`},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',x=>{logs+=x;});child.stderr.on('data',x=>{logs+=x;});
  const runtime=await until(async()=>{if(child.exitCode!==null) throw Error('child exited: '+logs); try{const x=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return x.pid===child.pid&&x;}catch{return false;}},'host start');
  const api=async(path,value,expected=200)=>{const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  await rpc('settings.update',{patch:{model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${fixture.address().port}/v1`,api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  const session=await api('/api/sessions',{title:'实验'},201);
  await rpc('weixin.login.start');
  const pending=await until(async()=>{const status=await rpc('weixin.status');return status.state==='awaiting_confirmation'&&status;},'wechat login');
  await rpc('weixin.bind',{expected_revision:pending.revision,conversation_id:session.id});
  await api(`/api/sessions/${session.id}/messages`,{commandId:'desktop-start',text:'桌面启动实验'},202);
  const approval=await until(async()=>(await rpc('action.approvals.list',{conversation_id:session.id})).approvals.find(a=>a.command_id==='desktop-start'),'desktop approval',5000).catch(async e=>{e.message+=` logs=${logs} errors=${modelErrors.map(x=>x.stack)} requests=${JSON.stringify(requests)} command=${JSON.stringify(await rpc('command.list',{conversation_id:session.id,command_id:'desktop-start'}))} history=${JSON.stringify(await api(`/api/sessions/${session.id}/history?events=1`))}`;throw e;});
  await rpc('action.approvals.decide',{approval_id:approval.id,conversation_id:session.id,allow:true});
  await until(()=>jobId,'job started',5000).catch(async e=>{e.message+=` requests=${JSON.stringify(requests)} errors=${modelErrors.map(x=>x.stack)} history=${JSON.stringify((await api(`/api/sessions/${session.id}/history?events=1`)).events.filter(x=>['tool/result','assistant/attempt','turn/end'].includes(x.type)))}`;throw e;});
  await until(async()=>(await rpc('command.list',{conversation_id:session.id,command_id:'desktop-start'})).commands.some(x=>x.state==='completed'),'desktop turn complete');
  const send=(id,text)=>queue.push({message_type:1,message_state:2,message_id:String(id),from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'fixture-context',item_list:[{type:1,text_item:{text}}]});
  send(1,'微信查询进度');
  await until(()=>replies.some(text=>text.includes('EXPERIMENT_PROGRESS_73')),'progress reply');
  send(2,'微信停止实验');
  await until(()=>requests.some(x=>x.prompt.includes('微信停止实验') && x.toolResults.length),'disabled stop');
  assert.equal(replies.some(text=>text.includes('/approve ')),false);
  await rpc('settings.update',{patch:{weixin_remote_control_enabled:true}});
  const afterEnable=replies.length;send(20,'微信查询进度');
  await until(()=>replies.slice(afterEnable).some(text=>text.includes('EXPERIMENT_PROGRESS_73')&&text.includes('running')),'progress after enabling');
  const previous=replies.length;send(3,'微信停止实验');
  const request=await until(()=>replies.slice(previous).find(text=>text.includes('/approve ')),'remote stop approval');
  const code=request.match(/\/approve ([a-f0-9]{16})/)[1];send(4,`/deny ${code}`);
  await until(()=>replies.slice(previous).some(text=>text.includes('未批准')),'denied stop reply');
  const before=replies.length;send(5,'微信停止实验');
  const approved=await until(()=>replies.slice(before).find(text=>text.includes('/approve ')),'second remote stop approval');
  send(6,`/approve ${approved.match(/\/approve ([a-f0-9]{16})/)[1]}`);
  await until(()=>replies.slice(before).some(text=>text.includes('cancelled')),'stopped reply');
  const startAt=replies.length;send(7,'微信启动新命令');
  const startReview=await until(()=>replies.slice(startAt).find(text=>text.includes('/approve ')),'remote command approval');
  assert.match(startReview,/remote-marker/);
  send(8,`/deny ${startReview.match(/\/approve ([a-f0-9]{16})/)[1]}`);
  await until(()=>replies.slice(startAt).some(text=>text.includes('未批准')),'denied command');
  assert.equal(existsSync(join(home,'remote-marker.txt')),false);
  const startAgain=replies.length;send(9,'微信启动新命令');
  const approvedStart=await until(()=>replies.slice(startAgain).find(text=>text.includes('/approve ')),'remote command second approval');
  send(10,`/approve ${approvedStart.match(/\/approve ([a-f0-9]{16})/)[1]}`);
  await until(()=>existsSync(join(home,'remote-marker.txt')),'approved remote command effect');
  assert.equal((await readFile(join(home,'remote-marker.txt'),'utf8')).trim(),'yes');
  await until(()=>replies.slice(startAgain).some(text=>text.includes('实验结果')),'remote command completed');
  await rm(join(home,'remote-marker.txt'));
  // Global trust does not automatically grant a paired channel trust. Its
  // separate switch takes effect for the next remote command in either direction.
  await rpc('settings.update',{patch:{action_approval_mode:'trusted'}});
  const trustedAt=replies.length;send(101,'微信启动新命令');
  const trustedReview=await until(()=>replies.slice(trustedAt).find(text=>text.includes('/approve ')),'trusted without remote inheritance still prompts');
  assert.equal(existsSync(join(home,'remote-marker.txt')),false);
  send(102,`/deny ${trustedReview.match(/\/approve ([a-f0-9]{16})/)[1]}`);
  await until(()=>replies.slice(trustedAt).some(text=>text.includes('未批准')),'trusted remote denial completed');
  await rpc('settings.update',{patch:{weixin_auto_approve_enabled:true}});
  const inheritedAt=replies.length;send(103,'微信启动新命令');
  await until(()=>existsSync(join(home,'remote-marker.txt')),'remote inherited trust executed');
  await until(()=>replies.slice(inheritedAt).some(text=>text.includes('实验结果')),'inherited remote command completed');
  assert.equal(replies.slice(inheritedAt).some(text=>text.includes('/approve ')),false);
  assert.equal((await readFile(join(home,'remote-marker.txt'),'utf8')).trim(),'yes');
  await rm(join(home,'remote-marker.txt'));
  await rpc('settings.update',{patch:{weixin_auto_approve_enabled:false}});
  const revokedTrustAt=replies.length;send(104,'微信启动新命令');
  const revokedTrust=await until(()=>replies.slice(revokedTrustAt).find(text=>text.includes('/approve ')),'remote inheritance revoked prompts');
  assert.equal(existsSync(join(home,'remote-marker.txt')),false);
  send(105,`/deny ${revokedTrust.match(/\/approve ([a-f0-9]{16})/)[1]}`);
  await until(()=>replies.slice(revokedTrustAt).some(text=>text.includes('未批准')),'revoked trust denial completed');
  const questionAt=replies.length;send(106,'微信询问方案');
  const question=await until(()=>replies.slice(questionAt).find(text=>text.includes('/answer ')),'native question delivered to WeChat');
  assert.match(question,/请选择远程方案/);
  await delay(200);
  send(107,`/answer ${question.match(/\/answer ([a-f0-9]{16})/)[1]} 方案 B`);
  await until(()=>requests.some(r=>r.prompt.includes('微信询问方案')&&JSON.stringify(r.toolResults).includes('方案 B')),'native structured answer reaches same model turn');
  await until(()=>replies.slice(questionAt).some(text=>text.includes('实验结果')&&text.includes('方案 B')),'native question turn resumes over WeChat');
  const beforeDisable=replies.length;send(11,'微信启动新命令');
  const revoked=await until(()=>replies.slice(beforeDisable).find(text=>text.includes('/approve ')),'pending command');
  await rpc('settings.update',{patch:{weixin_remote_control_enabled:false}});
  send(12,`/approve ${revoked.match(/\/approve ([a-f0-9]{16})/)[1]}`);
  await delay(1000);
  assert.equal(existsSync(join(home,'remote-marker.txt')),false,'revoked approval cannot execute');
  const history=await api(`/api/sessions/${session.id}/history`);
  assert.ok(history.messages.some(message=>message.role==='user'&&message.input_origin==='desktop'&&message.text==='桌面启动实验'));
  assert.ok(history.messages.some(message=>message.role==='user'&&message.input_origin==='weixin'&&message.text==='微信查询进度'));
  assert.equal(remoteHintSeen,true,'enabled WeChat capability context must reach the model');
  assert.deepEqual(modelErrors,[]);
});
