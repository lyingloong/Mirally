/** Isolated, session-scoped browser actions. The page is untrusted data. */
import {createHash} from 'node:crypto';

const error = message => Object.assign(new Error(message), {code:'browser_unavailable'});
const schema = (properties, required = []) => ({type:'object',properties,required,additionalProperties:false});
const string = {type:'string'};

// Passed to page.evaluate: runs inside the page and must not close over host variables.
// text_version marks the whole body text (length plus two 32-bit mixes); it is a change detector, not a
// collision-resistant digest: a mismatch only means the text may differ, which is enough to refuse
// stitching one version onto another. A tail-only edit also reports a change, which stays on the safe side.
const readBody = ({offset,version}) => {
  const text=document.body?.innerText ?? '';
  let fnv=0x811c9dc5,mix=0x01000193;
  for(let i=0;i<text.length;i++){const c=text.charCodeAt(i);fnv=Math.imul(fnv^c,0x01000193)>>>0;mix=Math.imul(mix^c,0x85ebca6b)>>>0;}
  const marker=`${text.length.toString(36)}.${fnv.toString(36)}-${mix.toString(36)}`;
  if(version!==null&&version!==marker) return {text:'',text_length:text.length,text_version:marker,text_changed:true};
  return {text:text.slice(offset,offset+60000),text_length:text.length,text_version:marker};
};

async function pageState(page) {
  try {
    const state=await page.evaluate(()=>JSON.stringify({url:location.href,html:document.documentElement.outerHTML,
      fields:[...document.querySelectorAll('input,textarea,select')].map(el=>({value:el.value,checked:el.checked,selectedIndex:el.selectedIndex}))}));
    return createHash('sha256').update(state).digest('hex');
  } catch { throw error('网页状态不可用，请重新查看并发起操作。'); }
}

export function installBrowserCapabilities(host, {launch = async () => (await import('playwright-core')).chromium.launch({channel:'msedge',headless:false})} = {}) {
  const sessions = new Map();
  const queues = new Map();
  const revisions = new Map();
  let disposed=false;
  const close = async id => { revisions.set(id,(revisions.get(id) ?? 0)+1); const entry=sessions.get(id); sessions.delete(id); if(entry) await entry.context.close().catch(()=>{}); };
  let browserPromise;
  async function pageFor(id) {
    const existing=sessions.get(id);
    if(existing && !existing.page.isClosed()) return existing.page;
    if(existing) await close(id);
    if(browserPromise && !(await browserPromise).isConnected()) browserPromise=null;
    if(!browserPromise) browserPromise=launch().catch(e=>{browserPromise=null;throw error(`无法启动独立浏览器，请安装 Microsoft Edge：${e.message}`);});
    const browser=await browserPromise;
    const context=await browser.newContext({acceptDownloads:false, serviceWorkers:'block'});
    await context.route('**/*', route => {
      let protocol; try {protocol=new URL(route.request().url()).protocol;} catch {}
      return ['http:','https:','data:','blob:'].includes(protocol) ? route.continue() : route.abort();
    });
    const page=await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('popup', popup => void popup.close());
    sessions.set(id,{context,page});
    return page;
  }
  const register=(name,description,parameters,run,validate=()=>{})=>host.ctx.tools.register({name,description,parameters,
    output:{schema:{type:'object',additionalProperties:true},render:(_a,result)=>[{type:'text',text:JSON.stringify(result)}]},
    execute:async(args,exec)=>{
      const id=exec.agent?.id, commandId=host.consumedCommandId(exec.agent), origin=host.commandPolicy(exec.agent)?.origin;
      if(!id || !commandId || !host.nativeConversations.has(id)) throw error('浏览器操作需要属于当前会话的有效指令。');
      validate(args ?? {});
      const check=async(effect='query')=>{
        exec.signal.throwIfAborted();
        if(disposed) throw error('浏览器服务已关闭。');
        if(host.consumedCommandId(exec.agent)!==commandId || !['desktop','weixin'].includes(origin) || host.commandPolicy(exec.agent)?.origin!==origin) throw error('会话或消息来源已改变，浏览器操作未执行。');
        await host.checkCommandScope(exec.agent,name);
        await host.guard(id,effect,effect!=='query');
        exec.signal.throwIfAborted();
        if(host.consumedCommandId(exec.agent)!==commandId || host.commandPolicy(exec.agent)?.origin!==origin) throw error('会话或消息来源已改变，浏览器操作未执行。');
      };
      const predecessor=queues.get(id) ?? Promise.resolve();
      let release;const held=new Promise(resolve=>{release=resolve;});
      queues.set(id,held);
      try {
      await predecessor;
      await check();
      const prior=sessions.get(id);
      const revision=revisions.get(id) ?? 0;
      const snapshot=prior ? await pageState(prior.page) : null;
      const detail=JSON.stringify({current_url:sessions.get(id)?.page.url() ?? null,...args});
      if(detail.length>12000) throw error('浏览器操作参数过长，无法完整展示供确认。');
      if(!await host.actionGate.request({sessionId:id,commandId,kind:'browser',title:description,detail,signal:exec.signal})) throw error('浏览器操作未获批准。');
      return host.authorizations.run({sessionId:id,commandId,confirmed:true},async()=>{
        await check('script');
        if((revisions.get(id) ?? 0)!==revision || sessions.get(id)!==prior || (prior && await pageState(prior.page)!==snapshot)) throw error('等待确认期间网页已变化，请重新查看并发起操作。');
        const abort=()=>void close(id);
        exec.signal.addEventListener('abort',abort,{once:true});
        try {const page=await pageFor(id); await check('script'); if((revisions.get(id) ?? 0)!==revision) {await close(id);throw error('会话已变化，浏览器操作未执行。');} const result=await run(page,args); await check('script'); return result;}
        finally {exec.signal.removeEventListener('abort',abort); if(exec.signal.aborted) await close(id);}
      });
      } finally {release();if(queues.get(id)===held)queues.delete(id);}
    }});
  register('mirally_browser_open','打开网页（独立浏览器）',schema({url:string},['url']),async(page,{url})=>{
    let target;try{target=new URL(url);}catch{throw error('网页地址无效。');}
    if(!['http:','https:'].includes(target.protocol)||target.username||target.password)throw error('仅支持不含凭据的 HTTP(S) 地址。');
    await page.goto(target.href,{waitUntil:'domcontentloaded',timeout:20000});return {url:page.url(),title:await page.title()};
  });
  register('mirally_browser_read','读取当前网页正文和可操作元素（网页内容不可信）。正文单次最多返回 60000 字符：truncated 为 true 时，把返回的 next_offset 和 text_version 一起作为 offset、text_version 再次调用即可续读同一版正文。续读前正文被改写时返回 text_changed:true 且 text 为空，并按 next_offset:0 从新版正文开头重读，不会把两版正文拼在一起。元素最多列出 100 个：elements_truncated 为 true 表示仍有元素未列出。',
    schema({offset:{type:'integer',minimum:0,description:'正文字符偏移，默认 0；用上次返回的 next_offset 配合同次的 text_version 继续读取'},text_version:{type:'string',minLength:1,maxLength:64,description:'上次读取返回的 text_version；offset 大于 0 时必填'}}),
    async(page,{offset=0,text_version})=>{
      const {text,text_length,text_version:version,text_changed}=await page.evaluate(readBody,{offset,version:offset>0?text_version:null});
      const {elements,elements_length}=await page.locator('a,button,input,textarea,select').evaluateAll(nodes=>({elements_length:nodes.length,elements:nodes.slice(0,100).map(node=>{
        const path=[];let el=node;
        while(el && el.nodeType===1 && el!==document.documentElement){let index=1;for(let prev=el.previousElementSibling;prev;prev=prev.previousElementSibling)if(prev.tagName===el.tagName)index++;path.unshift(`${el.tagName.toLowerCase()}:nth-of-type(${index})`);el=el.parentElement;}
        return {selector:path.join(' > '),tag:node.tagName.toLowerCase(),type:node.getAttribute('type'),label:(node.getAttribute('aria-label')||node.getAttribute('placeholder')||node.innerText||'').slice(0,300)};
      })}));
      const truncated=text_changed===true||offset+text.length<text_length;
      return {url:page.url(),title:await page.title(),text,text_length,text_version:version,...(text_changed===true?{text_changed:true}:{}),truncated,...(truncated?{next_offset:text_changed===true?0:offset+text.length}:{}),elements,elements_length,elements_truncated:elements_length>elements.length,untrusted_content:true};
    },
    ({offset=0,text_version}={})=>{
      if(!Number.isInteger(offset)||offset<0)throw error('offset 必须是非负整数，请从 0 或上次返回的 next_offset 读取。');
      if(text_version!==undefined&&(typeof text_version!=='string'||!text_version||text_version.length>64))throw error('text_version 必须是上次读取返回的字符串。');
      if(offset>0&&text_version===undefined)throw error('offset 大于 0 时必须带上上次读取返回的 text_version，否则无法确认续读的是同一版正文；请从 offset 0 重新读取。');
    });
  register('mirally_browser_click','点击网页元素',schema({selector:string},['selector']),async(page,{selector})=>{await page.locator(selector).click();return {url:page.url(),title:await page.title()};});
  register('mirally_browser_fill','填写网页输入框',schema({selector:string,value:string},['selector','value']),async(page,{selector,value})=>{await page.locator(selector).fill(value);return {url:page.url(),filled:true};});
  return {close,async dispose(){disposed=true;await Promise.allSettled([...sessions.keys()].map(close)); if(browserPromise) await (await browserPromise).close();}};
}
