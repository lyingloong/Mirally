/** Adapt native DSH approval/question seams to the existing Mirally conversation transport. */
import {randomUUID, randomBytes} from 'node:crypto';

export const NATIVE_INTERACTION_METHODS = new Set([
  'interaction.questions.list', 'interaction.questions.answer', 'interaction.plan.get', 'interaction.plan.set',
]);

const failure = (message, code = 'interaction_invalid', status = 400) => Object.assign(new Error(message), {code, status});
const aborted = () => Object.assign(failure('问题所属回合已结束，请在会话中继续。', 'ASK_ABORTED'), {name:'UserQuestionError'});

function owner(host, agent) {
  try { return host.nativeRuntime?.owner(agent) ?? null; } catch { return null; }
}

function sameOwner(host, entry) {
  const live = owner(host, entry.agent);
  return live && live.rootSessionId === entry.scope.rootSessionId && live.commandId === entry.scope.commandId
    && live.conversation.conversation_id === entry.scope.conversation.conversation_id
    && host.handles.get(live.rootSessionId)?.agent === entry.rootAgent;
}

/** Show the complete logged operation; an incomplete request must never be approved. */
export function nativeApprovalDetail(request) {
  if (!request.agent || typeof request.callId !== 'string') return null;
  const events = request.agent.session?.snapshotEvents?.() ?? [];
  const call = events.findLast(event => event.type === 'tool/call' && event.data.callId === request.callId);
  if (!call || call.data.name !== request.toolName || typeof call.data.arguments !== 'string') return null;
  const detail = `工具：${request.toolName}\n完整参数：\n${call.data.arguments}\n\n申请原因：${request.reason ?? '此操作需要额外权限。'}`;
  return detail.length <= 24_000 ? detail : null;
}

function validateQuestions(questions) {
  if (!Array.isArray(questions) || !questions.length || questions.length > 16
    || new Set(questions.map(item => item.id)).size !== questions.length
    || questions.some(item => typeof item.id !== 'string' || !item.id || typeof item.question !== 'string' || !item.question.trim())
    || JSON.stringify(questions).length > 256_000) throw failure('问题无法完整展示，请缩短后重新询问。');
  // These are presentation data, not live Agent/Session objects. Keep a detached
  // snapshot so the answer validates against what the human actually received.
  return structuredClone(questions);
}

export function validateNativeAnswers(questions, answers) {
  if (!Array.isArray(answers) || answers.length !== questions.length
    || new Set(answers.map(item => item?.id)).size !== answers.length) throw failure('请逐项回答所有问题。');
  for (const question of questions) {
    const answer = answers.find(item => item?.id === question.id);
    const labels = new Set((question.options ?? []).map(item => item.label));
    if (!answer || !Array.isArray(answer.selected) || new Set(answer.selected).size !== answer.selected.length
      || answer.selected.some(label => typeof label !== 'string' || !labels.has(label))
      || (!question.multiSelect && answer.selected.length > 1)
      || (answer.custom !== undefined && (typeof answer.custom !== 'string' || answer.custom.length > 24_000))
      || (!answer.selected.length && !answer.custom?.trim())) throw failure('请选择提供的选项，或填写回答。');
  }
  if (JSON.stringify(answers).length > 128_000) throw failure('回答过长，请缩短后重试。');
  return {answers:answers.map(({id, selected, custom}) => ({id, selected:[...selected], ...(custom?.trim() ? {custom} : {})}))};
}

export function installNativeInteractions(host) {
  const pending = new Map();
  let closed = false;
  const changed = conversation_id => host.broadcast('interaction', {action:'questions-changed', conversation_id});

  const disposers = [host.ctx.on('approval/request', async (request, next) => {
    const scope = owner(host, request.agent);
    if (!scope) return next();
    const detail = nativeApprovalDetail(request);
    if (!detail) return 'unavailable';
    const rootAgent = host.handles.get(scope.rootSessionId)?.agent;
    if (!rootAgent || !sameOwner(host, {agent:request.agent, rootAgent, scope})) return 'cancelled';
    // The native service has already applied the session's ask/never policy.
    // A grant here affects only this request; sandbox mode is never changed.
    const allowed = await host.actionGate.request({sessionId:scope.rootSessionId, commandId:scope.commandId,
      kind:'shell', title:`允许 DSH 工具 ${request.toolName} 的本次权限申请`, detail, signal:request.signal});
    if (!sameOwner(host, {agent:request.agent, rootAgent, scope})) return 'cancelled';
    return allowed ? 'allowed-once' : 'rejected';
  }, {global:true}), host.ctx.on('user-questions/request', async (request, next) => {
    const scope = owner(host, request.agent);
    if (!scope) return next();
    request.signal?.throwIfAborted();
    if (closed) throw aborted();
    const rootAgent = host.handles.get(scope.rootSessionId)?.agent;
    if (!rootAgent || !sameOwner(host, {agent:request.agent, rootAgent, scope})) throw aborted();
    await host.guard(scope.rootSessionId);
    if (!sameOwner(host, {agent:request.agent, rootAgent, scope})) throw aborted();
    if (pending.size >= 32) throw failure('已有较多问题等待回答，请先处理。');
    const questions = validateQuestions(request.questions), id = randomUUID();
    const value = {id, conversation_id:scope.conversation.conversation_id, session_id:scope.rootSessionId,
      command_id:scope.commandId, questions, state:'open', can_reply:true, created_at:Date.now(),
      ...(request.wait?.callId ? {call_id:request.wait.callId} : {})};
    const policy=host.commandPolicies?.get(scope.commandId);
    let remoteScope=policy?.origin==='weixin' ? policy : null, fromDesktop=false;
    // A question originating on desktop may also be answered by the paired
    // phone when that same conversation is selected.
    if (!remoteScope && policy?.origin==='desktop' && host.weixin?.state && host.rpc) {
      try {
        const state=await host.weixin.state();
        if (state.bound && state.enabled
          && state.conversation_id===value.conversation_id && state.context_token && sameOwner(host,{agent:request.agent,rootAgent,scope})) {
          remoteScope={installation_id:state.installation_id,revision:state.revision,conversation_id:state.conversation_id,command_id:scope.commandId};
          await host.weixin.authorize(remoteScope);fromDesktop=true;
        }
      } catch { remoteScope=null; }
    }
    request.signal?.throwIfAborted();
    if (!sameOwner(host,{agent:request.agent,rootAgent,scope})) throw aborted();
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (answer, error) => {
        if (settled) return;
        settled = true; request.signal?.removeEventListener('abort', cancel);
        pending.delete(id); changed(value.conversation_id);
        if (error) reject(error); else resolve(answer);
      };
      const cancel = () => finish(undefined, aborted());
      const remote=remoteScope?{scope:remoteScope,fromDesktop,code:randomBytes(8).toString('hex'),delivered:false,answers:new Map(),index:0,busy:false}:null;
      pending.set(id, {value, agent:request.agent, rootAgent, scope, finish, remote});
      request.signal?.addEventListener('abort', cancel, {once:true});
      if (request.signal?.aborted) return cancel();
      changed(value.conversation_id);
      if (remote) Promise.resolve().then(()=>host.weixin.requestUserQuestion(value,remote.scope,remote.code,0)).catch(()=>{});
    });
  }, {global:true})];

  const activeConversation = async conversationId => {
    if (typeof conversationId !== 'string' || !conversationId) throw failure('请选择会话。');
    const conversation = await host.conversation(conversationId);
    if (conversation.archived || conversation.frozen) throw failure('请先恢复会话，再操作。', 'interaction_stale', 409);
    return conversation;
  };

  return {
    remoteDeliveryCurrent(id, scope, index) {
      const entry=pending.get(id), remote=entry?.remote;
      return Boolean(remote && remote.scope.installation_id===scope.installation_id && remote.scope.revision===scope.revision
        && remote.scope.conversation_id===scope.conversation_id && (index === undefined || index === remote.index) && sameOwner(host,entry));
    },
    remoteDelivered(id, scope, index) {
      if (this.remoteDeliveryCurrent(id,scope,index)) pending.get(id).remote.delivered=true;
    },
    async answerActiveRemote(event, text) {
      const entries=[...pending.values()].filter(entry=>entry.remote?.delivered && this.remoteDeliveryCurrent(entry.value.id,event));
      if (!entries.length) return null;
      if (entries.length !== 1) throw failure('当前有多个问题，请在电脑中选择要回答的问题。', 'interaction_ambiguous', 409);
      return this.answerRemote(event,entries[0].remote.code,text);
    },
    async answerRemote(event, code, text) {
      const entry=[...pending.values()].find(item=>item.remote?.code===code);
      if (!entry || !entry.remote.delivered || !this.remoteDeliveryCurrent(entry.value.id,event))
        throw failure('问题已失效，或问题详情尚未完整送达。', 'interaction_stale', 409);
      if (entry.remote.busy) throw failure('正在处理上一条回答，请稍候。');
      entry.remote.busy=true;
      try {
        await host.weixin.authorize(entry.remote.scope);
        if (!entry.remote.delivered || !this.remoteDeliveryCurrent(entry.value.id,event)) throw aborted();
        const questions=entry.value.questions, index=entry.remote.index, content=text.trim();
        if (!content || content.length>24_000) throw failure('请填写不超过 24000 字的回答。');
        const question=questions[index], options=question.options ?? [];
        let selected=[];
        if (/^\d+(?:\s*[,，、]\s*\d+)*$/.test(content) && options.length) {
          const indexes=content.split(/\s*[,，、]\s*/).map(Number);
          if (indexes.some(n=>!Number.isSafeInteger(n) || n<1 || n>options.length)
            || new Set(indexes).size!==indexes.length || (!question.multiSelect && indexes.length>1))
            throw failure('请选择有效的选项编号，或直接填写回答。');
          selected=indexes.map(n=>options[n-1].label);
        } else {
          const option=options.find(item=>item.label===content);
          if (option) selected=[option.label];
        }
        const answer={id:question.id,selected,...(!selected.length?{custom:content}:{})};
        entry.remote.answers.set(question.id,answer);
        if (index+1<questions.length) {
          entry.remote.index=index+1;entry.remote.delivered=false;
          try { await host.weixin.requestUserQuestion(entry.value,entry.remote.scope,entry.remote.code,index+1); }
          catch (error) { entry.remote.index=index;entry.remote.delivered=true;entry.remote.answers.delete(question.id);throw error; }
          return {complete:false};
        }
        await this.request('interaction.questions.answer',{conversation_id:entry.value.conversation_id,question_id:entry.value.id,remote_scope:entry.remote.scope,
          answers:questions.map(item=>entry.remote.answers.get(item.id))});
        return {complete:true};
      } finally { entry.remote.busy=false; }
    },
    async request(method, params = {}) {
      if (closed) throw failure('交互服务已关闭。', 'not_ready', 503);
      if (!NATIVE_INTERACTION_METHODS.has(method)) throw failure('未知交互操作。');
      const conversation = await activeConversation(params.conversation_id);
      if (method === 'interaction.questions.list') {
        const questions = [];
        for (const entry of pending.values()) {
          if (entry.value.conversation_id !== conversation.conversation_id) continue;
          if (sameOwner(host, entry)) questions.push(entry.value);
          else entry.finish(undefined, aborted());
        }
        const agent = host.handles.get(conversation.session_id)?.agent;
        const nativeState = agent && host.ctx.get('sessionProjections')?.stateOf(agent.session, 'userQuestions');
        for (const item of nativeState?.questions?.active ?? []) {
          if (item.state === 'continued' && !questions.some(question => question.call_id === item.callId))
            questions.push({id:`continued:${item.callId}`, conversation_id:conversation.conversation_id,
              session_id:conversation.session_id, call_id:item.callId, questions:item.questions, state:'continued', can_reply:false,
              message:'这个问题的原回合已继续。请在会话中补充回答；此处不会恢复已结束的工具调用。'});
        }
        return {questions};
      }
      if (method === 'interaction.questions.answer') {
        const entry = pending.get(params.question_id);
        if (!entry || entry.value.conversation_id !== conversation.conversation_id || !sameOwner(host, entry))
          throw failure('问题所属回合已结束或已变化，请在会话中继续。', 'interaction_stale', 409);
        const answer = validateNativeAnswers(entry.value.questions, params.answers);
        await host.checkCommandScope(entry.rootAgent, 'ask_user_question');
        await host.guard(entry.scope.rootSessionId);
        if (params.remote_scope) {
          if (!entry.remote || params.remote_scope !== entry.remote.scope) throw aborted();
          await host.weixin.authorize(entry.remote.scope);
        }
        if (pending.get(params.question_id) !== entry || !sameOwner(host, entry))
          throw failure('问题所属回合已结束或已变化，请在会话中继续。', 'interaction_stale', 409);
        entry.finish(answer);
        return {ok:true};
      }
      const service = host.ctx.get('planMode');
      if (!service) return {available:false, active:false};
      if (method === 'interaction.plan.set' && typeof params.active !== 'boolean') throw failure('计划模式参数无效。');
      const {handle} = await host.ensureNative(conversation);
      const outcome = method === 'interaction.plan.set' ? service.set(handle.agent, params.active) : undefined;
      if (outcome) {
        await host.ctx.sessions.flush(handle.agent.session);
        host.broadcast('interaction', {action:'plan-changed', conversation_id:conversation.conversation_id});
      }
      return {available:true, ...service.get(handle.agent), ...(outcome ? {outcome} : {})};
    },
    stop() {
      closed = true;
      for (const entry of pending.values()) entry.finish(undefined, aborted());
      for (const dispose of disposers) if (typeof dispose === 'function') dispose();
    },
  };
}
