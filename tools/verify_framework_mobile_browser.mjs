/** Sequential real built phone/desktop UI acceptance against an isolated Host. */
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve,join} from 'node:path';
const require=createRequire(resolve('integrations/openclaw/package.json'));
const {chromium}=require('playwright-core');
const runtime=JSON.parse(await readFile(process.argv[2],'utf8'));
assert.equal(runtime.mode,'test','Requires an isolated test-mode product home');
const output=resolve('.artifacts/mobile-work-browser');await mkdir(output,{recursive:true});
const desktopRpc=async(method,params={})=>{
  const response=await fetch(runtime.url+'/api/rpc',{method:'POST',headers:{Authorization:'Bearer '+runtime.token,'Content-Type':'application/json'},body:JSON.stringify({method,params})});
  const value=await response.json();assert.ok(response.ok && !value.error,JSON.stringify(value));return value.result;
};
async function until(fn,label,timeout=25_000){const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await fn())return;await new Promise(r=>setTimeout(r,80));}throw new Error(label+' timed out');}
const browser=await chromium.launch({channel:'msedge',headless:true});
const desktop=await browser.newPage({viewport:{width:1280,height:940}});
const phoneContext=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
const phone=await phoneContext.newPage();const errors=[];
for(const page of [desktop,phone])page.on('pageerror',error=>errors.push(error.message));
const checks=[];const pass=label=>{checks.push(label);console.log('PASS '+label);};
try {
  await desktop.goto(runtime.url+'/settings/channels#token='+runtime.token,{waitUntil:'domcontentloaded'});
  await desktop.locator('details[data-testid="mirally-mobile-options"] > summary').click();
  await desktop.getByTestId('mobile-pair-create').waitFor();
  await desktop.getByTestId('mobile-pair-create').click();
  await until(async()=>!!(await desktop.getByTestId('mobile-pair-code').textContent())?.trim(),'desktop pairing code');
  const code=(await desktop.getByTestId('mobile-pair-code').textContent()).trim();
  await phone.goto(runtime.mobile_url+'#pair='+code,{waitUntil:'domcontentloaded'});
  await phone.locator('input[name="device_name"]').fill('浏览器验收手机');
  await phone.getByRole('button',{name:'配对并进入',exact:true}).click();
  await phone.getByRole('navigation',{name:'主要导航'}).waitFor();
  assert.equal(new URL(phone.url()).hash,'');
  assert.equal((await phone.request.get(new URL('/favicon.svg',runtime.mobile_url).href)).status(),200);
  pass('desktop settings issues a real one-use code; the phone pairs and clears it from the URL');

  const nav=()=>phone.getByRole('navigation',{name:'主要导航'});
  await nav().getByRole('button',{name:'工作',exact:true}).click();
  await phone.getByRole('button',{name:'查看工作详情：外部实验关注'}).click();
  await phone.getByRole('button',{name:'核对最新状态',exact:true}).click();
  await until(async()=>(await phone.locator('mirally-work-panel').innerText()).includes('第 42 轮'),'real observation');
  await phone.screenshot({path:join(output,'phone-observation.png'),fullPage:true});
  pass('phone work detail reads a real status file with metrics and observation time');

  await nav().getByRole('button',{name:'会话',exact:true}).click();
  await phone.getByTestId('mobile-conversation').filter({hasText:'手机改名'}).click();
  await until(async()=>(await phone.locator('.mirally-mobile-transcript').innerText()).includes('验证手机接续'),'canonical history');
  await phone.getByRole('textbox',{name:'消息内容'}).fill('手机页面真实发送测试');
  await phone.getByRole('button',{name:'发送',exact:true}).click();
  await until(async()=>(await phone.locator('.mirally-mobile-transcript').innerText()).includes('手机页面真实发送测试'),'new canonical message');
  await until(async()=>!(await phone.getByRole('button',{name:'停止回复',exact:true}).count()),'reply completion');
  await phone.getByText('管理会话',{exact:true}).click();
  await phone.getByRole('button',{name:'重命名',exact:true}).click();
  await phone.getByRole('textbox',{name:'会话名称',exact:true}).fill('手机页面改名完成');
  await phone.getByRole('button',{name:'保存名称',exact:true}).click();
  await until(async()=>(await desktopRpc('conversation.list')).conversations.some(c=>c.title==='手机页面改名完成'),'metadata owner update');
  await phone.getByRole('button',{name:'归档会话',exact:true}).click();
  await phone.getByRole('button',{name:'恢复会话',exact:true}).waitFor();
  await phone.getByRole('button',{name:'恢复会话',exact:true}).click();
  await phone.getByRole('button',{name:'归档会话',exact:true}).waitFor();
  pass('phone browses real history, sends a native turn, and renames/archives/restores the same conversation');

  await nav().getByRole('button',{name:'工作',exact:true}).click();
  if(await phone.getByRole('button',{name:'返回工作列表',exact:true}).count())await phone.getByRole('button',{name:'返回工作列表',exact:true}).click();
  await phone.getByRole('button',{name:'查看工作详情：手机控制脚本'}).click();
  await phone.getByRole('button',{name:'准备运行',exact:true}).click();
  const form=phone.getByTestId('work-run-form');
  await form.getByLabel('脚本路径（工作区相对路径）',{exact:true}).fill('处理 脚本.py');
  await form.getByLabel('参数（JSON 字符串数组）',{exact:true}).fill('[]');
  await form.locator('textarea').fill('结果 文件.txt');
  await form.getByRole('button',{name:'查看并确认影响',exact:true}).click();
  await phone.getByTestId('work-approval').waitFor();
  assert.match(await phone.getByTestId('work-approval').innerText(),/处理 脚本.py/);
  assert.match(await phone.getByTestId('work-approval').innerText(),/沙箱/);
  await phone.getByTestId('work-approve').click();
  await until(async()=>(await phone.getByTestId('work-run').first().innerText()).includes('运行完成'),'real UI run completion');
  const downloadPromise=phone.waitForEvent('download');
  await phone.getByTestId('work-run').first().getByRole('button',{name:'下载',exact:true}).click();
  const download=await downloadPromise;assert.match(download.suggestedFilename(),/结果 文件/);
  await phone.screenshot({path:join(output,'phone-work-result.png'),fullPage:true});
  pass('phone reviews exact script effects, starts a real run and downloads its verified artifact');

  await nav().getByRole('button',{name:'设置',exact:true}).click();
  await phone.getByRole('combobox',{name:/^默认首页/}).selectOption('work');
  await until(async()=>!(await phone.getByRole('combobox',{name:/^默认首页/}).isDisabled()),'settings save');
  const inboxSetting=phone.locator('.mirally-mobile-nav-setting').filter({hasText:'消息'});
  await inboxSetting.getByRole('checkbox').uncheck();
  await nav().getByRole('button',{name:'更多',exact:true}).waitFor();
  await nav().getByRole('button',{name:'更多',exact:true}).click();
  await phone.getByRole('group',{name:'更多入口'}).getByRole('button',{name:'消息',exact:true}).waitFor();
  await phone.reload({waitUntil:'domcontentloaded'});
  await phone.getByRole('heading',{name:'工作',exact:true}).waitFor();
  await nav().getByRole('button',{name:'设置',exact:true}).click();
  const completed=phone.getByRole('checkbox',{name:'运行完成',exact:true});
  await completed.uncheck();
  await until(async()=>(await desktopRpc('work.settings.get')).settings.notifications.completed===false,'notification owner update');
  await completed.check();
  await until(async()=>(await desktopRpc('work.settings.get')).settings.notifications.completed===true,'notification reset');
  pass('phone settings persist across reload, hidden navigation stays accessible and notification policy updates its owner');

  for(const width of [320,390,736]) {
    await phone.setViewportSize({width,height:900});
    const bounds=await phone.evaluate(()=>({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
    assert.ok(bounds.scroll<=bounds.client+1,JSON.stringify({width,...bounds}));
  }
  await phone.emulateMedia({colorScheme:'dark'});
  await phone.setViewportSize({width:390,height:844});
  await phone.screenshot({path:join(output,'phone-settings-dark.png'),fullPage:true});
  await desktop.goto(runtime.url+'/inbox#token='+runtime.token,{waitUntil:'domcontentloaded'});
  const desktopWorkEntry=desktop.locator('.sidebar-nav a.nav-item[href="/tasks"]');
  await desktopWorkEntry.waitFor({state:'visible'});
  assert.equal(await desktopWorkEntry.count(),1,'The retained sidebar must expose a real Work destination');
  await desktopWorkEntry.click();
  await until(async()=>new URL(desktop.url()).pathname==='/tasks','desktop sidebar Work navigation');
  await desktop.getByTestId('work-item').filter({hasText:'手机控制脚本'}).waitFor();
  // Real owner data: a pending approval and an explicitly ended work must be
  // discoverable without confusing a completed run with an ended work.
  await desktopRpc('work.run.prepare',{work_id:runtime.work_id,script:'处理 脚本.py',arguments:[],cwd:'.',output_paths:[],timeout_seconds:300,request_id:'browser-list-approval'});
  const historical=(await desktopRpc('work.create',{title:'已结束的真实工作',goal:'用于验证历史筛选',request_id:'browser-list-history'})).work;
  await desktopRpc('work.update',{work_id:historical.id,expected_revision:historical.revision,patch:{intent:'ended'}});
  await until(async()=>(await desktop.getByTestId('work-item').filter({hasText:'手机控制脚本'}).innerText()).includes('需要确认'),'pending approval in list');
  assert.match(await desktop.getByTestId('work-item').filter({hasText:'外部实验关注'}).innerText(),/第 42 轮/);
  const filters=desktop.getByRole('navigation',{name:'筛选工作'});
  await filters.getByRole('button',{name:/需要处理/}).click();
  await desktop.getByTestId('work-item').filter({hasText:'手机控制脚本'}).waitFor();
  assert.equal(await desktop.getByTestId('work-item').filter({hasText:'外部实验关注'}).count(),0);
  await filters.getByRole('button',{name:/已结束/}).click();
  await desktop.getByTestId('work-item').filter({hasText:'已结束的真实工作'}).waitFor();
  await filters.getByRole('button',{name:/全部/}).click();
  await desktop.getByRole('searchbox',{name:'搜索工作'}).fill('手机控制脚本');
  await until(async()=>await desktop.getByTestId('work-item').count()===1,'desktop work search');
  await desktop.getByRole('button',{name:'查看工作详情：手机控制脚本'}).click();
  await desktop.getByTestId('work-approval').waitFor();
  await desktop.getByRole('button',{name:'返回工作列表'}).click();
  await desktop.getByTestId('work-item').filter({hasText:'手机控制脚本'}).waitFor();
  await desktop.getByRole('searchbox',{name:'搜索工作'}).fill('');
  await filters.getByRole('button',{name:/关注中/}).click();
  await desktop.screenshot({path:join(output,'desktop-work-list.png'),fullPage:true});
  await desktop.setViewportSize({width:480,height:900});
  await desktop.waitForTimeout(450); // Allow the retained shell's responsive drawer transition to settle.
  const drawer=desktop.locator('.topbar-nav-toggle');
  if(await drawer.getAttribute('aria-expanded')==='true') {
    await drawer.click();
    await until(async()=>await drawer.getAttribute('aria-expanded')==='false','close narrow navigation');
  }
  const desktopBounds=await desktop.evaluate(()=>({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
  assert.ok(desktopBounds.scroll<=desktopBounds.client+1,JSON.stringify(desktopBounds));
  await filters.getByRole('button',{name:/需要处理/}).click();
  await desktop.getByTestId('work-item').filter({hasText:'手机控制脚本'}).waitFor();
  await filters.getByRole('button',{name:/关注中/}).click();
  await desktop.screenshot({path:join(output,'desktop-work-list-narrow.png'),fullPage:true});
  await desktop.setViewportSize({width:1280,height:940});
  if(await desktop.getByRole('button',{name:'开始工作',exact:true}).count()) {
    await desktop.getByRole('button',{name:'开始工作',exact:true}).click();
    await desktop.locator('.mirally-work__cards').waitFor({state:'visible'});
    await desktop.locator('[data-work="chat"]').click();
    await desktop.goto(runtime.url+'/tasks#token='+runtime.token,{waitUntil:'domcontentloaded'});
    await desktop.getByTestId('work-item').filter({hasText:'手机控制脚本'}).waitFor();
  }
  await desktop.getByTestId('work-item').filter({hasText:'手机控制脚本'}).click();
  await until(async()=>new URL(desktop.url()).pathname!=='/tasks','continue work conversation');
  const modelTrigger=desktop.locator('openclaw-chat-pane.chat-pane-cache__pane--active [data-chat-model-select]');
  await modelTrigger.waitFor();
  await until(async()=>await modelTrigger.getAttribute('aria-disabled')==='false','conversation model selector becomes available');
  await modelTrigger.click();
  assert.equal(await modelTrigger.locator('..').getAttribute('open'),'');
  pass('the conversation model selector opens');
  const workConversation=(await desktopRpc('work.get',{work_id:runtime.work_id})).work.conversation_id;
  const mobileResponse=await fetch(new URL('/api/mobile/rpc',runtime.mobile_url),{method:'POST',headers:{Authorization:'Bearer '+runtime.mobile_token,'Content-Type':'application/json'},body:JSON.stringify({method:'conversation.send',params:{conversation_id:workConversation,command_id:'browser-origin-live',text:'跨端来源标记验收'}})});
  assert.ok(mobileResponse.ok,await mobileResponse.text());
  const originMessage=desktop.locator('.chat-group.user').filter({hasText:'跨端来源标记验收'});
  await originMessage.waitFor();
  await originMessage.locator('.mirally-message-origin').getByText('手机网页',{exact:true}).waitFor();
  await desktop.screenshot({path:join(output,'desktop-message-origin.png')});
  await desktop.reload({waitUntil:'domcontentloaded'});
  await originMessage.locator('.mirally-message-origin').getByText('手机网页',{exact:true}).waitFor();
  pass('a real mobile message is labelled in the open desktop conversation and after reload');
  pass('desktop sidebar, real progress, approvals, filters, search and conversation navigation work; phone layouts fit 320, 390 and 736px');
  assert.deepEqual(errors,[],'Browser page errors');
  console.log(JSON.stringify({passed:checks.length,checks}));
} catch(error) {
  await phone.screenshot({path:join(output,'phone-failure.png'),fullPage:true}).catch(()=>{});
  await desktop.screenshot({path:join(output,'desktop-failure.png'),fullPage:true}).catch(()=>{});
  console.error('DESKTOP STATE', (await desktop.locator('body').innerText()).slice(-3500));
  console.error('PHONE STATE', (await phone.locator('body').innerText()).slice(-3500));
  throw error;
} finally {await phoneContext.close();await browser.close();}
