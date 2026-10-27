import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {AsyncLocalStorage} from 'node:async_hooks';
import {installBrowserCapabilities} from '../kernel/dsh/packages/mirally/mirally-product/src/browser-capabilities.mjs';

const require=createRequire(new URL('../kernel/dsh/packages/mirally/mirally-product/package.json',import.meta.url));
const {chromium}=require('playwright-core');

test('isolated browser observes and interacts with a local page after individual approvals',async()=>{
  const server=createServer((_req,res)=>res.end('<title>Fixture</title><input id="name"><button onclick="document.querySelector(\'#result\').textContent=document.querySelector(\'#name\').value">Go</button><p id="result"></p>'));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const tools=new Map(), approvals=[];
  let consumed='cmd',origin='desktop',revoked=false,browser;
  const host={ctx:{tools:{register:tool=>tools.set(tool.name,tool)}},nativeConversations:new Map([['s',{}]]),activeCommands:new Map([['s','queued-other']]),consumedCommandId:()=>consumed,commandPolicy:()=>({origin}),checkCommandScope:async()=>{if(revoked) throw Error('revoked');},authorizations:new AsyncLocalStorage(),guard:async()=>{},actionGate:{request:async item=>{approvals.push(item);return true;}}};
  const pack=installBrowserCapabilities(host,{launch:async()=>browser=await chromium.launch({channel:'msedge',headless:true})});
  const call=(name,args)=>tools.get(name).execute(args,{agent:{id:'s'},signal:new AbortController().signal});
  try{
    await call('mirally_browser_open',{url:`http://127.0.0.1:${server.address().port}/`});
    assert.equal(approvals[0].commandId,'cmd');
    await call('mirally_browser_fill',{selector:'#name',value:'测试内容'});
    assert.match(approvals.at(-1).detail,/测试内容/);
    await call('mirally_browser_click',{selector:'button'});
    const result=await call('mirally_browser_read',{});
    assert.match(result.text,/测试内容/);assert.equal(result.untrusted_content,true);
    assert.ok(result.elements.some(item=>item.tag==='input' && item.selector));
    assert.equal(approvals.length,4);
    host.actionGate.request=async()=>false;
    await assert.rejects(call('mirally_browser_click',{selector:'button'}),/未获批准/);
    host.actionGate.request=async()=>true;
    await assert.rejects(call('mirally_browser_open',{url:'file:///etc/passwd'}),/仅支持/);
    origin='weixin'; await call('mirally_browser_read',{});
    host.actionGate.request=async()=>{revoked=true;return true;};
    await assert.rejects(call('mirally_browser_click',{selector:'button'}),/revoked/); revoked=false;
    origin='mobile';await assert.rejects(call('mirally_browser_click',{selector:'button'}),/消息来源/);origin='desktop';
    host.actionGate.request=async()=>{consumed='next';return true;};
    await assert.rejects(call('mirally_browser_click',{selector:'button'}),/消息来源/);consumed='cmd';
    host.actionGate.request=async()=>{await browser.contexts()[0].pages()[0].evaluate(()=>document.querySelector('#name').value='changed');return true;};
    await assert.rejects(call('mirally_browser_click',{selector:'button'}),/网页已变化/);
    const controller=new AbortController();
    host.actionGate.request=async({signal})=>{controller.abort();signal.throwIfAborted();return true;};
    await assert.rejects(tools.get('mirally_browser_click').execute({selector:'button'},{agent:{id:'s'},signal:controller.signal}),{name:'AbortError'});
  }finally{await pack.dispose();await new Promise(resolve=>server.close(resolve));}
});
