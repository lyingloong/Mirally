/** A remote schedule must never silently acquire desktop authority. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,rm,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,basename,dirname} from 'node:path';
import {ScheduledActions} from '../kernel/dsh/packages/mirally/mirally-product/src/scheduled-actions.mjs';

async function fixture(t) {
  const home=await mkdtemp(join(tmpdir(),'mirally-scheduled-remote-'));
  t.after(async()=>{const resolved=await realpath(home);assert.equal(dirname(resolved),await realpath(tmpdir()));assert.ok(basename(resolved).startsWith('mirally-scheduled-remote-'));await rm(resolved,{recursive:true,force:true});});
  let now=Date.parse('2026-10-29T02:00:00Z');
  const state={bound:true,enabled:true,token:'fixture',installation_id:'a'.repeat(32),revision:1,conversation_id:'assistant'},settings={weixin_remote_control_enabled:true};
  const host={home,ctx:{},broadcast:()=>{},conversation:async()=>({generation:1,archived:false,frozen:false}),
    rpc:async method=>method==='calendar.settings.get'?{confirmed:true,time_zone:'Asia/Shanghai'}
      :method==='channel.weixin.get'?{state:{...state}}:method==='settings.get'?{...settings}
      :method==='command.list'?{commands:[]}:Promise.reject(new Error('unexpected '+method))};
  const actions=new ScheduledActions(host,{now:()=>now});
  const create=()=>actions.create({conversation_id:'target',generation:1,request_key:'fixture',
    input:{title:'检查',prompt:'检查进度',after_seconds:60},authority:{origin:'weixin',installation_id:state.installation_id,source_conversation_id:'assistant'}});
  return {home,state,settings,host,actions,create,due:()=>now+=60000};
}

test('persisted remote schedule never falls back to desktop execution',async t=>{
  const f=await fixture(t);let desktop=0;f.host.sendScheduled=async()=>{desktop++;return {accepted:true};};
  const job=await f.create();assert.equal(job.authority.origin,'weixin');
  const restored=new ScheduledActions(f.host,{now:()=>Date.parse('2026-10-29T02:01:00Z')});
  await restored.tick();assert.equal(desktop,0);assert.equal(restored.list()[0].status,'paused');
  assert.match(restored.list()[0].last_error,/尚未接入/);
});

test('remote occurrence authority ignores the legacy setting and revokes on rebind',async t=>{
  const f=await fixture(t),job=await f.create();let observed;
  f.host.sendWeixinScheduled=async(id,request,scheduleId)=>{observed={id,request,scheduleId};return {accepted:true};};
  f.due();await f.actions.tick();assert.equal(observed.id,'target');assert.equal(f.actions.list()[0].status,'dispatched');
  const check=()=>f.actions.authorizeWeixinOccurrence(job.id,'target',observed.request.commandId);
  f.state.revision++;f.state.conversation_id='other';assert.equal((await check()).conversation_id,'other');
  f.settings.weixin_remote_control_enabled=false;await check();
  f.settings.weixin_remote_control_enabled=true;f.state.installation_id='b'.repeat(32);await assert.rejects(check(),{code:'schedule_scope_revoked'});
  f.state.installation_id='a'.repeat(32);f.state.enabled=false;await assert.rejects(check(),{code:'schedule_scope_revoked'});
});

test('revoked binding before due prevents dispatch; untrusted provenance is rejected',async t=>{
  const f=await fixture(t);let called=0;f.host.sendWeixinScheduled=async()=>{called++;return {accepted:true};};
  await assert.rejects(f.actions.create({conversation_id:'target',generation:1,request_key:'bad',input:{title:'x',prompt:'y',after_seconds:60},authority:{origin:'weixin',installation_id:'bad',source_conversation_id:'assistant'}}),{code:'schedule_store_invalid'});
  await f.create();f.state.enabled=false;f.due();await f.actions.tick();
  assert.equal(called,0);assert.equal(f.actions.list()[0].status,'paused');assert.deepEqual(f.actions.list()[0].history,[]);
  const path=join(f.home,'scheduled-actions.json'),value=JSON.parse(await readFile(path,'utf8'));value.jobs[0].authority.installation_id='other';await writeFile(path,JSON.stringify(value));
  assert.throws(()=>new ScheduledActions(f.host),{code:'schedule_store_invalid'});
});

test('uncertain dispatch after admission starts cannot be replayed',async t=>{
  const f=await fixture(t);let calls=0;
  f.host.sendWeixinScheduled=async()=>{calls++;throw Object.assign(new Error('scope changed after admission'),{code:'schedule_scope_revoked'});};
  await f.create();f.due();await f.actions.tick();
  assert.equal(f.actions.list()[0].status,'unknown');assert.equal(f.actions.list()[0].history[0].status,'unknown');
  await f.actions.tick();assert.equal(calls,1);
});
