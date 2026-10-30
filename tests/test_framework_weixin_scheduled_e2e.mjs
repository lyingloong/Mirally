/** WeChat-created scheduled tasks retain remote authority across a real host restart. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,realpath,rm,writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename,dirname,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {ProductMaterials} from '../kernel/dsh/packages/mirally/mirally-product/src/materials.mjs';

const root=fileURLToPath(new URL('../',import.meta.url));
async function until(check,label,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const value=await check();if(value)return value;await delay(70);}throw Error(`timeout: ${label}`);}

test('WeChat schedule survives restart and retains channel approval and revocation', {timeout:120000},async t=>{
  const python=join(root,'.venv/Scripts/python.exe');
  if(process.platform!=='win32'||!existsSync(python)){t.skip('requires Windows product environment');return;}
  const home=await mkdtemp(join(tmpdir(),'mirally weixin schedule 中文 '));let child,logs='',runtime;
  const queue=[],replies=[],errors=[],requests=[],media=[];let artifactId;
  const server=createServer(async(req,res)=>{
    try {
      const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=Buffer.concat(chunks).toString();
      if(req.url.startsWith('/ilink/')){
        res.setHeader('content-type','application/json');
        if(req.url.startsWith('/ilink/bot/get_bot_qrcode'))return res.end(JSON.stringify({qrcode:'fixture',qrcode_img_content:'fixture-scan'}));
        if(req.url.startsWith('/ilink/bot/get_qrcode_status'))return res.end(JSON.stringify({status:'confirmed',bot_token:'fixture',ilink_bot_id:'bot',ilink_user_id:'owner'}));
        if(req.url==='/ilink/bot/getupdates'){await delay(100);return res.end(JSON.stringify({ret:0,msgs:queue.splice(0),get_updates_buf:'cursor'}));}
        if(req.url==='/ilink/bot/getuploadurl')return res.end(JSON.stringify({ret:0,upload_param:'fixture-upload'}));
        if(req.url==='/ilink/bot/sendmessage'){const msg=JSON.parse(body).msg,item=msg.item_list[0];if(item.text_item)replies.push(item.text_item.text);else media.push(msg);return res.end(JSON.stringify({ret:0}));}
        if(req.url==='/ilink/bot/getconfig')return res.end(JSON.stringify({ret:0,typing_ticket:'fixture'}));
        if(['/ilink/bot/sendtyping','/ilink/bot/msg/notifystart','/ilink/bot/msg/notifystop'].includes(req.url))return res.end(JSON.stringify({ret:0}));
        throw Error('unexpected ilink '+req.url);
      }
      if(req.url.startsWith('/c2c/upload?')) {res.writeHead(200,{'x-encrypted-param':'fixture-download'});return res.end();}
      const input=JSON.parse(body), user=input.messages.findLast(message=>message.role==='user' && ['REMOTE_CREATE','REMOTE_SECOND','REMOTE_RUN'].some(text=>JSON.stringify(message.content).includes(text)));
      const content=JSON.stringify(user?.content),tail=input.messages.slice(input.messages.lastIndexOf(user)+1),done=tail.some(item=>item.role==='tool');
      requests.push({content,done});let delta,reason;
      if(!done){
        const fired=content.includes('定时任务'),second=content.includes('REMOTE_SECOND');
        const name=fired?'mirally_run_command':'mirally_schedule_action';
        const args=fired?{command:`Set-Content -LiteralPath '${join(home,'scheduled-marker.txt').replaceAll("'","''")}' -Value worked`,cwd:home}
          :{title:second?'微信暂停测试':'微信重启测试',prompt:'REMOTE_RUN 检查实验',after_seconds:second?7:12};
        delta={tool_calls:[{index:0,id:fired?'scheduled-action':'create-schedule',type:'function',function:{name,arguments:JSON.stringify(args)}}]};reason='tool_calls';
      }else if(content.includes('定时任务') && tail.filter(item=>item.role==='tool').length===1) {
        delta={tool_calls:[{index:0,id:'scheduled-calendar',type:'function',function:{name:'mirally_create_event',arguments:JSON.stringify({title:'定时写入日历',start_local:'2030-01-01T09:00'})}}]};reason='tool_calls';
      }else if(content.includes('定时任务') && tail.filter(item=>item.role==='tool').length===2) {
        delta={tool_calls:[{index:0,id:'scheduled-file',type:'function',function:{name:'mirally_send_weixin_artifact',arguments:JSON.stringify({artifact_id:artifactId})}}]};reason='tool_calls';
      }else {delta={content:content.includes('定时任务')?'定时回合已处理。':'定时任务已保存。'};reason='stop';}
      res.writeHead(200,{'content-type':'text/event-stream'});
      for(const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:reason}],usage:{prompt_tokens:20,completion_tokens:10,total_tokens:30}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
      res.end('data: [DONE]\n\n');
    }catch(error){errors.push(error.stack);res.writeHead(500);res.end('fixture error');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const stop=async()=>{if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}};
  t.after(async()=>{await stop();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally weixin schedule 中文 '));await rm(canonical,{recursive:true,force:true});});
  const preload=join(home,'fixture-model.mjs'),models=pathToFileURL(join(root,'kernel/dsh/packages/mirally/mirally-product/src/models.mjs')).href;
  await writeFile(preload,`import {ProductModels} from ${JSON.stringify(models)};\nconst sync=ProductModels.prototype.sync;const requireOptions=ProductModels.prototype.requireOptions;\nProductModels.prototype.sync=function(s){if(this.testMode&&!this.fixture){this.registration?.();this.fixture=new ProductModels(this.ctx,{testMode:false});}if(this.fixture){const value=sync.call(this.fixture,s);this.options=value;return value;}return sync.call(this,s);};\nProductModels.prototype.requireOptions=function(...a){return this.fixture?requireOptions.call(this.fixture,...a):requireOptions.call(this,...a);};\n`);
  const start=async()=>{child=spawn(process.execPath,['--import',pathToFileURL(preload).href,join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',python,'--test-mode'],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',MIRALLY_TEST_WEIXIN_URL:`http://127.0.0.1:${server.address().port}`},stdio:['pipe','pipe','pipe']});
    child.stdout.on('data',value=>logs+=value);child.stderr.on('data',value=>logs+=value);
    runtime=await until(async()=>{if(child.exitCode!==null)throw Error(logs);try{const x=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return x.pid===child.pid&&x;}catch{return false;}},'host start');};
  const api=async(path,value,expected=200)=>{const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  const send=(id,text)=>queue.push({message_type:1,message_state:2,message_id:id,from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'fixture-context',item_list:[{type:1,text_item:{text}}]});
  await start();
  await rpc('settings.update',{patch:{action_approval_mode:'ask',model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  await rpc('calendar.settings.update',{time_zone:'Asia/Shanghai'});
  const session=await api('/api/sessions',{title:'远程定时'},201);
  await rpc('weixin.login.start');const login=await until(async()=>{const status=await rpc('weixin.status');return status.state==='awaiting_confirmation'&&status;},'login');
  await rpc('weixin.bind',{expected_revision:login.revision,conversation_id:session.id});
  await rpc('settings.update',{patch:{weixin_remote_control_enabled:true,weixin_auto_approve_enabled:false}});
  send('1','REMOTE_CREATE 请稍后执行');
  await until(()=>replies.some(value=>value.includes('定时任务已保存')),'scheduled request accepted').catch(error=>{throw Error(`${error.message} replies=${JSON.stringify(replies)} logs=${logs}`);});
  const before=JSON.parse(await readFile(join(home,'scheduled-actions.json'),'utf8')).jobs[0];
  assert.equal(before.authority.origin,'weixin');assert.equal(before.status,'active');
  const material=await new ProductMaterials({home,broadcast:()=>{}}).publish(before.conversation_id,'fixture',{name:'报告.txt',content:'scheduled result'});
  artifactId=material.id;
  await stop();await start();await delay(300);
  const approval=await until(()=>replies.find(value=>value.includes('/approve ')),'remote scheduled approval').catch(error=>{throw Error(`${error.message} replies=${JSON.stringify(replies)} requests=${JSON.stringify(requests)} errors=${JSON.stringify(errors)} logs=${logs}`);});
  assert.equal(existsSync(join(home,'scheduled-marker.txt')),false);
  send('2',`/approve ${approval.match(/\/approve ([0-9]{9})/)[1]}`);
  await until(()=>existsSync(join(home,'scheduled-marker.txt')),'explicitly approved scheduled action');
  await until(async()=>(await api(`/api/sessions/${session.id}/history`)).messages.some(value=>value.text?.includes('定时回合已处理')),'scheduled completion');
  await until(()=>media.length,'scheduled artifact sent',10000).catch(async error=>{throw Error(`${error.message} replies=${JSON.stringify(replies)} history=${JSON.stringify(await api(`/api/sessions/${session.id}/history`))} errors=${JSON.stringify(errors)} logs=${logs}`);});
  assert.equal(media[0].context_token,'fixture-context');assert.equal(media[0].item_list[0].file_item.file_name,'报告.txt');
  assert.ok((await rpc('calendar.event.list',{limit:100})).events.some(event=>event.title==='定时写入日历'));
  const history=await api(`/api/sessions/${session.id}/history`);
  assert.ok(history.messages.some(message=>message.input_origin==='weixin'&&message.text.includes('定时任务「微信重启测试」')));
  const replyOffset=replies.length;send('3','REMOTE_SECOND 再过几秒检查');
  await until(()=>replies.slice(replyOffset).some(value=>value.includes('定时任务已保存')),'second schedule saved');
  const current=await rpc('weixin.status');await rpc('weixin.disconnect',{expected_revision:current.revision});
  await until(async()=>{const data=JSON.parse(await readFile(join(home,'scheduled-actions.json'),'utf8'));return data.jobs.find(job=>job.record.title==='微信暂停测试')?.status==='paused';},'revoked task pauses');
  assert.equal(requests.filter(item=>item.content.includes('微信暂停测试')).length,0);
  assert.deepEqual(errors,[]);
});
