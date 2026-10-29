/** Isolated real DSH + Python state and local OpenAI/iLink fixtures. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,realpath,rm,writeFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {tmpdir} from 'node:os';
import {basename,dirname,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=fileURLToPath(new URL('../',import.meta.url));
async function until(check,label,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const value=await check();if(value)return value;await delay(80);}throw Error(`timeout: ${label}`);}
const shanghaiMinute = date => {
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date).map(x=>[x.type,x.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
};

test('model creates local-time meetings and real iLink receives reminder for the same conversation', {timeout:120000},async t=>{
  const home=await mkdtemp(join(tmpdir(),'mirally calendar 中文 '));let child,logs='';
  const requests=[],replies=[],queue=[],errors=[];
  const tomorrow=shanghaiMinute(new Date(Date.now()+86400000)).slice(0,10);
  const meetingLocal=`${tomorrow}T11:00`;
  // The second reminder is already due, though the event itself is future.
  const triggerLocal=shanghaiMinute(new Date(Date.now()+3600000));
  const server=createServer(async(req,res)=>{
    try {
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      const body=Buffer.concat(chunks).toString();
      if(req.url.startsWith('/ilink/')) {
        res.setHeader('content-type','application/json');
        if(req.url.startsWith('/ilink/bot/get_bot_qrcode')) return res.end(JSON.stringify({qrcode:'fixture',qrcode_img_content:'fixture-scan'}));
        if(req.url.startsWith('/ilink/bot/get_qrcode_status')) return res.end(JSON.stringify({status:'confirmed',bot_token:'fixture-token',ilink_bot_id:'bot',ilink_user_id:'owner'}));
        if(req.url==='/ilink/bot/getupdates'){await delay(100);return res.end(JSON.stringify({ret:0,msgs:queue.splice(0),get_updates_buf:'cursor'}));}
        if(req.url==='/ilink/bot/sendmessage'){replies.push(JSON.parse(body).msg.item_list[0].text_item.text);return res.end(JSON.stringify({ret:0}));}
        throw Error(`unexpected ilink ${req.url}`);
      }
      const input=JSON.parse(body); requests.push(input);
      const user=input.messages.findLast(message=>message.role==='user' && /CALENDAR_(?:MEETING|NOW|WARMUP|REMOTE)/.test(JSON.stringify(message.content)));
      const content=JSON.stringify(user?.content),tail=input.messages.slice(input.messages.lastIndexOf(user)+1);
      const tools=tail.filter(item=>item.role==='tool');
      let delta,finish;
      if(content.includes('CALENDAR_WARMUP')) {delta={content:'通道就绪。'};finish='stop';}
      else if(!tools.length) {
        const immediate=content.includes('CALENDAR_NOW'),remote=content.includes('CALENDAR_REMOTE');
        const args={title:immediate?'即将开始的会议':remote?'微信会议':'明日上午会议',notes:'测试会议',start_local:immediate?triggerLocal:remote?`${tomorrow}T13:00`:meetingLocal,reminder_offsets_minutes:[60]};
        delta={tool_calls:[{index:0,id:immediate?'immediate':'meeting',type:'function',function:{name:'mirally_create_event',arguments:JSON.stringify(args)}}]};finish='tool_calls';
      } else {delta={content:'已处理会议安排。'};finish='stop';}
      res.writeHead(200,{'content-type':'text/event-stream'});
      for(const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:finish}],usage:{prompt_tokens:20,completion_tokens:10,total_tokens:30}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
      res.end('data: [DONE]\n\n');
    } catch(error) {errors.push(error);res.writeHead(500);res.end('fixture error');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    const canonical=await realpath(home);assert.equal(dirname(canonical),await realpath(tmpdir()));assert.ok(basename(canonical).startsWith('mirally calendar 中文 '));await rm(canonical,{recursive:true,force:true});
  });
  const preload=join(home,'fixture-model.mjs'),models=pathToFileURL(join(root,'kernel/dsh/packages/mirally/mirally-product/src/models.mjs')).href;
  await writeFile(preload,`import {ProductModels} from ${JSON.stringify(models)};\nconst sync=ProductModels.prototype.sync;\nconst requireOptions=ProductModels.prototype.requireOptions;\nProductModels.prototype.sync=function(s){if(this.testMode&&!this.fixture){this.registration?.();this.fixture=new ProductModels(this.ctx,{testMode:false});}if(this.fixture){const value=sync.call(this.fixture,s);this.options=value;return value;}return sync.call(this,s);};\nProductModels.prototype.requireOptions=function(...a){return this.fixture?requireOptions.call(this.fixture,...a):requireOptions.call(this,...a);};\n`);
  child=spawn(process.execPath,['--import',pathToFileURL(preload).href,join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',join(root,'.venv/Scripts/python.exe'),'--test-mode'],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',MIRALLY_TEST_WEIXIN_URL:`http://127.0.0.1:${server.address().port}`},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',c=>{logs+=c;});child.stderr.on('data',c=>{logs+=c;});
  const runtime=await until(async()=>{if(child.exitCode!==null)throw Error(logs);try{const r=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return r.pid===child.pid&&r;}catch{return false;}},'host start');
  const api=async(path,value,expected=200)=>{const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  const complete=async(id,command)=>until(async()=>(await rpc('command.list',{conversation_id:id,command_id:command})).commands.some(c=>c.state==='completed'),'completion '+command).catch(error=>{error.message+=`\nlogs=${logs}\nrequests=${requests.length}\nerrors=${errors.map(e=>e.message)}`;throw error;});
  await rpc('settings.update',{patch:{model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  assert.equal((await rpc('calendar.settings.get')).confirmed,false);
  await rpc('calendar.settings.update',{time_zone:'Asia/Shanghai'});
  const session=await api('/api/sessions',{title:'会议安排'},201);
  await rpc('weixin.login.start');
  const login=await until(async()=>{const state=await rpc('weixin.status');return state.state==='awaiting_confirmation'&&state;},'iLink login');
  await rpc('weixin.bind',{expected_revision:login.revision,conversation_id:session.id});
  queue.push({message_type:1,message_state:2,message_id:'100',from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'fixture-context',item_list:[{type:1,text_item:{text:'CALENDAR_WARMUP'}}]});
  await until(()=>replies.some(text=>text.includes('通道就绪')),'channel context');
  await api(`/api/sessions/${session.id}/messages`,{commandId:'meeting',text:'CALENDAR_MEETING 明天 11 点开会，提前一小时提醒我。'},202);
  await complete(session.id,'meeting');
  let events=(await rpc('calendar.event.list')).events;
  let event=events.find(item=>item.title==='明日上午会议');
  assert.ok(event,'model tool must actually create event');
  assert.equal(event.start_at,`${tomorrow}T11:00:00+08:00`);assert.equal(event.time_zone,'Asia/Shanghai');assert.equal(event.conversation_id,session.id);
  assert.deepEqual(event.reminder_offsets_minutes,[60]);
  const commitments=(await rpc('commitment.list')).commitments;
  const meeting=commitments.find(item=>item.title==='明日上午会议');
  assert.ok(meeting);assert.equal(new Date(meeting.due_at).toISOString(),`${tomorrow}T02:00:00.000Z`);
  assert.ok(requests.some(input=>JSON.stringify(input).includes('Asia/Shanghai')),'model receives confirmed timezone');
  if(process.env.MIRALLY_CALENDAR_GUI==='1') {
    const require=createRequire(join(root,'integrations/openclaw/package.json'));
    const browser=await require('playwright-core').chromium.launch({channel:'msedge',headless:true});
    try {
      const page=await browser.newPage({viewport:{width:1280,height:900}}),pageErrors=[];
      page.on('pageerror',error=>pageErrors.push(error.message));
      await page.addInitScript(()=>{window.__desktopNotices=[];window.pywebview={api:{notifyReminder:async(title,body)=>{window.__desktopNotices.push({title,body});return true;}}};});
      await page.goto(`${runtime.url}/settings/general#token=${runtime.token}`);
      await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
      const zone=page.getByTestId('mirally-time-zone');await zone.waitFor();
      assert.equal(await zone.inputValue(),'Asia/Shanghai');
      await page.getByRole('button',{name:'保存',exact:true}).last().click();
      const weixinSwitch=page.locator('wa-switch').filter({hasText:'微信提醒'});
      await weixinSwitch.click();
      await until(async()=>(await rpc('calendar.settings.get')).channels.weixin===false,'disable WeChat reminder');
      await weixinSwitch.click();
      await until(async()=>(await rpc('calendar.settings.get')).channels.weixin===true,'enable WeChat reminder');
      await page.goto(`${runtime.url}/reminders`);
      await page.getByTestId('mirally-calendar-event').filter({hasText:'明日上午会议'}).waitFor();
      assert.match(await page.locator('mirally-calendar-panel').innerText(),/11:00/);
      const row=page.getByTestId('mirally-calendar-event').first();
      await row.getByRole('button',{name:'编辑'}).click();
      await row.getByLabel('备注').fill('会议备注已更新');
      await row.getByRole('button',{name:'保存'}).click();
      await until(async()=>((await rpc('calendar.event.get',{event_id:event.event_id})).event.notes==='会议备注已更新'),'UI edit');
      event=(await rpc('calendar.event.get',{event_id:event.event_id})).event;
      const screenshots=join(root,'.artifacts','calendar-ui');await mkdir(screenshots,{recursive:true});
      await page.screenshot({path:join(screenshots,'calendar.png')});
      await page.getByRole('button',{name:'提醒',exact:true}).click();
      await page.getByTestId('mirally-reminder-title').fill('页面提醒');
      await page.getByTestId('mirally-reminder-time').fill(`${tomorrow}T12:00`);
      await page.getByTestId('mirally-reminder-create').click();
      await until(async()=>(await rpc('commitment.list')).commitments.some(item=>item.title==='页面提醒'),'UI reminder');
      const uiReminder=(await rpc('commitment.list')).commitments.find(item=>item.title==='页面提醒');
      assert.equal(new Date(uiReminder.due_at).toISOString(),`${tomorrow}T04:00:00.000Z`);
      await page.setViewportSize({width:480,height:800});
      await page.getByRole('button',{name:'日程',exact:true}).click();
      await page.getByTestId('mirally-calendar-event').filter({hasText:'明日上午会议'}).waitFor();
      await page.screenshot({path:join(screenshots,'calendar-narrow.png')});
      const due=(await rpc('calendar.time.resolve',{local_time:triggerLocal})).iso;
      await rpc('calendar.event.create',{title:'桌面提醒测试',start_at:due,reminder_offsets_minutes:[60],conversation_id:session.id});
      await page.waitForFunction(()=>window.__desktopNotices?.some(item=>item.title==='桌面提醒测试'));
      await until(async()=>(await rpc('calendar.delivery.list',{channel:'desktop',include_terminal:true})).deliveries.some(item=>item.title==='桌面提醒测试'&&item.status==='sent'),'desktop native bridge receipt');
      assert.deepEqual(pageErrors,[]);
    } finally {await browser.close();}
  }
  await rpc('settings.update',{patch:{weixin_remote_control_enabled:true}});
  queue.push({message_type:1,message_state:2,message_id:'101',from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'fixture-context',item_list:[{type:1,text_item:{text:'CALENDAR_REMOTE 安排明天 13 点的会议。'}}]});
  await until(async()=>(await rpc('calendar.event.list')).events.some(item=>item.title==='微信会议'),'WeChat creates event');
  const remoteEvent=(await rpc('calendar.event.list')).events.find(item=>item.title==='微信会议');
  assert.equal(remoteEvent.conversation_id,session.id);assert.equal(remoteEvent.start_at,`${tomorrow}T13:00:00+08:00`);
  queue.push({message_type:1,message_state:2,message_id:'102',from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'fixture-context',item_list:[{type:1,text_item:{text:'明日日程'}}]});
  const listing=await until(()=>replies.find(text=>text.includes('明日日程')&&text.includes('明日上午会议')),'numbered calendar list');
  const number=listing.match(/^(\d+)\..*明日上午会议/m)?.[1];assert.ok(number);assert.match(listing,/\d+\..*微信会议/);
  queue.push({message_type:1,message_state:2,message_id:'103',from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'fixture-context',item_list:[{type:1,text_item:{text:`查看日程 ${number}`}}]});
  await until(()=>replies.find(text=>text.includes('明日上午会议')&&text.includes('提醒：提前 60 分钟')),'calendar details');
  await rpc('calendar.event.cancel',{event_id:event.event_id,expected_version:event.version});
  assert.equal((await rpc('commitment.list')).commitments.find(item=>item.id===meeting.id).status,'cancelled');

  await api(`/api/sessions/${session.id}/messages`,{commandId:'now',text:'CALENDAR_NOW 建立即将开始的会议，提前一小时提醒。'},202);
  await complete(session.id,'now');
  events=(await rpc('calendar.event.list')).events;
  const immediate=events.find(item=>item.title==='即将开始的会议');assert.ok(immediate);assert.equal(immediate.conversation_id,session.id);
  const notice=await until(()=>replies.find(text=>text.includes('提醒：即将开始的会议')),'WeChat reminder');
  assert.match(notice,/即将开始的会议/);
  const remote=await until(async()=>{const result=await rpc('calendar.delivery.list',{channel:'weixin',include_terminal:true});return result.deliveries.find(item=>item.title==='即将开始的会议'&&item.status==='sent');},'WeChat receipt');
  assert.equal(remote.conversation_id,session.id);
  const desktop=await until(async()=>{const result=await rpc('calendar.delivery.list',{channel:'desktop'});return result.deliveries.find(item=>item.title==='即将开始的会议');},'desktop delivery');
  assert.equal(desktop.status,'pending');assert.equal(desktop.conversation_id,session.id);
  const claim=await rpc('calendar.delivery.claim',{delivery_id:desktop.delivery_id});assert.equal(claim.should_send,true);assert.equal(claim.delivery.eligible,true);
  await rpc('calendar.delivery.ack',{delivery_id:desktop.delivery_id,claim_token:claim.claim_token,status:'sent'});
  assert.equal((await rpc('calendar.delivery.get',{delivery_id:desktop.delivery_id})).delivery.status,'sent');
  assert.deepEqual(errors,[]);
});
