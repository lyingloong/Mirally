import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JobNotifications} from '../kernel/dsh/packages/mirally/mirally-product/src/job-notifications.mjs';

test('explicitly registered owned jobs create one truthful notice on settlement',async()=>{
  const shell={job_id:'shell-1',status:'running',exit_code:null},native={id:'native-1',owner:'s1',status:'running'};
  const calls=[];const host={shellJobs:{get:(owner,id)=>{if(owner!=='s1'||id!=='shell-1')throw Error('not found');return shell;},view:x=>x},
    ctx:{get:()=>({get:()=>native})},rpc:async(method,args)=>{calls.push({method,args});return {};}};
  const n=new JobNotifications(host);
  await n.tick();assert.equal(calls.length,0);
  const base={sessionId:'s1',conversationId:'c1',generation:1,sources:[],installationId:'bound'};
  n.add({...base,jobId:'shell-1',kind:'shell'});n.add({...base,jobId:'shell-1',kind:'shell'});
  n.add({...base,jobId:'native-1',kind:'native'});await n.tick();assert.equal(calls.length,0);
  shell.status='finished';shell.exit_code=3;native.status='killed';await n.tick();await n.tick();
  assert.equal(calls.length,2);assert.match(calls[0].args.title,/失败/);assert.match(calls[1].args.title,/停止/);
  assert.ok(calls.every(call=>call.args.conversation_id==='c1'&&call.args.installation_id==='bound'));
});

test('notification time is when settlement is observed, not when registered',async()=>{
  const realNow=Date.now;let now=1000;Date.now=()=>now;
  try {
    const job={status:'running'},calls=[];
    const n=new JobNotifications({shellJobs:{get:()=>job,view:x=>x},ctx:{},rpc:async(_method,args)=>calls.push(args)});
    n.add({sessionId:'s',conversationId:'c',generation:1,jobId:'j',kind:'shell'});
    now=9000;job.status='finished';job.exit_code=0;await n.tick();
    assert.equal(calls[0].due_at_utc_ms,9000);
  } finally {Date.now=realNow;}
});

test('unowned and other-session native jobs are never registered',()=>{
  let owner;const n=new JobNotifications({ctx:{get:()=>({get:()=>({id:'x',owner,status:'running'})})}});
  const base={sessionId:'s1',conversationId:'c1',generation:1,jobId:'x',kind:'native'};
  assert.throws(()=>n.add(base),/不属于/);owner='s2';assert.throws(()=>n.add(base),/不属于/);
});

test('native completion does not mistake nonzero or ambiguous exits for success',async()=>{
  const job={owner:'s',status:'completed'},calls=[];
  const n=new JobNotifications({ctx:{get:()=>({get:()=>job})},rpc:async(_method,args)=>calls.push(args)});
  const base={sessionId:'s',conversationId:'c',generation:1,kind:'native'};
  for (const [id,detail,expected] of [['nonzero','exit code: 5','失败'],['missing',undefined,'已结束'],
    ['invalid','exit code: unknown','已结束'],['sandbox','exit code: 0; [sandbox: denied]','已结束'],['zero','exit code: 0','完成']]) {
    job.detail=detail;n.add({...base,jobId:id});await n.tick();assert.equal(calls.at(-1).title,`后台作业${expected}`);
  }
});

test('transport retries preserve notification identity and revoked scope does not retry',async()=>{
  let ncall=0;const calls=[];const host={shellJobs:{get:()=>({status:'finished',exit_code:0}),view:x=>x},ctx:{},rpc:async(_method,args)=>{
    calls.push(args);if(++ncall===1)throw Error('lost');}};
  const n=new JobNotifications(host);n.add({sessionId:'s1',conversationId:'c1',generation:1,jobId:'x',kind:'shell',sources:[]});
  await n.tick();await n.tick();assert.equal(calls.length,2);assert.deepEqual(calls[0],calls[1]);
  n.add({sessionId:'s1',conversationId:'c1',generation:1,jobId:'x',kind:'shell',sources:[]});
  host.rpc=async()=>{ncall++;throw Object.assign(Error('revoked'),{code:'CONFLICT'});};
  await n.tick();const count=ncall;await n.tick();assert.equal(ncall,count);
});
