/** Real product/DSH/SQLite, with isolated local model and iLink protocol fixtures. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir, mkdtemp, readFile, realpath, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {setTimeout as delay} from 'node:timers/promises';

const root = fileURLToPath(new URL('../', import.meta.url));
async function until(check, label, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await check(); if (value) return value; await delay(50); }
  throw Error(`timeout: ${label}`);
}

test('conversation history, model-created work, titles and WeChat share one owner', {timeout: 120000}, async t => {
  const taskHome = await mkdtemp(join(tmpdir(), 'mirally work conversation 中文 '));
  const requests = [], queue = [], replies = [], errors = [];
  let child, logs = '', workId, prepareBlocked = false, releasePrepare;
  const prepareGate = new Promise(resolve => { releasePrepare = resolve; });
  const server = createServer(async (req, res) => {
    try {
      const chunks = []; for await (const chunk of req) chunks.push(chunk);
      const body = Buffer.concat(chunks).toString();
      res.setHeader('content-type', 'application/json');
      if (req.url.startsWith('/ilink/')) {
        if (req.url.startsWith('/ilink/bot/get_bot_qrcode')) return res.end(JSON.stringify({qrcode:'fixture',qrcode_img_content:'fixture-scan'}));
        if (req.url.startsWith('/ilink/bot/get_qrcode_status')) return res.end(JSON.stringify({status:'confirmed',bot_token:'fixture-token',ilink_bot_id:'bot',ilink_user_id:'owner'}));
        if (req.url === '/ilink/bot/getupdates') { await delay(100); return res.end(JSON.stringify({ret:0,msgs:queue.splice(0),get_updates_buf:'cursor'})); }
        if (req.url === '/ilink/bot/sendmessage') { replies.push(JSON.parse(body).msg.item_list[0].text_item.text); return res.end(JSON.stringify({ret:0})); }
        throw Error('unexpected iLink path');
      }
      const input = JSON.parse(body), lastUser = input.messages.findLast(message => message.role === 'user'
        && ['背景信息','加入工作列表','调整工作目标','准备页任务','微信更新目标'].some(value => JSON.stringify(message.content).includes(value)));
      const prompt = JSON.stringify(lastUser?.content), tail = input.messages.slice(input.messages.lastIndexOf(lastUser) + 1);
      const tools = tail.filter(message => message.role === 'tool');
      const turn = prompt.includes('背景信息') ? 'background' : prompt.includes('加入工作列表') ? 'promote'
        : prompt.includes('调整工作目标') ? 'update' : prompt.includes('准备页任务') ? 'prepare'
        : prompt.includes('微信更新目标') ? 'remote' : 'unknown';
      requests.push({turn, input});
      let name, args, reply;
      if (turn === 'background') reply = '背景已记录。';
      else if (turn === 'promote' && tools.length < 2) {
        name = 'mirally_create_work'; args = tools.length ? {title:'另一个名称',goal:'不同的目标'} : {title:'GPU 监测',goal:'关注 GPU 空闲情况'};
      } else if (turn === 'promote') reply = '已处理工作请求。';
      else if ((turn === 'update' || turn === 'remote') && tools.length === 0) { name = 'mirally_current_work'; args = {}; }
      else if ((turn === 'update' || turn === 'remote') && tools.length === 1) {
        name = 'mirally_update_work'; args = {work_id:workId, ...(turn === 'update' ? {title:'GPU 训练监测',goal:'每小时检查显存'} : {goal:'远程检查 GPU 进度'})};
      } else if (turn === 'update') reply = '已处理工作修改。';
      else if (turn === 'remote') reply = 'REMOTE_DONE';
      else if (turn === 'prepare' && tools.length === 0) { prepareBlocked = true; await prepareGate; name = 'mirally_create_work'; args = {title:'模型整理的任务',goal:'整理材料并分析实验'}; }
      else if (turn === 'prepare') reply = '准备页委托已处理。';
      else throw Error('unexpected model prompt ' + prompt);
      const delta = name ? {tool_calls:[{index:0,id:`call-${turn}-${tools.length}`,type:'function',function:{name,arguments:JSON.stringify(args)}}]} : {content:reply};
      res.writeHead(200, {'content-type':'text/event-stream'});
      for (const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},
        {id:'fixture',choices:[{index:0,delta:{},finish_reason:name?'tool_calls':'stop'}],usage:{prompt_tokens:30,completion_tokens:10,total_tokens:40}}]) res.write(`data: ${JSON.stringify(frame)}\n\n`);
      res.end('data: [DONE]\n\n');
    } catch (error) { errors.push(error); res.writeHead(500); res.end('fixture error'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    releasePrepare();
    if (child?.exitCode === null) { child.stdin.end(); try { await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw Error('cleanup timeout');})]); } catch { child.kill(); await Promise.race([once(child,'exit'),delay(3000)]); } }
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    const canonical = await realpath(taskHome); assert.equal(dirname(canonical), await realpath(tmpdir()));
    assert.ok(basename(canonical).startsWith('mirally work conversation 中文 ')); await rm(canonical,{recursive:true,force:true});
  });
  // Test mode isolates iLink; use the real DSH model adapter only for this child.
  const preload = join(taskHome, 'fixture-model.mjs');
  const models = pathToFileURL(join(root,'kernel/dsh/packages/mirally/mirally-product/src/models.mjs')).href;
  await writeFile(preload, `import {ProductModels} from ${JSON.stringify(models)};\nconst sync=ProductModels.prototype.sync;\nconst requireOptions=ProductModels.prototype.requireOptions;\nProductModels.prototype.sync=function(s){if(this.testMode&&!this.fixture){this.registration?.();this.fixture=new ProductModels(this.ctx,{testMode:false});}if(this.fixture){const value=sync.call(this.fixture,s);this.options=value;return value;}return sync.call(this,s);};\nProductModels.prototype.requireOptions=function(...a){return this.fixture?requireOptions.call(this.fixture,...a):requireOptions.call(this,...a);};\n`);
  child = spawn(process.execPath, ['--import', pathToFileURL(preload).href, join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),
    '--root',root,'--home',taskHome,'--python',join(root,'.venv/Scripts/python.exe'),'--test-mode'],
    {cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',MIRALLY_TEST_WEIXIN_URL:`http://127.0.0.1:${server.address().port}`},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data', data => { logs += data; }); child.stderr.on('data', data => { logs += data; });
  const runtime = await until(async () => { if (child.exitCode !== null) throw Error(logs); try { const value=JSON.parse(await readFile(join(taskHome,'runtime.json'),'utf8')); return value.pid===child.pid && value; } catch { return false; } }, 'host startup');
  const api = async (path, value, expected=200) => { const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})}); assert.equal(response.status,expected,await response.clone().text());return response.json(); };
  const rpc = async (method, params={}) => (await api('/api/rpc',{method,params})).result;
  const complete = async (id, command) => until(async () => (await rpc('command.list',{conversation_id:id,command_id:command})).commands.some(item=>item.state==='completed'), 'completion '+command).catch(async error => {
    error.message += `\nlogs=${logs}\nerrors=${errors.map(item=>item.message)}\nrequests=${requests.map(item=>item.turn)}\ncommands=${JSON.stringify(await rpc('command.list',{conversation_id:id,command_id:command}))}`;
    throw error;
  });
  await rpc('settings.update',{patch:{model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture',model_id:'fixture',reasoning_effort:'off',max_tokens:1024}}});
  const session = await api('/api/sessions',{},201);
  const send = async (id,commandId,text,extra={}) => api(`/api/sessions/${id}/messages`,{commandId,text,...extra},202);
  await send(session.id,'background','背景信息：请关注训练服务器空闲 GPU。'); await complete(session.id,'background');
  await send(session.id,'promote','请把这段对话加入工作列表。'); await complete(session.id,'promote');
  const works = (await rpc('work.by_conversation',{conversation_id:session.id})).works;
  assert.equal(works.length,1); workId=works[0].id;
  assert.equal(works[0].title,'GPU 监测'); assert.equal(works[0].goal,'关注 GPU 空闲情况');
  assert.equal((await api('/api/sessions')).sessions.find(item=>item.id===session.id).title, 'GPU 监测');
  assert.ok(JSON.stringify(requests.find(item=>item.turn==='promote').input.messages).includes('背景信息'));
  await send(session.id,'promote','请把这段对话加入工作列表。');
  assert.equal((await rpc('work.by_conversation',{conversation_id:session.id})).works.length,1);
  await send(session.id,'update','调整工作目标并更名。'); await complete(session.id,'update');
  const updated = (await rpc('work.get',{work_id:workId})).work;
  assert.equal(updated.title,'GPU 训练监测'); assert.equal(updated.goal,'每小时检查显存');
  assert.equal((await api('/api/sessions')).sessions.find(item=>item.id===session.id).title,'GPU 训练监测');

  const prepared = await api('/api/sessions',{},201);
  const material = await api('/api/materials',{conversation_id:prepared.id,name:'实验.txt',media_type:'text/plain',data:Buffer.from('材料证据').toString('base64')},201);
  await send(prepared.id,'prepare','准备页任务：分析实验材料。',{materials:[material.id],work:{title:'参考名称',goal:'参考目标'}});
  await until(()=>prepareBlocked,'model awaiting preparation');
  assert.equal((await rpc('work.by_conversation',{conversation_id:prepared.id})).works.length,0,'no work is fabricated before the model calls its tool');
  releasePrepare(); await complete(prepared.id,'prepare');
  const fromPrepare = (await rpc('work.by_conversation',{conversation_id:prepared.id})).works;
  assert.equal(fromPrepare.length,1); assert.equal(fromPrepare[0].title,'模型整理的任务');
  const preparedHistory = await api(`/api/sessions/${prepared.id}/history`);
  assert.ok(preparedHistory.messages.some(message=>message.role==='user'&&message.text.includes('参考名称')&&message.text.includes('工作跟进')));
  assert.ok(JSON.stringify(requests.find(item=>item.turn==='prepare').input).includes('材料证据'));

  if (process.env.MIRALLY_WORK_GUI === '1') {
    // Exercise the built desktop page with the same real DSH and local model.
    // This optional browser test must be run separately from other GUI tests.
    const gui = await api('/api/sessions',{},201);
    await send(gui.id,'gui-background','背景信息：这是需要持续关注的 GPU 实验。');
    await complete(gui.id,'gui-background');
    const require = createRequire(join(root,'integrations/openclaw/package.json'));
    const browser = await require('playwright-core').chromium.launch({channel:'msedge',headless:true});
    try {
      const page=await browser.newPage({viewport:{width:1280,height:900}}), pageErrors=[];
      page.on('pageerror',error=>pageErrors.push(error.message));
      await page.goto(`${runtime.url}/chat/main/${encodeURIComponent(gui.id)}#token=${runtime.token}`);
      await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
      const pane=page.locator('openclaw-chat-pane.chat-pane-cache__pane--active');
      const input=pane.locator('textarea[aria-label="聊天输入框"]');
      await input.fill('保留的草稿');
      await pane.locator('.chat-header-session-menu__trigger').click();
      const item=pane.locator('openclaw-chat-header-session-menu wa-dropdown-item[value="mirally-work"]');
      await item.waitFor();
      await page.waitForFunction(()=>{const item=document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active openclaw-chat-header-session-menu wa-dropdown-item[value="mirally-work"]');return item && !item.hasAttribute('disabled') && item.textContent.includes('加入工作');});
      await item.click();
      const linked=await until(async()=>{const result=await rpc('work.by_conversation',{conversation_id:gui.id});return result.works[0];},'work created from UI');
      assert.equal(await input.inputValue(),'保留的草稿');
      await until(async()=>(await rpc('command.list',{conversation_id:gui.id})).commands.some(item=>item.state==='completed'&&item.command_id!=='gui-background'),'UI model turn completed');
      const badge=pane.getByRole('button',{name:'查看关联工作'});
      await badge.waitFor();
      const screenshots=join(root,'.artifacts','work-conversation-ui');await mkdir(screenshots,{recursive:true});
      await page.screenshot({path:join(screenshots,'conversation.png')});
      await badge.click();
      await page.getByRole('button',{name:'返回工作列表'}).click();
      await page.getByTestId('work-item').filter({hasText:linked.title}).first().waitFor();
      await page.screenshot({path:join(screenshots,'work-list.png')});
      await page.getByTestId('work-item').filter({hasText:linked.title}).first().click();
      await page.waitForFunction(id=>document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active')?.state?.sessionKey==='agent:main:'+id,gui.id);
      assert.equal(await page.locator('openclaw-chat-pane.chat-pane-cache__pane--active textarea[aria-label="聊天输入框"]').inputValue(),'保留的草稿');
      assert.deepEqual(pageErrors,[]);
    } finally { await browser.close(); }
  }

  await rpc('weixin.login.start');
  const login = await until(async()=>{const state=await rpc('weixin.status');return state.state==='awaiting_confirmation'&&state;},'iLink fixture login');
  await rpc('weixin.bind',{expected_revision:login.revision,conversation_id:session.id});
  const sendWeixin = (id,text) => queue.push({message_type:1,message_state:2,message_id:String(id),from_user_id:'owner',to_user_id:'bot',create_time_ms:Date.now(),context_token:'fixture-context',item_list:[{type:1,text_item:{text}}]});
  const beforeDisabled = replies.length; sendWeixin(1,'微信更新目标：测试禁用。');
  await until(()=>replies.slice(beforeDisabled).includes('REMOTE_DONE'),'denied WeChat turn');
  assert.equal((await rpc('work.get',{work_id:workId})).work.goal,'每小时检查显存');
  await rpc('settings.update',{patch:{weixin_remote_control_enabled:true}});
  const beforeEnabled = replies.length; sendWeixin(2,'微信更新目标：请修改。');
  await until(()=>replies.slice(beforeEnabled).includes('REMOTE_DONE'),'allowed WeChat turn');
  assert.equal((await rpc('work.get',{work_id:workId})).work.goal,'远程检查 GPU 进度');
  const history = await api(`/api/sessions/${session.id}/history`);
  assert.ok(history.messages.some(message=>message.role==='user'&&message.input_origin==='weixin'&&message.text.includes('微信更新目标')));
  assert.equal((await rpc('work.by_conversation',{conversation_id:session.id})).works.length,1);
  assert.deepEqual(errors,[]);
});
