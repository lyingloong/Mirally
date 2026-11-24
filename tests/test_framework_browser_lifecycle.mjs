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

// The fake page evaluates the in-page functions against a temporary global document, so read slicing runs for real.
function readFixture({text,elementCount}) {
  const calls=[],nodes=Array.from({length:elementCount},(_,i)=>({nodeType:1,tagName:'BUTTON',previousElementSibling:null,parentElement:null,getAttribute:()=>null,innerText:`操作${i}`}));
  let body=text;
  const withDocument=async run=>{globalThis.document={body:{innerText:body},documentElement:{outerHTML:'<html>RAW-HTML-MARKER</html>'},querySelectorAll:()=>[]};globalThis.location={href:'http://fixture.test/page'};try{return await run();}finally{delete globalThis.document;delete globalThis.location;}};
  const page={isClosed:()=>false,setDefaultTimeout(){},on(){},url:()=>'http://fixture.test/page',title:async()=>'Fixture',calls,
    evaluate:(fn,args)=>{calls.push('evaluate');return withDocument(()=>fn(args));},
    locator:()=>({evaluateAll:fn=>{calls.push('evaluateAll');return withDocument(()=>fn(nodes));}})};
  const tools=new Map(),approvals=[];
  const host={ctx:{tools:{register:tool=>tools.set(tool.name,tool)}},nativeConversations:new Map([['s',{}]]),consumedCommandId:()=>'c',commandPolicy:()=>({origin:'desktop'}),checkCommandScope:async()=>{},authorizations:new AsyncLocalStorage(),guard:async()=>{},actionGate:{request:async item=>{approvals.push(item);return true;}}};
  const browser={isConnected:()=>true,newContext:async()=>({route:async()=>{},newPage:async()=>page,close:async()=>{}}),close:async()=>{}};
  const pack=installBrowserCapabilities(host,{launch:async()=>browser});
  const read=args=>tools.get('mirally_browser_read').execute(args??{},{agent:{id:'s'},signal:new AbortController().signal});
  return {page,approvals,read,setBody:value=>{body=value;},pack};
}

test('browser read paginates long body text by offset and marks text and element truncation honestly',async()=>{
  const text=Array.from({length:130001},(_,i)=>String.fromCharCode(97+i%26)).join('');
  const {page,approvals,read,pack}=readFixture({text,elementCount:150});
  try {
    const first=await read();
    assert.equal(first.url,'http://fixture.test/page');assert.equal(first.title,'Fixture');assert.equal(first.untrusted_content,true);
    assert.ok(!JSON.stringify(first).includes('RAW-HTML-MARKER'));
    assert.equal(first.text.length,60000);assert.equal(first.text_length,130001);
    assert.equal(first.truncated,true);assert.equal(first.next_offset,60000);assert.equal(typeof first.text_version,'string');
    assert.equal(first.elements.length,100);assert.equal(first.elements_length,150);assert.equal(first.elements_truncated,true);
    const second=await read({offset:first.next_offset,text_version:first.text_version});
    assert.equal(second.text.length,60000);assert.equal(second.text_length,130001);assert.equal(second.text_version,first.text_version);
    assert.equal(second.truncated,true);assert.equal(second.next_offset,120000);
    const third=await read({offset:second.next_offset,text_version:second.text_version});
    assert.equal(third.text.length,10001);assert.equal(third.text_length,130001);
    assert.equal(third.truncated,false);assert.ok(!('next_offset'in third));
    assert.equal(first.text+second.text+third.text,text);
    const invalid=[[{offset:-1},/非负整数/],[{offset:1.5},/非负整数/],[{offset:'10'},/非负整数/],[{offset:null},/非负整数/],
      [{offset:60000},/text_version/],[{offset:60000,text_version:1},/text_version/],[{offset:10,text_version:''},/text_version/],[{text_version:'x'.repeat(65)},/text_version/]];
    for(const [args,message] of invalid) {
      const reads=page.calls.length,asks=approvals.length;
      await assert.rejects(read(args),message);
      assert.equal(page.calls.length,reads,'invalid pagination arguments must not touch the page');
      assert.equal(approvals.length,asks,'invalid pagination arguments must not request approval');
    }
  } finally {await pack.dispose();}
});

test('browser read reports short pages as complete and past-the-end offsets as empty',async()=>{
  const {read,pack}=readFixture({text:'短文正文',elementCount:2});
  try {
    const result=await read();
    assert.equal(result.text,'短文正文');assert.equal(result.text_length,4);
    assert.equal(result.truncated,false);assert.ok(!('next_offset'in result));
    assert.equal(result.elements.length,2);assert.equal(result.elements_length,2);assert.equal(result.elements_truncated,false);
    assert.equal(result.untrusted_content,true);
    const past=await read({offset:4,text_version:result.text_version});
    assert.equal(past.text,'');assert.equal(past.text_length,4);assert.equal(past.text_version,result.text_version);
    assert.equal(past.truncated,false);assert.ok(!('next_offset'in past));assert.ok(!('text_changed'in past));
  } finally {await pack.dispose();}
});

test('browser read does not stitch a rewritten page onto the previous page and restarts from the start',async()=>{
  const text=Array.from({length:130001},(_,i)=>String.fromCharCode(97+i%26)).join('');
  const rewritten=Array.from({length:130001},(_,i)=>String.fromCharCode(122-i%26)).join('');
  assert.equal(rewritten.length,text.length,'same length keeps the check honest about content, not just length');
  const {read,setBody,pack}=readFixture({text,elementCount:3});
  try {
    const first=await read();
    assert.equal(first.text,text.slice(0,60000));assert.equal(first.truncated,true);assert.equal(first.next_offset,60000);
    setBody(rewritten);
    const stale=await read({offset:first.next_offset,text_version:first.text_version});
    assert.equal(stale.text,'');assert.equal(stale.text_changed,true);
    assert.equal(stale.text_length,rewritten.length);assert.notEqual(stale.text_version,first.text_version);
    assert.equal(stale.truncated,true);assert.equal(stale.next_offset,0);
    assert.equal(stale.elements.length,3);assert.equal(stale.elements_truncated,false);
    const restart=await read({offset:stale.next_offset,text_version:stale.text_version});
    assert.ok(!('text_changed'in restart));assert.equal(restart.text,rewritten.slice(0,60000));
    assert.equal(restart.truncated,true);assert.equal(restart.next_offset,60000);assert.equal(restart.text_version,stale.text_version);
    const second=await read({offset:restart.next_offset,text_version:restart.text_version});
    assert.ok(!('text_changed'in second));assert.equal(second.text,rewritten.slice(60000,120000));
    assert.equal(second.truncated,true);assert.equal(second.next_offset,120000);
    const third=await read({offset:second.next_offset,text_version:second.text_version});
    assert.ok(!('text_changed'in third));assert.equal(third.text,rewritten.slice(120000));
    assert.equal(third.truncated,false);assert.ok(!('next_offset'in third));
    assert.equal(restart.text+second.text+third.text,rewritten);
    assert.ok(!(restart.text+second.text+third.text).includes(text.slice(0,300)),'the discarded version must not leak into the continuation');
  } finally {await pack.dispose();}
});
