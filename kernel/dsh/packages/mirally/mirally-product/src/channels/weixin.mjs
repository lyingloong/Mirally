/** Product channel: no model loop, transcript or authority is owned here. */
import {randomUUID} from 'node:crypto';
import {WeixinApi, validateWeixinBaseUrl} from './weixin-api.mjs';
import {WeixinWorks, WORK_HELP} from './weixin-work.mjs';

const MAX_AGE = 10 * 60_000;
const LOGIN_TTL = 5 * 60_000;
const HELP = '这是你的 Mirally 微信入口（第一版支持文字私聊）。\n/new 新建会话\n/sessions 列出会话\n/use 编号 切换会话\n/status 查看当前会话\n/stop 停止当前回复\n/help 查看帮助\n对话工具仅允许明确的只读查询；工作执行须单独确认。\n' + WORK_HELP;
const blocked = () => Object.assign(new Error('微信绑定或会话已改变，请重新发起。'), {code: 'weixin_scope_revoked', status: 403});
const pause = (ms, signal) => new Promise(resolve => {
  if (signal?.aborted) return resolve();
  const done = () => { clearTimeout(timer); signal?.removeEventListener('abort', done); resolve(); };
  const timer = setTimeout(done, ms);
  signal?.addEventListener('abort', done, {once: true});
});
const eventId = value => typeof value === 'string' && value.trim() && value.length <= 200 ? value
  : Number.isSafeInteger(value) && value >= 0 ? String(value) : null;

/** Bounded Unicode segments. Never include model reasoning or raw tool events. */
function boundedReply(value) {
  const chars = Array.from(String(value ?? '').trim());
  if (!chars.length) return '';
  const clipped = chars.length > 14000;
  return chars.slice(0, 14000).join('') + (clipped ? '\n（回复较长，请在桌面查看完整内容。）' : '');
}
export function weixinTextParts(value) {
  const text = boundedReply(value);
  const all = Array.from(text), parts = [];
  for (let start = 0; start < all.length; start += 3500) parts.push(all.slice(start, start + 3500).join(''));
  return parts.length > 1 ? parts.map((part, index) => `[${index + 1}/${parts.length}] ${part}`) : parts;
}

export class WeixinChannel {
  constructor(product, {baseUrl, allowLoopback = false, apiFactory, now = Date.now} = {}) {
    this.product = product;
    this.allowLoopback = allowLoopback;
    this.baseUrl = validateWeixinBaseUrl(baseUrl ?? 'https://ilinkai.weixin.qq.com', {allowLoopback});
    this.apiFactory = apiFactory ?? (origin => new WeixinApi({baseUrl: origin, allowLoopback, botAgent: 'Mirally/0.1.0'}));
    this.now = now;
    this.phase = 'unbound';
    this.error = '';
    this.jobs = new Set();
    this.closed = false;
    this.management = Promise.resolve();
    this.works = new WeixinWorks(this);
  }

  async state() { return (await this.product.rpc('channel.weixin.get')).state; }
  async store(method, params = {}) { return this.product.rpc(`channel.weixin.${method}`, params); }
  api(origin = this.baseUrl) { return this.apiFactory(validateWeixinBaseUrl(origin, {allowLoopback: this.allowLoopback})); }

  async start() {
    const state = await this.state();
    this.phase = state.credential_error ? 'login_required' : state.bound ? state.enabled ? 'reconnecting' : 'disconnected' : state.token ? 'awaiting_confirmation' : 'unbound';
    if (state.credential_error) this.error = '微信凭据无法在当前系统读取，请重新扫码或解除绑定。';
    // Opening history never resumes an interrupted model input. The surviving
    // transcript may prove a reply; otherwise ask for a fresh human message.
    if (state.installation_id && state.bound && state.enabled && state.token) await this.reconcile(state);
    if (state.enabled && state.bound && state.token) this.connect();
    this.deliveryTimer = setInterval(() => { void this.pump().catch(() => {}); }, 1000);
    this.deliveryTimer.unref();
  }

  async status() {
    const state = await this.state();
    const conversation = state.conversation_id ? await this.product.conversation(state.conversation_id).catch(() => null) : null;
    const login = this.login && this.login.expiresAt > this.now() ? {
      id: this.login.id, qr_content: this.login.qrContent, expires_at: this.login.expiresAt,
      needs_verify_code: this.phase === 'need_verifycode',
    } : undefined;
    return {state: state.credential_error ? 'login_required' : this.phase, enabled: state.enabled, bound: state.bound,
      installation_id: state.installation_id, revision: state.revision,
      peer_id: state.peer_id, bot_id: state.bot_id, conversation_id: state.conversation_id,
      conversation_title: conversation?.title, ...(login ? {login} : {}),
      ...(this.error ? {last_error: this.error} : {}), outbox: state.outbox_counts};
  }

  async request(method, params = {}) {
    if (method === 'weixin.status') return this.status();
    const operation = async () => {
      if (this.closed) throw blocked();
      if (method === 'weixin.login.start') await this.startLogin();
      else if (method === 'weixin.login.cancel') {
        this.cancelLogin();
        let state = await this.state();
        // A phone scan grants a credential, not the local product binding.
        // Cancelling this confirmation must also discard that pending secret.
        if (!state.bound && state.token) ({state} = await this.store('unbind', {expected_revision: state.revision}));
        this.phase = state.bound ? state.enabled ? 'reconnecting' : 'disconnected' : state.token ? 'awaiting_confirmation' : 'unbound';
        if (state.enabled) this.connect();
      } else if (method === 'weixin.login.verify') {
        if (!this.login || this.phase !== 'need_verifycode' || typeof params.code !== 'string' || !/^[A-Za-z0-9]{1,32}$/.test(params.code))
          throw Object.assign(new Error('请输入微信显示的有效验证码。'), {code: 'invalid_request', status: 400});
        this.login.verifyCode = params.code;
        this.phase = 'waiting_scan';
      } else if (['weixin.bind', 'weixin.select', 'weixin.disconnect', 'weixin.reconnect', 'weixin.unbind'].includes(method)) {
        this.cancelLogin();
        const old = await this.state();
        if (!Number.isSafeInteger(params.expected_revision) || params.expected_revision !== old.revision) throw blocked();
        // Stop input before replacing credentials/routes; old work cannot inherit
        // a newly selected conversation or a later binding's authority.
        await this.disconnectTransport();
        await this.product.cancelWeixinCommands(old.installation_id);
        let state;
        if (method === 'weixin.bind') ({state} = await this.store('bind', params));
        if (method === 'weixin.select') ({state} = await this.store('select', params));
        if (method === 'weixin.disconnect' || method === 'weixin.reconnect')
          ({state} = await this.store('enable', {expected_revision: params.expected_revision, enabled: method === 'weixin.reconnect'}));
        if (method === 'weixin.unbind') { this.cancelLogin(); ({state} = await this.store('unbind', params)); }
        this.error = '';
        this.phase = state.bound ? state.enabled ? 'reconnecting' : 'disconnected' : 'unbound';
        if (state.enabled) this.connect();
      } else throw Object.assign(new Error('微信操作尚未支持。'), {code: 'method_not_allowed', status: 403});
      this.product.broadcast('configuration', {fields: ['weixin']});
      return this.status();
    };
    const result = this.management.catch(() => {}).then(operation);
    this.management = result;
    return result;
  }

  cancelLogin() {
    this.login?.controller.abort();
    this.login = null;
  }

  async startLogin() {
    if (this.closed) throw blocked();
    this.cancelLogin();
    await this.disconnectTransport();
    if (this.closed) throw blocked();
    const old = await this.state();
    if (this.closed) throw blocked();
    if (old.enabled) await this.store('enable', {expected_revision: old.revision, enabled: false});
    await this.product.cancelWeixinCommands(old.installation_id);
    if (this.closed) throw blocked();
    const login = {id: randomUUID(), controller: new AbortController(), expiresAt: this.now() + LOGIN_TTL, origin: this.baseUrl};
    this.login = login;
    this.error = ''; this.phase = 'waiting_scan';
    try {
      const qr = await this.api().getQr({tokens: old.token ? [old.token] : [], signal: login.controller.signal});
      if (this.login !== login || this.closed) return;
      login.qrcode = qr.qrcode; login.qrContent = qr.qrcode_img_content;
      this.loginTask = this.pollLogin(login).catch(() => {
        if (this.login === login && !this.closed) { this.phase = 'error'; this.error = '微信扫码连接失败，请重新获取二维码。'; }
      });
    } catch (error) {
      if (this.login === login) { this.cancelLogin(); this.phase = 'error'; this.error = '无法获取微信二维码，请检查网络后重试。'; }
      throw Object.assign(new Error(this.error || '微信扫码已取消。'), {code: 'weixin_login_failed', status: 502});
    }
  }

  async pollLogin(login) {
    while (this.login === login && !login.controller.signal.aborted && !this.closed && this.now() < login.expiresAt) {
      if (this.phase === 'need_verifycode' && !login.verifyCode) { await pause(300, login.controller.signal); continue; }
      let result;
      try {
        result = await this.api(login.origin).pollQr({qrcode: login.qrcode, verifyCode: login.verifyCode, signal: login.controller.signal});
        delete login.verifyCode;
      } catch (error) {
        if (login.controller.signal.aborted) return;
        if (error.kind !== 'timeout') { this.error = '微信扫码暂时无法连接，正在重试。'; await pause(1500, login.controller.signal); }
        continue;
      }
      if (this.login !== login || login.controller.signal.aborted || this.closed) return;
      this.error = '';
      if (result.status === 'scaned_but_redirect') {
        login.redirects = (login.redirects ?? 0) + 1;
        if (login.redirects > 3) throw blocked();
        login.origin = validateWeixinBaseUrl(`https://${result.redirect_host}`, {allowLoopback: this.allowLoopback});
      } else if (result.status === 'confirmed') {
        await this.store('save', {token: result.bot_token, peer_id: result.ilink_user_id,
          bot_id: result.ilink_bot_id, base_url: result.baseurl ?? login.origin});
        if (this.login !== login || this.closed) return;
        this.phase = 'awaiting_confirmation';
        this.cancelLogin();
        this.product.broadcast('configuration', {fields: ['weixin']});
        return;
      } else if (result.status === 'expired' || result.status === 'verify_code_blocked') {
        this.phase = 'expired'; this.cancelLogin(); return;
      } else if (result.status === 'binded_redirect') {
        // The server can recognize a token supplied in local_token_list. Keep
        // only this product's existing identity; never infer or adopt another
        // installation. Restoring transport still requires a local action.
        const saved = await this.state();
        if (saved.token && saved.peer_id && saved.bot_id) {
          this.phase = saved.bound ? 'disconnected' : 'awaiting_confirmation';
          this.error = saved.bound ? '已有本机微信绑定，可恢复连接；若登录仍失效，请先在微信处理原连接后重新扫码。' : '';
        } else {
          this.phase = 'error'; this.error = '微信未返回新凭据，请在微信检查原连接后重新扫码。';
        }
        this.cancelLogin(); return;
      } else this.phase = result.status === 'scaned' ? 'scanned' : result.status === 'need_verifycode' ? 'need_verifycode' : 'waiting_scan';
      await pause(500, login.controller.signal);
    }
    if (this.login === login) { this.phase = 'expired'; this.cancelLogin(); }
  }

  connect() {
    if (this.closed || this.connection) return;
    const controller = new AbortController();
    this.connection = controller;
    this.connectionTask = this.poll(controller).catch(() => {
      if (!this.closed && !controller.signal.aborted) { this.phase = 'error'; this.error = '微信连接已停止，请重新连接。'; }
    }).finally(() => { if (this.connection === controller) this.connection = null; });
  }

  async disconnectTransport() {
    const pending = this.connectionTask;
    this.connection?.abort(); this.sendController?.abort();
    await pending;
    this.connection = null;
    await this.pumping?.catch(() => {});
  }

  async poll(controller) {
    let failures = 0;
    while (!controller.signal.aborted && !this.closed) {
      const state = await this.state();
      if (!state.enabled || !state.bound || !state.token) return;
      try {
        const update = await this.api(state.base_url).updates({token: state.token, cursor: state.cursor || '', signal: controller.signal});
        if (controller.signal.aborted || this.closed) return;
        this.phase = 'connected'; this.error = ''; failures = 0;
        for (const message of update.msgs) {
          if (controller.signal.aborted) return;
          await this.receive(state, message);
        }
        if (typeof update.get_updates_buf === 'string' && update.get_updates_buf)
          await this.store('cursor', {installation_id: state.installation_id, revision: state.revision, cursor: update.get_updates_buf});
        await this.pump();
        // A compliant server holds the request. Also bound a fast empty response.
        await pause(250, controller.signal);
      } catch (error) {
        if (controller.signal.aborted || this.closed) return;
        if (error.credentialStale) { this.phase = 'login_required'; this.error = '微信登录已失效，请重新扫码。'; return; }
        if (error.kind === 'timeout') continue;
        this.phase = 'reconnecting'; this.error = '微信暂时断开，正在重新连接。';
        await pause(Math.min(30000, 1000 * 2 ** Math.min(failures++, 5)), controller.signal);
      }
    }
  }

  async authorize(scope) {
    const state = await this.state();
    if (this.closed || !state.enabled || !state.bound || state.installation_id !== scope.installation_id
      || state.revision !== scope.revision || state.conversation_id !== scope.conversation_id) throw blocked();
    return state;
  }

  async receive(state, message) {
    if (!message || message.message_type !== 1 || message.group_id || message.from_user_id !== state.peer_id
      || message.to_user_id && message.to_user_id !== state.bot_id || message.message_state === 1) return;
    const id = eventId(message.message_id ?? message.client_id);
    if (!id) return;
    const items = Array.isArray(message.item_list) ? message.item_list : [];
    const supported = items.length && items.every(item => item?.type === 1 && typeof item.text_item?.text === 'string');
    const text = supported ? items.map(item => item.text_item.text).join('\n').trim() : '[当前消息包含尚未支持的内容]';
    if (!text || text.length > 100000) return;
    const timestamp = Number.isSafeInteger(message.create_time_ms) && message.create_time_ms >= 0 ? message.create_time_ms : undefined;
    let event;
    try {
      ({event} = await this.store('admit', {installation_id: state.installation_id, revision: state.revision,
        event_id: id, text, context_token: message.context_token, created_at: timestamp}));
    } catch (error) {
      // A poison event must not prevent later messages in this batch or keep
      // retrying a conflicting identity forever. Storage outages still retry.
      if (!['CONFLICT', 'INVALID_PARAMS'].includes(error.code)) throw error;
      this.error = '忽略了一条格式异常或身份冲突的微信消息。';
      return;
    }
    if (event.duplicate) return;
    if (!supported || !message.context_token || timestamp === undefined
      || message.create_time_ms > this.now() + 60000 || this.now() - message.create_time_ms > MAX_AGE) {
      await this.store('event', {installation_id: event.installation_id, event_id: id, state: 'rejected'});
      if (message.context_token) await this.reply(event, '当前只支持新收到的文字消息，请重新发送文字。', {control: true});
      return;
    }
    if (text.startsWith('/') || this.works.handles(text)) {
      try {
        if (this.works.handles(text)) await this.works.control(event);
        else await this.control(event, state);
      }
      catch { await this.reply(event, '这项操作未完成，请在桌面检查当前绑定与状态。', {control: true}); }
      await this.store('event', {installation_id: event.installation_id, event_id: id, state: 'completed'});
      return;
    }
    try {
      await this.authorize(event);
      await this.store('event', {installation_id: event.installation_id, event_id: id, state: 'submitted'});
      const accepted = await this.product.sendWeixin(event);
      if (!accepted.accepted) throw blocked();
      const job = this.finish(event).catch(() => {}).finally(() => this.jobs.delete(job));
      this.jobs.add(job);
    } catch (error) {
      await this.store('event', {installation_id: event.installation_id, event_id: id, state: error.code === 'conversation_busy' ? 'rejected' : 'unknown'});
      await this.reply(event, error.code === 'conversation_busy' ? '这个会话正在回复，请稍后再发，或用 /stop 停止。'
        : '这条消息未能完成接入，请在桌面检查模型连接与会话状态后重新发送。', {control: true});
    }
  }

  async control(event, state) {
    const [name, ...arguments_] = event.text.trim().split(/\s+/);
    const value = arguments_.join(' ');
    const current = await this.authorize(event);
    if (name === '/stop') {
      await this.product.cancel(event.conversation_id);
      await this.reply(event, '已停止当前会话的回复；已发生的操作不会撤销。', {control: true});
    } else if (name === '/help') await this.reply(event, HELP, {control: true});
    else if (name === '/status') {
      const conversation = await this.product.conversation(event.conversation_id);
      await this.reply(event, `当前会话：${conversation.title}\n${this.product.summary(conversation).status === 'running' ? '正在回复' : '可以发送文字'}\n${HELP}`, {control: true});
    } else if (name === '/sessions') {
      const rows = (await this.product.rpc('conversation.list', {archived_filter: 'active', limit: 20})).conversations;
      const shortId = item => item.conversation_id.replace(/^conversation_/, '').slice(0, 12);
      await this.reply(event, '发送 /use 编号 切换；/new 新建。\n' + rows.map(item => `${shortId(item)}  ${item.title}`).join('\n'), {control: true});
    } else if (name === '/new' || name === '/use') {
      let conversationId;
      if (name === '/use') {
        if (!/^[A-Za-z0-9_-]{8,128}$/.test(value)) throw blocked();
        const rows = (await this.product.rpc('conversation.list', {archived_filter: 'active', limit: 200})).conversations;
        const matches = rows.filter(item => item.conversation_id === value || item.conversation_id.replace(/^conversation_/, '').startsWith(value));
        if (matches.length !== 1) throw blocked();
        conversationId = matches[0].conversation_id;
      }
      await this.product.cancelWeixinCommands(state.installation_id);
      const {state: selected} = await this.store('select', {expected_revision: current.revision, ...(conversationId ? {conversation_id: conversationId} : {})});
      const conversation = await this.product.conversation(selected.conversation_id);
      await this.reply({...event, revision: selected.revision, conversation_id: selected.conversation_id}, `已切换到：${conversation.title}`, {control: true});
    } else await this.reply(event, HELP, {control: true});
  }

  async finish(event) {
    const result = await this.product.finishWeixin(event);
    try { await this.authorize(event); } catch { return; }
    if (result.state === 'completed' && result.text) {
      await this.reply(event, result.text);
      await this.store('event', {installation_id: event.installation_id, event_id: event.event_id, state: 'completed', result_ref: result.ref});
    } else {
      await this.reply(event, result.state === 'cancelled' ? '这轮回复已停止。' : '这轮回复未能确认完成，请到桌面查看；不会自动重新执行。', {control: true});
      await this.store('event', {installation_id: event.installation_id, event_id: event.event_id, state: 'unknown'});
    }
  }

  async reply(event, text, {control = false} = {}) {
    try { await this.authorize(event); } catch { return; }
    const body = boundedReply(text);
    if (!body) return;
    // One durable delivery owns all segments. A partial failure cannot leave
    // later segments as independent pending sends that overtake the first one.
    await this.store('enqueue', {
      installation_id: event.installation_id, revision: event.revision,
      key: `${event.command_id}:${control ? 'control' : 'reply'}`,
      ...(control ? {} : {conversation_id: event.conversation_id, command_id: event.command_id}),
      text: body, context_token: event.context_token,
    });
  }

  async notifyWorks() {
    if (this.closed || !this.product.workRequest) return;
    const state = await this.state();
    if (!state.bound || !state.enabled || !state.context_token || this.phase === 'login_required') return;
    const {settings} = await this.product.rpc('work.settings.get');
    if (settings.channels?.weixin !== true) return;
    const {events} = await this.product.rpc('work.notifications', {limit: 20, channel: 'weixin', installation_id: state.installation_id, revision: state.revision});
    for (const event of events) {
      const eventTime = Number(event.created_at);
      // Notifications have a bounded useful life. They remain queryable in
      // work history but are never blindly replayed after a long outage.
      if (!Number.isFinite(eventTime) || this.now() - eventTime > MAX_AGE || eventTime > this.now() + 60000) {
        await this.product.rpc('work.notifications.ack', {event_id: event.event_id, channel: 'weixin', status: 'suppressed',
          installation_id: state.installation_id, revision: state.revision});
        continue;
      }
      if (settings.notifications?.[event.kind] === false || event.work?.notifications?.[event.kind] === false) continue;
      try {
        await this.authorize({...state, conversation_id: state.conversation_id});
        const claimed = await this.product.rpc('work.notifications.claim', {event_id: event.event_id, channel: 'weixin',
          installation_id: state.installation_id, revision: state.revision});
        const destination = claimed.event?.deliveries?.weixin;
        if (destination?.status !== 'queued' || destination.installation_id !== state.installation_id || destination.revision !== state.revision) continue;
        await this.authorize({...state, conversation_id: state.conversation_id});
        // Repeating this exact immutable enqueue is safe even after a crash
        // between destination claim and insertion. Outbox terminal rows survive
        // and cannot become another send; another destination never adopts it.
        const {delivery} = await this.store('enqueue', {installation_id: state.installation_id, revision: state.revision,
          key: `work-notification:${event.event_id}`, text: boundedReply(`${event.title}\n${event.text}\n/work ${event.work_id} 查看详情`),
          });
        if (['sent', 'failed', 'unknown', 'cancelled'].includes(delivery.status))
          await this.product.rpc('work.notifications.ack', {event_id: event.event_id, channel: 'weixin', installation_id: state.installation_id, revision: state.revision,
            status: delivery.status === 'cancelled' ? 'suppressed' : delivery.status});
      } catch (error) {
        if (['CONFLICT', 'weixin_scope_revoked'].includes(error.code)) return;
        throw error;
      }
    }
  }

  async canDeliverWork(delivery) {
    if (!delivery.key?.startsWith('work-notification:')) return true;
    const {settings} = await this.product.rpc('work.settings.get');
    if (settings.channels?.weixin !== true) return false;
    const {event} = await this.product.rpc('work.notifications.get', {event_id: delivery.key.slice('work-notification:'.length)});
    const destination = event?.deliveries?.weixin;
    return Boolean(event && destination?.status === 'queued' && destination.installation_id === delivery.installation_id
      && destination.revision === delivery.revision && event.work?.intent === 'watching'
      && event.eligible_channels?.weixin !== false && !event.work?.observation?.withdrawn && this.now() - Number(event.created_at) <= MAX_AGE
      && settings.notifications?.[event.kind] !== false && event.work?.notifications?.[event.kind] !== false);
  }

  async reconcile(state) {
    const {events} = await this.store('events', {installation_id: state.installation_id, states: ['queued', 'unknown'], limit: 200});
    for (const event of events) {
      if (event.revision !== state.revision) continue;
      if (event.state === 'queued') await this.store('event', {installation_id: event.installation_id, event_id: event.event_id, state: 'rejected'});
      try {
        const result = await this.product.weixinResult(event).catch(() => null);
        if (result?.state === 'completed' && result.text) await this.reply(event, result.text);
        else await this.reply(event, '上次连接中断，有一条消息未能确认完成；请查看桌面或重新发起，不会自动重跑。', {control: true});
      } catch (error) {
        // An immutable receipt may already contain the original failure notice.
        // Keep it; changing its body or replaying the input is not recovery.
        if (error.code !== 'CONFLICT') this.error = '有历史微信消息需要在桌面核对，未自动重跑。';
      }
    }
  }

  pump() {
    if (this.pumping) return this.pumping;
    this.pumping = this.dispatchOutbox().finally(() => { this.pumping = null; });
    return this.pumping;
  }

  async dispatchOutbox() {
    if (this.closed || !this.connection || this.connection.signal.aborted || this.phase === 'login_required') return;
    const state = await this.state();
    if (!state.bound || !state.enabled) return;
    const {deliveries} = await this.store('outbox', {installation_id: state.installation_id, limit: 4});
    for (const pending of deliveries) {
      if (this.closed || !this.connection || this.connection.signal.aborted) return;
      const {delivery} = await this.store('claim', {delivery_id: pending.delivery_id});
      if (delivery.status !== 'sending' || !delivery.state) continue;
      let status = 'unknown', receipt, acceptedParts = 0, failedPart;
      try {
        await this.authorize({...delivery, conversation_id: delivery.state.conversation_id});
        if (delivery.command_id && !await this.product.canDeliverWeixin(delivery) || !await this.canDeliverWork(delivery)) {
          status = 'cancelled';
        } else {
          const controller = new AbortController(); this.sendController = controller;
          const parts = weixinTextParts(delivery.text);
          for (let index = 0; index < parts.length; index++) {
            failedPart = index;
            await this.authorize({...delivery, conversation_id: delivery.state.conversation_id});
            if (delivery.command_id && !await this.product.canDeliverWeixin(delivery) || !await this.canDeliverWork(delivery)) throw blocked();
            await this.api(delivery.state.base_url).sendText({token: delivery.state.token, to: delivery.state.peer_id,
              text: parts[index], contextToken: delivery.context_token || delivery.state.context_token,
              clientId: `${delivery.delivery_id}:${index}`, signal: AbortSignal.any([controller.signal, this.connection.signal])});
            acceptedParts++;
          }
          status = 'sent'; receipt = {accepted: true, client_id: delivery.delivery_id, accepted_parts: acceptedParts};
        }
      } catch (error) {
        status = error.code === 'weixin_scope_revoked' ? 'cancelled' : error.deliveryState === 'rejected' ? 'failed' : 'unknown';
        receipt = {accepted: false, accepted_parts: acceptedParts, ...(failedPart !== undefined ? {failed_part: failedPart} : {}),
          reason: status === 'unknown' ? 'unconfirmed' : status === 'cancelled' ? 'revoked' : 'provider_rejected'};
        if (error.credentialStale) { this.phase = 'login_required'; this.error = '微信登录已失效，请重新扫码。'; this.connection?.abort(); }
        else this.error = status === 'unknown' ? '有回复未能确认发送，请在桌面查看；不会自动重发。' : '有回复未能送出，请在桌面查看发送状态。';
      } finally { this.sendController = null; }
      await this.store('ack', {delivery_id: delivery.delivery_id, status, ...(receipt ? {receipt} : {})});
      if (delivery.key?.startsWith('work-notification:')) await this.product.rpc('work.notifications.ack', {
        event_id: delivery.key.slice('work-notification:'.length), channel: 'weixin',
        installation_id: delivery.installation_id, revision: delivery.revision, status: status === 'cancelled' ? 'suppressed' : status});
      if (status !== 'sent') return;
    }
  }

  async stop() {
    if (this.closed) return;
    this.closed = true;
    clearInterval(this.deliveryTimer);
    this.cancelLogin();
    await this.disconnectTransport();
    const state = await this.state().catch(() => null);
    if (state?.installation_id) await this.product.cancelWeixinCommands(state.installation_id);
    await Promise.allSettled([...this.jobs]);
    await this.loginTask;
  }
}
