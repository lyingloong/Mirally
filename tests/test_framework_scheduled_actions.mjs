import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {ScheduledActions, installScheduledActionTools, scheduleRecord} from '../kernel/dsh/packages/mirally/mirally-product/src/scheduled-actions.mjs';

function fixture(t) {
  const home=mkdtempSync(join(tmpdir(),'mirally-scheduled-'));t.after(()=>rmSync(home,{recursive:true,force:true}));
  let time=Date.parse('2026-10-29T02:00:00Z'); const calls=[],tools=new Map();
  const agent={id:'native-1'},conversation={conversation_id:'conv-1',generation:1,session_id:'native-1',archived:false,frozen:false};
  const policy={origin:'desktop'};
  const host={home,ctx:{tools:{register:tool=>tools.set(tool.name,tool)}},
    nativeRuntime:{owner:()=>({rootSessionId:agent.id,conversation,commandId:'command-1'})},
    commandPolicy:()=>policy,consumedCommandId:()=> 'command-1',checkCommandScope:async()=>{},guard:async()=>{},
    rpc:async method=>{if(method==='command.list')return {commands:[{command_id:calls.at(-1)?.input.commandId,state:'completed'}]};
      assert.equal(method,'calendar.settings.get');return {confirmed:true,time_zone:'Asia/Shanghai'};},
    conversation:async()=>conversation,sendScheduled:async(id,input,job)=>{assert.equal(actions.ownsDispatch(job,id,input.commandId),true);calls.push({id,input});return {accepted:true};},broadcast:()=>{}};
  const actions=new ScheduledActions(host,{now:()=>time}); installScheduledActionTools(host,actions);
  const exec={agent,signal:new AbortController().signal};
  return {home,actions,tools,exec,agent,conversation,policy,host,calls,setTime:value=>{time=value;},now:()=>time};
}

test('DSH rules use confirmed timezone and reject invalid or ambiguous instants',()=>{
  const now=Date.parse('2026-10-29T02:00:00Z');
  const daily=scheduleRecord('id',{title:'检查',prompt:'检查进度',daily:'11:00:00'},now,'Asia/Shanghai');
  assert.equal(daily.scheduledAt,'2026-10-29T03:00:00.000Z');
  assert.throws(()=>scheduleRecord('id',{title:'检查',prompt:'检查进度',at:'2026-10-30T11:00:00'},now,'Asia/Shanghai'));
  assert.throws(()=>scheduleRecord('id',{title:'检查',prompt:'检查进度',daily:'11:00:00',after_seconds:60},now,'Asia/Shanghai'));
});

test('explicit schedule is deduplicated, uses real session send, and reports admission rather than completion',async t=>{
  const f=fixture(t); const tool=f.tools.get('mirally_schedule_action');
  const input={title:'检查实验',prompt:'检查 GPU 进度',after_seconds:60};
  const first=(await tool.execute(input,f.exec)).schedule;
  assert.equal((await tool.execute(input,f.exec)).schedule.id,first.id);
  assert.equal(f.actions.list('conv-1').length,1);
  f.setTime(f.now()+60_000);await Promise.all([f.actions.tick(),f.actions.tick()]);
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].id,'conv-1');
  assert.match(f.calls[0].input.commandId,/^schedule-[a-f0-9]{40}$/);
  assert.match(f.calls[0].input.text,/检查 GPU 进度/);
  const job=f.actions.list('conv-1')[0];assert.equal(job.status,'dispatched');assert.equal(job.history[0].status,'accepted');
  assert.equal((await f.actions.listObserved('conv-1'))[0].history[0].command_state,'completed');
  assert.equal(f.actions.ownsDispatch(job.id,'conv-1',f.calls[0].input.commandId),false);
  const restored=new ScheduledActions(f.host,{now:f.now});assert.equal(restored.list('conv-1')[0].status,'dispatched');
  await restored.tick();assert.equal(f.calls.length,1);
});

test('recurrence coalesces missed executions and pause/cancel work',async t=>{
  const f=fixture(t),tool=f.tools.get('mirally_schedule_action'),manage=f.tools.get('mirally_manage_scheduled_action');
  const job=(await tool.execute({title:'监测',prompt:'检查 GPU',every_seconds:60},f.exec)).schedule;
  await manage.execute({schedule_id:job.id,operation:'pause'},f.exec);f.setTime(f.now()+180_000);await f.actions.tick();assert.equal(f.calls.length,0);
  await manage.execute({schedule_id:job.id,operation:'resume'},f.exec);await f.actions.tick();
  assert.equal(f.calls.length,1);assert.equal(f.actions.list('conv-1')[0].status,'active');
  assert.equal(Date.parse(f.actions.list('conv-1')[0].record.scheduledAt),f.now()+60_000);
  await manage.execute({schedule_id:job.id,operation:'cancel'},f.exec);f.setTime(f.now()+120_000);await f.actions.tick();assert.equal(f.calls.length,1);
});

test('daily action follows a changed product timezone without dispatching an old-zone occurrence',async t=>{
  const f=fixture(t),tool=f.tools.get('mirally_schedule_action');let zone='Asia/Shanghai';
  f.host.rpc=async()=>({confirmed:true,time_zone:zone});
  const job=(await tool.execute({title:'每日检查',prompt:'检查实验',daily:'11:00:00'},f.exec)).schedule;
  assert.equal(job.record.scheduledAt,'2026-10-29T03:00:00.000Z');
  zone='Europe/London';f.setTime(Date.parse('2026-10-29T03:00:00Z'));await f.actions.tick();
  assert.equal(f.calls.length,0);
  const current=f.actions.list('conv-1')[0];assert.equal(current.record.timeZone,'Europe/London');
  assert.equal(current.record.scheduledAt,'2026-10-29T11:00:00.000Z');
});

test('rescheduling replaces the future rule without creating a duplicate',async t=>{
  const f=fixture(t),create=f.tools.get('mirally_schedule_action'),update=f.tools.get('mirally_update_scheduled_action');
  const job=(await create.execute({title:'检查',prompt:'查看实验',after_seconds:60},f.exec)).schedule;
  const changed=(await update.execute({schedule_id:job.id,title:'检查 GPU',prompt:'查看实验显存',after_seconds:180},f.exec)).schedule;
  assert.equal(changed.id,job.id);assert.equal(f.actions.list().length,1);
  f.setTime(f.now()+60_000);await f.actions.tick();assert.equal(f.calls.length,0);
  f.setTime(f.now()+120_000);await f.actions.tick();assert.equal(f.calls.length,1);assert.match(f.calls[0].input.text,/查看实验显存/);
  await assert.rejects(update.execute({schedule_id:job.id,title:'检查',prompt:'再次执行',after_seconds:60},f.exec),{code:'schedule_conflict'});
});

test('unknown admission or crash is never replayed automatically',async t=>{
  const f=fixture(t),tool=f.tools.get('mirally_schedule_action');
  const job=(await tool.execute({title:'检查',prompt:'执行',after_seconds:60},f.exec)).schedule;
  f.host.sendScheduled=async()=>{throw new Error('unknown transport');};f.setTime(f.now()+60_000);await f.actions.tick();
  assert.equal(f.actions.list('conv-1')[0].status,'unknown');
  await f.actions.tick();assert.equal(f.actions.list('conv-1')[0].history.length,1);
  const file=join(f.home,'scheduled-actions.json'),data=JSON.parse(readFileSync(file,'utf8'));
  data.jobs[0].status='dispatching';data.jobs[0].pending={command_id:'old',occurrence_at:job.record.scheduledAt};writeFileSync(file,JSON.stringify(data));
  const restored=new ScheduledActions(f.host,{now:f.now});assert.equal(restored.list('conv-1')[0].status,'unknown');
  await restored.tick();assert.equal(f.calls.length,0);
});

test('remote creation, revoked scope, changed session and corrupted state fail closed',async t=>{
  const f=fixture(t),tool=f.tools.get('mirally_schedule_action'),input={title:'检查',prompt:'检查实验',after_seconds:60};
  f.policy.origin='weixin';await assert.rejects(tool.execute(input,f.exec),{code:'schedule_remote_unavailable'});f.policy.origin='desktop';
  f.host.rpc=async()=>{f.policy.origin='weixin';return {confirmed:true,time_zone:'Asia/Shanghai'};};
  await assert.rejects(tool.execute(input,f.exec),{code:'schedule_remote_unavailable'});assert.equal(f.actions.list().length,0);
  f.policy.origin='desktop';f.host.rpc=async()=>({confirmed:true,time_zone:'Asia/Shanghai'});
  await tool.execute(input,f.exec);f.conversation.generation=2;f.setTime(f.now()+60_000);await f.actions.tick();
  assert.equal(f.calls.length,0);assert.equal(f.actions.list()[0].status,'unknown');
  writeFileSync(join(f.home,'scheduled-actions.json'),'broken');assert.throws(()=>new ScheduledActions(f.host),{code:'schedule_store_invalid'});
});
