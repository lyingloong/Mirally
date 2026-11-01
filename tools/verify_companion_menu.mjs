import {loadPlaywright} from './testing/dependencies.cjs';
/** Offline real-DOM checks for companion menus; no Gateway or model requests. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const {chromium} = loadPlaywright();
let origin;
const source=await fs.readFile(path.join(root,'mirally/assets/control-ui-companion.js'),'utf8');
const css=await fs.readFile(path.join(root,'mirally/assets/control-ui-companion.css'),'utf8');
const svg='<svg viewBox="0 0 120 150"><circle cx="60" cy="70" r="40" fill="teal"/></svg>';
const server=http.createServer((request,response)=>{
  const pathname=new URL(request.url,origin).pathname;
  if(pathname==='/companion.js'){response.setHeader('Content-Type','text/javascript; charset=utf-8');response.end(source.replaceAll('__MIRALLY_ORIGIN__',JSON.stringify(origin)).replaceAll('__MIRALLY_MARK__',JSON.stringify('data:image/svg+xml,'+encodeURIComponent(svg))).replaceAll('__MIRALLY_PETS__',JSON.stringify({otter:svg,fox:svg})));return;}
  if(pathname==='/companion.css'){response.setHeader('Content-Type','text/css');response.end(css);return;}
  response.setHeader('Content-Type','text/html; charset=utf-8');response.end(`<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/companion.css"></head><body><openclaw-app></openclaw-app><openclaw-chat-pane class="chat-pane-cache__pane--active"></openclaw-chat-pane><script>
    window.__posts=[];window.chrome=window.chrome||{};window.chrome.webview={postMessage:value=>window.__posts.push(value)};
    window.__mirallyOffice={createReplyTracker:()=>()=>null,isFinal:row=>row.role==='assistant',handoff:()=>null,plain:value=>value};
    document.querySelector('openclaw-app').context={};
    const pane=document.querySelector('openclaw-chat-pane');pane.paneId='synthetic';pane.state={sessionKey:'fixture',connected:true,chatMessage:'',chatMessages:[],handleSendChat:()=>{},handleChatDraftChange:value=>pane.state.chatMessage=value};
  </script><script src="/companion.js"></script></body></html>`);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
origin=`http://127.0.0.1:${server.address().port}`;
let browser;
const errors=[],findings=[];
try{
  browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await context.routeWebSocket('**',socket=>socket.close());
  const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
  await page.goto(origin);await page.waitForFunction(()=>window.__mirallyCompanion);
  await page.evaluate(()=>window.__mirallyCompanion.dock());
  const surface=page.locator('.mirally-companion');
  for(const form of ['rail','badge','pet']){
    await page.mouse.move(300,50);
    await page.evaluate(form=>{window.__mirallyCompanion.setForm(form);window.__mirallyCompanion.setView('compact');},form);
    const anchor=page.locator('.mirally-companion__rail');
    const anchorBox=await anchor.boundingBox();
    await page.mouse.move(anchorBox.x+anchorBox.width/2,anchorBox.y+anchorBox.height/2);
    if(form==='rail'){
      await page.waitForFunction(()=>document.querySelector('.mirally-companion').dataset.view==='quick');
      await anchor.click({button:'right'});
    }else await anchor.click({button:'right'});
    await page.waitForFunction(()=>document.querySelector('.mirally-companion').dataset.view==='menu');
    await page.waitForTimeout(850);
    assert.equal(await surface.getAttribute('data-view'),'menu',`${form} menu must survive shape resize without real movement`);
    assert.equal(await page.locator('.mirally-companion__menu-actions').isVisible(),true);
    await page.keyboard.press('Escape');assert.equal(await surface.getAttribute('data-view'),'compact');
    findings.push(`${form}: right click opens a stable menu; Escape closes it`);
  }
  await page.evaluate(()=>{window.__mirallyCompanion.setForm('rail');window.__mirallyCompanion.setView('detail');});
  await page.waitForTimeout(300);
  const editResult=await page.evaluate(()=>{
    const draft=document.querySelector('.mirally-companion textarea');const rect=draft.getBoundingClientRect();
    const event=new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2});
    return {allowed:draft.dispatchEvent(event),nativeAccepted:window.__mirallyCompanion.requestContextMenu(rect.x+10,rect.y+10),view:document.querySelector('.mirally-companion').dataset.view};
  });
  assert.deepEqual(editResult,{allowed:true,nativeAccepted:false,view:'detail'});
  const selectedResult=await page.evaluate(()=>{
    const label=document.querySelector('.mirally-companion__status');const range=document.createRange();range.selectNodeContents(label);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
    const allowed=label.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2}));selection.removeAllRanges();return allowed;
  });assert.equal(selectedResult,true);
  findings.push('draft editing and selected text keep native context menus');
  const nativeResult=await page.evaluate(()=>{
    const target=document.querySelector('.mirally-companion__header');const r=target.getBoundingClientRect();
    return [window.__mirallyCompanion.requestContextMenu(20,20),window.__mirallyCompanion.requestContextMenu(NaN,1),window.__mirallyCompanion.requestContextMenu(r.x+10,r.y+10),window.__mirallyCompanion.requestContextMenu(r.x+10,r.y+10)];
  });assert.deepEqual(nativeResult,[false,false,true,false]);
  await page.waitForTimeout(850);assert.equal(await surface.getAttribute('data-view'),'menu');
  await page.mouse.move(1260,450);await page.mouse.move(300,50);await page.waitForTimeout(550);
  assert.equal(await surface.getAttribute('data-view'),'compact','real movement away closes the menu');
  await page.evaluate(()=>window.__mirallyCompanion.openMenu());
  await page.evaluate(()=>window.dispatchEvent(new Event('blur')));
  assert.equal(await surface.getAttribute('data-view'),'compact');
  await page.evaluate(()=>window.__mirallyCompanion.workspace());
  assert.equal(await page.evaluate(()=>window.__mirallyCompanion.openMenu()),false);
  findings.push('native requests are hit-tested and idempotent; movement away, blur and workspace boundaries are respected');
  // A fresh page has never received a pointer event. Exercise the native-only
  // entry before Playwright moves the mouse, rather than reusing DOM history.
  const nativeOnly=await context.newPage();nativeOnly.on('pageerror',error=>errors.push(String(error)));
  await nativeOnly.addInitScript(()=>{window.__pointerHistory=[];for(const type of ['pointerenter','pointermove','pointerdown','pointerup'])document.addEventListener(type,event=>window.__pointerHistory.push(event.type),true);});
  await nativeOnly.goto(origin);await nativeOnly.waitForFunction(()=>window.__mirallyCompanion);
  const nativeOnlyPoint=await nativeOnly.evaluate(async()=>{
    await window.__mirallyCompanion.dock();window.__mirallyCompanion.setForm('pet');window.__mirallyCompanion.setView('compact');
    const rail=document.querySelector('.mirally-companion__rail').getBoundingClientRect();
    const x=rail.x+rail.width/2,y=rail.y+rail.height/2;
    const history=[...window.__pointerHistory];
    const opened=window.__mirallyCompanion.requestContextMenu(x,y);
    document.querySelector('.mirally-companion').dispatchEvent(new PointerEvent('pointerleave',{clientX:x,clientY:y,screenX:0,screenY:0}));
    return {x,y,opened,history};
  });
  assert.deepEqual(nativeOnlyPoint.history,[]);assert.equal(nativeOnlyPoint.opened,true);
  await nativeOnly.waitForTimeout(850);
  assert.equal(await nativeOnly.locator('.mirally-companion').getAttribute('data-view'),'menu','native-only menu survives a geometry leave at its initial CSS position');
  await nativeOnly.mouse.move(nativeOnlyPoint.x,nativeOnlyPoint.y);
  await nativeOnly.mouse.move(300,50);await nativeOnly.waitForTimeout(550);
  assert.equal(await nativeOnly.locator('.mirally-companion').getAttribute('data-view'),'compact','native-only menu closes after real movement outside');
  await nativeOnly.close();
  findings.push('fresh native-only entry without pointer history survives geometry leave and closes on real movement away');
  await page.evaluate(()=>window.__mirallyCompanion.dock());
  for(const form of ['rail','badge','pet']){
    await page.evaluate(form=>{window.__mirallyCompanion.setForm(form);window.__mirallyCompanion.setView('detail');},form);
    await page.waitForTimeout(300);
    const immediateShape=await page.evaluate(()=>{
      window.__mirallyCompanion.setView('menu');
      return window.__posts.filter(item=>item.action==='shape').at(-1);
    });
    const [sx,sy,sw,sh]=immediateShape.rect;
    for(const [x,y,w,h]of immediateShape.parts)assert.ok(x>=sx-0.1&&y>=sy-0.1&&x+w<=sx+sw+0.1&&y+h<=sy+sh+0.1,`${form}: immediate detail-to-menu clip escapes surface ${JSON.stringify(immediateShape)}`);
  }
  findings.push('detail-to-menu rail positions are final on the first shape message, before animation frames');
  for(const width of [400,267,235]){
    await page.setViewportSize({width,height:454});
    await page.mouse.move(5,5);await page.evaluate(()=>window.__mirallyCompanion.dock());
    for(const form of ['rail','badge','pet'])for(const view of ['menu','quick','reply']){
      const shape=await page.evaluate(({form,view})=>{
        window.__mirallyCompanion.setForm(form);window.__mirallyCompanion.setView(view);window.__mirallyCompanion.syncShape();
        return window.__posts.filter(item=>item.action==='shape').at(-1);
      },{form,view});
      for(const [x,y,w,h]of [shape.rect,...shape.parts]){
        assert.ok(x>=-0.1&&y>=-0.1&&x+w<=width+0.1&&y+h<=454.1,`${width}/${form}/${view} clip outside viewport: ${[x,y,w,h]}`);
      }
      const gap=await page.evaluate(()=>{
        const rail=document.querySelector('.mirally-companion__rail').getBoundingClientRect(),panel=document.querySelector('.mirally-companion__panel').getBoundingClientRect();return rail.left-panel.right;
      });assert.ok(gap>=7.9,`${width}/${form}/${view} anchor overlaps panel`);
      if(view==='menu'){
        for(const action of ['quick','detail','workspace']){
          const button=page.locator(`.mirally-companion__menu-actions [data-action="${action}"]`);
          assert.equal(await button.isVisible(),true);
          await button.click({trial:true});
        }
        const formButton=page.locator('button[data-form="pet"]');
        assert.equal(await formButton.isVisible(),true);await formButton.click({trial:true});
        if(form==='pet')await page.locator('button[data-pet="fox"]').click({trial:true});
      }
    }
    findings.push(`${width}px viewport: all three forms and menu/quick/reply clip parts stay inside viewport without overlap`);
  }
  assert.deepEqual(errors,[]);
  const output=path.join(root,'.artifacts/companion-menu-verification');await fs.mkdir(output,{recursive:true});
  await fs.writeFile(path.join(output,'results.json'),JSON.stringify({findings,errors},null,2));
  console.log(JSON.stringify({findings,errors},null,2));
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
