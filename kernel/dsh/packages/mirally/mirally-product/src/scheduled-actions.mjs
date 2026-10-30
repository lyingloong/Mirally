/** Product-owned scheduled actions. Plain reminders remain owned by commitments. */
import {createHash, randomUUID} from 'node:crypto';
import {existsSync, readFileSync, renameSync, unlinkSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {createAtScheduleRecord, createAfterScheduleRecord, createDailyScheduleRecord,
  createEveryScheduleRecord, createWeeklyScheduleRecord, createCronScheduleRecord,
  decodeScheduleRecord, isRecurringScheduleRecord, resolveRecurringOccurrence}
  from '@deepseek-ai/dsh-schedule';

const fail = (message, code = 'invalid_schedule') => Object.assign(new Error(message), {code, status:400});
const clone = value => structuredClone(value);
const MAX_JOBS = 1000;
const MAX_HISTORY = 50;
const selectors = ['at', 'after_seconds', 'every_seconds', 'daily', 'weekly', 'cron'];

// Legacy jobs predate provenance and are desktop-owned. Remote provenance is
// never inferred from a conversation, its name, or a caller-provided prompt.
function scheduleAuthority(value) {
  if (value === undefined || value === null) return {origin:'desktop'};
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw fail('定时任务来源无效。', 'schedule_store_invalid');
  if (value.origin === 'desktop' && Object.keys(value).length === 1) return {origin:'desktop'};
  if (value.origin === 'weixin' && Object.keys(value).length === 3
    && typeof value.installation_id === 'string' && /^[a-f0-9]{32}$/.test(value.installation_id)
    && typeof value.source_conversation_id === 'string' && value.source_conversation_id.length > 0
    && value.source_conversation_id.length <= 512)
    return {origin:'weixin', installation_id:value.installation_id, source_conversation_id:value.source_conversation_id};
  throw fail('定时任务来源无效。', 'schedule_store_invalid');
}

function boundedText(value, label, max) {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw fail(`${label}不能为空且不能超过 ${max} 个字符。`);
  return value.trim();
}

/** DSH calculates the wall-clock/recurrence rule; it does not own action execution. */
export function scheduleRecord(id, input, now, timeZone) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw fail('定时任务参数无效。');
  const title = boundedText(input.title, '名称', 120);
  const prompt = boundedText(input.prompt, '任务内容', 12000);
  const supplied = selectors.filter(key => input[key] !== undefined);
  if (supplied.length !== 1) throw fail('请选择一种执行时间。');
  const selected = supplied[0], arg = input[selected];
  if (selected === 'at') return createAtScheduleRecord(id, prompt, arg, now, title);
  if (selected === 'after_seconds') return createAfterScheduleRecord(id, prompt, arg, now, title);
  if (selected === 'every_seconds') return createEveryScheduleRecord(id, prompt, arg, now, title);
  if (!timeZone) throw fail('请先在设置中确认时区。', 'timezone_unconfirmed');
  if (selected === 'daily') return createDailyScheduleRecord(id, prompt, {time:arg, time_zone:timeZone}, now, title);
  if (selected === 'weekly') return createWeeklyScheduleRecord(id, prompt, {...arg, time_zone:timeZone}, now, title);
  return createCronScheduleRecord(id, prompt, {expression:arg, time_zone:timeZone}, now, title);
}

/** Only one running Mirally host owns this file. Writes precede observable success. */
export class ScheduledActions {
  constructor(host, {now = () => Date.now(), file = join(host.home, 'scheduled-actions.json')} = {}) {
    this.host = host; this.now = now; this.file = file; this.chain = Promise.resolve(); this.closed = false;
    this.state = this.read();
    let changed = false;
    for (const job of this.state.jobs) {
      if (job.status === 'dispatching') {
        // Never replay an action after a crash while an external effect may have happened.
        job.status = 'unknown'; job.last_error = '上次启动时执行结果未确认，请检查原会话。'; changed = true;
      }
    }
    if (changed) this.save(this.state);
  }

  read() {
    if (!existsSync(this.file)) return {version:1, jobs:[]};
    let value;
    try { value = JSON.parse(readFileSync(this.file, 'utf8')); }
    catch { throw fail('定时任务记录无法读取，请先检查数据文件。', 'schedule_store_invalid'); }
    if (value?.version !== 1 || !Array.isArray(value.jobs) || value.jobs.length > MAX_JOBS
      || new Set(value.jobs.map(job => job.id)).size !== value.jobs.length) throw fail('定时任务记录无效。', 'schedule_store_invalid');
    for (const job of value.jobs) {
      if (!job || typeof job.id !== 'string' || !job.id || typeof job.conversation_id !== 'string' || !job.conversation_id
        || !Number.isSafeInteger(job.generation) || job.generation < 1
        || !['active','paused','dispatching','unknown','dispatched','cancelled'].includes(job.status)
        || !Array.isArray(job.history) || job.history.length > MAX_HISTORY || job.record?.id !== job.id)
        throw fail('定时任务记录无效。', 'schedule_store_invalid');
      try { decodeScheduleRecord(job.record); } catch { throw fail('定时任务时间规则无效。', 'schedule_store_invalid'); }
      scheduleAuthority(job.authority);
    }
    return value;
  }

  save(value) {
    const temporary = `${this.file}.${randomUUID()}.tmp`;
    try { writeFileSync(temporary, JSON.stringify(value), {mode:0o600}); renameSync(temporary, this.file); }
    catch (error) { try { if (existsSync(temporary)) unlinkSync(temporary); } catch {} throw error; }
    this.state = value;
  }

  serialize(action) {
    if (this.closed) return Promise.reject(fail('定时任务服务已关闭。', 'schedule_unavailable'));
    const run = this.chain.then(action);
    this.chain = run.catch(() => {});
    return run;
  }

  async zone() {
    const value = await this.host.rpc('calendar.settings.get');
    return value?.confirmed ? value.time_zone : null;
  }

  async recordForInput(id, input) {
    const zone = await this.zone();
    if (!zone) throw fail('请先在设置中确认时区。', 'timezone_unconfirmed');
    let normalized = input;
    if (input.at !== undefined) {
      if (typeof input.at !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(input.at))
        throw fail('请按设置时区提供 YYYY-MM-DDTHH:mm 格式的执行时间。');
      const resolved = await this.host.rpc('calendar.time.resolve', {local_time:input.at});
      if (resolved.time_zone !== zone || typeof resolved.iso !== 'string') throw fail('时区已变化，请重新设置。', 'timezone_changed');
      normalized = {...input, at:resolved.iso};
    }
    return scheduleRecord(id, normalized, this.now(), zone);
  }

  create({conversation_id, generation, input, request_key, authority, authorize = async () => {}}) {
    return this.serialize(async () => {
      if (typeof conversation_id !== 'string' || !conversation_id || !Number.isSafeInteger(generation) || generation < 1)
        throw fail('会话不可用。');
      const key = boundedText(request_key, '请求标识', 300);
      const provenance = scheduleAuthority(authority);
      // Keep the old digest for desktop jobs so existing idempotent retries
      // remain compatible after upgrading the store.
      const digest = createHash('sha256').update(JSON.stringify(provenance.origin === 'desktop'
        ? [conversation_id, generation, input] : [conversation_id, generation, input, provenance])).digest('hex');
      const prior = this.state.jobs.find(job => job.request_key === key);
      if (prior) {
        if (prior.digest !== digest) throw fail('此请求已用于其他定时任务。', 'schedule_conflict');
        return clone(prior);
      }
      if (this.state.jobs.length >= MAX_JOBS) throw fail('定时任务数量已达上限。');
      const id = `schedule_${randomUUID()}`;
      const record = await this.recordForInput(id, input);
      await authorize();
      const job = {id, conversation_id, generation, request_key:key, digest, record, status:'active', created_at:this.now(), history:[],
        ...(provenance.origin === 'weixin' ? {authority:provenance} : {})};
      const next = clone(this.state); next.jobs.push(job); this.save(next); this.changed(job);
      return clone(job);
    });
  }

  list(conversation_id) {
    return clone(this.state.jobs.filter(job => !conversation_id || job.conversation_id === conversation_id));
  }

  async listObserved(conversation_id) {
    const jobs = this.list(conversation_id);
    for (const job of jobs) for (const entry of job.history) {
      try {
        const result = await this.host.rpc('command.list', {conversation_id:job.conversation_id, command_id:entry.command_id});
        const command = result.commands?.find(item => item.command_id === entry.command_id);
        // This is the real command receipt, not a claim that its goal succeeded.
        entry.command_state = command?.state ?? 'unknown';
      } catch { entry.command_state = 'unknown'; }
    }
    return jobs;
  }

  /** Called only by the host's internal source gate immediately before send. */
  ownsDispatch(id, conversation_id, command_id) {
    const job = this.state.jobs.find(item => item.id === id);
    return Boolean(job && job.status === 'dispatching' && job.conversation_id === conversation_id
      && job.pending?.command_id === command_id);
  }

  /** Recheck the stable binding and current user policy, never a stored token. */
  async authorizeWeixinOccurrence(id, conversationId, commandId) {
    const job = this.state.jobs.find(item => item.id === id && item.conversation_id === conversationId);
    const authority = scheduleAuthority(job?.authority);
    const pending = job?.status === 'dispatching' && job.pending?.command_id === commandId;
    const accepted = !['cancelled','unknown'].includes(job?.status) && job?.history?.some(entry => entry.command_id === commandId && entry.status === 'accepted');
    if (!job || authority.origin !== 'weixin' || !(pending || accepted))
      throw fail('定时微信任务已变化。', 'schedule_scope_revoked');
    const {state} = await this.host.rpc('channel.weixin.get');
    if (!state?.bound || !state.enabled || !state.token || state.installation_id !== authority.installation_id
      || !Number.isSafeInteger(state.revision) || typeof state.conversation_id !== 'string' || !state.conversation_id)
      throw fail('微信连接已关闭，定时任务已暂停。', 'schedule_scope_revoked');
    const conversation = await this.host.conversation(conversationId);
    if (conversation.archived || conversation.frozen || conversation.generation !== job.generation)
      throw fail('定时任务所属会话已变化。', 'schedule_scope_revoked');
    const latest = this.state.jobs.find(item => item.id === id && item.conversation_id === conversationId);
    const latestAuthority = scheduleAuthority(latest?.authority);
    const stillPending = latest?.status === 'dispatching' && latest.pending?.command_id === commandId;
    const stillAccepted = !['cancelled','unknown'].includes(latest?.status) && latest?.history?.some(entry => entry.command_id === commandId && entry.status === 'accepted');
    if (!latest || latestAuthority.origin !== 'weixin' || latestAuthority.installation_id !== authority.installation_id
      || latestAuthority.source_conversation_id !== authority.source_conversation_id || !(stillPending || stillAccepted))
      throw fail('定时微信任务已变化。', 'schedule_scope_revoked');
    // Return only the current route identity. Credentials remain owned by the channel.
    return {installation_id:state.installation_id,revision:state.revision,conversation_id:state.conversation_id};
  }

  change(conversation_id, id, operation, authorize = async () => {}, authority) {
    return this.serialize(async () => {
      const next = clone(this.state), job = next.jobs.find(item => item.id === id && item.conversation_id === conversation_id);
      if (!job) throw fail('找不到此会话的定时任务。', 'schedule_not_found');
      const actor = scheduleAuthority(authority);
      if (operation === 'pause' && job.status === 'active') job.status = 'paused';
      else if (operation === 'resume' && job.status === 'paused') {
        job.status = 'active';
        // A remote resume is a fresh remote grant, never a desktop grant.
        if (actor.origin === 'weixin') job.authority = actor;
      }
      else if (operation === 'cancel' && !['dispatching','cancelled'].includes(job.status)) job.status = 'cancelled';
      else throw fail('当前状态无法执行此操作。', 'schedule_conflict');
      await authorize();
      this.save(next); this.changed(job); return clone(job);
    });
  }

  update(conversation_id, id, input, authorize = async () => {}, authority) {
    return this.serialize(async () => {
      const next = clone(this.state), job = next.jobs.find(item => item.id === id && item.conversation_id === conversation_id);
      if (!job) throw fail('找不到此会话的定时任务。', 'schedule_not_found');
      if (!['active','paused'].includes(job.status)) throw fail('此任务已结束或执行结果未知，请先检查原会话。', 'schedule_conflict');
      const actor = scheduleAuthority(authority);
      job.record = await this.recordForInput(id, input);
      await authorize();
      if (actor.origin === 'weixin') job.authority = actor;
      job.updated_at = this.now(); this.save(next); this.changed(job); return clone(job);
    });
  }

  changed(job) { this.host.broadcast?.('schedule', {conversation_id:job.conversation_id, schedule_id:job.id, status:job.status}); }

  async notifyFinished() {
    for (const snapshot of this.state.jobs) for (const entry of snapshot.history) {
      if (entry.status !== 'accepted' || entry.notification_created) continue;
      try {
        const {commands} = await this.host.rpc('command.list', {conversation_id:snapshot.conversation_id,command_id:entry.command_id});
        if (commands?.find(command=>command.command_id === entry.command_id)?.state !== 'completed') continue;
        const conversation = await this.host.conversation(snapshot.conversation_id);
        if (conversation.frozen || conversation.archived || conversation.generation !== snapshot.generation) continue;
        // This means the conversation turn ended, not that its goal succeeded.
        // Stable content and time make a crash between persistence and marking safe.
        await this.host.rpc('commitment.create',{title:`定时任务「${snapshot.record.title}」的回复已结束`,
          body:'打开原会话查看结果。',due_at_utc_ms:entry.dispatched_at,conversation_id:snapshot.conversation_id,
          request_id:`scheduled-result:${entry.command_id}`});
        const next=clone(this.state), job=next.jobs.find(item=>item.id===snapshot.id);
        const saved=job?.history.find(item=>item.command_id===entry.command_id);
        if (saved) { saved.notification_created=true; this.save(next); }
      } catch (error) {
        this.host.ctx?.logger?.warn?.(`Scheduled result notification: ${error.message}`);
      }
    }
  }

  tick() {
    if (this.ticking) return this.ticking;
    const pending = this.serialize(async () => {
      await this.notifyFinished();
      if (!this.state.jobs.some(job => job.status === 'active')) return;
      const zone = await this.zone();
      // Without a confirmed product timezone we cannot safely interpret
      // wall-clock rules; leave them untouched until the user confirms it.
      if (!zone) return;
      const revised = clone(this.state); let changed = false;
      for (const job of revised.jobs) {
        const rule = job.record;
        if (job.status !== 'active' || !['daily','weekly','cron'].includes(rule.kind) || rule.timeZone === zone) continue;
        const input = {title:rule.title,prompt:rule.prompt,
          ...(rule.kind === 'daily' ? {daily:rule.time}
            : rule.kind === 'weekly' ? {weekly:{time:rule.time,weekdays:rule.weekdays}} : {cron:rule.expression})};
        job.record = scheduleRecord(job.id,input,this.now(),zone); job.time_zone_updated_at = this.now(); changed = true;
      }
      if (changed) this.save(revised);
      const due = this.state.jobs.filter(job => job.status === 'active' && Date.parse(job.record.scheduledAt) <= this.now());
      for (const item of due) {
        if (this.closed) break;
        let state = clone(this.state), job = state.jobs.find(value => value.id === item.id);
        if (!job || job.status !== 'active') continue;
        const now = this.now();
        if (Date.parse(job.record.scheduledAt) > now) continue;
        const occurrence = isRecurringScheduleRecord(job.record)
          ? resolveRecurringOccurrence(job.record, now) : {occurrenceAt:job.record.scheduledAt};
        const commandId = `schedule-${createHash('sha256').update(`${job.id}:${occurrence.occurrenceAt}`).digest('hex').slice(0, 40)}`;
        job.status = 'dispatching'; job.pending = {command_id:commandId, occurrence_at:occurrence.occurrenceAt};
        this.save(state); this.changed(job);
        let result, error, dispatchStarted = false;
        try {
          const conversation = await this.host.conversation(job.conversation_id);
          if (conversation.archived || conversation.frozen || conversation.generation !== job.generation)
            throw fail('会话已归档、停止或发生变化，请检查任务。', 'schedule_conversation_changed');
          const request = {commandId, text:`定时任务「${job.record.title}」到期。请执行用户事先安排的任务：\n${job.record.prompt}`};
          if (scheduleAuthority(job.authority).origin === 'weixin') {
            await this.authorizeWeixinOccurrence(job.id,job.conversation_id,commandId);
            if (typeof this.host.sendWeixinScheduled !== 'function') throw fail('微信定时执行尚未接入，请在电脑中设置。','schedule_remote_unavailable');
            dispatchStarted = true;
            result = await this.host.sendWeixinScheduled(job.conversation_id,request,job.id);
          } else { dispatchStarted = true; result = await this.host.sendScheduled(job.conversation_id,request,job.id); }
          if (!result?.accepted) throw fail('原会话没有确认接收任务。', 'schedule_admission_unknown');
        } catch (caught) { error = caught; }
        state = clone(this.state); job = state.jobs.find(value => value.id === item.id);
        if (!job || job.status !== 'dispatching' || job.pending?.command_id !== commandId) continue;
        // Explicit pre-dispatch revocation cannot have executed an action; keep
        // it paused instead of presenting an unconfirmed execution outcome.
        const revoked = !dispatchStarted && error && ['schedule_scope_revoked','schedule_remote_unavailable'].includes(error.code);
        if (!revoked) job.history.push({command_id:commandId, occurrence_at:occurrence.occurrenceAt, dispatched_at:this.now(), status:error?'unknown':'accepted'});
        job.history = job.history.slice(-MAX_HISTORY); delete job.pending;
        if (revoked) { job.status = 'paused'; job.last_error = String(error.message ?? error).slice(0, 1000); }
        else if (error) { job.status = 'unknown'; job.last_error = String(error.message ?? error).slice(0, 1000); }
        else if (occurrence.nextScheduledAt) { job.record.scheduledAt = occurrence.nextScheduledAt; job.status = 'active'; delete job.last_error; }
        else { job.status = 'dispatched'; delete job.last_error; }
        this.save(state); this.changed(job);
      }
    });
    this.ticking = pending.finally(() => { this.ticking = undefined; });
    return this.ticking;
  }

  async dispose() { this.closed = true; await this.chain; }
}

const timeProperties = {
  at:{type:'string',description:'Future local datetime YYYY-MM-DDTHH:mm in the user\'s confirmed timezone.'},
  after_seconds:{type:'integer',minimum:1}, every_seconds:{type:'integer',minimum:60},
  daily:{type:'string',description:'Local time HH:mm:ss in the user\'s confirmed timezone.'},
  weekly:{type:'object',properties:{time:{type:'string'},weekdays:{type:'array',items:{type:'integer',minimum:1,maximum:7}}},required:['time','weekdays'],additionalProperties:false},
  cron:{type:'string',description:'Five-field cron expression in the confirmed timezone.'},
};
const render = {schema:{type:'object',additionalProperties:true}, render:(_args,result)=>[{type:'text',text:JSON.stringify(result)}]};

/** Tools operate on this conversation only and cannot grant a future action permission. */
export function installScheduledActionTools(host, schedules) {
  const context = async (exec, name, write = false) => {
    exec.signal.throwIfAborted();
    const owner = host.nativeRuntime.owner(exec.agent);
    if (!owner || owner.rootSessionId !== exec.agent.id || !owner.commandId) throw fail('请在主会话中管理定时任务。', 'schedule_scope');
    const policy = host.commandPolicy(exec.agent);
    if (write && policy?.origin !== 'desktop' && policy?.origin !== 'weixin') throw fail('当前来源不能设置定时执行。', 'schedule_remote_unavailable');
    if (write && policy?.schedule_id) throw fail('定时回合不能自行修改定时任务。','schedule_scope');
    await host.checkCommandScope(exec.agent, name);
    await host.guard(exec.agent.id);
    await host.checkCommandScope(exec.agent, name);
    exec.signal.throwIfAborted();
    if (host.commandPolicy(exec.agent) !== policy || host.consumedCommandId(exec.agent) !== owner.commandId)
      throw fail('会话或授权已变化，请重新操作。', 'schedule_scope');
    const authority = policy?.origin === 'weixin' ? scheduleAuthority({origin:'weixin',installation_id:policy.installation_id,
      source_conversation_id:policy.conversation_id}) : {origin:'desktop'};
    return {...owner, schedule_authority:authority};
  };
  host.ctx.tools.register({name:'mirally_schedule_action',
    description:'Only when the user explicitly requests future action in this conversation, schedule an agent task. For a simple reminder use the reminder/calendar tool instead. A scheduled action still requires the normal permissions. Supply exactly one time selector and never claim the task has executed.',
    parameters:{type:'object',properties:{title:{type:'string'},prompt:{type:'string'},...timeProperties},required:['title','prompt'],additionalProperties:false}, output:render,
    execute:async (input,exec)=>{const owner=await context(exec,'mirally_schedule_action',true);
      const digest=createHash('sha256').update(JSON.stringify(input)).digest('hex');
      return {schedule:await schedules.create({conversation_id:owner.conversation.conversation_id,generation:owner.conversation.generation,
        input,authority:owner.schedule_authority,request_key:`${owner.commandId}:scheduled-action:${digest}`,
        authorize:async()=>{const current=await context(exec,'mirally_schedule_action',true);
          if (current.commandId !== owner.commandId || current.conversation.generation !== owner.conversation.generation
            || JSON.stringify(current.schedule_authority)!==JSON.stringify(owner.schedule_authority)) throw fail('会话已变化。','schedule_scope');}})};}});
  host.ctx.tools.register({name:'mirally_scheduled_actions',description:'List scheduled agent actions in this conversation. A dispatched action is accepted for execution, not necessarily completed.',
    parameters:{type:'object',properties:{},additionalProperties:false},output:render,
    execute:async (_input,exec)=>{const owner=await context(exec,'mirally_scheduled_actions');return {schedules:await schedules.listObserved(owner.conversation.conversation_id)};}});
  host.ctx.tools.register({name:'mirally_manage_scheduled_action',description:'Pause, resume, or cancel a scheduled action in this conversation. Unknown execution outcomes must be checked in the conversation; they cannot be automatically replayed.',
    parameters:{type:'object',properties:{schedule_id:{type:'string'},operation:{type:'string',enum:['pause','resume','cancel']}},required:['schedule_id','operation'],additionalProperties:false},output:render,
    execute:async (input,exec)=>{const owner=await context(exec,'mirally_manage_scheduled_action',true);
      return {schedule:await schedules.change(owner.conversation.conversation_id,input.schedule_id,input.operation,async()=>{
        const current=await context(exec,'mirally_manage_scheduled_action',true);
        if (current.commandId !== owner.commandId || current.conversation.generation !== owner.conversation.generation
          || JSON.stringify(current.schedule_authority)!==JSON.stringify(owner.schedule_authority)) throw fail('会话已变化。','schedule_scope');},owner.schedule_authority)};}});
  host.ctx.tools.register({name:'mirally_update_scheduled_action',description:'Replace the title, task and timing of an active or paused scheduled action in this conversation. First list the task and preserve any details the user did not change. Exactly one time selector is required.',
    parameters:{type:'object',properties:{schedule_id:{type:'string'},title:{type:'string'},prompt:{type:'string'},...timeProperties},required:['schedule_id','title','prompt'],additionalProperties:false},output:render,
    execute:async (args,exec)=>{const owner=await context(exec,'mirally_update_scheduled_action',true);const {schedule_id,...input}=args;
      return {schedule:await schedules.update(owner.conversation.conversation_id,schedule_id,input,async()=>{
        const current=await context(exec,'mirally_update_scheduled_action',true);
        if (current.commandId !== owner.commandId || current.conversation.generation !== owner.conversation.generation
          || JSON.stringify(current.schedule_authority)!==JSON.stringify(owner.schedule_authority)) throw fail('会话已变化。','schedule_scope');},owner.schedule_authority)};}});
}
