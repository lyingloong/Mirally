import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AsyncLocalStorage} from 'node:async_hooks';
import {installBrowserCapabilities} from '../kernel/dsh/packages/mirally/mirally-product/src/browser-capabilities.mjs';

test('browser starts lazily, respects scope and discards an in-flight startup after session close',async()=>{
  const tools=new Map();let origin='desktop',launches=0,started,ready,closed=0,navigated=0;
  const launching=new Promise(resolve=>{ready=resolve;});
  const start=new Promise(resolve=>{started=resolve;});
  const page={isClosed:()=>false,setDefaultTimeout(){},on(){},goto:async()=>{navigated++;}};
  const context={route:async()=>{},newPage:async()=>page,close:async()=>{closed++;}};
  const browser={isConnected:()=>true,newContext:async()=>context,close:async()=>{}};
  const host={ctx:{tools:{register:tool=>tools.set(tool.name,tool)}},nativeConversations:new Map([['s',{}]]),consumedCommandId:()=> 'c',commandPolicy:()=>({origin}),checkCommandScope:async()=>{},authorizations:new AsyncLocalStorage(),guard:async()=>{},actionGate:{request:async()=>true}};
  const pack=installBrowserCapabilities(host,{launch:async()=>{launches++;started();await launching;return browser;}});
  const call=()=>tools.get('mirally_browser_open').execute({url:'http://127.0.0.1/'},{agent:{id:'s'},signal:new AbortController().signal});
  origin='mobile';await assert.rejects(call(),/消息来源/);assert.equal(launches,0);origin='desktop';
  const pending=call();await start;await pack.close('s');ready();
  await assert.rejects(pending,/会话已变化/);assert.equal(navigated,0);assert.equal(closed,1);
  await pack.dispose();await assert.rejects(call(),/已关闭/);
});

test('browser launch failure is reported only when requested and does not break registration',async()=>{
  const tools=new Map(),host={ctx:{tools:{register:tool=>tools.set(tool.name,tool)}},nativeConversations:new Map([['s',{}]]),consumedCommandId:()=> 'c',commandPolicy:()=>({origin:'desktop'}),checkCommandScope:async()=>{},authorizations:new AsyncLocalStorage(),guard:async()=>{},actionGate:{request:async()=>true}};
  const pack=installBrowserCapabilities(host,{launch:async()=>{throw new Error('not installed');}});
  assert.ok(tools.has('mirally_browser_read'));
  await assert.rejects(tools.get('mirally_browser_open').execute({url:'https://example.test/'},{agent:{id:'s'},signal:new AbortController().signal}),/安装 Microsoft Edge/);
  await pack.dispose();
});
