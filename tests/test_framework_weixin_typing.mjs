import test from 'node:test';
import assert from 'node:assert/strict';
import {WeixinTyping} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-typing.mjs';

const event={installation_id:'a',revision:1,conversation_id:'c',command_id:'cmd',context_token:'ctx'};
const wait=()=>new Promise(resolve=>setImmediate(resolve));
async function until(fn) {for(let i=0;i<30;i++){if(fn())return;await wait();}throw new Error('typing state did not settle');}
function fixture() {
  const calls=[];let enabled=true,authorized=true;
  const api={getTypingTicket:async()=>({typing_ticket:'ticket'}),sendTyping:async value=>calls.push(value.active)};
  const channel={product:{rpc:async()=>({weixin_typing_enabled:enabled})},authorize:async()=>{
    if(!authorized)throw Error('revoked');return {token:'secret',peer_id:'user',base_url:'https://ilinkai.weixin.qq.com'};
  },api:()=>api};
  return {calls,api,channel,set enabled(value){enabled=value;},set authorized(value){authorized=value;}};
}
test('typing starts for an admitted live request and stops at completion',async()=>{
  const f=fixture(), typing=new WeixinTyping(f.channel,{intervalMs:100000}),stop=typing.begin(event);
  await until(()=>f.calls.length===1);assert.deepEqual(f.calls,[true]);
  stop();await until(()=>f.calls.length===2);assert.deepEqual(f.calls,[true,false]);typing.stop();
});
test('disabled typing or revoked route sends nothing',async()=>{
  const f=fixture();f.enabled=false;const typing=new WeixinTyping(f.channel),stop=typing.begin(event);
  await until(()=>typing.entries.size===0);stop();assert.deepEqual(f.calls,[]);
  f.enabled=true;f.authorized=false;typing.begin(event);await until(()=>typing.entries.size===0);assert.deepEqual(f.calls,[]);typing.stop();
});
test('completion during ticket lookup never emits a late typing indicator',async()=>{
  const f=fixture();let resolve;f.api.getTypingTicket=()=>new Promise(done=>resolve=done);
  const typing=new WeixinTyping(f.channel),stop=typing.begin(event);await until(()=>typeof resolve==='function');
  stop();resolve({typing_ticket:'ticket'});await wait();await wait();assert.deepEqual(f.calls,[]);typing.stop();
});
test('overlapping turns do not clear another live turn indicator',async()=>{
  const f=fixture(),typing=new WeixinTyping(f.channel,{intervalMs:100000});
  const a=typing.begin(event),b=typing.begin({...event,command_id:'other'});
  await until(()=>f.calls.length===2);a();await wait();assert.deepEqual(f.calls,[true,true]);
  b();await until(()=>f.calls.length===3);assert.deepEqual(f.calls,[true,true,false]);typing.stop();
});
