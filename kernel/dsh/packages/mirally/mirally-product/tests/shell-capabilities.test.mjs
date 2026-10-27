import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {AsyncLocalStorage} from 'node:async_hooks';
import {installShellCapabilities, runPowerShell, ShellJobs} from '../src/shell-capabilities.mjs';

test('PowerShell executes in the reviewed directory, reports exit status and bounds output', {skip: process.platform !== 'win32'}, async t => {
  const cwd = await mkdtemp(join(tmpdir(), 'mirally-shell-中文 '));
  t.after(() => rm(cwd, {recursive:true, force:true}));
  const value = await runPowerShell('Write-Output "hello"; exit 7', cwd);
  assert.equal(value.status, 'finished'); assert.equal(value.exit_code, 7); assert.match(value.stdout, /hello/);
  const large = await runPowerShell("'x' * 80000", cwd);
  assert.equal(large.truncated, true); assert.ok(Buffer.byteLength(large.stdout) <= 65536);
});

test('PowerShell timeout and cancellation terminate active command', {skip: process.platform !== 'win32'}, async () => {
  const timeout = await runPowerShell('Start-Sleep -Seconds 20', tmpdir(), {timeoutMs: 500});
  assert.equal(timeout.status, 'timeout');
  const controller = new AbortController();
  const pending = runPowerShell('Start-Sleep -Seconds 20', tmpdir(), {signal:controller.signal});
  setTimeout(() => controller.abort(), 500);
  assert.equal((await pending).status, 'cancelled');
});

test('approval is bound to active desktop command and shows the entire command', async () => {
  let tool, approved = false, requested, policy = {origin:'desktop'}, consumed = 'command-1';
  const active = new Map([['native', 'command-1']]);
  const host = {home: tmpdir(), nativeConversations: new Map([['native', {conversation_id:'conversation'}]]), activeCommands: active,
    authorizations: new AsyncLocalStorage(), ctx:{tools:{register: value => {if(value.name==='mirally_run_command') tool = value;}}},
    commandPolicy: () => policy, consumedCommandId: () => consumed, checkCommandScope: async () => {}, guard: async () => {},
    actionGate: {request: async request => {requested = request; return approved;}}};
  installShellCapabilities(host);
  const signal = new AbortController().signal;
  const exec = {agent:{id:'native'}, signal};
  await assert.rejects(tool.execute({command:'Write-Output "quoted"', cwd:tmpdir()}, exec), /未批准/);
  assert.match(requested.detail, /Write-Output "quoted"/); assert.equal(requested.commandId, 'command-1'); assert.equal(requested.sessionId, 'native'); assert.equal(requested.kind, 'shell');
  policy = {origin:'weixin'};
  await assert.rejects(tool.execute({command:'echo no', cwd:tmpdir()}, exec), /桌面会话/);
  policy = {origin:'desktop'};
  host.actionGate.request = async request => {active.set('native','queued'); requested = request; return false;};
  await assert.rejects(tool.execute({command:'echo queued', cwd:tmpdir()}, exec), /未批准/);
  assert.equal(requested.commandId, 'command-1');
  approved = true;
  host.actionGate.request = async () => {active.set('native','command-2'); consumed = 'command-2'; return true;};
  await assert.rejects(tool.execute({command:'echo no', cwd:tmpdir()}, exec), /状态已改变/);
});

test('managed jobs expose live output, isolate conversations, stop and dispose', {skip: process.platform !== 'win32'}, async () => {
  const jobs = new ShellJobs();
  const job = await jobs.start({sessionId:'one', commandId:'command', command:'Write-Output "ready"; Start-Sleep -Seconds 20', cwd:tmpdir(), timeoutMs:30000});
  assert.equal(job.status, 'running');
  await assert.rejects(async () => jobs.get('two', job.job_id), /不属于/);
  const end = Date.now() + 8000;
  while (!jobs.view(jobs.get('one', job.job_id)).stdout.includes('ready') && Date.now() < end) await new Promise(r => setTimeout(r, 100));
  assert.match(jobs.view(jobs.get('one', job.job_id)).stdout, /ready/);
  assert.equal((await jobs.stop('one', job.job_id)).status, 'cancelled');
  const second = await jobs.start({sessionId:'one', commandId:'command', command:'Start-Sleep -Seconds 20', cwd:tmpdir(), timeoutMs:30000});
  await jobs.dispose();
  assert.equal(jobs.view(jobs.get('one', second.job_id)).status, 'cancelled');
});

test('approved background command can be inspected and stopped through model tools', {skip: process.platform !== 'win32'}, async t => {
  const tools = new Map();
  const host = {home:tmpdir(), nativeConversations:new Map([['native',{conversation_id:'conversation'}]]), activeCommands:new Map([['native','first']]),
    authorizations:new AsyncLocalStorage(), ctx:{tools:{register: value => tools.set(value.name,value)}},
    commandPolicy:()=>({origin:'desktop'}), consumedCommandId:()=>host.activeCommands.get('native'), checkCommandScope:async()=>{}, guard:async()=>{}, actionGate:{request:async()=>true}};
  const jobs = installShellCapabilities(host); t.after(() => jobs.dispose());
  const exec = {agent:{id:'native'}, signal:new AbortController().signal};
  const job = await tools.get('mirally_run_command').execute({command:'Start-Sleep -Seconds 20', cwd:tmpdir(), background:true},exec);
  assert.equal(job.status,'running');
  host.activeCommands.set('native','next');
  assert.equal((await tools.get('mirally_job_status').execute({job_id:job.job_id},exec)).status,'running');
  assert.equal((await tools.get('mirally_stop_job').execute({job_id:job.job_id},exec)).status,'cancelled');
});
