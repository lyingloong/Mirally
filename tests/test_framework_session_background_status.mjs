import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

test('session background status counts only running owned jobs and refreshes on change',()=>{
 let shell=[{status:'running'}], native=[{owner:'native-a',status:'running'},{owner:undefined,status:'running'},{owner:'other',status:'running'},{owner:'native-a',status:'settled'}];
 const events=[];const host={shellJobs:{list:id=>id==='native-a'?shell:[]},ctx:{get:()=>({list:()=>native})},nativeConversations:new Map([['native-a',{conversation_id:'chat-a'}],['native-b',{conversation_id:'chat-b'}]]),broadcast:(type,data)=>events.push({type,data})};
 host.activeBackgroundJobs=MirallyProduct.prototype.activeBackgroundJobs;
 host.refreshBackgroundJobStates=MirallyProduct.prototype.refreshBackgroundJobStates;
 assert.equal(host.activeBackgroundJobs('native-a'),2);assert.equal(host.activeBackgroundJobs('native-b'),0);
 host.refreshBackgroundJobStates();assert.deepEqual(events,[{type:'session-metadata',data:{session_id:'chat-a'}}]);
 host.refreshBackgroundJobStates();assert.equal(events.length,1);
 shell=[];native=[];host.refreshBackgroundJobStates();assert.equal(events.length,2);
 host.ctx.get=()=>({list:()=>{throw Error('unavailable')}});assert.equal(host.activeBackgroundJobs('native-a'),null);host.refreshBackgroundJobStates();assert.equal(events.length,2);
});
