/**
 * Real message refresh acceptance, with disposable product facts and transport faults.
 * node tools/verify_framework_messages.mjs <test-mode runtime.json> [output-directory]
 * Creates overdue commitments in that isolated Host; never substitutes list data.
 */
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadPlaywright} from './testing/dependencies.cjs';

assert.ok(process.argv[2],'Usage: node tools/verify_framework_messages.mjs <isolated test-mode runtime.json> [output-directory]');
const runtime=JSON.parse(await readFile(resolve(process.argv[2]),'utf8'));
assert.equal(runtime.mode,'test','Requires an isolated --test-mode Host; product data will be created.');
const address=new URL(runtime.url);
assert.ok(address.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(address.hostname),'Requires a loopback test Host.');
assert.ok(typeof runtime.token==='string'&&runtime.token.length>0,'Missing test Host authentication token.');
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const output=resolve(process.argv[3]??join(root,'output/tmp/verify-framework-messages',randomUUID()));
await mkdir(output,{recursive:true});
const {chromium}=loadPlaywright();
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});
page.setDefaultTimeout(20000);
const report={runtimeMode:runtime.mode,checks:[],errors:[],fixtureNotes:[
  'Commitments, inbox rows and read state come from real isolated product RPCs.',
  'Pending/error tests delay or abort local inbox.list transport; responses are never fabricated.',
  'Reader continuity uses the existing inbox product-event invalidation handler without navigating to the header.',
]};
page.on('pageerror',error=>report.errors.push(error.message));
const tag='消息刷新验收 '+randomUUID().slice(0,8);
const fixtureCount=12;
const controller=page.locator('mirally-messages-page');
const rows=page.getByTestId('mirally-inbox-item').filter({hasText:tag});
const main=page.locator('#control-ui-main');
const refresh=()=>controller.getByRole('button',{name:'刷新',exact:true}).click();
const rpc=(method,params={})=>page.evaluate(({method,params})=>window.__mirallyHost.rpc(method,params),{method,params});
const settled=()=>page.waitForFunction(()=>document.querySelector('mirally-messages-page [aria-busy]')?.getAttribute('aria-busy')==='false');
const frame=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const check=(name,data={})=>{report.checks.push({name,...data});console.log('PASS',name);};
async function waitForRows(count) {
  await page.waitForFunction(({tag,count})=>[...document.querySelectorAll('[data-testid="mirally-inbox-item"]')].filter(row=>row.textContent.includes(tag)).length===count,{tag,count});
}
async function retained(handles) {
  assert.equal(await rows.count(),handles.length,'Loaded fixture rows remain visible.');
  for(const handle of handles)assert.equal(await handle.evaluate(node=>node.isConnected),true,'Refresh retains each existing row DOM node.');
}
async function within(promise) {
  let timer;
  try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Expected inbox.list request was not observed.')),20000);})]);}
  finally{clearTimeout(timer);}
}
let releaseHeld=()=>{};
async function holdInbox() {
  let observed;
  const seen=new Promise(resolve=>{observed=resolve;});
  const gate=new Promise(resolve=>{releaseHeld=resolve;});
  const handler=async route=>{
    if(route.request().postDataJSON()?.method==='inbox.list'){observed();await gate;}
    await route.continue();
  };
  await page.route('**/api/rpc',handler);
  return {seen,async finish(){releaseHeld();await settled();await page.unroute('**/api/rpc',handler);}};
}
async function noHorizontalOverflow(width) {
  const measurement=await page.evaluate(()=>{
    const main=document.querySelector('#control-ui-main');
    const rows=[...document.querySelectorAll('[data-testid="mirally-inbox-item"]')];
    return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
      mainWidth:main.clientWidth,mainScrollWidth:main.scrollWidth,
      overflowingRows:rows.filter(row=>row.scrollWidth>row.clientWidth+1).length};
  });
  assert.equal(measurement.viewport,width);
  assert.ok(measurement.documentWidth<=width+1,'Document fits the viewport.');
  assert.ok(measurement.mainScrollWidth<=measurement.mainWidth+1,'Message content fits its scrolling container.');
  assert.equal(measurement.overflowingRows,0);
  return measurement;
}

try {
  await page.goto(runtime.url+'/inbox#token='+encodeURIComponent(runtime.token));
  await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');
  await controller.waitFor();await settled();
  for(let index=0;index<fixtureCount;index++)await rpc('commitment.create',{
    title:`${tag} ${String(index).padStart(2,'0')}：阅读与刷新`,
    body:'来自隔离产品 owner 的真实消息。\n'+'长中文内容用于检查刷新期间的阅读连续性及窄屏换行。\n'.repeat(5),
    due_at_utc_ms:Date.now()-1000,channel:'inbox',request_id:randomUUID(),
  });
  // Listing realizes due commitments using the same public owner API as the page.
  await rpc('inbox.list');await refresh();await settled();await waitForRows(fixtureCount);
  const original=await rows.elementHandles();
  check('real overdue commitments appear in the inbox',{fixtureCount});

  const uiPending=await holdInbox();
  await refresh();await within(uiPending.seen);
  await controller.getByRole('button',{name:'刷新中…',exact:true}).waitFor();
  await retained(original);
  assert.equal(await controller.getByText('正在读取…',{exact:true}).count(),0,'Refresh does not replace loaded content with initial loading UI.');
  await page.screenshot({path:join(output,'01-refresh-pending.png')});
  await uiPending.finish();await retained(original);
  check('real refresh button retains loaded row identity while request is pending and after success');

  // Focus a real row action, then simulate the public background invalidation
  // notification. Clicking the header refresh would itself scroll away first.
  const reader=rows.nth(5).getByRole('button',{name:'标记已读',exact:true});
  await reader.focus();await frame();
  const focus=await reader.elementHandle();
  const before=await main.evaluate(node=>node.scrollTop);
  assert.ok(before>0,'Fixture places the reader below the header.');
  const backgroundPending=await holdInbox();
  await page.evaluate(()=>window.dispatchEvent(new CustomEvent('mirally-product-event',{detail:{type:'inbox'}})));
  await within(backgroundPending.seen);await frame();await retained(original);
  assert.equal(await focus.evaluate(node=>document.activeElement===node),true,'Pending refresh preserves reader focus.');
  assert.ok(Math.abs(await main.evaluate(node=>node.scrollTop)-before)<=2,'Pending refresh preserves reading position.');
  await backgroundPending.finish();await frame();await retained(original);
  assert.equal(await focus.evaluate(node=>document.activeElement===node),true,'Completed refresh preserves the same focused action.');
  const after=await main.evaluate(node=>node.scrollTop);
  assert.ok(Math.abs(after-before)<=2,'Completed refresh preserves reading position.');
  await page.screenshot({path:join(output,'02-reader-continuity.png')});
  check('background invalidation preserves row DOM, reading scroll and keyboard focus',{before,after});

  let failed=false;
  const fail=async route=>{
    if(!failed&&route.request().postDataJSON()?.method==='inbox.list'){failed=true;await route.abort('failed');}
    else await route.continue();
  };
  await page.route('**/api/rpc',fail);await refresh();
  await controller.getByRole('alert').filter({hasText:'保留上次读取的内容'}).waitFor();await settled();
  await retained(original);
  const failureText=await controller.getByRole('alert').innerText();
  assert.match(failureText,/请刷新重试/);assert.doesNotMatch(failureText,/gateway|Failed to fetch/i,'Transport diagnostics stay out of the primary recovery message.');
  const details=controller.locator('details').filter({hasText:'错误详情'});
  assert.equal(await details.getAttribute('open'),null,'Diagnostic details are collapsed by default.');
  const rawDetail=await details.locator('pre').textContent();
  assert.ok(rawDetail.trim().length>0,'The original failure is retained for diagnosis.');
  assert.equal(await details.locator('pre').isVisible(),false);
  assert.equal(await controller.getByText('读取失败，刷新后重试。',{exact:true}).count(),0,'A failed refresh does not claim the loaded snapshot is absent.');
  await page.screenshot({path:join(output,'03-refresh-failed.png')});
  await details.locator('summary').click();
  assert.equal(await details.locator('pre').isVisible(),true,'Native details expose the diagnostic on demand.');
  await page.screenshot({path:join(output,'03-refresh-error-details.png')});
  await page.unroute('**/api/rpc',fail);await refresh();await settled();await retained(original);
  assert.equal(await controller.getByRole('alert').count(),0,'Successful retry clears the previous failure.');
  assert.equal(await details.count(),0,'Successful retry removes expired diagnostic details.');
  check('aborted real request retains the snapshot, reports failure, and recovers through the refresh button');

  const readTitle=await rows.first().locator('h3').innerText();
  await rows.first().getByRole('button',{name:'标记已读',exact:true}).click();
  await controller.getByText('已标记为已读。',{exact:true}).waitFor();await settled();
  const facts=await rpc('inbox.list');
  assert.equal(facts.items.find(item=>item.title===readTitle)?.state,'read','Mark-read is persisted by the actual owner.');
  await page.getByTestId('mirally-inbox-unread').check();await waitForRows(fixtureCount-1);
  assert.equal(await rows.filter({hasText:readTitle}).count(),0);
  await page.getByTestId('mirally-inbox-unread').uncheck();await waitForRows(fixtureCount);
  check('real mark-read and unread filter retain their behavior after refresh recovery');

  const desktop=await noHorizontalOverflow(1280);
  await page.screenshot({path:join(output,'04-desktop.png')});
  await page.setViewportSize({width:375,height:812});await frame();
  const narrow=await noHorizontalOverflow(375);
  await rows.last().scrollIntoViewIfNeeded();
  await page.screenshot({path:join(output,'05-narrow.png')});
  check('desktop and 375px inbox fit without horizontal overflow',{desktop,narrow});
  assert.deepEqual(report.errors,[],'No uncaught browser errors.');
  report.verdict='PASS';
} catch(error) {
  report.verdict='FAIL';report.failure=error.stack;
  await page.screenshot({path:join(output,'failure.png')}).catch(()=>{});
  throw error;
} finally {
  releaseHeld();
  await writeFile(join(output,'messages-report.json'),JSON.stringify(report,null,2));
  await browser.close();
}
