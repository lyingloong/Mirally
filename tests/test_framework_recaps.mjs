import test from 'node:test';
import assert from 'node:assert/strict';
import {refreshRecap} from '../kernel/dsh/packages/mirally/mirally-product/src/recaps.mjs';

test('recap uses bounded current generation history and writes only after guard', async()=>{
  const calls=[]; let input;
  const host={rpc:async(method,params)=>{calls.push([method,params]);if(method==='internal.recap.reserve')return {status:'reserved',id:'lease',generation:2};
    if(method==='guard.check')return {allow:true};
    if(method==='internal.recap.complete')return params.summary ? {changed:true,conversation:{metadata_version:2,recap:{text:params.summary}}}:{changed:false};},
    conversation:async()=>({generation:2,session_id:'native'}),
    history:async()=>({gaps:[],messages:[{generation:1,role:'user',text:'old private'},{generation:2,role:'user',text:'current'}]}),
    modelCall:async value=>{input=value;return {text:'{"title":"项目","summary":"进展"}'};}, broadcast:()=>{}};
  const result=await refreshRecap(host,'a',{manual:true,signal:new AbortController().signal});
  assert.equal(result.status,'ready');assert.match(input.prompt,/current/);assert.doesNotMatch(input.prompt,/old private/);
  assert.equal(calls.find(x=>x[0]==='internal.recap.reserve')[1].manual,true);
  assert.ok(calls.findIndex(x=>x[0]==='guard.check')<calls.findIndex(x=>x[0]==='internal.recap.complete'));
});

test('disabled automatic recap makes no model request',async()=>{
  const host={rpc:async()=>({status:'disabled'}),modelCall:()=>{throw Error('must not call');}};
  assert.deepEqual(await refreshRecap(host,'a'),{status:'disabled'});
});

test('changed authorization suppresses a late model result',async()=>{
  let calls=0;const writes=[];
  const host={rpc:async(method,params)=>{if(method==='internal.recap.reserve')return {status:'reserved',id:'lease',generation:1};
    if(method==='guard.check')return {allow:true};writes.push(params);return {changed:false};},
    conversation:async()=>({generation:1,session_id:'native'}),history:async()=>({messages:[{generation:1,role:'user',text:'x'}]}),
    modelCall:async()=>({text:'{"title":"x","summary":"y"}'}),broadcast:()=>{}};
  await assert.rejects(refreshRecap(host,'a',{manual:true,validate:async()=>{if(++calls===2)throw Error('revoked');}}),/revoked/);
  assert.equal(writes.length,1);assert.equal(writes[0].summary,undefined);
});
