import test from 'node:test';
import assert from 'node:assert/strict';
import {WeixinLifecycle} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-lifecycle.mjs';

test('lifecycle signals are ordered, best effort, and scoped to the current installation',async()=>{
  let state={installation_id:'install',revision:4,token:'token',base_url:'https://service',enabled:true,bound:true};
  const sent=[];const manager=new WeixinLifecycle({state:async()=>({...state}),api:()=>({notifyLifecycle:async args=>sent.push(args)})});
  manager.notify({...state},true);manager.notify({...state},false);await manager.queue;
  assert.deepEqual(sent.map(item=>item.active),[true,false]);assert.ok(sent.every(item=>item.signal instanceof AbortSignal));
  const old={...state};state={...state,installation_id:'new',revision:5,token:'other'};
  manager.notify(old,true);await manager.queue;assert.equal(sent.length,2);
});

test('rejected lifecycle signals do not stop later calls',async()=>{
  const state={installation_id:'install',revision:1,token:'token',base_url:'https://service',enabled:true,bound:true};
  let attempts=0;const manager=new WeixinLifecycle({state:async()=>state,api:()=>({notifyLifecycle:async()=>{if(++attempts===1)throw Error('unavailable');}})});
  manager.notify(state,true);manager.notify(state,false);await manager.queue;assert.equal(attempts,2);
});
