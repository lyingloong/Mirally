/** Product ownership over real DSH sessions, delegated agents and human commands. */
import {createHash,randomUUID} from 'node:crypto';

export class NativeRuntime {
  constructor(product) { this.product = product; this.delegations = new Map(); }

  owner(agent) {
    if (!agent) return null;
    const delegated = this.delegations.get(agent.id);
    if (delegated) return delegated;
    const conversation = this.product.nativeConversations.get(agent.id);
    return conversation ? {rootSessionId:agent.id, conversation, commandId:this.product.consumedCommandId(agent)} : null;
  }

  install() {
    const host = this.product;
    host.ctx.on('agent/created', ({agent}) => {
      const parentId = agent.session.header.parentSession;
      if (!parentId || agent.session.header.origin !== 'subagent') return;
      const parent = host.ctx.agents.get(parentId), owner = this.owner(parent);
      if (!owner?.commandId) throw new Error('Delegated agent has no active Mirally conversation command');
      this.delegations.set(agent.id, {...owner});
      host.nativeConversations.set(agent.id, owner.conversation);
      // Spawn and fork retain DSH's native model/policy inheritance. Product
      // context has the same source owner; no second model loop is introduced.
      agent.ctx.systemPrompt.context({name:'mirally:personal-context', order:900, required:true, interpolate:false,
        text:() => host.contexts.get(owner.rootSessionId) ?? ''});
      agent.ctx.on('agent/pre-step', async (_payload, next) => {
        await host.checkCommandScope(agent);
        await host.guard(owner.rootSessionId);
        return next();
      });
    });
    host.ctx.on('agent/disposed', ({agent}) => {
      if (!this.delegations.delete(agent.id)) return;
      host.nativeConversations.delete(agent.id);
    });
  }

  async inspect(id) {
    const host = this.product;
    const {conversation, handle} = await host.ensureNative(await host.conversation(id));
    const agent = handle.agent;
    return {session_id:agent.id, generation:conversation.generation, status:agent.status,
      commands:host.ctx.get('commands')?.list(agent) ?? [],
      projections:host.ctx.get('sessionProjections')?.snapshot(agent.session).values ?? {},
      children:await host.ctx.get('subagents')?.listChildren(agent.id) ?? [],
      tools:host.ctx.tools.schemas(agent).map(tool=>tool.name)};
  }

  async stopJobs(rootSessionId) {
    const jobs=this.product.ctx.get('jobs');
    if (!jobs) return [];
    const owners=[rootSessionId,...[...this.delegations].filter(([,owner])=>owner.rootSessionId===rootSessionId).map(([id])=>id)];
    const pending=[];
    for (const owner of owners) for (const job of jobs.list(owner)) {
      if (job.owner!==owner || !['running','stopping'].includes(job.status)) continue;
      jobs.kill(job.id,owner,'Mirally conversation stopped');
      pending.push(jobs.wait(job.id,5000,owner));
    }
    return Promise.all(pending);
  }

  async command(id, input) {
    if (!input || typeof input.line !== 'string' || input.line.length > 16000
      || !/^\/(compact|plan|goal)(?:\s|$)/u.test(input.line))
      throw Object.assign(new Error('请选择 /compact、/plan 或 /goal 命令。'), {status:400,code:'invalid_command'});
    const host = this.product;
    const {conversation, handle} = await host.serial(id, async()=>host.ensureNative(await host.conversation(id), {forInput:true}));
    await host.guard(conversation.session_id);
    const commands = host.ctx.get('commands');
    if (!commands) throw new Error('Native command registry is unavailable');
    return host.runEffect(id, async signal => {
      const result = await commands.execute(handle.agent, input.line, [], signal);
      if (!result) throw new Error('Native command is unavailable');
      await host.ctx.sessions.flush(handle.agent.session);
      host.broadcast('session-command', {session_id:id, ...result});
      return result;
    });
  }

  async fork(id, input = {}) {
    const host = this.product;
    if (input.title !== undefined && (typeof input.title !== 'string' || !input.title.trim() || input.title.length>512)
      || input.requestId !== undefined && (typeof input.requestId!=='string' || !input.requestId || input.requestId.length>200))
      throw Object.assign(new Error('分叉会话参数无效。'),{status:400,code:'invalid_request'});
    const requestId=input.requestId ?? randomUUID();
    const identity=`conversation_fork_${createHash('sha256').update(JSON.stringify([id,requestId])).digest('hex').slice(0,32)}`;
    const requestHash=createHash('sha256').update(JSON.stringify([id,input.title ?? null])).digest('hex');
    return host.serial(id,async()=>{
      const {conversation,handle}=await host.ensureNative(await host.conversation(id));
      await host.guard(handle.agent.id);
      await host.ctx.sessions.flush(handle.agent.session);
      const end=handle.agent.session.snapshotEvents().findLast(event=>event.type==='turn/end');
      if (!end) throw Object.assign(new Error('此会话还没有完整回合可分叉。'),{status:409,code:'fork_no_history'});
      const target=await host.rpc('internal.conversation.fork',{conversation_id:identity,parent_conversation_id:id,
        parent_generation:conversation.generation,parent_session_id:handle.agent.id,boundary_seq:end.seq,
        request_hash:requestHash,...(input.title?{title:input.title}:{})});
      const result=await host.serial(identity,async()=>host.ensureNative(await host.conversation(target.conversation_id)));
      return host.summary(result.conversation);
    });
  }

  async seed(conversation) {
    const fork=conversation.fork;
    if (!fork || conversation.generation!==1) return {};
    const host=this.product;
    let events,header;
    const live=host.handles.get(fork.parent_session_id)?.agent.session;
    if (live) {events=live.snapshotEvents();header=live.header;}
    else {
      const reader=await host.ctx.sessionPersistence.open(fork.parent_session_id,'read');
      try {const snapshot=await reader.read();events=snapshot.events;header=snapshot.header;} finally {await reader.close();}
    }
    const seed=events.slice(0,fork.boundary_seq+1);
    if (seed.at(-1)?.type!=='turn/end' || seed.at(-1).seq!==fork.boundary_seq)
      throw new Error('原生会话的分叉边界不可用；没有创建空白替代会话。');
    return {seed,inheritedEventCount:seed.length,meta:{cwd:header.cwd,parentSession:fork.parent_session_id,isSeeded:true}};
  }
}
