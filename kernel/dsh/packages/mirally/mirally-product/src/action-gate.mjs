/** Local, one-action decisions bound to the exact live conversation command. */
import {randomUUID, randomBytes} from 'node:crypto';

const failure = message => Object.assign(new Error(message), {code:'action_approval_invalid', status:400});

export class ActionGate {
  constructor(host, {timeoutMs = 300_000} = {}) { this.host = host; this.timeoutMs = timeoutMs; this.pending = new Map(); this.closed = false; }

  async request({sessionId, commandId, kind, title, detail, signal}) {
    signal?.throwIfAborted();
    if (this.closed) throw failure('动作确认服务已关闭。');
    const conversation = this.host.conversationForSession(sessionId);
    if (!conversation || !commandId || !['shell','browser','desktop_observation','desktop_action'].includes(kind)
      || typeof title !== 'string' || !title.trim() || title.length > 256
      || typeof detail !== 'string' || !detail.trim() || detail.length > 24_000)
      throw failure('无法完整展示此动作，未执行。');
    if (this.pending.size >= 32 || [...this.pending.values()].some(entry => entry.value.session_id === sessionId))
      throw failure('此会话已有待确认动作，请先处理。');
    const agent = this.host.handles.get(sessionId)?.agent;
    if (!agent || this.host.consumedCommandId(agent) !== commandId) throw failure('动作所属会话已变化，未执行。');
    await this.host.checkCommandScope(agent, 'mirally_action');
    await this.host.guard(sessionId);
    signal?.throwIfAborted();
    if (this.closed || this.host.handles.get(sessionId)?.agent !== agent || this.host.consumedCommandId(agent) !== commandId
      || this.host.conversationForSession(sessionId)?.conversation_id !== conversation.conversation_id)
      throw failure('动作所属会话已变化，未执行。');
    // A user-selected standing grant affects only new requests. Already pending
    // decisions remain explicit, and absent/unknown policies fail to prompting.
    if (typeof this.host.actionPermission === 'function') {
      const scope = {conversationId:conversation.conversation_id, sessionId, commandId};
      if (await this.host.actionPermission(scope) === true) {
        await this.host.checkCommandScope(agent, 'mirally_action');
        await this.host.guard(sessionId);
        const stillAllowed = await this.host.actionPermission(scope) === true;
        signal?.throwIfAborted();
        if (this.closed || this.host.handles.get(sessionId)?.agent !== agent || this.host.consumedCommandId(agent) !== commandId
          || this.host.conversationForSession(sessionId)?.conversation_id !== conversation.conversation_id)
          throw failure('动作所属会话已变化，未执行。');
        if (stillAllowed) return true;
      }
    }
    signal?.throwIfAborted();
    if (this.closed || this.host.handles.get(sessionId)?.agent !== agent || this.host.consumedCommandId(agent) !== commandId
      || this.host.conversationForSession(sessionId)?.conversation_id !== conversation.conversation_id)
      throw failure('动作所属会话已变化，未执行。');
    if (this.pending.size >= 32 || [...this.pending.values()].some(entry => entry.value.session_id === sessionId))
      throw failure('此会话已有待确认动作，请先处理。');
    const id = randomUUID(), createdAt = Date.now();
    return new Promise((resolve, reject) => {
      let done = false;
      const finish = (outcome, error) => {
        if (done) return; done = true; clearTimeout(timer); signal?.removeEventListener('abort', abort);
        this.pending.delete(id); this.host.broadcast('action', {action:'approval-changed', conversation_id:conversation.conversation_id});
        if (error) reject(error); else resolve(outcome);
      };
      const abort = () => finish(false, signal.reason ?? new Error('动作已取消。'));
      const timer = setTimeout(() => finish(false), this.timeoutMs);
      timer.unref?.();
      const value = {id, conversation_id:conversation.conversation_id, session_id:sessionId, command_id:commandId,
        kind, title, detail, created_at:createdAt, expires_at:createdAt + this.timeoutMs};
      const policy = this.host.commandPolicies?.get(commandId);
      const remote = policy?.origin === 'weixin' ? {scope: policy, code: randomBytes(8).toString('hex'), delivered: false} : null;
      this.pending.set(id, {value, finish, remote});
      signal?.addEventListener('abort', abort, {once:true});
      if (signal?.aborted) return abort();
      this.host.broadcast('action', {action:'approval-changed', conversation_id:conversation.conversation_id});
      if (remote) {
        Promise.resolve().then(() => this.host.weixin.requestActionApproval(value, remote.scope, remote.code))
          .catch(() => finish(false));
      }
    });
  }

  remoteDeliveryCurrent(id, scope) {
    const entry = this.pending.get(id), remote = entry?.remote;
    return Boolean(remote && remote.scope.installation_id === scope.installation_id && remote.scope.revision === scope.revision
      && remote.scope.conversation_id === scope.conversation_id && entry.value.command_id === remote.scope.command_id
      && this.host.consumedCommandId(this.host.handles.get(entry.value.session_id)?.agent) === entry.value.command_id
      && this.host.conversationForSession(entry.value.session_id)?.conversation_id === scope.conversation_id);
  }

  remoteDelivered(id, scope) {
    if (this.remoteDeliveryCurrent(id, scope)) this.pending.get(id).remote.delivered = true;
  }

  async decideRemote(event, code, allow) {
    const entry = [...this.pending.values()].find(item => item.remote?.code === code);
    if (!entry || !entry.remote.delivered || !this.remoteDeliveryCurrent(entry.value.id, event)) throw failure('确认已失效或动作详情尚未完整送达。');
    await this.host.weixin.authorize(entry.remote.scope);
    if (!this.remoteDeliveryCurrent(entry.value.id, event) || !entry.remote.delivered) throw failure('动作确认已失效。');
    return this.decide({approval_id:entry.value.id, conversation_id:event.conversation_id, allow});
  }

  list(conversationId) {
    if (typeof conversationId !== 'string' || !conversationId) throw failure('请选择会话。');
    return {approvals:[...this.pending.values()].map(entry => entry.value).filter(value => value.conversation_id === conversationId)};
  }

  async decide({approval_id, conversation_id, allow}) {
    if (typeof approval_id !== 'string' || typeof conversation_id !== 'string' || typeof allow !== 'boolean') throw failure('动作确认参数无效。');
    const entry = this.pending.get(approval_id);
    if (!entry || entry.value.conversation_id !== conversation_id) throw failure('动作已结束或确认已失效。');
    if (allow) {
      const agent = this.host.handles.get(entry.value.session_id)?.agent;
      if (!agent || this.host.consumedCommandId(agent) !== entry.value.command_id) throw failure('动作所属回合已变化，请重新发起。');
      await this.host.checkCommandScope(agent, 'mirally_action');
      await this.host.guard(entry.value.session_id);
      if (this.pending.get(approval_id) !== entry) throw failure('动作已结束或确认已失效。');
      if (this.host.handles.get(entry.value.session_id)?.agent !== agent || this.host.consumedCommandId(agent) !== entry.value.command_id
        || this.host.conversationForSession(entry.value.session_id)?.conversation_id !== conversation_id)
        throw failure('动作所属回合已变化，请重新发起。');
    }
    entry.finish(allow);
    return {ok:true};
  }

  dispose() { this.closed = true; for (const entry of this.pending.values()) entry.finish(false); }
}
