/** Fresh-home Mirally identity and desktop preference acceptance on a real Host. */
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(join(root,'integrations/openclaw/package.json'));
const {chromium}=require(process.env.MIRALLY_PLAYWRIGHT??'playwright-core');
const runtime=JSON.parse(await readFile(process.argv[2],'utf8'));assert.equal(runtime.mode,'test');
const output=resolve(process.argv[3]??join(root,'.artifacts/branding-close'));await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const errors=[];
const oldBrand=/OpenClaw|Clawd(?:bot)?|龙虾|lobster/i;
async function ready(page){await page.waitForFunction(()=>document.querySelector('openclaw-app')?.context?.gateway?.snapshot?.phase==='connected');}
async function go(page,path){await page.goto(runtime.url+path);await ready(page);}
async function rpc(page,method,params={}){return page.evaluate(({method,params})=>window.__mirallyLegacyHost.rpc(method,params),{method,params});}
async function noOldBrand(page){
  assert.ok(!oldBrand.test(await page.locator('body').innerText()),'visible product text is Mirally');
  const links=await page.locator('a[href]').evaluateAll(nodes=>nodes.filter(node=>node.getClientRects().length).map(node=>node.href));
  assert.ok(!links.some(link=>/openclaw\.(?:ai|dev)|github\.com\/openclaw/i.test(link)),'visible help links are product-owned');
  assert.ok(!oldBrand.test(await page.title()),'document title is product-owned');
}
try{
  // This must pass even when no product script can run.
  const plain=await browser.newContext({javaScriptEnabled:false});
  const staticPage=await plain.newPage();await staticPage.goto(runtime.url+'/new');
  assert.equal(await staticPage.title(),'Mirally');
  assert.ok(!oldBrand.test(await staticPage.locator('#openclaw-mount-fallback').textContent()));
  const manifest=await (await plain.request.get(runtime.url+'/manifest.webmanifest')).json();
  assert.equal(manifest.name,'Mirally');assert.equal(manifest.short_name,'Mirally');assert.ok(!oldBrand.test(manifest.description));
  const icon=await (await plain.request.get(runtime.url+'/favicon.svg')).text();
  assert.ok(icon.includes('data-mirally-mascot="mark"'));assert.ok(!oldBrand.test(icon));
  for(const path of ['/favicon-32.png','/apple-touch-icon.png','/favicon.ico'])assert.equal((await plain.request.get(runtime.url+path)).status(),200);
  await plain.close();

  const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(15000);page.on('pageerror',error=>errors.push(error.message));
  await page.goto(runtime.url+'/new#token='+runtime.token);await ready(page);
  assert.equal((await rpc(page,'settings.get')).model.model_id,'','requires an unconfigured isolated home');
  const setup=page.locator('.agent-chat__welcome--setup');await setup.waitFor();
  await setup.locator('openclaw-mascot[data-mirally-mascot=companion-m]').waitFor();
  assert.equal(await setup.locator('canvas').count(),0,'the no-model illustration no longer paints the lobster Canvas');
  await noOldBrand(page);await page.screenshot({path:join(output,'unconfigured.png')});
  await setup.getByRole('button',{name:/连接模型|Connect a model/}).click();
  await page.getByTestId('mirally-settings-model-id').waitFor();
  await go(page,'/settings/general');
  const close=page.getByTestId('mirally-settings-close-behavior');await close.waitFor();
  await page.waitForFunction(()=>document.querySelector('[data-testid=mirally-settings-general]')?.getAttribute('aria-busy')==='false');
  assert.equal(await close.inputValue(),'tray');
  await close.selectOption('exit');await page.getByTestId('mirally-settings-notice').filter({hasText:'关闭方式已保存'}).waitFor();
  await page.reload();await ready(page);await close.waitFor();
  await page.waitForFunction(()=>document.querySelector('[data-testid=mirally-settings-general]')?.getAttribute('aria-busy')==='false');
  assert.equal(await close.inputValue(),'exit');
  const invalid=await page.evaluate(async()=>{const r=await fetch('/api/ui-preferences',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:'mirally.window.closeBehavior',value:'invalid'})});return {status:r.status,body:await r.json()};});
  assert.equal(invalid.status,400);assert.ok(invalid.body.error);
  await close.selectOption('tray');await page.getByTestId('mirally-settings-notice').filter({hasText:'关闭方式已保存'}).waitFor();
  await noOldBrand(page);await page.screenshot({path:join(output,'close-preferences.png')});
  await go(page,'/settings/appearance');await page.locator('wa-radio[value=dark]').click();
  await page.waitForFunction(()=>document.querySelector('openclaw-app').context.theme.resolvedMode==='dark');
  await noOldBrand(page);await page.screenshot({path:join(output,'appearance-dark.png')});
  await page.locator('wa-radio[value=light]').click();
  await page.waitForFunction(()=>document.querySelector('openclaw-app').context.theme.resolvedMode==='light');
  const activeIcons=await page.locator('link[rel~=icon]').evaluateAll(nodes=>nodes.map(node=>node.href));
  for(const href of activeIcons){assert.ok(!href.startsWith('data:')||!oldBrand.test(decodeURIComponent(href)));if(href.endsWith('.svg'))assert.ok(!oldBrand.test(await (await page.request.get(href)).text()));}
  assert.deepEqual(errors,[]);
  await page.close();

  // Missing credentials exercises the actual auth failure/reconnect surface.
  const guest=await browser.newContext();const login=await guest.newPage();
  await login.goto(runtime.url+'/new#token=invalid-branding-acceptance');
  await login.locator('.login-gate [data-mirally-connection]').waitFor({timeout:20000});
  await noOldBrand(login);await login.screenshot({path:join(output,'connection-error.png')});
  await guest.close();

  // Fail the real app modules: the initial inline fallback must still be Mirally.
  const broken=await browser.newContext();const fallback=await broken.newPage();
  await fallback.route(url=>url.pathname.startsWith('/assets/')&&url.pathname.endsWith('.js'),route=>route.abort('failed'));
  // Keep the original failed document visible while its ordinary recovery fetch
  // also fails. Do not mutate the app, fallback markup or its real timeout.
  await fallback.route(url=>url.searchParams.has('openclaw_mount_recovery'),route=>route.abort('failed'));
  await fallback.goto(runtime.url+'/new');
  await fallback.locator('#openclaw-mount-fallback').waitFor({state:'visible',timeout:20000});
  assert.ok(!oldBrand.test(await fallback.locator('#openclaw-mount-fallback').innerText()));
  assert.equal(await fallback.title(),'Mirally');
  await fallback.screenshot({path:join(output,'startup-fallback.png')});
  await broken.close();
  console.log('PASS Mirally branding: no-JS title/manifest/icons, fresh unconfigured M illustration and real model setup link, desktop close preference persistence/validation, appearance and runtime icons, actual auth failure, failed-module startup fallback');
}finally{await browser.close();}
