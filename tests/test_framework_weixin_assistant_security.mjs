import test from 'node:test';
import assert from 'node:assert/strict';
import {ActionGate} from '../kernel/dsh/packages/mirally/mirally-product/src/action-gate.mjs';
import {installNativeInteractions} from '../kernel/dsh/packages/mirally/mirally-product/src/native-interactions.mjs';
import {WeixinWorks} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-work.mjs';
import {WeixinCalendar} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-calendar.mjs';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
const policy={origin:'weixin',installation_id:'install',revision:7,conversation_id:'assistant',
  target_conversation_id:'target',source_command_id:'source-command',command_id:'target-command'};

test('a delegated target approval is answered from the assistant route only while the target scope remains valid',async()=>{
  const agent={id:'target-native'}, delivered=[], command='target-command';let enabled=true;
  const host={handles:new Map([['target-native',{agent}]]),commandPolicies:new Map([[command,policy]]),
    conversationForSession:()=>({conversation_id:'target'}),consumedCommandId:()=>command,
    checkCommandScope:async()=>{if(!enabled)throw Error('revoked');},guard:async()=>{},broadcast() {}};
  host.actionGate=new ActionGate(host);
  host.weixin={authorize:async scope=>{if(scope.installation_id!=='install'||scope.revision!==7||scope.conversation_id!=='assistant')throw Error('route revoked');},
    requestActionApproval:async(value,scope,code)=>{delivered.push({value,scope,code});host.actionGate.remoteDelivered(value.id,scope);}};
  const waiting=host.actionGate.request({sessionId:'target-native',commandId:command,kind:'shell',title:'执行实验',detail:'完整命令'});
  await flush();assert.equal(delivered.length,1);
  const code=delivered[0].code;
  await assert.rejects(host.actionGate.decideRemote({...policy,conversation_id:'target'},code,true));
  await assert.rejects(host.actionGate.decideRemote({...policy,revision:8},code,true));
  enabled=false;await assert.rejects(host.actionGate.decideRemote(policy,code,true),/revoked/);
  enabled=true;await host.actionGate.decideRemote(policy,code,true);assert.equal(await waiting,true);
  await assert.rejects(host.actionGate.decideRemote(policy,code,true));host.actionGate.dispose();
});

test('a delegated target question is delivered to the assistant route and released in the target session',async()=>{
  const hooks=new Map(),agent={id:'target-native'},conversation={conversation_id:'target',session_id:'target-native',generation:1},sent=[];
  let enabled=true;
  const host={ctx:{on:(name,listener)=>{hooks.set(name,listener);return()=>hooks.delete(name);},get:()=>undefined},
    handles:new Map([['target-native',{agent}]]),commandPolicies:new Map([['target-command',policy]]),
    nativeRuntime:{owner:()=>({rootSessionId:'target-native',commandId:'target-command',conversation})},
    checkCommandScope:async()=>{if(!enabled)throw Error('revoked');},guard:async()=>{},broadcast(){},
    conversation:async id=>{assert.equal(id,'target');return conversation;},
    weixin:{authorize:async scope=>{assert.equal(scope.conversation_id,'assistant');},requestUserQuestion:async(value,scope,_code,index)=>sent.push({value,scope,index})}};
  const interactions=installNativeInteractions(host);
  const pending=hooks.get('user-questions/request')({agent,questions:[{id:'confirm',question:'选择方案',options:[{label:'甲'},{label:'乙'}]}]},()=>{});
  await flush();assert.equal(sent.length,1);assert.equal(sent[0].value.conversation_id,'target');
  interactions.remoteDelivered(sent[0].value.id,policy,0);
  assert.equal(await interactions.answerActiveRemote({...policy,revision:8},'2'),null);
  enabled=false;await assert.rejects(interactions.answerActiveRemote(policy,'2'),/revoked/);
  enabled=true;assert.deepEqual(await interactions.answerActiveRemote(policy,'2'),{complete:true});
  assert.deepEqual(await pending,{answers:[{id:'confirm',selected:['乙']}]});interactions.stop();
});

test('work and calendar switching does not cancel a running assistant delegation',async()=>{
  const state={mode:'assistant',installation_id:'install',revision:7,conversation_id:'assistant'};
  let cancelled=0,selected=0;const replies=[];
  const channel={authorize:async()=>state,hasActiveInput:()=>true,numberedLists:{select:()=> 'id'},
    reply:async(_event,text)=>replies.push(text),store:async()=>{selected++;throw Error('must not select');},
    product:{cancelWeixinCommands:async()=>{cancelled++;},
      workRequest:async(method)=>method==='work.get'?{work:{id:'id',title:'实验',intent:'watching',conversation_id:'target'}}:{},
      rpc:async(method)=>method==='calendar.settings.get'?{confirmed:true,time_zone:'Asia/Shanghai'}:
        {event:{event_id:'id',title:'会议',status:'active',conversation_id:'target'}}}};
  const event={...state,text:'进入工作 1'};
  await new WeixinWorks(channel).control(event);
  await new WeixinCalendar(channel).control({...event,text:'进入日程 1'});
  assert.equal(cancelled,0);assert.equal(selected,0);assert.equal(replies.length,2);
  assert.ok(replies.every(text=>text.includes('正在处理')));
});
