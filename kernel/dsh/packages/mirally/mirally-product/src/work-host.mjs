/** The authenticated product control plane. Models never approve or launch runs. */
import {createHash} from 'node:crypto';

export const WORK_METHODS = new Set(['work.create', 'work.list', 'work.get', 'work.update', 'work.observe', 'work.by_conversation',
  'work.run.prepare', 'work.run.approve', 'work.run.start', 'work.run.cancel', 'work.run.get', 'work.run.logs', 'work.run.artifact',
  'work.settings.get', 'work.settings.update']);

const denied = message => Object.assign(new Error(message), {code: 'work_scope_revoked', status: 403});
export class WorkHost {
  constructor(product) { this.product = product; }

  async actor(principal) {
    if (!principal) return 'desktop';
    if (principal.origin === 'weixin') {
      await this.product.weixin.authorize(principal);
      return `weixin:${principal.installation_id}:${principal.revision}`;
    }
    if (principal.origin === 'mobile' && this.product.mobile?.authorize(principal))
      return `mobile:${principal.id}:${principal.revision}`;
    throw denied('当前远程身份已失效，请重新连接。');
  }

  async request(method, params = {}, principal = null) {
    if (!WORK_METHODS.has(method)) throw denied('这项工作操作尚未开放。');
    const actor = await this.actor(principal);
    // Never accept identity, approval authority or an actor from JSON input.
    const input = {...params};
    delete input.principal;
    delete input.actor;
    if (method === 'work.create' && !input.conversation_id) {
      // A stable source request has one primary conversation even if its reply
      // was lost. This is not another transcript or reasoning loop.
      const requestId = input.request_id;
      const conversationId = requestId ? `conversation_work_${createHash('sha256').update(`${actor}:${requestId}`).digest('hex').slice(0, 32)}` : undefined;
      const conversation = await this.product.rpc('conversation.ensure', {
        ...(conversationId ? {conversation_id: conversationId} : {}), title: input.title});
      input.conversation_id = conversation.conversation_id;
    }
    if (['work.create', 'work.run.prepare', 'work.run.start'].includes(method) && input.request_id)
      input.request_id = `work-request-${createHash('sha256').update(`${actor}:${input.request_id}`).digest('hex')}`;
    if (['work.run.prepare', 'work.run.approve', 'work.run.start'].includes(method)) input.actor = actor;
    await this.actor(principal);
    let result = await this.product.rpc(method, input);
    if (method === 'work.list' || method === 'work.get' || method === 'work.by_conversation') {
      result = await this.withConversationActivity(result, method);
      // A remote binding may be revoked while the owner is being read.
      await this.actor(principal);
    }
    if (!['work.list', 'work.get', 'work.by_conversation', 'work.run.get', 'work.run.logs', 'work.run.artifact', 'work.settings.get'].includes(method))
      this.product.broadcast('work', {method, work_id: result.work?.work_id ?? result.work?.id ?? result.run?.work_id ?? input.work_id,
        ...(result.work?.conversation_id ? {conversation_id: result.work.conversation_id} : {})});
    if (result.conversation_title_changed && result.conversation)
      this.product.broadcast('session-metadata', {session_id: result.conversation.conversation_id, metadata_version: result.conversation.metadata_version});
    return result;
  }

  async withConversationActivity(result, method) {
    const works = method === 'work.list' || method === 'work.by_conversation' ? result.works : [result.work];
    if (!Array.isArray(works)) return result;
    const ids = [...new Set(works.map(work => work?.conversation_id).filter(id => typeof id === 'string' && id))];
    const activity = new Map();
    // One bounded owner query avoids a cross-process request per work card.
    try {
      if (ids.length > 200) throw new Error('conversation activity limit exceeded');
      const batch = ids.length ? await this.product.rpc('internal.conversation.activity_batch', {conversation_ids: ids}) : {conversations: []};
      if (!Array.isArray(batch.conversations) || !this.product.handles || !this.product.activeCommands)
        throw new Error('conversation activity unavailable');
      for (const conversation of batch.conversations) {
          const id = conversation?.conversation_id;
          if (!ids.includes(id) || typeof conversation.frozen !== 'boolean' || typeof conversation.archived !== 'boolean'
            || !(conversation.session_id === null || typeof conversation.session_id === 'string')) continue;
          const session = conversation.session_id;
          const handle = session && this.product.handles.get(session);
          const command = session && this.product.activeCommands.get(session);
          let state = 'unknown';
          if (conversation.frozen || conversation.archived || !session) state = 'idle';
          else if (!handle && !command) state = 'idle';
          else if (handle?.agent?.status === 'running' && command) state = 'running';
          else if (handle?.agent?.status === 'idle' && !command) state = 'idle';
          activity.set(id, state);
      }
    } catch { /* A failed owner read remains unknown, never an inferred idle. */ }
    const decorate = work => work && ({...work, conversation_activity: activity.get(work.conversation_id) ?? 'unknown'});
    return method === 'work.list' || method === 'work.by_conversation' ? {...result, works: works.map(decorate)} : {...result, work: decorate(result.work)};
  }

  async inbox() {
    const [inbox, work] = await Promise.all([this.product.rpc('inbox.list', {}),
      this.product.rpc('work.notifications', {channel: 'inbox', limit: 100})]);
    const items = (work.events ?? []).map(event => ({delivery_id: `work:${event.event_id}`, kind: 'work', state: 'unread',
      title: event.title, body: event.text, updated_at: event.created_at,
      conversation_id: event.work?.conversation_id, work_id: event.work_id}));
    return {...inbox, items: [...items, ...(inbox.items ?? [])].sort((a, b) => Number(b.updated_at) - Number(a.updated_at))};
  }

  async ackInbox(params) {
    if (!String(params.delivery_id ?? '').startsWith('work:')) return this.product.rpc('inbox.ack', params);
    if (!['read', 'dismissed', 'replied'].includes(params.state)) throw denied('不支持的工作通知处理。');
    await this.product.rpc('work.notifications.ack', {event_id: params.delivery_id.slice(5), channel: 'inbox',
      status: params.state === 'dismissed' ? 'suppressed' : 'sent'});
    return {delivery_id: params.delivery_id, state: params.state};
  }

  async tick() {
    if (this.ticking) return;
    this.ticking = true;
    try {
      if (Date.now() - (this.lastObserve ?? 0) >= 30_000) {
        this.lastObserve = Date.now();
        const offset = this.observeOffset ?? 0;
        const page = await this.product.rpc('work.list', {limit: 200, offset});
        let scanned = 0, observed = 0;
        for (const work of page.works) {
          scanned++;
          if (work.intent !== 'watching' || !work.source) continue;
          observed++;
          try {
            const result = await this.product.rpc('work.observe', {work_id: work.work_id ?? work.id});
            this.product.broadcast('work', {method: 'work.observe', work_id: work.work_id ?? work.id, observed_at: result.observation?.observed_at});
          } catch (error) { this.product.ctx?.logger?.warn(`Work observation: ${error.code ?? 'unavailable'}`); }
          if (observed >= 20) break;
        }
        this.observeOffset = scanned < page.works.length ? offset + scanned : (page.has_more ? page.next_offset : 0);
      }
      await this.product.weixin?.notifyWorks();
    }
    finally { this.ticking = false; }
  }
}

export const shortWorkId = work => String(work.work_id ?? work.id).replace(/^work_/, '').slice(0, 12);
const STATES = {running: '正在运行', queued: '等待启动', pending: '等待处理', awaiting_approval: '等待确认',
  approval_required: '等待确认', needs_approval: '等待确认', timed_out: '执行超时', cancelling: '正在停止', cancel_requested: '正在停止', cancelled: '已停止',
  completed: '执行完成', succeeded: '执行完成', failed: '失败', unknown: '待核对', observing: '正在关注', watching: '正在关注',
  ended: '已结束关注', archived: '已归档', idle: '等待安排'};
export function workSummary(work, {detail = false, observation, runs = []} = {}) {
  const state = work.status ?? work.state ?? work.intent ?? 'idle';
  const lines = [`${shortWorkId(work)}  ${work.title}`, STATES[state] ?? state];
  const last = work.observation ?? observation;
  const text = last?.summary ?? work.summary ?? work.latest_summary;
  if (text) lines.push(work.source?.kind === 'log' ? String(text).slice(-700) : String(text).slice(0, 700));
  const progress = last?.progress;
  if (Number.isFinite(progress?.current) && Number.isFinite(progress?.total) && progress.total > 0)
    lines.push(`来源进度：${progress.current}/${progress.total}`);
  const observed = last?.observed_at ?? last?.created_at ?? work.updated_at;
  if (observed) lines.push(`更新时间：${typeof observed === 'number' ? new Date(observed).toISOString() : observed}`);
  if (detail) {
    if (work.goal) lines.push(`目标：${work.goal}`);
    const run = runs[0] ?? work.latest_run;
    if (run) lines.push(`最近运行：${run.run_id ?? run.id}（${STATES[run.state ?? run.status] ?? run.state ?? run.status}）`);
    if (last?.gaps?.length) lines.push(`信息缺口：${last.gaps.join('；')}`);
    if (last?.metrics && typeof last.metrics === 'object') {
      const metrics = Object.entries(last.metrics).slice(0, 6).map(([key, value]) => `${key}: ${String(value).slice(0, 120)}`);
      if (metrics.length) lines.push(`来源指标：${metrics.join('；')}`);
    }
    if (last?.truncated) lines.push('日志已截断，以上仅为末尾摘录。');
    lines.push(`/work ${shortWorkId(work)} 查看；/work-use ${shortWorkId(work)} 继续讨论。`);
    if (run) lines.push('停止当前回复用 /stop；停止真实运行需 /work-cancel 运行编号。');
    else if (work.source) lines.push('这项工作仅观察外部状态，没有启动或停止外部进程的权限。');
  }
  return lines.filter(Boolean).join('\n');
}
