/** Real DSH, product owner and an isolated WeChat/model transport. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync} from 'node:fs';
import {mkdir, mkdtemp, readFile, realpath, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {createRequire} from 'node:module';
import {createCipheriv, createDecipheriv, createHash, randomBytes} from 'node:crypto';

const root=fileURLToPath(new URL('../',import.meta.url));
async function until(fn,label,timeout=25000){const end=Date.now()+timeout;while(Date.now()<end){const v=await fn();if(v)return v;await delay(70);}throw Error(`timeout: ${label}`);}

test('assistant coordinates the original DSH session with remote authority', {timeout:90000}, async t=>{
  const python=join(root,'.venv/Scripts/python.exe');
  if(process.platform!=='win32'||!existsSync(python)||!existsSync(join(root,'kernel/dsh/packages/core/agent/lib/index.js'))){t.skip('requires built Windows kernel');return;}
  const home=await mkdtemp(join(tmpdir(),'mirally assistant e2e 中文 '));
  const fileKey=randomBytes(16),cipher=createCipheriv('aes-128-ecb',fileKey,null);
  const encryptedFile=Buffer.concat([cipher.update(Buffer.from('实验文件内容 BETA-82')),cipher.final()]);
  const queue=[],replies=[],errors=[],seen=[],paths=[];let child,logs='',targetId,uploadBytes,mediaMessage,allowUpload=false;
  const artifactId='abcdef0123456789abcdef0123456789abcd',artifactContent='实验结果 DELTA-42';
  const fixture=createServer(async(req,res)=>{
    try {
      const chunks=[];for await(const chunk of req)chunks.push(chunk);const bytes=Buffer.concat(chunks),raw=bytes.toString();
      if(req.url.startsWith('/c2c/download')){res.writeHead(200,{'content-type':'application/octet-stream'});return res.end(encryptedFile);}
      if(req.url.startsWith('/c2c/upload')){uploadBytes=bytes;res.writeHead(200,{'x-encrypted-param':'outbound-fixture'});return res.end();}
      if(req.url.startsWith('/ilink/')){
        paths.push(req.url);
        res.setHeader('content-type','application/json');
        if(req.url.startsWith('/ilink/bot/get_bot_qrcode'))return res.end(JSON.stringify({qrcode:'fixture',qrcode_img_content:'fixture-scan'}));
        if(req.url.startsWith('/ilink/bot/get_qrcode_status'))return res.end(JSON.stringify({status:'confirmed',bot_token:'fixture',ilink_bot_id:'bot',ilink_user_id:'owner'}));
        if(req.url==='/ilink/bot/getuploadurl'){await until(()=>allowUpload,'source completion before media upload',20000);return res.end(JSON.stringify({ret:0,upload_param:'outbound'}));}
        if(req.url==='/ilink/bot/sendmessage'){const item=JSON.parse(raw).msg.item_list[0];if(item.text_item)replies.push(item.text_item.text);else mediaMessage=item;return res.end(JSON.stringify({ret:0,message_id:String(replies.length)}));}
        if(req.url==='/ilink/bot/getupdates'){await delay(100);return res.end(JSON.stringify({ret:0,msgs:queue.splice(0),get_updates_buf:'cursor'}));}
        return res.end(JSON.stringify({ret:0,typing_ticket:'fixture'}));
      }
      if(req.url==='/v1/audio/transcriptions'){res.writeHead(200,{'content-type':'application/json'});return res.end(JSON.stringify({text:'语音识别测试'}));}
      const input=JSON.parse(raw), user=[...input.messages].reverse().find(m=>m.role==='user' && ['桌面实验上下文','请助理继续实验','请助理发送报告','来自微信的委托','附件测试','微信语音转写'].some(value=>JSON.stringify(m.content).includes(value))), last=JSON.stringify(user?.content);
      const from= input.messages.lastIndexOf(user);const toolResults=input.messages.slice(from+1).filter(m=>m.role==='tool');
      seen.push({last,toolResults,all:JSON.stringify(input.messages)});
      let tool,args,answer;
      if(last.includes('桌面实验上下文')) answer='桌面记录：实验编号 ALPHA-73。';
      else if(last.includes('来自微信的委托') && last.includes('发送报告')) {if(!toolResults.length){tool='mirally_send_weixin_artifact';args={artifact_id:artifactId};}else {assert.match(JSON.stringify(toolResults),/已排队/);answer='报告已排队发送。';}}
      else if(last.includes('来自微信的委托')) {assert.match(JSON.stringify(input.messages),/ALPHA-73/);answer='原实验会话确认 ALPHA-73。';}
      else if(last.includes('附件测试')) {assert.match(last,/BETA-82/);answer='附件内容 BETA-82 已接入。';}
      else if(last.includes('微信语音转写')) answer='收到语音转写。';
      else if(last.includes('请助理继续实验')) {
        if(!toolResults.length){tool='mirally_find_conversations';args={query:'实验'};}
        else if(toolResults.length===1){assert.match(JSON.stringify(toolResults[0]),/实验/);tool='mirally_delegate_conversation';args={conversation_id:targetId,instruction:'查看实验编号并回复'};}
        else {assert.match(JSON.stringify(toolResults.at(-1)),/ALPHA-73/);answer='助理收到：原实验会话确认 ALPHA-73。';}
      } else if(last.includes('请助理发送报告')) {
        if(!toolResults.length){tool='mirally_delegate_conversation';args={conversation_id:targetId,instruction:'请通过微信发送报告'};}
        else answer='助理已安排发送报告。';
      } else throw Error('unexpected prompt '+last);
      const delta=tool?{tool_calls:[{index:0,id:`call-${toolResults.length}`,type:'function',function:{name:tool,arguments:JSON.stringify(args)}}]}:{content:answer};
      res.writeHead(200,{'content-type':'text/event-stream'});
      for(const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:tool?'tool_calls':'stop'}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
      res.end('data: [DONE]\n\n');
    } catch(e){errors.push(e.stack);res.writeHead(500);res.end('fixture error');}
  });
  await new Promise(resolve=>fixture.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
    fixture.closeAllConnections();await new Promise(resolve=>fixture.close(resolve));const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally assistant e2e 中文 '));await rm(canonical,{recursive:true,force:true});});
  const preload=join(home,'fixture-model.mjs'),models=pathToFileURL(join(root,'kernel/dsh/packages/mirally/mirally-product/src/models.mjs')).href;
  await writeFile(preload,`import {ProductModels} from ${JSON.stringify(models)};\nconst sync=ProductModels.prototype.sync;const requireOptions=ProductModels.prototype.requireOptions;\nProductModels.prototype.sync=function(s){if(this.testMode&&!this.fixture){this.registration?.();this.fixture=new ProductModels(this.ctx,{testMode:false});}if(this.fixture){const value=sync.call(this.fixture,s);this.options=value;return value;}return sync.call(this,s);};\nProductModels.prototype.requireOptions=function(...a){return this.fixture?requireOptions.call(this.fixture,...a):requireOptions.call(this,...a);};\n`);
  child=spawn(process.execPath,['--import',pathToFileURL(preload).href,join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',python,'--test-mode'],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',MIRALLY_TEST_WEIXIN_URL:`http://127.0.0.1:${fixture.address().port}`},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',x=>logs+=x);child.stderr.on('data',x=>logs+=x);
  const runtime=await until(async()=>{if(child.exitCode!==null)throw Error(logs);try{const value=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return value.pid===child.pid&&value;}catch{return false;}},'host');
  const api=async(path,value,expected=200)=>{const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  await rpc('settings.update',{patch:{model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${fixture.address().port}/v1`,api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  targetId=(await api('/api/sessions',{title:'实验'},201)).id;
  const outputDir=join(home,'workspace','outputs',createHash('sha256').update(targetId).digest('hex').slice(0,24));
  await mkdir(outputDir,{recursive:true});await mkdir(join(home,'artifacts'),{recursive:true});
  const outputPath=join(outputDir,`${artifactId.slice(0,8)}-报告.txt`);await writeFile(outputPath,artifactContent);
  await writeFile(join(home,'artifacts',artifactId+'.json'),JSON.stringify({id:artifactId,conversation_id:targetId,name:'报告.txt',title:'报告.txt',bytes:Buffer.byteLength(artifactContent),path:join('workspace','outputs',basename(outputDir),basename(outputPath)),created_at:new Date().toISOString()}));
  await api(`/api/sessions/${targetId}/messages`,{commandId:'desktop-initial',text:'桌面实验上下文'},202);
  await until(async()=>(await rpc('command.list',{conversation_id:targetId,command_id:'desktop-initial'})).commands.some(x=>x.state==='completed'),'desktop history').catch(error=>{error.message+=` errors=${JSON.stringify(errors)} seen=${JSON.stringify(seen)} logs=${logs}`;throw error;});
  await rpc('weixin.login.start');const pending=await until(async()=>{const value=await rpc('weixin.status');return value.state==='awaiting_confirmation'&&value;},'login');
  await rpc('weixin.bind',{expected_revision:pending.revision,conversation_id:targetId});
  await rpc('settings.update',{patch:{weixin_remote_control_enabled:true}});
  const direct=await rpc('weixin.status');const assistant=await rpc('weixin.mode',{expected_revision:direct.revision,mode:'assistant'});
  assert.equal(assistant.mode,'assistant');assert.notEqual(assistant.conversation_id,targetId);
  const listed=(await api('/api/sessions')).sessions;
  assert.equal(listed[0].id,assistant.conversation_id);assert.equal(listed[0].pinned,true);
  await delay(350); // Let the aborted fixture long poll finish without consuming the new route's input.
  queue.push({message_type:1,message_state:2,message_id:'1',from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'context',item_list:[{type:1,text_item:{text:'请助理继续实验'}}]});
  await until(()=>replies.some(text=>text.includes('助理收到：原实验会话确认 ALPHA-73')),'assistant delegated result').catch(async error=>{error.message+=` status=${JSON.stringify(await rpc('weixin.status'))} paths=${JSON.stringify(paths.slice(-15))} replies=${JSON.stringify(replies)} errors=${JSON.stringify(errors)} seen=${JSON.stringify(seen.map(x=>({last:x.last,toolResults:x.toolResults})))} logs=${logs}`;throw error;});
  const history=await api(`/api/sessions/${targetId}/history`);
  assert.ok(history.messages.some(message=>message.input_origin==='weixin'&&message.text.includes('来自微信的委托')));
  assert.ok(history.messages.some(message=>message.text.includes('ALPHA-73')));
  const previousSourceCommands=new Set((await rpc('command.list',{conversation_id:assistant.conversation_id})).commands.map(command=>command.command_id));
  queue.push({message_type:1,message_state:2,message_id:'send-artifact',from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'context',item_list:[{type:1,text_item:{text:'请助理发送报告'}}]});
  await until(async()=>{const commands=(await rpc('command.list',{conversation_id:assistant.conversation_id})).commands;return commands.some(command=>!previousSourceCommands.has(command.command_id)&&command.state==='completed');},'source assistant completed before file send');
  allowUpload=true;
  await until(()=>mediaMessage,'delegated artifact delivery').catch(error=>{error.message+=` errors=${JSON.stringify(errors)} seen=${JSON.stringify(seen.map(x=>({last:x.last,toolResults:x.toolResults})))} logs=${logs}`;throw error;});
  assert.equal(mediaMessage.type,4);assert.equal(mediaMessage.file_item.file_name,'报告.txt');
  const mediaKey=Buffer.from(Buffer.from(mediaMessage.file_item.media.aes_key,'base64').toString('ascii'),'hex');
  const decipher=createDecipheriv('aes-128-ecb',mediaKey,null);assert.equal(Buffer.concat([decipher.update(uploadBytes),decipher.final()]).toString(),artifactContent);
  if(process.env.MIRALLY_SPEECH_GUI==='1') {
    const require=createRequire(join(root,'integrations/openclaw/package.json'));
    const browser=await require('playwright-core').chromium.launch({channel:'msedge',headless:true});
    try {
      const page=await browser.newPage({viewport:{width:1280,height:900}}),pageErrors=[];
      page.on('pageerror',error=>pageErrors.push(error.message));
      await page.addInitScript(()=>{
        Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}});
        window.MediaRecorder=class {
          state='inactive';mimeType='audio/webm';ondataavailable=null;onstop=null;onerror=null;
          start(){this.state='recording';}
          stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['fixture audio'],{type:'audio/webm'})});this.onstop?.();}
        };
      });
      await page.goto(`${runtime.url}/settings/general#token=${runtime.token}`);
      await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
      const settings=page.locator('mirally-speech-settings');await settings.getByLabel('识别服务').selectOption('openai_compatible');
      await settings.getByLabel('语音服务地址').fill(`http://127.0.0.1:${fixture.address().port}/v1`);
      await settings.getByLabel('语音模型').fill('fixture-asr');await settings.getByLabel('语音 API 密钥').fill('fixture-key');
      await settings.getByRole('button',{name:'保存'}).click();
      await until(async()=>(await rpc('speech.settings')).provider==='openai_compatible','speech UI save');
      const screenshots=join(root,'.artifacts','speech-ui');await mkdir(screenshots,{recursive:true});
      await settings.scrollIntoViewIfNeeded();await page.screenshot({path:join(screenshots,'settings.png')});
      await page.goto(`${runtime.url}/chat/main/${encodeURIComponent(targetId)}#token=${runtime.token}`);
      const pane=page.locator('openclaw-chat-pane.chat-pane-cache__pane--active'),input=pane.locator('textarea[aria-label="聊天输入框"]');
      await input.fill('原有草稿');const voice=pane.locator('mirally-voice-input');await voice.getByRole('button',{name:'语音输入'}).click();
      await voice.getByRole('button',{name:'停止录音'}).click();
      await page.waitForFunction(()=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active textarea[aria-label="聊天输入框"]')?.value.includes('语音识别测试'),null,{timeout:8000}).catch(async error=>{throw Error(`${error.message} voice=${await voice.evaluate(e=>JSON.stringify({html:e.innerHTML,phase:e.phase,generation:e.generation,session:e.sessionKey,capture:e.captureSession,draft:e.draft,handler:typeof e.onDraftChange}))} draft=${await input.inputValue()} paths=${JSON.stringify(paths.slice(-10))} pageErrors=${JSON.stringify(pageErrors)} logs=${logs}`);});
      assert.equal(await input.inputValue(),'原有草稿 语音识别测试');
      await page.screenshot({path:join(screenshots,'chat-wide.png')});
      await page.route('**/api/sessions/*/recap', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'ready',recap:{text:'测试摘要',stale:false}})});
      });
      const sessionMenu=pane.locator('openclaw-chat-header-session-menu');
      await sessionMenu.locator('button[aria-haspopup="menu"]').click();
      await sessionMenu.getByText('会话摘要',{exact:true}).click();
      const recap=page.locator('dialog.mirally-recap-dialog');
      await recap.waitFor({state:'visible'});
      await recap.getByRole('button',{name:'生成摘要'}).click();
      await recap.getByText('测试摘要',{exact:true}).waitFor();
      await page.screenshot({path:join(screenshots,'recap.png')});
      await recap.getByRole('button',{name:'关闭'}).click();
      assert.equal(await input.inputValue(),'原有草稿 语音识别测试');
      await page.setViewportSize({width:640,height:800});await page.emulateMedia({colorScheme:'dark'});
      const voiceStyle=await voice.getByRole('button',{name:'语音输入'}).evaluate(el=>({color:getComputedStyle(el).color,opacity:getComputedStyle(el).opacity,disabled:el.disabled}));
      assert.equal(voiceStyle.disabled,false);
      await page.screenshot({path:join(screenshots,'chat-narrow.png')});assert.deepEqual(pageErrors,[]);
    } finally {await browser.close();}
  }
  const now=await rpc('weixin.status');const restored=await rpc('weixin.mode',{expected_revision:now.revision,mode:'direct'});
  assert.equal(restored.conversation_id,targetId);assert.equal(restored.mode,'direct');
  await delay(350);
  const sendItems=(id,item_list)=>queue.push({message_type:1,message_state:2,message_id:id,from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'context',item_list});
  sendItems('2',[{type:1,text_item:{text:'附件测试'}},{type:4,file_item:{file_name:'实验.txt',len:String(Buffer.byteLength('实验文件内容 BETA-82')),media:{encrypt_query_param:'fixture',aes_key:Buffer.from(fileKey.toString('hex')).toString('base64')}}}]);
  await until(()=>replies.some(text=>text.includes('附件内容 BETA-82')),'inbound file').catch(error=>{throw Error(`${error.message} replies=${JSON.stringify(replies)} errors=${JSON.stringify(errors)}`);});
  sendItems('3',[{type:3,voice_item:{text:'实验现在怎样'}}]);
  await until(()=>replies.some(text=>text.includes('收到语音转写')),'inbound voice');
  const mediaHistory=await api(`/api/sessions/${targetId}/history`);
  assert.ok(mediaHistory.messages.some(message=>message.input_origin==='weixin'&&message.text.includes('微信语音转写：实验现在怎样')));
  assert.deepEqual(errors,[]);
});
