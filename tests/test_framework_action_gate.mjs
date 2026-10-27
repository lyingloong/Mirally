import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ActionGate} from '../kernel/dsh/packages/mirally/mirally-product/src/action-gate.mjs';

function fixture() {
  const agent={id:'session'}, host={handles:new Map([['session',{agent}]]), command:'command',
    conversationForSession:()=>({conversation_id:'conversation'}), consumedCommandId:()=>host.command,
    checkCommandScope:async()=>{}, guard:async()=>{}, broadcast:()=>{}};
  return {host,gate:new ActionGate(host),request:{sessionId:'session',commandId:'command',kind:'shell',title:'执行命令',detail:'完整命令'}};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('approval is bound to one conversation and live command',async()=>{
  const {host,gate,request}=fixture(), result=gate.request(request);
  await flush(); const [approval]=gate.list('conversation').approvals;
  assert.equal(approval.detail,'完整命令'); assert.deepEqual(gate.list('other').approvals,[]);
  await assert.rejects(gate.decide({approval_id:approval.id,conversation_id:'other',allow:true}));
  host.command='new-command';
  await assert.rejects(gate.decide({approval_id:approval.id,conversation_id:'conversation',allow:true}));
  await gate.decide({approval_id:approval.id,conversation_id:'conversation',allow:false});
  assert.equal(await result,false); assert.deepEqual(gate.list('conversation').approvals,[]);
});

test('concurrent requests cannot bypass the one-pending-per-session limit',async()=>{
  const {gate,request}=fixture();
  const first=gate.request(request), second=gate.request(request);
  await assert.rejects(second,/已有待确认/);
  gate.dispose(); assert.equal(await first,false);
});

test('cancel and disposal remove pending approvals',async()=>{
  const {gate,request}=fixture(), controller=new AbortController();
  const result=gate.request({...request,signal:controller.signal});
  await flush(); controller.abort(); await assert.rejects(result);
  assert.deepEqual(gate.list('conversation').approvals,[]);
  const other=gate.request(request); await flush(); gate.dispose(); assert.equal(await other,false);
  await assert.rejects(gate.request(request),/关闭/);
});

test('command changing during asynchronous guard cannot create an approval',async()=>{
  const {host,gate,request}=fixture();
  host.guard=async()=>{host.command='new-command';};
  await assert.rejects(gate.request(request),/已变化/);
  assert.deepEqual(gate.list('conversation').approvals,[]);
});

test('standing permission skips only the prompt and still checks active scope and guard',async()=>{
  const {host,gate,request}=fixture(), seen=[];
  host.actionPermission=async scope=>{seen.push(scope); return true;};
  let scopes=0, guards=0;
  host.checkCommandScope=async()=>{scopes++;}; host.guard=async()=>{guards++;};
  assert.equal(await gate.request(request),true);
  assert.equal(scopes,2); assert.equal(guards,2);
  assert.deepEqual(seen,[{conversationId:'conversation',sessionId:'session',commandId:'command'},
    {conversationId:'conversation',sessionId:'session',commandId:'command'}]);
  assert.deepEqual(gate.list('conversation').approvals,[]);
});

test('revoked or non-boolean standing permission requires an ordinary decision',async()=>{
  const {host,gate,request}=fixture(); let calls=0;
  host.actionPermission=async()=>++calls===1;
  const result=gate.request(request); await flush();
  const [approval]=gate.list('conversation').approvals;
  assert.ok(approval); await gate.decide({approval_id:approval.id,conversation_id:'conversation',allow:false});
  assert.equal(await result,false);
  host.actionPermission=async()=>1;
  const next=gate.request(request); await flush(); assert.equal(gate.list('conversation').approvals.length,1);
  gate.dispose(); assert.equal(await next,false);
});

test('auto grant cannot authorize stale commands or bypass a revoked scope',async()=>{
  const a=fixture(); let calls=0;
  a.host.actionPermission=async()=>{if (++calls===2) a.host.command='other'; return true;};
  await assert.rejects(a.gate.request(a.request),/已变化/);
  const b=fixture(); let scopeChecks=0;
  b.host.actionPermission=async()=>true;
  b.host.checkCommandScope=async()=>{if (++scopeChecks===2) throw new Error('scope revoked');};
  await assert.rejects(b.gate.request(b.request),/scope revoked/);
});

test('enabling a standing grant does not resolve an already pending decision',async()=>{
  const {host,gate,request}=fixture(); host.actionPermission=async()=>false;
  const result=gate.request(request); await flush();
  host.actionPermission=async()=>true;
  const [approval]=gate.list('conversation').approvals; assert.ok(approval);
  await gate.decide({approval_id:approval.id,conversation_id:'conversation',allow:false});
  assert.equal(await result,false);
});
