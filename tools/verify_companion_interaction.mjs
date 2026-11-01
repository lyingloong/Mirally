import {loadPlaywright} from './testing/dependencies.cjs';
/**
 * Offline checks against Mirally's actual companion JS, CSS and artwork.
 * The native message bridge and owning chat pane are fixture boundaries;
 * no model, Gateway, private profile, or native window is started.
 *
 * Run sequentially with other GUI acceptance tools:
 *   node tools/verify_companion_interaction.mjs [output-directory]
 * MIRALLY_PLAYWRIGHT may name an installed playwright-core module/path.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.resolve(process.argv[2] ?? path.join(root, '.artifacts/companion-interaction'));
const {chromium} = loadPlaywright();
const [source, css, office, otter, fox, logo] = await Promise.all([
  'control-ui-companion.js', 'control-ui-companion.css', 'control-ui-office.js',
  'mirally-pet-otter.svg', 'mirally-pet.svg', 'mirally-128.png',
].map(name => fs.readFile(path.join(root, 'mirally/assets', name), name.endsWith('.png') ? undefined : 'utf8')));

let origin;
const fixture = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>Mirally companion interaction acceptance</title>
<style>
  :root{--accent:#0878b2;--bg-elevated:#fff;--text:#192b3a;--muted:#627387;--border:#e1e8ef;--bg-hover:#edf3f9;--accent-foreground:#fff}
  html,body{margin:0;width:100%;height:100%;font-family:"Segoe UI","Microsoft YaHei",sans-serif}
  body{background:#edf3f9} .fixture-context{position:fixed;inset:0;padding:40px;color:#627387;pointer-events:none}
</style><link rel="stylesheet" href="/companion.css"></head><body>
<div class="fixture-context">Mirally · offline interaction fixture</div>
<openclaw-app></openclaw-app><openclaw-chat-pane class="chat-pane-cache__pane--active"></openclaw-chat-pane>
<script>
  window.__posts=[];window.__probes=[];window.__pendingProbe=null;
  window.chrome=window.chrome||{};
  window.chrome.webview={postMessage:value=>window.__posts.push({...value,at:performance.now()})};
  document.querySelector('openclaw-app').context={};
  window.fixturePane=document.querySelector('openclaw-chat-pane');
  fixturePane.paneId='interaction-fixture';fixturePane.paneTitle='一起打磨手头的工作';
  fixturePane.state={sessionKey:'interaction-fixture',connected:true,chatMessage:'',chatAttachments:[],
    chatMessages:[{role:'user',content:'一起检查这份设计方案'}],
    handleSendChat:()=>{},handleChatDraftChange:value=>fixturePane.state.chatMessage=value};
  window.__startProbe=(label,duration=650)=>{
    if(window.__pendingProbe)throw new Error('Motion probes must run sequentially');
    const start=performance.now(),firstPost=window.__posts.length,samples=[];
    window.__pendingProbe=new Promise(resolve=>{
      function sample(now){
        const node=document.querySelector('.mirally-companion'),r=node.getBoundingClientRect();
        samples.push({at:now-start,view:node.dataset.view,x:r.x,y:r.y,width:r.width,height:r.height});
        if(now-start<duration)return requestAnimationFrame(sample);
        const intervals=samples.slice(1).map((item,index)=>item.at-samples[index].at).sort((a,b)=>a-b);
        const percentile=p=>intervals.length?intervals[Math.min(intervals.length-1,Math.floor(intervals.length*p))]:null;
        const result={label,samples,frames:samples.length,
          frameIntervalsMs:{median:percentile(.5),p95:percentile(.95),max:intervals.at(-1)??null},
          shapeMessages:window.__posts.slice(firstPost).filter(item=>item.action==='shape').length};
        window.__probes.push(result);window.__pendingProbe=null;resolve(result);
      }requestAnimationFrame(sample);
    });
  };
</script><script src="/office.js"></script><script src="/companion.js"></script></body></html>`;
const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, origin).pathname;
  if (pathname === '/companion.js') {
    response.setHeader('Content-Type', 'text/javascript; charset=utf-8');
    response.end(source.replaceAll('__MIRALLY_ORIGIN__', JSON.stringify(origin))
      .replaceAll('__MIRALLY_MARK__', JSON.stringify('data:image/png;base64,' + logo.toString('base64')))
      .replaceAll('__MIRALLY_PETS__', JSON.stringify({otter, fox})));
  } else if (pathname === '/office.js') {
    response.setHeader('Content-Type', 'text/javascript; charset=utf-8');
    response.end(office.replaceAll('__MIRALLY_ORIGIN__', JSON.stringify(origin)));
  } else if (pathname === '/companion.css') {
    response.setHeader('Content-Type', 'text/css; charset=utf-8'); response.end(css);
  } else if (pathname === '/') {
    response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end(fixture);
  } else { response.writeHead(404); response.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
origin = `http://127.0.0.1:${server.address().port}`;
await fs.mkdir(output, {recursive:true});

let browser, currentPage;
const errors = [], findings = [], metrics = [], screenshots = [];
async function context(options = {}) {
  const result = await browser.newContext({viewport:{width:1280,height:900}, ...options});
  await result.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  await result.routeWebSocket('**', socket => socket.close());
  return result;
}
async function ready(ctx) {
  const page = await ctx.newPage(); currentPage = page;
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(origin); await page.waitForFunction(() => window.__mirallyCompanion);
  await page.mouse.move(24, 24);
  await page.evaluate(() => window.__mirallyCompanion.dock());
  await page.waitForTimeout(80);
  return page;
}
const view = page => page.locator('.mirally-companion').getAttribute('data-view');
const action = (page, name) => page.locator(`[data-action="${name}"]:visible`).first();
const rail = page => page.locator('.mirally-companion__rail');
async function enter(page) {
  const r = await rail(page).boundingBox(); assert.ok(r, 'companion anchor must have bounds');
  await page.mouse.move(r.x + r.width / 2, r.y + r.height / 2);
}
async function outside(page) { await page.mouse.move(24, 24); }
async function waitView(page, value) {
  await page.waitForFunction(value => document.querySelector('.mirally-companion').dataset.view === value, value);
}
async function image(page, name) {
  const filename = name + '.png'; await page.screenshot({path:path.join(output, filename)});
  screenshots.push(filename);
}
async function collectProbe(page) {
  const probe = await page.evaluate(() => window.__pendingProbe ?? window.__probes.at(-1));
  metrics.push(probe); return probe;
}
function stableBounds(probe, target) {
  const samples = probe.samples.filter(sample => sample.view === target);
  assert.ok(samples.length >= 2, `${probe.label}: need multiple ${target} layout samples`);
  for (const key of ['width', 'height']) {
    const values = samples.map(sample => sample[key]);
    assert.ok(Math.max(...values) - Math.min(...values) <= 1,
      `${probe.label}: outer ${key} must stay stable during content motion`);
  }
  assert.ok(probe.shapeMessages <= 4, `${probe.label}: a stable surface should not rebuild native geometry every frame (${probe.shapeMessages})`);
}
async function pinState(page, expected) {
  assert.equal(await action(page, 'pin').getAttribute('aria-pressed'), String(expected));
}
async function collapse(page) {
  await action(page, 'collapse').click(); await waitView(page, 'compact');
  assert.equal(await rail(page).evaluate(node => document.activeElement === node), true, 'collapse returns focus to the anchor');
}
async function preserve(page, draft, attachmentIds) {
  assert.deepEqual(await page.evaluate(() => ({draft:fixturePane.state.chatMessage,
    ids:fixturePane.state.chatAttachments.map(item => item.id),key:fixturePane.state.sessionKey})),
  {draft,ids:attachmentIds,key:'interaction-fixture'});
}

try {
  browser = await chromium.launch({channel:'msedge',headless:true});
  const normal = await context(); const page = await ready(normal);

  await enter(page); await page.waitForTimeout(35); await outside(page); await page.waitForTimeout(240);
  assert.equal(await view(page), 'compact', 'brief pointer passes must not open the sidebar');
  findings.push('brief mouse passes do not open the companion');

  await page.evaluate(() => window.__startProbe('normal-hover-open'));
  await enter(page); await waitView(page, 'quick'); await pinState(page, false);
  stableBounds(await collectProbe(page), 'quick');
  const layout = await page.evaluate(() => {
    const surface=document.querySelector('.mirally-companion'), panel=document.querySelector('.mirally-companion__panel');
    return {properties:getComputedStyle(surface).transitionProperty.split(',').map(value=>value.trim()),
      quickPanelHeight:panel.getBoundingClientRect().height};
  });
  for (const forbidden of ['all','width','height']) assert.equal(layout.properties.includes(forbidden), false,
    `outer geometry must not animate ${forbidden}`);
  assert.ok(Math.abs(layout.quickPanelHeight - 188) <= 1, 'quick panel has room for its readable header and composer');
  await image(page, '01-hover-preview');
  findings.push('mouse dwell opens a temporary quick composer with fixed outer geometry');

  await outside(page); await page.waitForTimeout(180);
  assert.equal(await view(page), 'quick', 'crossing away briefly must respect the close grace');
  await enter(page); await page.waitForTimeout(300);
  assert.equal(await view(page), 'quick', 'returning cancels the pending close');
  await outside(page); await waitView(page, 'compact');
  findings.push('leave grace allows re-entry; preview closes after a real departure');

  const selectTitle=()=>page.evaluate(()=>{
    const range=document.createRange();range.selectNodeContents(document.querySelector('[data-slot="peek-title"]'));
    getSelection().removeAllRanges();getSelection().addRange(range);
  });
  await enter(page);await waitView(page,'quick');await selectTitle();await outside(page);
  await page.waitForTimeout(550);assert.equal(await view(page),'quick','active text selection protects a preview');
  await page.evaluate(()=>getSelection().removeAllRanges());await waitView(page,'compact');
  findings.push('active text selection protects reading, then releases the preview when cleared');

  await rail(page).click(); await waitView(page, 'detail'); await pinState(page, true);
  await outside(page); await page.waitForTimeout(650);
  assert.equal(await view(page), 'detail', 'click-opened rail remains open away from the pointer');
  await collapse(page); await page.waitForTimeout(250);
  assert.equal(await view(page), 'compact', 'explicit collapse does not immediately reopen on hover or focus');
  await page.keyboard.press('Enter'); await waitView(page, 'detail'); await pinState(page, true);
  await outside(page); await page.waitForTimeout(650); assert.equal(await view(page), 'detail');
  await page.keyboard.press('Escape'); await waitView(page, 'compact');
  assert.equal(await rail(page).evaluate(node => document.activeElement === node), true);
  findings.push('click and keyboard open persistently; collapse and Escape restore anchor focus');
  await page.evaluate(()=>window.__mirallyCompanion.setForm('badge',false));

  await outside(page); await enter(page); await waitView(page, 'quick'); await pinState(page, false);
  await action(page, 'pin').click(); await pinState(page, true);
  await outside(page); await page.waitForTimeout(650); assert.equal(await view(page), 'quick');
  await action(page, 'pin').click(); await pinState(page, false);
  await outside(page); await waitView(page, 'compact');
  findings.push('pin has an accessible pressed state and controls persistent versus automatic dismissal');

  await page.evaluate(() => {
    fixturePane.state.chatAttachments=[{id:'material-a',fileName:'设计草图.txt',mimeType:'text/plain',content:'fixture material'}];
    window.__mirallyCompanion.refresh();
  });
  await enter(page); await waitView(page, 'quick');
  const draft = '保留这份草稿和材料，稍后接着讨论。';
  await page.locator('[data-slot="draft"]').fill(draft); await pinState(page, true);
  await outside(page); await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  await page.waitForTimeout(650); assert.equal(await view(page), 'quick');
  await preserve(page, draft, ['material-a']);
  await image(page, '02-pinned-draft'); await collapse(page);
  await preserve(page, draft, ['material-a']);
  await page.keyboard.press('Enter'); await waitView(page, 'quick');
  assert.equal(await page.locator('[data-slot="draft"]').inputValue(), draft);
  await action(page, 'detail').click(); await waitView(page, 'detail');
  await outside(page); await page.waitForTimeout(650); assert.equal(await view(page), 'detail');
  assert.equal(await page.locator('[data-slot="attachments"]').textContent(), '设计草图.txt');
  await image(page, '03-detail'); await preserve(page, draft, ['material-a']);
  await page.keyboard.press('Escape'); await waitView(page, 'compact');
  await preserve(page, draft, ['material-a']);
  findings.push('typing and explicit detail protect the interaction across leave/blur; drafts, materials and ownership survive collapse');

  // A stationary surface and repeated explicit geometry requests must be quiet.
  await outside(page); await page.waitForTimeout(300);
  const quiet = await page.evaluate(async () => {
    const before=window.__posts.filter(item=>item.action==='shape').length;
    for(let index=0;index<12;index++){
      window.__mirallyCompanion.syncShape();window.__mirallyCompanion.refresh();
      await new Promise(resolve=>requestAnimationFrame(resolve));
    }
    await new Promise(resolve=>setTimeout(resolve,220));
    return window.__posts.filter(item=>item.action==='shape').length-before;
  });
  assert.equal(quiet, 0, 'quiet layout must not resend identical native shapes');
  const transitions = await page.evaluate(async () => {
    const before=window.__posts.filter(item=>item.action==='shape').length;
    for(let index=0;index<8;index++){
      window.__mirallyCompanion.setView(index%2?'compact':'quick');
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    }
    return window.__posts.filter(item=>item.action==='shape').length-before;
  });
  assert.ok(transitions <= 10, `eight target changes have bounded shape work (${transitions})`);
  metrics.push({label:'shape-budget',quietMessages:quiet,eightTransitions:transitions});
  findings.push('identical geometry is silent and repeated state changes have bounded native shape messages');

  // Mouse hover is intentionally different from touch activation.
  const touchContext = await context({hasTouch:true}); const touch = await ready(touchContext);
  await touch.evaluate(() => document.querySelector('.mirally-companion').dispatchEvent(
    new PointerEvent('pointerenter',{pointerType:'touch',bubbles:false})));
  await touch.waitForTimeout(260); assert.equal(await view(touch), 'compact', 'touch contact must not trigger a mouse hover preview');
  const touchBounds = await rail(touch).boundingBox();
  await touch.touchscreen.tap(touchBounds.x+touchBounds.width/2,touchBounds.y+touchBounds.height/2);
  await waitView(touch, 'detail'); await pinState(touch, true);
  assert.equal(await touch.evaluate(()=>matchMedia('(pointer:coarse)').matches),true);
  for(const target of ['detail','quick','menu']) {
    await touch.evaluate(target=>window.__mirallyCompanion.setView(target),target);
    await touch.waitForTimeout(200);
    const controls=await touch.locator([
      '.mirally-companion__header button:visible', '.mirally-companion__quick-header button:visible',
      '.mirally-companion__composer button:visible', '.mirally-companion__navigation button:visible',
      '.mirally-companion__menu-actions button:visible', '.mirally-companion__appearance button:visible',
    ].join(',')).evaluateAll(nodes=>{
      const panel=document.querySelector('.mirally-companion__panel').getBoundingClientRect();
      return nodes.map(node=>{const r=node.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
        return {label:node.getAttribute('aria-label')??node.textContent.trim(),width:r.width,height:r.height,
          inside:r.x>=panel.x-1&&r.right<=panel.right+1&&r.y>=panel.y-1&&r.bottom<=panel.bottom+1,
          hit:node===hit||node.contains(hit)};});
    });
    assert.ok(controls.length>0);
    for(const control of controls){
      assert.ok(control.height>=40,`${target}: touch target is at least 40px high: ${JSON.stringify(control)}`);
      assert.ok(control.inside&&control.hit,`${target}: touch action is visible and reachable: ${JSON.stringify(control)}`);
    }
  }
  await image(touch,'07-touch-menu');
  await touch.evaluate(()=>window.__mirallyCompanion.setView('detail'));
  await touch.locator('[data-slot="draft"]').fill('触屏按钮可达性检查');
  for(const [width,height] of [[400,454],[267,454],[235,350]]) {
    await touch.setViewportSize({width,height});
    for(const form of ['rail','badge','pet'])for(const target of ['detail','quick','menu']) {
      await touch.evaluate(({form,target})=>{window.__mirallyCompanion.setForm(form,false);window.__mirallyCompanion.setView(target);},{form,target});
      await touch.waitForTimeout(200);
      const geometry=await touch.evaluate(()=>{
        window.__mirallyCompanion.syncShape();
        const panel=document.querySelector('.mirally-companion__panel');
        return {shape:window.__posts.filter(item=>item.action==='shape').at(-1),overflow:panel.scrollWidth-panel.clientWidth};
      });
      assert.ok(geometry.overflow<=1,`${width}/${form}/${target} must not need horizontal scrolling (${geometry.overflow}px)`);
      for(const [x,y,w,h]of [geometry.shape.rect,...geometry.shape.parts])
        assert.ok(x>=-1&&y>=-1&&x+w<=width+1&&y+h<=height+1,`${width}/${form}/${target} shape fits viewport: ${JSON.stringify(geometry)}`);
      for(const name of target==='menu'?['quick','detail','workspace','minimize']:target==='quick'?['pin','collapse','send','workspace','detail']:['pin','collapse','send','workspace','menu']) {
        try{await action(touch,name).click({trial:true});}
        catch(error){throw new Error(`${width}/${form}/${target}/${name}: ${error.message}`,{cause:error});}
      }
    }
  }
  await touch.close(); await touchContext.close(); currentPage=page;
  findings.push('touch tap opens persistently; 40px actions and three forms remain reachable at 400/267/235px widths, including a 350px-high viewport');

  await outside(page);
  await page.evaluate(() => {window.__mirallyCompanion.setForm('pet');window.__mirallyCompanion.setView('compact');});
  await enter(page); await page.waitForTimeout(240);
  assert.equal(await view(page), 'compact', 'pet remains a pet on hover');
  await rail(page).click(); await waitView(page, 'quick'); await pinState(page, true);
  assert.equal(await page.locator('.mirally-companion').getAttribute('data-reaction'), 'poke');
  await image(page, '04-pet-quick');
  const dragBefore=await page.evaluate(()=>window.__posts.filter(item=>item.action==='drag').length);
  const dragBounds=await rail(page).boundingBox(),dx=dragBounds.x+dragBounds.width/2,dy=dragBounds.y+dragBounds.height/2;
  await page.mouse.move(dx,dy);await page.mouse.down();await page.mouse.move(dx-14,dy+12,{steps:3});await page.mouse.up();
  assert.ok(await page.evaluate(before=>window.__posts.filter(item=>item.action==='drag').length>before,dragBefore), 'pet drag still delegates a real pointer gesture to the native host');
  findings.push('pet click opens conversation and reacts; dragging still delegates to the native move');

  await action(page, 'menu').click(); await waitView(page, 'menu'); await image(page, '05-more-menu');
  const minimize=action(page,'minimize');assert.equal(await minimize.isVisible(), true);
  assert.match(await minimize.getAttribute('aria-label') ?? await minimize.innerText(), /最小化/);
  const workspace=action(page,'workspace');
  assert.match(await workspace.getAttribute('aria-label') ?? await workspace.innerText(), /主窗口/);
  await minimize.click();
  assert.equal(await page.evaluate(()=>window.__posts.some(item=>item.action==='minimize')),true);
  if (await view(page)!=='menu') {
    if(await view(page)==='compact'){await rail(page).click();await waitView(page,'quick');}
    await action(page,'menu').click();await waitView(page,'menu');
  }
  await action(page,'workspace').click();
  assert.equal(await page.evaluate(()=>document.documentElement.dataset.mirallyMode),'workspace');
  assert.equal(await page.evaluate(()=>window.__posts.some(item=>item.action==='workspace')),true);
  await preserve(page,draft,['material-a']);
  findings.push('more menu exposes explicit minimize and main-window actions without losing the chat draft');

  const tooltip=page.locator('.mirally-companion-tip');
  const checkTip=async(expectedParts)=>{
    const value=await page.evaluate(()=>{
      const r=document.querySelector('.mirally-companion-tip').getBoundingClientRect();
      return {box:[r.x,r.y,r.width,r.height],shape:window.__posts.filter(item=>item.action==='shape').at(-1),
        width:innerWidth,height:innerHeight};
    });
    assert.equal(value.shape.parts.length,expectedParts);
    for(const [x,y,w,h]of [value.shape.rect,...value.shape.parts])
      assert.ok(x>=0&&y>=0&&x+w<=value.width+1&&y+h<=value.height+1,`tooltip clip fits viewport: ${JSON.stringify(value)}`);
    assert.ok(value.shape.parts.some(part=>value.box.every((n,index)=>Math.abs(n-part[index])<.1)),'native shape must include the actual tooltip');
    return value;
  };
  await outside(page);
  await page.evaluate(()=>{window.__mirallyCompanion.setForm('pet');window.__mirallyCompanion.dock();});
  await waitView(page,'compact');await enter(page);await tooltip.waitFor({state:'visible'});
  const tipBox=(await checkTip(2)).box,anchorBox=await rail(page).boundingBox();
  assert.ok(tipBox[0]+tipBox[2]<=anchorBox.x,'compact hint is readable outside the pet boundary');
  assert.equal(await tooltip.evaluate(node=>node.parentElement===document.body),true);
  assert.equal(await rail(page).getAttribute('title'),null,'no duplicate clipped native title');
  assert.match(await rail(page).getAttribute('aria-describedby'),/mirally-companion-tip/);
  await page.mouse.move(tipBox[0]+tipBox[2]/2,tipBox[1]+tipBox[3]/2);await page.waitForTimeout(550);
  assert.equal(await tooltip.isVisible(),true,'a hint remains available while the pointer reads it');
  await image(page,'08-tip-outside-pet');
  await outside(page);await tooltip.waitFor({state:'hidden'});
  assert.equal(await rail(page).getAttribute('aria-describedby'),null);

  await page.evaluate(()=>{window.__mirallyCompanion.setForm('rail');window.__mirallyCompanion.setView('quick');});
  // This programmatic form switch has no user dwell; finish finite entry motion
  // before placing the pointer on the control whose tooltip is under test.
  await page.waitForFunction(()=>!document.querySelector('.mirally-companion').getAnimations({subtree:true})
    .some(animation=>animation.playState==='running'&&animation.effect.getComputedTiming().endTime!==Infinity));
  await action(page,'pin').hover();await tooltip.waitFor({state:'visible'});await checkTip(3);
  await outside(page);await page.waitForTimeout(70);await action(page,'pin').hover();await page.waitForTimeout(230);
  assert.equal(await tooltip.isVisible(),true,'returning to the same control cancels tooltip dismissal');
  await page.keyboard.press('Escape');await tooltip.waitFor({state:'hidden'});
  assert.equal(await view(page),'quick','first Escape dismisses the hint without closing the panel');
  await page.keyboard.press('Escape');await waitView(page,'compact');
  await page.waitForTimeout(550);assert.equal(await tooltip.isVisible(),false,'collapse focus does not immediately reopen the dismissed hint');

  for(const width of [400,267,235]){
    await page.setViewportSize({width,height:454});
    await page.evaluate(()=>{window.__mirallyCompanion.setForm('rail');window.__mirallyCompanion.setView('detail');});
    await page.waitForTimeout(220);
    await action(page,'collapse').hover();await tooltip.waitFor({state:'visible'});await checkTip(2);
    await outside(page);await tooltip.waitFor({state:'hidden'});
  }
  await page.setViewportSize({width:1280,height:900});
  findings.push('body-level hints extend beyond companion edges, join native clipping, survive re-entry, dismiss with Escape and fit narrow viewports');

  const reducedContext=await context({reducedMotion:'reduce'}),reduced=await ready(reducedContext);
  await reduced.evaluate(()=>window.__startProbe('reduced-motion-hover-open'));
  await enter(reduced);await waitView(reduced,'quick');stableBounds(await collectProbe(reduced),'quick');
  const animated=await reduced.evaluate(()=>{
    const nodes=[document.querySelector('.mirally-companion'),...document.querySelectorAll('.mirally-companion *')];
    return nodes.filter(node=>{
      const style=getComputedStyle(node);
      return style.animationName.split(',').some(value=>value.trim()!=='none') ||
        style.transitionDuration.split(',').some(value=>parseFloat(value)>0);
    }).map(node=>node.className?.baseVal??node.className);
  });
  assert.deepEqual(animated,[],'reduced motion disables presentation and pet animations');
  await image(reduced,'06-reduced-motion');await reduced.close();await reducedContext.close();currentPage=page;
  findings.push('reduced-motion preference removes content transitions and pet animation');
  assert.deepEqual(errors,[]);
} catch(error) {
  errors.push(error.stack??String(error));
  try { if(currentPage&&!currentPage.isClosed())await image(currentPage,'failure'); } catch {}
  process.exitCode=1;
} finally {
  const report={findings,metrics,screenshots,errors,
    scope:'Real companion DOM/CSS/artwork with fixture native bridge; no native clipping or full-product GUI claim.',
    timing:'Frame intervals are observations, not fixed-FPS pass criteria.'};
  await fs.writeFile(path.join(output,'results.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({findings,metrics:metrics.map(({samples,...metric})=>metric),screenshots,errors,output},null,2));
  await browser?.close();await new Promise(resolve=>server.close(resolve));
}
