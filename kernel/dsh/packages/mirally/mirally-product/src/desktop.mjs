/** Confirmed UFO desktop subtask transport; the UFO kernel remains unchanged. */
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {randomUUID, createHash} from 'node:crypto';
import {mkdirSync, readFileSync, writeFileSync, renameSync, existsSync, readdirSync} from 'node:fs';
import {join} from 'node:path';

export class DesktopTasks {
  constructor(product, {spawnWorker = spawn} = {}) {
    this.product = product;
    this.spawnWorker = spawnWorker;
    this.jobs = new Map();
    this.startQueue = Promise.resolve();
    this.directory = join(product.home, 'actions');
    mkdirSync(this.directory, {recursive: true});
  }

  path(id) { return join(this.directory, createHash('sha256').update(id).digest('hex') + '.json'); }

  record(job) {
    const value = {id: job.id, kind: 'desktop', conversation_id: job.conversation_id, command_id: job.command_id,
      text: job.text, state: job.state, started_at: job.started_at, updated_at: new Date().toISOString(),
      ...(job.question ? {question: job.question} : {}), ...(job.result ? {result: job.result} : {})};
    const path = this.path(job.id);
    writeFileSync(path + '.tmp', JSON.stringify(value), {mode: 0o600});
    renameSync(path + '.tmp', path);
    this.product.broadcast('action', {action: value});
    return value;
  }

  list(conversationId) {
    const items = [];
    for (const file of readdirSync(this.directory).filter(name => name.endsWith('.json')).slice(-200)) {
      try {
        const value = JSON.parse(readFileSync(join(this.directory, file), 'utf8'));
        if (value.kind !== 'desktop' || (conversationId && value.conversation_id !== conversationId)) continue;
        if (!this.jobs.has(value.id) && ['starting', 'running', 'waiting_for_user', 'stopping'].includes(value.state)) value.state = 'unknown';
        items.push(value);
      } catch (error) { this.product.ctx.logger.warn(`Action receipt is unreadable: ${error.code ?? 'invalid_json'}`); }
    }
    return {actions: items.sort((a, b) => b.started_at.localeCompare(a.started_at))};
  }

  start(input) {
    const next = this.startQueue.catch(() => {}).then(() => this.startOne(input));
    this.startQueue = next;
    return next;
  }

  async startOne({conversation_id: id, command_id: commandId, text, confirmed}) {
    if (confirmed !== true || !id || !commandId || typeof text !== 'string' || !text.trim() || text.length > 32000) throw new Error('A confirmed bounded desktop task and conversation command are required');
    const product = this.product;
    const jobId = `desktop-${createHash('sha256').update(id + ':' + commandId).digest('hex')}`;
    const receipt = this.jobs.get(jobId) ?? this.list(id).actions.find(item => item.id === jobId);
    if (receipt) {
      const command = await product.rpc('command.admit', {conversation_id: id, command_id: commandId, text: JSON.stringify({desktop_task: text})});
      return {replay: true, command, action: this.list(id).actions.find(item => item.id === jobId)};
    }
    const privateSettings = await product.rpc('internal.settings.get');
    if (!privateSettings.model.image_enabled) throw new Error('Desktop tasks require a model configured for image input');
    if (!existsSync(join(product.root, 'UFO/ufo'))) throw new Error('Prepare the optional UFO submodule and dependencies first');
    if ([...this.jobs.values()].some(job => !job.done)) throw new Error('Another desktop task is active; stop it before starting a new task');
    const {conversation} = await product.ensureNative(await product.conversation(id), {forInput: true});
    const command = await product.rpc('command.admit', {conversation_id: id, command_id: commandId, text: JSON.stringify({desktop_task: text})});
    if (!command.should_dispatch) return {replay: true, command, action: this.list(id).actions.find(item => item.id === jobId)};
    await product.rpc('command.ack', {conversation_id: id, command_id: commandId, state: 'submitted'});
    const job = {id: jobId, conversation_id: id, command_id: commandId, native_id: conversation.session_id,
      text, state: 'starting', started_at: new Date().toISOString(), controller: new AbortController(), done: false};
    this.jobs.set(jobId, job);
    const store = {sessionId: job.native_id, commandId, confirmed: true};
    job.authorize = async () => {
      job.controller.signal.throwIfAborted();
      await product.authorizations.run(store, () => product.guard(job.native_id, 'desktop', true));
      job.controller.signal.throwIfAborted();
    };
    this.record(job);
    job.promise = this.run(job).catch(async error => {
      await this.finish(job, {status: job.controller.signal.aborted ? 'cancelled' : 'unknown', message: error.message});
    });
    return {action: this.record(job)};
  }

  send(job, frame) {
    if (job.child?.stdin.writable) job.child.stdin.write(JSON.stringify(frame) + '\n');
  }

  async run(job) {
    await job.authorize();
    const product = this.product;
    const model = product.models.requireOptions();
    const child = job.child = this.spawnWorker(product.config.python, ['-m', 'mirally.runtime.ufo.worker', '--root', product.root], {
      cwd: product.root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'},
    });
    const lines = createInterface({input: child.stdout});
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', value => product.ctx.logger.warn(`UFO worker: ${value.slice(0, 1000)}`));
    const exited = new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('exit', (code, signal) => resolve({code, signal}));
    });
    const timeout = setTimeout(() => this.cancel(job.id, 'Task time budget expired'), 600000);
    timeout.unref();
    lines.on('line', line => {
      void (async () => {
        if (job.done) return;
        if (Buffer.byteLength(line) > 2_000_000) throw new Error('UFO protocol frame is too large');
        const frame = JSON.parse(line);
        if (frame.type === 'request') {
          try {
            await job.authorize();
            if (['authorize', 'authorize_model', 'authorize_observation'].includes(frame.operation)) this.send(job, {type: 'response', id: frame.id, ok: true, value: true});
            else if (['ask', 'confirm'].includes(frame.operation)) {
              if (job.question) throw new Error('Another desktop question is pending');
              job.question = {id: frame.id, type: frame.operation, text: frame.text, action: frame.action};
              job.state = 'waiting_for_user';
              this.record(job);
            } else throw new Error('Unsupported worker operation');
          } catch (error) { this.send(job, {type: 'response', id: frame.id, ok: false, error: error.message}); }
        } else if (frame.type === 'event') {
          job.progress = frame.event;
          if (!job.question && !job.controller.signal.aborted) job.state = 'running';
          product.broadcast('action', {id: job.id, conversation_id: job.conversation_id, progress: frame.event});
        } else if (frame.type === 'result') {
          await this.finish(job, frame.result);
          child.stdin.end();
        }
      })().catch(error => { this.cancel(job.id, error.message); });
    });
    this.send(job, {type: 'run', mode: 'desktop_subtask', run_id: job.id, text: job.text,
      context: {}, route: {api: 'openai-completions', base_url: product.url + '/internal/v1', model: model.model,
        key: product.token, parameters: {max_tokens: model.maxTokens ?? 2048, temperature: 0}}});
    try {
      await exited;
      if (!job.done) await this.finish(job, {status: 'unknown', message: 'Worker exited before a final result; inspect the desktop before retrying'});
    } finally { clearTimeout(timeout); clearTimeout(job.killTimer); lines.close(); }
  }

  async finish(job, result) {
    if (job.done) return;
    job.done = true;
    delete job.question;
    job.result = result;
    job.state = result.status ?? 'unknown';
    this.record(job);
    try { await this.product.rpc('command.ack', {conversation_id: job.conversation_id, command_id: job.command_id,
      state: job.state === 'completed' ? 'completed' : job.state === 'cancelled' ? 'cancelled' : 'unknown'}); }
    catch (error) { this.product.ctx.logger.warn(`Desktop receipt acknowledgement: ${error.message}`); }
  }

  async respond({id, request_id: requestId, confirmed, text}) {
    const job = this.jobs.get(id);
    if (!job || job.done || job.question?.id !== requestId) throw new Error('The desktop question is no longer active');
    await job.authorize();
    const question = job.question;
    let value;
    if (question.type === 'confirm') {
      if (typeof confirmed !== 'boolean') throw new Error('An explicit confirmation or refusal is required');
      value = confirmed;
    } else {
      if (typeof text !== 'string' || !text.trim() || text.length > 32000) throw new Error('A bounded answer is required');
      value = {text, receipt_id: randomUUID()};
    }
    this.send(job, {type: 'response', id: requestId, ok: true, value});
    delete job.question;
    job.state = 'running';
    return {action: this.record(job)};
  }

  cancel(id, reason = 'Stopped by user') {
    const job = this.jobs.get(id);
    if (!job || job.done) return;
    job.controller.abort(new Error(reason));
    job.state = 'stopping';
    this.send(job, {type: 'cancel'});
    this.record(job);
    job.killTimer ??= setTimeout(() => {
      if (!job.child || job.child.exitCode !== null) return;
      job.forced = true;
      if (process.platform === 'win32') {
        const kill = spawn('taskkill', ['/PID', String(job.child.pid), '/T', '/F'], {windowsHide: true, stdio: 'ignore'});
        kill.on('error', () => job.child.kill());
      } else job.child.kill('SIGKILL');
    }, 10000);
    job.killTimer.unref();
  }

  stopConversation(id) { for (const job of this.jobs.values()) if (job.conversation_id === id) this.cancel(job.id); }

  async dispose() {
    for (const job of this.jobs.values()) this.cancel(job.id, 'Host exiting');
    await Promise.allSettled([...this.jobs.values()].map(job => job.promise));
  }
}
