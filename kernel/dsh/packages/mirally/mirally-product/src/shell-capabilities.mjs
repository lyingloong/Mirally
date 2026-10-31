/** Foreground command execution. Approval covers a full, unsandboxed PowerShell command. */
import {spawn} from 'node:child_process';
import {mkdir, realpath, stat} from 'node:fs/promises';
import {isAbsolute, join, resolve} from 'node:path';
import {randomUUID} from 'node:crypto';

const fail = message => Object.assign(new Error(message), {code: 'command_unavailable'});
const LIMIT = 64 * 1024;

export async function runPowerShell(command, cwd, {signal, timeoutMs = 120000, onOutput, onSpawn, onTruncated} = {}) {
  if (process.platform !== 'win32') throw fail('命令执行目前仅支持 Windows。');
  signal?.throwIfAborted();
  const root = process.env.SystemRoot || process.env.WINDIR;
  if (!root || !isAbsolute(root)) throw fail('无法定位 Windows PowerShell。');
  const executable = join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const script = `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new();\n${command}`;
  return new Promise((resolveResult, reject) => {
    const child = spawn(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
      {cwd, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
    let state = 'finished', bytes = 0, killTask, done = false;
    const stdout = [], stderr = [];
    const terminate = reason => {
      if (state !== 'finished' || done) return;
      state = reason;
      if (!child.pid) return;
      killTask = new Promise(finish => {
        const killer = spawn(join(root, 'System32', 'taskkill.exe'), ['/PID', String(child.pid), '/T', '/F'], {windowsHide: true, shell: false, stdio: 'ignore'});
        killer.once('error', () => { child.kill(); finish(); });
        killer.once('close', () => { child.kill(); finish(); });
      });
    };
    const collect = (name, out) => chunk => { const left = Math.max(0, LIMIT - bytes); bytes += chunk.length; if (left) out.push(chunk.subarray(0, left)); onOutput?.(name, chunk); if (bytes > LIMIT) onTruncated?.(); };
    child.stdout.on('data', collect('stdout', stdout)); child.stderr.on('data', collect('stderr', stderr));
    child.once('spawn', () => onSpawn?.());
    const aborted = () => terminate('cancelled');
    signal?.addEventListener('abort', aborted, {once: true});
    if (signal?.aborted) aborted();
    const timer = setTimeout(() => terminate('timeout'), timeoutMs);
    child.once('error', error => { if (done) return; done = true; clearTimeout(timer); signal?.removeEventListener('abort', aborted); reject(fail(`无法启动 PowerShell：${error.code ?? error.message}`)); });
    child.once('close', async (code, sig) => {
      if (done) return; done = true; clearTimeout(timer); signal?.removeEventListener('abort', aborted);
      await killTask;
      resolveResult({status: state, exit_code: code, signal: sig, stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8'), truncated: bytes > LIMIT});
    });
  });
}

/** In-memory jobs belong to one conversation and never survive a host shutdown. */
export class ShellJobs {
  constructor() { this.jobs = new Map(); this.closed = false; }
  async start({sessionId, commandId, command, cwd, timeoutMs}) {
    if (this.closed) throw fail('命令服务正在退出。');
    if ([...this.jobs.values()].filter(job => job.status === 'running').length >= 8) throw fail('后台命令数量已达上限。');
    const old = [...this.jobs.values()].filter(job => job.status !== 'running');
    while (this.jobs.size >= 64 && old.length) this.jobs.delete(old.shift().id);
    const id = randomUUID(), controller = new AbortController();
    const job = {id, sessionId, commandId, command, cwd, status:'running', controller, stdout:[], stderr:[], created_at:new Date().toISOString()};
    this.jobs.set(id, job);
    let startedResolve, startedReject;
    const started = new Promise((yes, no) => {startedResolve = yes; startedReject = no;});
    job.completion = runPowerShell(command, cwd, {signal:controller.signal, timeoutMs,
      onSpawn: startedResolve, onTruncated: () => {job.truncated = true;}, onOutput: (stream, chunk) => {
        const tail = Buffer.concat([...job[stream], chunk]);
        job[stream] = [tail.subarray(Math.max(0, tail.length - LIMIT))];
      }}).then(result => {
        Object.assign(job, result, {stdout: job.stdout, stderr: job.stderr});
      }, error => { job.status = 'failed_to_start'; job.error = error.message; startedReject(error); });
    await started;
    return this.view(job);
  }
  view(job) { return {job_id:job.id, command:job.command, cwd:job.cwd, status:job.status, created_at:job.created_at,
    stdout:Buffer.concat(job.stdout).toString('utf8'), stderr:Buffer.concat(job.stderr).toString('utf8'),
    truncated:job.truncated ?? false, exit_code:job.exit_code ?? null, error:job.error ?? null}; }
  list(sessionId) { return [...this.jobs.values()].filter(job => job.sessionId === sessionId).map(job => ({job_id:job.id, command:job.command, cwd:job.cwd, status:job.status, created_at:job.created_at, exit_code:job.exit_code ?? null})); }
  get(sessionId, id) { const job = this.jobs.get(id); if (!job || job.sessionId !== sessionId) throw fail('后台命令不存在或不属于此会话。'); return job; }
  async stop(sessionId, id) { const job = this.get(sessionId, id); if (job.status === 'running') {job.controller.abort(); await job.completion;} return this.view(job); }
  async cancelSession(sessionId) { await Promise.all([...this.jobs.values()].filter(job => job.sessionId === sessionId && job.status === 'running').map(job => this.stop(sessionId, job.id))); }
  async dispose() { this.closed = true; await Promise.all([...this.jobs.values()].filter(job => job.status === 'running').map(job => this.stop(job.sessionId, job.id))); }
}

export function installShellCapabilities(host) {
  const jobs = new ShellJobs();
  const validOrigin = agent => ['desktop', 'weixin', 'schedule'].includes(host.commandPolicy(agent)?.origin);
  host.ctx.tools.register({name: 'mirally_run_command',
    description: 'Run a PowerShell command on the user’s Windows computer. Mirally enforces the user’s configured permission policy; call the tool rather than asking separately for confirmation. Use a managed background job for long experiments so this conversation can continue and inspect progress. Background mode returns immediately after launch; it does not wait for completion, even for sleep commands. Inspect job status or actual timestamps before claiming completion or elapsed time. This has full user privileges, is not sandboxed, and may access files and network. Use only when the user requests an action; report nonzero exit and timeouts honestly.',
    parameters: {type: 'object', properties: {command: {type: 'string', description: 'Complete PowerShell command to review'}, cwd: {type: 'string', description: 'Absolute working directory; defaults to Mirally workspace'}, background: {type:'boolean', description:'Start a managed background job'}, timeout_seconds: {type: 'integer', description: 'Foreground defaults to 120 seconds, maximum 300; background defaults to 86400 seconds, maximum 604800'}}, required: ['command'], additionalProperties: false},
    output: {schema: {type: 'object', additionalProperties: true}, render: (_args, result) => [{type: 'text', text: JSON.stringify(result)}]},
    execute: async (args, exec) => {
      const signal = exec.signal; signal.throwIfAborted();
      const native = exec.agent?.id, commandId = host.consumedCommandId(exec.agent);
      const conversation = host.nativeConversations.get(native);
      if (!conversation || !commandId || !validOrigin(exec.agent)) throw fail('命令只能由已授权的当前会话发起。');
      await host.checkCommandScope(exec.agent, 'mirally_run_command');
      await host.guard(native);
      if (typeof args.command !== 'string' || !args.command.trim() || args.command.includes('\0') || args.command.length > 16000) throw fail('命令不能为空或超过 16000 字符。');
      if (args.cwd !== undefined && (typeof args.cwd !== 'string' || !isAbsolute(args.cwd))) throw fail('工作目录必须是绝对路径。');
      if (args.cwd === undefined) await mkdir(resolve(host.home, 'workspace'), {recursive:true});
      const cwd = await realpath(args.cwd ?? resolve(host.home, 'workspace'));
      if (!(await stat(cwd)).isDirectory()) throw fail('工作目录不存在。');
      const seconds = args.timeout_seconds ?? (args.background ? 86400 : 120);
      if (args.background !== undefined && typeof args.background !== 'boolean') throw fail('后台运行设置无效。');
      const maximum = args.background ? 604800 : 300;
      if (!Number.isInteger(seconds) || seconds < 1 || seconds > maximum) throw fail(`超时时间必须在 1 到 ${maximum} 秒之间。`);
      const approved = await host.actionGate.request({sessionId: native, commandId, kind: 'shell', title: '运行 PowerShell 命令', detail: `运行方式：${args.background ? '受管理的后台作业' : '前台'}\n工作目录：${cwd}\n超时：${seconds} 秒\n命令：\n${args.command}\n此命令具有当前用户权限，可访问文件和网络。`, signal});
      signal.throwIfAborted();
      if (!approved) throw fail('用户未批准命令。');
      if (host.consumedCommandId(exec.agent) !== commandId || !validOrigin(exec.agent)) throw fail('会话状态已改变，请重新发起命令。');
      await host.checkCommandScope(exec.agent, 'mirally_run_command');
      return host.authorizations.run({sessionId: native, commandId, confirmed: true}, async () => {
        await host.guard(native, 'script', true); signal.throwIfAborted();
        if (host.consumedCommandId(exec.agent) !== commandId || !validOrigin(exec.agent)) throw fail('会话状态已改变，请重新发起命令。');
        const result = args.background
          ? await jobs.start({sessionId: native, commandId, command: args.command, cwd, timeoutMs: seconds * 1000})
          : await runPowerShell(args.command, cwd, {signal, timeoutMs: seconds * 1000});
        try {
          signal.throwIfAborted();
          if (host.consumedCommandId(exec.agent) !== commandId) throw fail('会话已改变，命令的迟到结果未交给模型。');
          await host.checkCommandScope(exec.agent, 'mirally_run_command');
          await host.guard(native, 'script', true);
          signal.throwIfAborted();
          if (host.consumedCommandId(exec.agent) !== commandId || !validOrigin(exec.agent)) throw fail('会话已改变，命令的迟到结果未交给模型。');
          return result;
        } catch (error) { if (result.job_id) await jobs.stop(native, result.job_id); throw error; }
      });
    }});
  const assertScope = async (exec, name, native, commandId) => {
    exec.signal.throwIfAborted();
    if (!host.nativeConversations.has(native) || !commandId || !validOrigin(exec.agent)) throw fail('后台命令仅可由已授权的所属会话操作。');
    await host.checkCommandScope(exec.agent, name);
    await host.guard(native);
    exec.signal.throwIfAborted();
    if (host.consumedCommandId(exec.agent) !== commandId || !validOrigin(exec.agent)) throw fail('会话状态已改变。');
  };
  host.ctx.tools.register({name:'mirally_job_list', description:'List compatibility jobs started by mirally_run_command in this conversation. Native pwsh/bash jobs use job_list and job_output; an empty list here does not mean no native work is running.',
    parameters:{type:'object', properties:{}, additionalProperties:false},
    output:{schema:{type:'object',additionalProperties:true}, render:(_args,result)=>[{type:'text',text:JSON.stringify(result)}]},
    execute:async(_args,exec)=>{
      const native=exec.agent?.id, commandId=host.consumedCommandId(exec.agent);
      await assertScope(exec, 'mirally_job_list', native, commandId);
      return {jobs:jobs.list(native)};
    }});
  for (const [name, description, action] of [
    ['mirally_job_status', 'Read status and bounded output of a managed background command in this conversation.', (session, id) => jobs.view(jobs.get(session, id))],
    ['mirally_stop_job', 'Stop a managed background command in this conversation. Mirally applies the configured permission policy.', (session, id) => jobs.stop(session, id)],
  ]) host.ctx.tools.register({name, description, parameters:{type:'object', properties:{job_id:{type:'string'}}, required:['job_id'], additionalProperties:false},
    output:{schema:{type:'object',additionalProperties:true}, render:(_args, result)=>[{type:'text',text:JSON.stringify(result)}]},
    execute:async(args,exec)=>{
      const native=exec.agent?.id, commandId=host.consumedCommandId(exec.agent);
      await assertScope(exec, name, native, commandId);
      const job = jobs.get(native, args.job_id);
      if (name === 'mirally_stop_job' && job.status === 'running') {
        const approved = await host.actionGate.request({sessionId:native, commandId, kind:'shell', title:'停止后台命令', detail:`作业：${job.id}\n工作目录：${job.cwd}\n命令：\n${job.command}`, signal:exec.signal});
        exec.signal.throwIfAborted();
        if (!approved) throw fail('用户未批准停止命令。');
        await assertScope(exec, name, native, commandId);
        return host.authorizations.run({sessionId:native,commandId,confirmed:true}, async () => {
          await host.guard(native, 'script', true);
          await assertScope(exec, name, native, commandId);
          const result = await action(native, args.job_id);
          await assertScope(exec, name, native, commandId);
          return result;
        });
      }
      return action(native, args.job_id);
    }});
  return jobs;
}
