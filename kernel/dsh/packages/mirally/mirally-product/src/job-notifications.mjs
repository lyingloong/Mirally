/** Optional completion notices for jobs already owned by an ordinary session. */
import {randomUUID} from 'node:crypto';

const fail = message => Object.assign(new Error(message), {code:'job_notification_unavailable'});
const output = {schema:{type:'object',additionalProperties:true},render:(_args,result)=>[{type:'text',text:JSON.stringify(result)}]};

export class JobNotifications {
  constructor(host) { this.host=host; this.pending=new Map(); }
  job(sessionId, id, kind) {
    if (kind === 'shell') return this.host.shellJobs?.view(this.host.shellJobs.get(sessionId,id));
    const job=this.host.ctx.get('jobs')?.get(id,sessionId);
    if (!job || job.owner !== sessionId) throw fail('作业不存在或不属于当前会话。');
    return job;
  }
  add({sessionId,conversationId,generation,jobId,kind,sources,installationId}) {
    if (this.pending.size>=256) throw fail('等待通知的作业数量已达上限。');
    const job=this.job(sessionId,jobId,kind);
    if (!job) throw fail('作业不存在或不属于当前会话。');
    const key=`${sessionId}:${kind}:${jobId}`;
    if (!this.pending.has(key)) this.pending.set(key,{sessionId,conversationId,generation,jobId,kind,sources,
      installationId, requestId:`job-notify:${randomUUID()}`});
    return {registered:true, job_id:jobId, note:'通知只在 Mirally 运行期间有效。'};
  }
  async tick() {
    for (const [key,item] of this.pending) {
      let job;
      try { job=this.job(item.sessionId,item.jobId,item.kind); }
      catch { this.pending.delete(key); continue; }
      if (!job || ['running','stopping'].includes(job.status)) continue;
      let outcome;
      if (item.kind==='shell') outcome=job.status==='finished' ? (job.exit_code===0?'完成':'失败')
        : job.status==='cancelled'?'已停止':job.status==='timeout'?'超时':job.status==='failed_to_start'?'启动失败':null;
      else if (job.status==='completed') {
        // DSH reports shell nonzero exits as completed with an exit-code
        // detail; sandbox markers can also make the outcome ambiguous.
        const code=/^exit code: (\d+)(?:;|$)/.exec(job.detail ?? '');
        outcome=code && !String(job.detail).includes('[sandbox:') ? (Number(code[1])===0?'完成':'失败') : '已结束';
      } else outcome=({failed:'失败',killed:'已停止'})[job.status];
      if (!outcome) { this.pending.delete(key); continue; }
      item.due ??= Date.now();
      try {
        await this.host.rpc('internal.job_notification.create',{conversation_id:item.conversationId,generation:item.generation,
          title:`后台作业${outcome}`,body:'打开原会话查看输出。',request_id:item.requestId,due_at_utc_ms:item.due,
          sources:item.sources,...(item.installationId?{installation_id:item.installationId}:{})});
        this.pending.delete(key);
      } catch (error) {
        if (['CONFLICT','NOT_FOUND','INVALID_PARAMS'].includes(error.code)) this.pending.delete(key);
        else this.host.ctx?.logger?.warn?.(`Job notification: ${error.message}`);
      }
    }
  }
}

export function installJobNotificationTool(host) {
  const notifications=new JobNotifications(host);
  host.ctx.tools.register({name:'mirally_notify_job',description:'Only when the user explicitly asks to be notified, register a completion notice for a background job in this conversation. Use job_list or mirally_job_list to obtain its exact ID and kind. Notifications require Mirally to remain running.',
    parameters:{type:'object',properties:{job_id:{type:'string'},kind:{type:'string',enum:['shell','native'],description:'shell for mirally_run_command jobs; native for DSH pwsh/bash jobs returned by job_list.'}},required:['job_id','kind'],additionalProperties:false},output,
    execute:async(args,exec)=>{
      const native=exec.agent?.id, commandId=host.consumedCommandId(exec.agent), conversation=host.nativeConversations.get(native),policy=host.commandPolicy(exec.agent);
      if (!conversation || !commandId || !['desktop','weixin','schedule'].includes(policy?.origin)) throw fail('通知只能由当前会话请求。');
      if (typeof args.job_id!=='string' || !args.job_id || args.job_id.length>200 || !['shell','native'].includes(args.kind)) throw fail('作业标识无效。');
      exec.signal.throwIfAborted(); await host.checkCommandScope(exec.agent,'mirally_notify_job'); await host.guard(native);
      const {commands}=await host.rpc('command.list',{conversation_id:conversation.conversation_id,command_id:commandId});
      if (host.consumedCommandId(exec.agent)!==commandId || host.commandPolicy(exec.agent)!==policy) throw fail('会话已变化。');
      await host.checkCommandScope(exec.agent,'mirally_notify_job'); exec.signal.throwIfAborted();
      const command=commands?.find(item=>item.command_id===commandId);
      if (!command || command.generation!==conversation.generation) throw fail('会话已变化。');
      return notifications.add({sessionId:native,conversationId:conversation.conversation_id,generation:conversation.generation,
        jobId:args.job_id,kind:args.kind,sources:command.sources??[],installationId:policy.origin==='weixin'?policy.installation_id:undefined});
    }});
  return notifications;
}
