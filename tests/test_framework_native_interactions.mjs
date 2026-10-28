import {test} from 'node:test';
import assert from 'node:assert/strict';
import {installNativeInteractions, nativeApprovalDetail, validateNativeAnswers} from '../kernel/dsh/packages/mirally/mirally-product/src/native-interactions.mjs';

function fixture() {
  const hooks=new Map(), events=[], session={snapshotEvents:()=>[{type:'tool/call',data:{callId:'call',name:'write',arguments:'{"path":"report.txt","content":"full text"}'}}]};
  const agent={id:'root',session}, child={id:'child',session};
  const conversation={conversation_id:'conversation',session_id:'root',generation:1};
  const host={ctx:{on:(name,listener)=>{hooks.set(name,listener);return()=>hooks.delete(name);},get:()=>undefined},
    handles:new Map([['root',{agent}]]), command:'command', guard:async()=>{}, checkCommandScope:async()=>{},
    conversation:async id=>{assert.equal(id,'conversation');return conversation;}, broadcast:(type,value)=>events.push({type,...value}),
    actionGate:{request:async request=>{host.approval=request;return true;}},
    nativeRuntime:{owner:who=>who===agent||who===child?{rootSessionId:'root',commandId:host.command,conversation}:null}};
  const bridge=installNativeInteractions(host), dispatch=(name,request)=>hooks.get(name)(request,()=>Promise.resolve('unavailable'));
  return {host,agent,child,bridge,dispatch,hooks,events};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const questions=[{id:'mode',question:'Choose a mode',options:[{label:'Fast'},{label:'Full'}]}];

test('native escalation uses the complete logged operation and the owning root action gate',async()=>{
  const {host,child,bridge,dispatch}=fixture();
  assert.equal(await dispatch('approval/request',{agent:child,toolName:'write',callId:'call',reason:'Write outside workspace'}),'allowed-once');
  assert.equal(host.approval.sessionId,'root');assert.equal(host.approval.commandId,'command');
  assert.match(host.approval.detail,/"content":"full text"/);assert.match(host.approval.detail,/Write outside workspace/);
  host.actionGate.request=async()=>false;
  assert.equal(await dispatch('approval/request',{agent:child,toolName:'write',callId:'call'}),'rejected');
  bridge.stop();assert.equal(fixture().hooks.size,2);
});

test('native escalation never approves missing, mismatched, or truncated argument presentations',()=>{
  const {agent}=fixture();
  assert.equal(nativeApprovalDetail({agent,toolName:'edit',callId:'call'}),null);
  assert.equal(nativeApprovalDetail({agent,toolName:'write'}),null);
  assert.equal(nativeApprovalDetail({agent,toolName:'write',callId:'call',reason:'x'.repeat(24000)}),null);
});

test('answers must name all exact questions and contain only offered selections',()=>{
  assert.throws(()=>validateNativeAnswers(questions,[]));
  assert.throws(()=>validateNativeAnswers(questions,[{id:'mode',selected:['unoffered']}]));
  assert.throws(()=>validateNativeAnswers(questions,[{id:'mode',selected:['Fast','Full']}]));
  assert.throws(()=>validateNativeAnswers(questions,[{id:'wrong',selected:['Fast']}]));
  assert.deepEqual(validateNativeAnswers(questions,[{id:'mode',selected:[],custom:'A human choice'}]),{answers:[{id:'mode',selected:[],custom:'A human choice'}]});
});

test('native human question returns the real structured answer on the same live turn',async()=>{
  const {agent,bridge,dispatch,events,hooks}=fixture();
  const waiting=dispatch('user-questions/request',{agent,questions});await flush();
  const {questions:[pending]}=await bridge.request('interaction.questions.list',{conversation_id:'conversation'});
  assert.equal(pending.command_id,'command');assert.deepEqual(pending.questions,questions);
  await bridge.request('interaction.questions.answer',{conversation_id:'conversation',question_id:pending.id,answers:[{id:'mode',selected:['Full']}]});
  assert.deepEqual(await waiting,{answers:[{id:'mode',selected:['Full']}]});
  assert.deepEqual(await bridge.request('interaction.questions.list',{conversation_id:'conversation'}),{questions:[]});
  assert.ok(events.filter(event=>event.action==='questions-changed').length>=2);
  bridge.stop();assert.equal(hooks.size,0);
});

test('abort and turn ownership changes cannot accept a late human answer',async()=>{
  const first=fixture(), controller=new AbortController();
  const waiting=first.dispatch('user-questions/request',{agent:first.agent,questions,signal:controller.signal});await flush();
  const rejected=assert.rejects(waiting,error=>error.code==='ASK_ABORTED');controller.abort();await rejected;
  assert.deepEqual(await first.bridge.request('interaction.questions.list',{conversation_id:'conversation'}),{questions:[]});first.bridge.stop();
  const second=fixture();const next=second.dispatch('user-questions/request',{agent:second.agent,questions});await flush();
  const {questions:[item]}=await second.bridge.request('interaction.questions.list',{conversation_id:'conversation'});
  second.host.command='other';
  await assert.rejects(second.bridge.request('interaction.questions.answer',{conversation_id:'conversation',question_id:item.id,answers:[{id:'mode',selected:['Fast']}]}),error=>error.code==='interaction_stale');
  const nextRejected=assert.rejects(next,error=>error.code==='ASK_ABORTED');second.bridge.stop();await nextRejected;
});

test('scope revocation during answer guard does not release a native question',async()=>{
  const {host,agent,bridge,dispatch}=fixture();const waiting=dispatch('user-questions/request',{agent,questions});await flush();
  const {questions:[item]}=await bridge.request('interaction.questions.list',{conversation_id:'conversation'});
  host.guard=async()=>{host.command='revoked';};
  await assert.rejects(bridge.request('interaction.questions.answer',{conversation_id:'conversation',question_id:item.id,answers:[{id:'mode',selected:['Fast']}]}),error=>error.code==='interaction_stale');
  const rejected=assert.rejects(waiting,error=>error.code==='ASK_ABORTED');bridge.stop();await rejected;
});
