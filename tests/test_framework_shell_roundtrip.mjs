import {loadPlaywright} from '../tools/testing/dependencies.cjs';
/** Real normal Host/DSH loop and local model fixture; no external model or user files. */
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

const root = fileURLToPath(new URL('../', import.meta.url));
async function until(check, label, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {const value = await check(); if (value) return value; await delay(40);}
  throw new Error(`Timed out: ${label}`);
}

test('real host binds command approval to the session, rejects or cancels safely, and executes approved commands', {timeout:120000}, async t => {
  if (process.platform !== 'win32') {t.skip('Windows PowerShell capability'); return;}
  const python = join(root, '.venv/Scripts/python.exe');
  if (!existsSync(python) || !existsSync(join(root,'kernel/dsh/packages/core/agent/lib/index.js'))) {t.skip('Prepare repository Python and kernel build'); return;}
  const home = await mkdtemp(join(tmpdir(), 'mirally command host 中文 '));
  let child, runtime, logs = '';
  const errors = [], calls = [], modelSteps=[]; let screenshotModelPayload = false;
  const server = createServer(async (req,res) => {
    try {
      const chunks=[]; for await(const chunk of req) chunks.push(chunk);
      const input=JSON.parse(Buffer.concat(chunks).toString('utf8')); calls.push(input.tools.map(tool=>tool.function.name));
      const last=input.messages.at(-1), lastUser=[...input.messages].reverse().find(message=>message.role==='user' && ['allow','deny','pending','running','screenshot'].includes(message.content));
      const prompt=JSON.stringify(lastUser.content);
      const name=['allow','deny','pending','running','screenshot'].find(item=>prompt.includes(item));
      assert.ok(name, prompt);
      const marker=join(home,`${name}.txt`).replaceAll("'","''");
      const running=join(home,'started.txt').replaceAll("'","''");
      const command=name==='running' ? `Set-Content -LiteralPath '${running}' -Value started; Start-Sleep -Seconds 3; Set-Content -LiteralPath '${marker}' -Value done` : `Set-Content -LiteralPath '${marker}' -Value done`;
      const isTool=last.role==='tool' || (name==='screenshot' && input.messages.slice(-2).some(message=>message.role==='tool'));
      modelSteps.push({name,last:last.role,roles:input.messages.map(message=>message.role)});
      if (name==='screenshot' && isTool) screenshotModelPayload=JSON.stringify(input.messages).includes('data:image/');
      const delta=isTool ? {content:'操作已结束。'} : {tool_calls:[{index:0,id:`call-${name}`,type:'function',function:name==='screenshot'?{name:'mirally_desktop_screenshot',arguments:'{}'}:{name:'mirally_run_command',arguments:JSON.stringify({command,cwd:home,timeout_seconds:30})}}]};
      res.writeHead(200,{'content-type':'text/event-stream'});
      for(const frame of [{id:'fixture',choices:[{index:0,delta,finish_reason:null}]},{id:'fixture',choices:[{index:0,delta:{},finish_reason:isTool?'stop':'tool_calls'}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}}]) res.write(`data: ${JSON.stringify(frame)}\n\n`);
      res.end('data: [DONE]\n\n');
    } catch(error) {errors.push(error);res.writeHead(500);res.end('fixture error');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(7000).then(()=>{throw new Error('cleanup timeout');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}
    server.closeAllConnections(); await new Promise(resolve=>server.close(resolve));
    const canonical=await realpath(home); assert.equal(dirname(canonical),await realpath(tmpdir())); assert.ok(basename(canonical).startsWith('mirally command host 中文 '));
    await rm(canonical,{recursive:true,force:true});
  });
  child=spawn(process.execPath,[join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',python],
    {cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
  child.stdout.on('data',data=>{logs+=data;});child.stderr.on('data',data=>{logs+=data;});
  runtime=await until(async()=>{try{const value=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return value.pid===child.pid&&value;}catch{return false;}},'startup '+logs);
  assert.equal(runtime.mode,'normal');
  const api=async(path,value,expected=200)=>{const response=await fetch(runtime.url+path,{method:value===undefined?'GET':'POST',headers:{authorization:`Bearer ${runtime.token}`,'content-type':'application/json'},...(value===undefined?{}:{body:JSON.stringify(value)})});assert.equal(response.status,expected,await response.clone().text());return response.json();};
  const rpc=async(method,params={})=>(await api('/api/rpc',{method,params})).result;
  await rpc('settings.update',{patch:{model:{provider:'deepseek',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture-key',model_id:'fixture-model',image_enabled:true,reasoning_effort:'off',max_tokens:1024}}});
  const first=await api('/api/sessions',{title:'命令验证'},201), other=await api('/api/sessions',{title:'另一会话'},201);
  const send=async name=>api(`/api/sessions/${first.id}/messages`,{commandId:`command-${name}`,text:name},202);
  const approval=async name=>{try{return await until(async()=>{const list=await rpc('action.approvals.list',{conversation_id:first.id});return list.approvals.find(item=>item.command_id===`command-${name}`);},`approval ${name}`,12000);}catch(error){error.message += `\nlogs=${logs}\nfixture errors=${errors.map(e=>e.message)}`;throw error;}};
  const completed=async name=>until(async()=>{const list=await rpc('command.list',{conversation_id:first.id,command_id:`command-${name}`});return list.commands.find(item=>['completed','cancelled'].includes(item.state));},`completion ${name} ${logs}`);
  await send('allow'); const allowed=await approval('allow');
  assert.equal(allowed.kind,'shell'); assert.match(allowed.detail,/allow\.txt/); assert.equal(allowed.conversation_id,first.id);
  await api('/api/rpc',{method:'action.approvals.decide',params:{approval_id:allowed.id,conversation_id:other.id,allow:true}},400);
  assert.equal(existsSync(join(home,'allow.txt')),false);
  await rpc('action.approvals.decide',{approval_id:allowed.id,conversation_id:first.id,allow:true}); await completed('allow');
  assert.equal((await readFile(join(home,'allow.txt'),'utf8')).trim(),'done');
  await send('deny'); const denied=await approval('deny'); await rpc('action.approvals.decide',{approval_id:denied.id,conversation_id:first.id,allow:false}); await completed('deny');
  assert.equal(existsSync(join(home,'deny.txt')),false);
  await send('pending'); await approval('pending'); await api(`/api/sessions/${first.id}/cancel`,{commandId:'command-pending'}); await completed('pending');
  assert.equal(existsSync(join(home,'pending.txt')),false); assert.equal((await rpc('action.approvals.list',{conversation_id:first.id})).approvals.length,0);
  await send('running'); const running=await approval('running'); await rpc('action.approvals.decide',{approval_id:running.id,conversation_id:first.id,allow:true});
  await until(()=>existsSync(join(home,'started.txt')),'command started');
  await api(`/api/sessions/${first.id}/cancel`,{commandId:'command-running'}); await completed('running');
  await delay(4000); assert.equal(existsSync(join(home,'running.txt')),false);
  assert.deepEqual(errors,[]); assert.ok(calls[0].includes('mirally_run_command'));

  // Real built product UI, using the same isolated Host; headless Edge never controls the desktop.
  const {chromium} = loadPlaywright();
  const browser=await chromium.launch({channel:'msedge',headless:true});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1280,height:900}}), pageErrors=[];
  page.on('pageerror',error=>pageErrors.push(error.message));
  await page.goto(runtime.url+'/settings/model-providers#token='+runtime.token);
  await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
  await page.evaluate(()=>window.__mirallyFramework.showPage('chat'));
  await page.locator('.new-session-page__message').fill('allow');
  await rm(join(home,'allow.txt'),{force:true});
  await page.locator('.new-session-page__start-submit').click();
  const card=page.getByRole('region',{name:'等待确认的动作'});
  await card.waitFor({timeout:20000});
  assert.match(await card.innerText(),/allow\.txt/); assert.match(await card.innerText(),/当前用户权限/);
  await card.getByRole('button',{name:'允许这次'}).click();
  await until(()=>existsSync(join(home,'allow.txt')),'UI approved command');
  await card.waitFor({state:'hidden'});
  const pane=page.locator('openclaw-chat-pane.chat-pane-cache__pane--active');
  await page.waitForFunction(()=>{const p=document.querySelector('openclaw-chat-pane.chat-pane-cache__pane--active');return p?.state?.chatMessages?.some(m=>m.role==='assistant') && !p.runActivity?.working;});
  await pane.locator('textarea[aria-label="聊天输入框"]').fill('deny');
  await pane.locator('.chat-send-btn--send').click();
  await card.waitFor({state:'visible'}); assert.match(await card.innerText(),/deny\.txt/);
  await card.getByRole('button',{name:'拒绝'}).click(); await card.waitFor({state:'hidden'});
  await delay(500); assert.equal(existsSync(join(home,'deny.txt')),false);
  assert.deepEqual(pageErrors,[]);
  // Real screen capture is opt-in; the screenshot remains in the isolated temporary home only.
  if (process.env.MIRALLY_TEST_DESKTOP_CAPTURE_E2E === '1') {
    await send('screenshot'); const shot=await approval('screenshot');
    assert.equal(shot.kind,'desktop_observation');
    await rpc('action.approvals.decide',{approval_id:shot.id,conversation_id:first.id,allow:true});
    try {await completed('screenshot');} catch (error) {error.message += `; model steps=${JSON.stringify(modelSteps)}; command=${JSON.stringify(await rpc('command.list',{conversation_id:first.id,command_id:'command-screenshot'}))}; fixture errors=${errors.map(e=>e.message)}`;throw error;}
    assert.equal(screenshotModelPayload,true,'next model request must contain a screenshot image payload');
  }
  await api('/api/shutdown',{}); await until(()=>child.exitCode!==null,'shutdown');
});
