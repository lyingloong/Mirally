import {test} from 'node:test';
import assert from 'node:assert/strict';
import {installShellCapabilities} from '../kernel/dsh/packages/mirally/mirally-product/src/shell-capabilities.mjs';

test('a scheduled session can start, inspect and stop its own ordinary background command', {skip:process.platform !== 'win32',timeout:30000}, async t => {
  const registered = new Map(), agent = {id:'native-session'}, policy = {origin:'schedule'};
  const host = {
    ctx:{tools:{register:tool=>registered.set(tool.name,tool)}},
    nativeConversations:new Map([[agent.id,{conversation_id:'conversation'}]]),
    consumedCommandId:()=> 'scheduled-command', commandPolicy:()=>policy,
    checkCommandScope:async()=>{},guard:async()=>{},home:process.cwd(),
    actionGate:{request:async()=>true}, authorizations:{run:async(_scope,callback)=>callback()},
  };
  const jobs=installShellCapabilities(host);
  t.after(()=>jobs.dispose());
  const exec={agent,signal:new AbortController().signal};
  const created=await registered.get('mirally_run_command').execute({command:'Write-Output "MIRALLY_JOB_READY"; Start-Sleep -Seconds 20',cwd:process.cwd(),background:true},exec);
  assert.equal(created.status,'running');
  assert.equal((await registered.get('mirally_job_list').execute({},exec)).jobs[0].job_id,created.job_id);
  let observed;
  for(let i=0;i<80;i++) {
    observed=await registered.get('mirally_job_status').execute({job_id:created.job_id},exec);
    if(observed.stdout.includes('MIRALLY_JOB_READY'))break;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  assert.match(observed.stdout,/MIRALLY_JOB_READY/);
  assert.equal((await registered.get('mirally_stop_job').execute({job_id:created.job_id},exec)).status,'cancelled');
  assert.throws(()=>jobs.get('other-session',created.job_id),/不属于此会话/);
});
