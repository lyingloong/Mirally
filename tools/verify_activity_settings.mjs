/** Exercise the pinned Control UI with synthetic Activity data and no Gateway. */
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'integrations/openclaw/package.json'));
const {chromium} = require('playwright-core');
const assets = path.join(root, 'integrations/openclaw/node_modules/openclaw/dist/control-ui');
const output = path.join(root, '.artifacts/activity-settings-verification');
const mime = {'.js':'text/javascript', '.css':'text/css', '.html':'text/html', '.json':'application/json',
  '.png':'image/png', '.svg':'image/svg+xml', '.woff2':'font/woff2'};
await fs.mkdir(output, {recursive:true});
const server = http.createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  let target = path.resolve(assets, '.' + pathname);
  if (target !== assets && !target.startsWith(assets + path.sep)) {response.writeHead(403).end(); return;}
  try {
    if (!(await fs.stat(target)).isFile()) target = path.join(assets, 'index.html');
  } catch {target = path.join(assets, 'index.html');}
  try {response.setHeader('Content-Type', mime[path.extname(target)] || 'application/octet-stream'); response.end(await fs.readFile(target));}
  catch {response.writeHead(404).end();}
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const brand = execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-X', 'utf8', '-c',
  'import sys; from mirally.control_ui_brand import brand_script; print(brand_script(sys.argv[1], desktop_subagent_ids=("internal-fixture",)))', origin],
  {cwd:root, encoding:'utf8', maxBuffer:2*1024*1024});
let browser;
const errors = [], findings = [];
try {
  browser = await chromium.launch({channel:'msedge', headless:true});
  const context = await browser.newContext({viewport:{width:1280,height:900},timezoneId:'Asia/Shanghai'});
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  await context.routeWebSocket('**', ws => ws.close());
  await context.addInitScript(() => {
    window.__activityRequests = [];
    let running = false, paused = false, enabled = false;
    let semantics = {enabled:false,model:'',effective_model:'fixture/default',max_calls_per_hour:24,max_tokens_per_day:60000};
    window.__semanticApplied = true;
    window.__rawStoragePaused = false;
    window.__semanticFailure = null;
    window.chrome = window.chrome || {};
    window.chrome.webview = {addEventListener() {}, removeEventListener() {}, postMessage(message) {
      if (message.type !== 'mirally-activity') return;
      window.__activityRequests.push(message);
      let result;
      if (message.action === 'start') {running = true; paused = false; result = {};}
      else if (message.action === 'set-enabled') {enabled = message.enabled; running = enabled; paused = false; result = {saved:true,enabled,capture_error:null};}
      else if (message.action === 'set-semantics') {
        semantics.enabled = message.enabled;
        if (Object.hasOwn(message,'model')) {semantics.model=message.model;semantics.effective_model=message.model||'fixture/default';}
        if (Object.hasOwn(message,'maxCallsPerHour')) semantics.max_calls_per_hour=message.maxCallsPerHour;
        if (Object.hasOwn(message,'maxTokensPerDay')) semantics.max_tokens_per_day=message.maxTokensPerDay;
        result={saved:true,applied:window.__semanticApplied,semantics,reload_error:window.__semanticApplied?null:'合成后台未运行'};
      }
      else if (message.action === 'pause') {paused = true; result = {};}
      else if (message.action === 'resume') {paused = false; window.__rawStoragePaused=false; result = {};}
      else if (message.action === 'stop') {running = false; paused = false; result = {};}
      else if (message.action === 'status') result = {
        activity:{configured:true,enabled,live_enabled:running,database_exists:true,session_id:running?'synthetic-session':null,
          collection_level:'content',capture:[{label:'前台窗口元数据',enabled:true}, {label:'所有显示器截图',enabled:true},
            {label:'OCR 正文',enabled:true},{label:'输入文本',enabled:true},{label:'剪贴板正文',enabled:false}],
          collector:{status:running ? (paused || window.__rawStoragePaused ? 'paused':'running'):'stopped',snapshot:!running,updated_at_utc_ms:1700000000000,
            pause_reason:window.__rawStoragePaused?'semantic_memory_pending_storage_quota':null},
          controls:{start:!running,pause:running&&!paused&&!window.__rawStoragePaused,resume:running&&(paused||window.__rawStoragePaused),stop:running,'set-enabled':true}},
        memory:{configured:true,enabled:true,health:{store:{active_count:24,statuses:{candidate:3}}},
          raw_retention:{enabled:true,configured_enabled:true,interval_seconds:3600,state:'scheduled',
            last_cleanup_utc_ms:1700000000000,reclaimed_bytes:3145728},
          controls:{'set-semantics':true},semantics:{...semantics,...window.__semanticBudgetWait?{max_calls_per_hour:6,max_tokens_per_day:6000000}:{}},runtime:{verified:true,status:'running',
            semantic:{state:window.__semanticBudgetWait?'budget_exhausted':window.__semanticFailure?'idle':semantics.enabled?'retry_pending':'disabled',lastSuccessUtcIso:'2026-10-15T01:00:00Z',lastAttemptUtcIso:'2026-10-15T07:27:00Z',lastError:window.__semanticBudgetWait?'Expecting value: line 1 column 1 (char 0)':window.__semanticFailure?null:semantics.enabled?'合成重试状态':''},
            semantic_maintenance:{pending_windows:3,oldest_pending_utc_ms:1700000000000,next_attempt_utc_ms:window.__semanticBudgetWait?Date.parse('2026-10-15T08:00:00Z'):window.__semanticFailure?null:1700000100000,window_seconds:300,raw_protection:'until_semantic_commit',last_failure:window.__semanticFailure,usage:window.__semanticBudgetWait?{requests_hourly:6,tokens_daily:547122}:{requests_hourly:5,tokens_daily:12000}}}}, profiles:{source:'openclaw'}
      };
      else if (message.action === 'memory-search') result = {status:'ok', memories:[
        {id:'fixture-'+message.offset,title:message.offset ? '下一页活动记忆':'本周办公安排',kind:'episodic',status:'active',content:'周三整理会议资料，周五准备汇报。'},
        {id:'candidate',title:'待核对的偏好',kind:'semantic',status:'candidate',content:'<img src=x onerror="window.__unsafe=true">合成候选正文'}
      ],next_offset:message.offset ? null:20,truncated:!message.offset};
      else if (message.action === 'memory-read') result = {status:'ok',memory:{title:'本周办公安排',kind:'episodic',status:'active',
        source_observation_ids:['synthetic-observation'],metadata:{source_retention_status:'partially_archived'}},
        text:message.from_line > 1 ? '后续正文':'周三整理会议资料。\n周五准备汇报。',from_line:message.from_line,line_count:100,truncated:message.from_line===1};
      else result = {opened:true};
      if (message.action === 'status' && window.__holdActivityStatus) {
        window.__heldActivityStatus = {requestId:message.requestId,result};
        return;
      }
      setTimeout(() => window.__mirallyActivity?.receive({requestId:message.requestId,result}), 0);
    }};
  });
  await context.addInitScript(brand);
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(origin);
  try {await page.waitForFunction(() => document.querySelector('openclaw-app')?.context?.navigate);}
  catch (error) {
    await page.screenshot({path:path.join(output,'mount-failed.png'),fullPage:true});
    await fs.writeFile(path.join(output,'mount-failed.json'), JSON.stringify({errors,body:await page.locator('body').innerText()},null,2));
    throw error;
  }
  const registration = (await fs.readdir(path.join(assets,'assets'))).find(name => /^agent-select-registration-.*\.js$/.test(name));
  assert.ok(registration, 'pinned native agent selector registration');
  const choices = await page.evaluate(async registration => {
    await import('/assets/' + registration);
    await customElements.whenDefined('openclaw-agent-select');
    const section = document.createElement('section');
    section.innerHTML = '<div class="chat-composer"><openclaw-agent-select id="verify-direct-picker"></openclaw-agent-select></div>' +
      '<div class="agent-scope-control"><openclaw-agent-select id="verify-history-picker"></openclaw-agent-select></div>' +
      '<div class="sidebar-agent-menu"><wa-dropdown-item class="sidebar-agent-menu__agent-switch" value="agent:internal-fixture">Internal</wa-dropdown-item></div>';
    document.querySelector('openclaw-app').append(section);
    const direct = section.querySelector('#verify-direct-picker'), history = section.querySelector('#verify-history-picker');
    const options = [{value:'main',label:'Mirally',agent:{id:'main'}},{value:'internal-fixture',label:'Internal desktop task',agent:{id:'internal-fixture'}}];
    direct.options = options; history.options = options;
    direct.value = history.value = 'main';
    await Promise.all([direct.updateComplete,history.updateComplete]);
    document.dispatchEvent(new Event('pointerdown', {bubbles:true}));
    const internal = [...direct.querySelectorAll('[data-agent-option]')].find(node => node.value === 'internal-fixture');
    const main = [...direct.querySelectorAll('[data-agent-option]')].find(node => node.value === 'main');
    const historyInternal = [...history.querySelectorAll('[data-agent-option]')].find(node => node.value === 'internal-fixture');
    const result = {hidden:internal.hidden,disabled:internal.disabled,mainHidden:main.hidden,historyHidden:historyInternal.hidden,
      sidebarHidden:section.querySelector('.sidebar-agent-menu__agent-switch').hidden,
      nativeOptions:direct.options.map(option=>option.value)};
    const selection = new CustomEvent('wa-select',{bubbles:true,cancelable:true,detail:{item:internal}});
    direct.querySelector('wa-dropdown').dispatchEvent(selection);
    result.blocked = selection.defaultPrevented;
    direct.options = [{value:'main',label:'Mirally',agent:{id:'main'}},{value:'research',label:'Research',agent:{id:'research'}}];
    await direct.updateComplete;
    document.dispatchEvent(new Event('pointerdown', {bubbles:true}));
    result.restored = [...direct.querySelectorAll('[data-agent-option]')].every(node => !node.hidden && !node.disabled);
    section.remove();
    return result;
  }, registration);
  assert.deepEqual(choices,{hidden:true,disabled:true,mainHidden:false,historyHidden:false,sidebarHidden:true,
    nativeOptions:['main','internal-fixture'],blocked:true,restored:true});
  findings.push('real native agent picker hides only internal desktop choices; history and other agents preserved');
  // The native app retains its real workspace while reconnecting. Use that
  // supported layout state in this isolated fixture; no fake authentication.
  await page.evaluate(() => {
    const app = document.querySelector('openclaw-app');
    app.context.gateway.stop();
    Object.assign(app.context.gateway.snapshot, {phase:'reconnecting',lastError:null,offlineStable:false});
    app.requestUpdate();
    window.__mirallyActivity.open();
  });
  try {await page.locator('.mirally-activity__entry').waitFor({timeout:5000});}
  catch (error) {
    await page.screenshot({path:path.join(output,'route-failed.png'),fullPage:true});
    await fs.writeFile(path.join(output,'route-failed.json'), JSON.stringify({errors,body:await page.locator('body').innerText(),
      context:await page.evaluate(() => {const context=document.querySelector('openclaw-app').context;return Object.keys(context);})},null,2));
    throw error;
  }
  try {await page.locator('[data-status="activeCount"]').filter({hasText:'24'}).waitFor({timeout:5000});}
  catch (error) {
    await page.screenshot({path:path.join(output,'status-failed.png'),fullPage:true});
    await fs.writeFile(path.join(output,'status-failed.json'), JSON.stringify({errors,url:page.url(),
      body:await page.locator('body').innerText(),requests:await page.evaluate(() => window.__activityRequests)},null,2));
    throw error;
  }
  assert.equal(await page.locator('openclaw-router-outlet').count(), 1);
  assert.equal(await page.locator('openclaw-router-outlet').evaluate(node => node.hidden && node.inert), true);
  assert.equal(await page.evaluate(() => window.__activityRequests.some(message => message.action==='start')), false);
  assert.equal(await page.locator('[data-status="retentionInterval"]').innerText(), '每小时');
  assert.equal(await page.locator('[data-status="retentionReleased"]').innerText(), '上次实际释放 3.0 MiB');
  findings.push('native settings route and sidebar; outlet retained; initial read does not capture');
  const semanticToggle = page.locator('[data-action="set-semantics"]');
  const semanticField = name => page.locator(`[data-semantic-field="${name}"]`);
  const semanticReceipt = page.locator('[data-slot="semantic-receipt"]');
  assert.equal(await semanticToggle.getAttribute('aria-checked'),'false');
  assert.equal(await semanticField('hour').inputValue(),'24');
  assert.equal(await semanticField('day').inputValue(),'60000');
  assert.equal(await page.locator('.mirally-activity__advanced [data-action="set-semantics"]').count(),0);
  assert.match(await page.locator('[data-slot="semantic-model"]').innerText(),/fixture\/default/);
  assert.equal(await page.locator('[data-slot="semantic-pending"]').innerText(),'3 个窗口');
  assert.match(await page.locator('[data-slot="semantic-usage"]').innerText(),/5 \/ 24 次.*12,000 \/ 60,000 tokens.*本地整理预算.*不是服务商余额/);
  await semanticToggle.click();
  await semanticReceipt.filter({hasText:'保存并应用'}).waitFor();
  assert.equal(await semanticToggle.getAttribute('aria-checked'),'true');
  assert.equal(await page.evaluate(() => window.__activityRequests.some(message => ['start','set-enabled'].includes(message.action))),false);
  await semanticField('model').fill('fixture/summary');
  await semanticField('hour').fill('36');
  await semanticField('day').fill('90000');
  await page.locator('[data-action="refresh"]').click();
  await page.locator('[data-slot="receipt"]').filter({hasText:'状态已更新'}).waitFor();
  assert.equal(await semanticField('hour').inputValue(),'36');
  await page.locator('[data-action="save-semantics"]').click();
  await semanticReceipt.filter({hasText:'保存并应用'}).waitFor();
  const semanticWrite = await page.evaluate(() => window.__activityRequests.filter(row=>row.action==='set-semantics').at(-1));
  assert.equal(semanticWrite.model,'fixture/summary');assert.equal(semanticWrite.maxCallsPerHour,36);assert.equal(semanticWrite.maxTokensPerDay,90000);
  assert.match(await page.locator('[data-slot="semantic-model"]').innerText(),/指定模型：fixture\/summary/);
  await page.evaluate(() => {window.__semanticApplied=false;});
  await semanticToggle.click();
  await semanticReceipt.filter({hasText:'设置已保存，后台尚未应用'}).waitFor();
  assert.match(await semanticReceipt.innerText(),/合成后台未运行/);
  assert.equal(await semanticToggle.getAttribute('aria-checked'),'false');
  await page.evaluate(() => {window.__semanticApplied=true;});
  await semanticToggle.click();
  await semanticReceipt.filter({hasText:'保存并应用'}).waitFor();
  findings.push('independent semantic switch, ordinary model/budget controls, authoritative status, preserved drafts and saved-but-not-applied receipt');
  await page.evaluate(() => {window.__semanticFailure={error:'合成持久化失败',exhausted:true,next_attempt_utc_ms:null};});
  await page.locator('[data-action="refresh"]').click();
  await page.locator('[data-slot="semantic-failure"]').filter({hasText:'合成持久化失败'}).waitFor();
  assert.match(await page.locator('[data-slot="semantic-failure"]').innerText(),/已达 3 次尝试上限，原始记录仍保留/);
  assert.equal(await page.locator('[data-status="semantics"]').innerText(),'总结重试已达上限');
  await page.locator('[data-slot="semantic-failure"]').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'semantic-exhausted.png'),fullPage:false});
  await page.evaluate(() => {window.__semanticFailure=null;});
  await page.locator('[data-action="refresh"]').click();
  await page.locator('[data-status="semantics"]').filter({hasText:'总结失败，等待重试'}).waitFor();
  findings.push('durable semantic failure survives a synthetic plugin restart; three-attempt exhaustion explicitly preserves raw activity');
  await page.evaluate(()=>{window.__semanticBudgetWait=true;});
  await page.locator('[data-action="refresh"]').click();
  await page.locator('[data-status="semantics"]').filter({hasText:'等待本地整理预算恢复'}).waitFor();
  assert.match(await page.locator('[data-slot="semantic-wait"]').innerText(),/每小时整理次数已达上限.*16:00.*本地时间/);
  assert.match(await page.locator('[data-slot="semantic-failure"]').innerText(),/最近一次失败.*15:27.*模型未返回可用的记忆内容/);
  assert.doesNotMatch(await page.locator('[data-slot="semantic-failure"]').innerText(),/Expecting value|3 次尝试/);
  assert.match(await page.locator('[data-slot="semantic-usage"]').innerText(),/6 \/ 6 次.*547,122 \/ 6,000,000 tokens.*不是服务商余额/);
  assert.match(await page.locator('[data-slot="diagnostics"]').textContent(),/Expecting value/);
  await page.locator('[data-slot="semantic-wait"]').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'budget-wait-history.png'),fullPage:false});
  await page.evaluate(()=>{window.__semanticBudgetWait=false;});await page.locator('[data-action="refresh"]').click();
  await page.locator('[data-status="semantics"]').filter({hasText:'总结失败，等待重试'}).waitFor();
  findings.push('current local budget wait and local recovery time are separate from timestamped historical parse failure; raw error remains in diagnostics');
  assert.equal(await page.locator('[data-action="open-memory"]').isEnabled(),true);
  assert.equal(await page.locator('.mirally-activity__memory-result').count(),0);
  findings.push('Activity settings links to the native Memory workspace without a duplicate memory browser');
  const toggle = page.locator('[data-action="set-enabled"]');
  for (const action of ['browse','monitor']) {
    await page.locator(`.mirally-activity [data-action="${action}"]`).click();
  }
  assert.equal(await page.locator('[data-action="start"]').isVisible(), false);
  await toggle.click();
  await page.waitForFunction(() => document.querySelector('[data-action="set-enabled"]')?.getAttribute('aria-checked') === 'true'
    && !document.querySelector('[data-action="set-enabled"]')?.disabled);
  for (const action of ['pause','resume','stop','start','stop']) {
    await page.locator(`.mirally-activity [data-action="${action}"]`).click();
    await page.locator('[data-slot="receipt"]').filter({hasText:'状态已更新'}).waitFor();
  }
  findings.push('browser shortcuts and synthetic capture controls');
  await toggle.click();
  await page.waitForFunction(() => document.querySelector('[data-action="set-enabled"]')?.getAttribute('aria-checked') === 'false'
    && !document.querySelector('[data-action="set-enabled"]')?.disabled);
  await toggle.click();
  await page.waitForFunction(() => document.querySelector('[data-action="set-enabled"]')?.getAttribute('aria-checked') === 'true'
    && !document.querySelector('[data-action="set-enabled"]')?.disabled);
  await page.locator('[data-action="pause"]').click();
  await page.locator('[data-action="resume"]').waitFor({state:'visible'});
  assert.equal(await toggle.getAttribute('aria-checked'), 'true');
  await toggle.click();
  await page.waitForFunction(() => document.querySelector('[data-action="set-enabled"]')?.getAttribute('aria-checked') === 'false'
    && !document.querySelector('[data-action="set-enabled"]')?.disabled);
  const preferences = await page.evaluate(() => window.__activityRequests.filter(message => message.action === 'set-enabled').map(message => message.enabled));
  assert.deepEqual(preferences, [true,false,true,false]);
  findings.push('persistent recording switch controls live capture; pause preserves preference');
  await toggle.click();
  await page.waitForFunction(() => document.querySelector('[data-action="set-enabled"]')?.getAttribute('aria-checked') === 'true'
    && !document.querySelector('[data-action="set-enabled"]')?.disabled);
  const resumesBeforeStoragePressure = await page.evaluate(() => window.__activityRequests.filter(message=>message.action==='resume').length);
  await page.evaluate(() => {window.__rawStoragePaused=true;});
  await page.locator('[data-action="refresh"]').click();
  await page.locator('[data-slot="activity-warning"]').filter({hasText:'原始记录存储已满，活动采集已暂停'}).waitFor();
  assert.match(await page.locator('[data-slot="activity-warning"]').innerText(),/完成语义整理并清理释放空间后，请点击继续/);
  await page.locator('[data-slot="activity-warning"]').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'storage-quota-paused.png'),fullPage:false});
  assert.equal(await page.evaluate(() => window.__activityRequests.filter(message=>message.action==='resume').length),resumesBeforeStoragePressure);
  await page.locator('[data-action="resume"]').click();
  await page.locator('[data-slot="receipt"]').filter({hasText:'状态已更新'}).waitFor();
  assert.doesNotMatch(await page.locator('[data-slot="activity-warning"]').innerText(),/原始记录存储已满/);
  findings.push('storage quota warning explicitly pauses activity with manual continuation; semantic usage labels UTC budget counters');
  for (const [width,height,theme] of [[1280,900,'light'],[1280,900,'dark'],[900,700,'light'],[480,720,'light']]) {
    await page.setViewportSize({width,height});
    await page.evaluate(theme => {
      document.documentElement.setAttribute('data-theme', theme);
      document.documentElement.setAttribute('data-theme-mode', theme);
      for (const node of document.querySelectorAll('*')) if (node.scrollTop) node.scrollTop = 0;
    }, theme);
    await page.screenshot({path:path.join(output,`settings-${width}-${theme}.png`),fullPage:true});
    await page.locator('[data-status="retentionInterval"]').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,`memory-${width}-${theme}.png`),fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await semanticToggle.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,`semantics-${width}-${theme}.png`),fullPage:false});
    for (const field of ['model','hour','day']) {
      const bounds = await semanticField(field).boundingBox();
      assert.ok(bounds && bounds.width > 100 && bounds.x >= 0 && bounds.x + bounds.width <= width);
    }
  }
  await page.evaluate(() => document.querySelector('openclaw-app').context.navigate('appearance', {search:''}));
  await page.waitForFunction(() => !document.documentElement.hasAttribute('data-mirally-activity'));
  assert.equal(await page.locator('openclaw-router-outlet').evaluate(node => node.hidden || node.inert), false);
  findings.push('light/dark/narrow layouts without horizontal overflow; native settings restored');
  await page.evaluate(() => {window.__holdActivityStatus=true;document.querySelector('openclaw-app').context.navigate('appearance',{search:'?mirally=activity'});});
  await page.locator('[data-slot="receipt"]').filter({hasText:'上次状态'}).waitFor();
  assert.notEqual(await page.locator('[data-status="collector"]').innerText(),'—');
  assert.equal(await page.locator('.mirally-activity button:enabled, .mirally-activity input:enabled, .mirally-activity select:enabled').count(),0);
  await page.waitForFunction(() => !!window.__heldActivityStatus);
  await page.evaluate(() => {window.__holdActivityStatus=false;window.__mirallyActivity.receive(window.__heldActivityStatus);});
  await page.locator('[data-slot="receipt"]').filter({hasText:'状态已更新'}).waitFor();
  assert.equal(await page.locator('[data-action="set-enabled"]').isEnabled(),true);
  assert.equal(await page.locator('[data-action="open-memory"]').isEnabled(),true);
  await page.locator('[data-action="open-memory"]').click();
  await page.waitForURL('**/settings/memory/memories');
  findings.push('returning immediately renders cached status with all controls locked until fresh status; native Memory link unlocks and navigates correctly');
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(output,'verification.json'), JSON.stringify({findings,errors,synthetic:true}, null, 2));
  console.log(JSON.stringify({passed:findings.length,findings,output:path.relative(root,output)}));
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
}
