/** Product channel: no model loop, transcript or authority is owned here. */
import {createHash, randomUUID} from 'node:crypto';
import {WeixinApi, validateWeixinBaseUrl} from './weixin-api.mjs';
import {WeixinNumberedLists, LIST_TEXT} from './weixin-numbered-list.mjs';
import {notifyReminders, canDeliverReminder, ackReminder} from './weixin-reminders.mjs';
import {WeixinCalendar} from './weixin-calendar.mjs';
import {WeixinInboundMedia, inspectWeixinMedia} from './weixin-media.mjs';
import {WeixinTyping} from './weixin-typing.mjs';
import {WeixinOutboundMedia} from './weixin-media-outbound.mjs';
import {WeixinLifecycle} from './weixin-lifecycle.mjs';

const MAX_AGE = 10 * 60_000;
const LOGIN_TTL = 5 * 60_000;
/**
 * Every prompt delivered to WeChat lives here: one entry answers "what happened"
 * and "what can I do next" for exactly one trigger. Tests import these constants
 * instead of repeating literals.
 */
export const WEIXIN_TEXT = {
  menu: 'Mirally 微信菜单\n直接说想做的事也可以。\n\n切换\n助理（/assistant on） · 会话模式（/assistant off）\n会话（/sessions） · 切换 1（/use 1） · 新对话 [名称]（/new）\n\n查看\n当前会话（/status） · 查看历史（/history）\n日程 · 今日日程 · 明日日程\n\n控制\n停止回复（/stop） · 菜单（/help）\n\n电脑保持 Mirally 联网运行。',
  received: '已收到，正在处理。',
  receivedAssistant: '已收到，助理正在处理。',
  mediaReceived: count => `收到，正在处理附件（${count} 项）。`,
  busySend: '当前会话还在回复上一条消息。等结束后再发，或发送“停止回复”。',
  busySwitch: '当前会话还在回复上一条消息。请等结束，或发送“停止回复”后再切换。',
  stopped: '已停止当前会话的回复。',
  notRunning: '当前会话没有在回复。',
  replyStopped: '这轮回复已停止。',
  replyUnknown: '这轮回复未能确认完成，请到桌面查看；不会自动重新执行。',
  notSubmitted: '这条消息没有送进会话。请在电脑检查模型连接后重新发送。',
  unconfirmed: '消息已送出，但结果还没有确认。请在电脑查看这个会话；不要重复发送。',
  mediaFailedFallback: '附件没有处理成功。请换一个文件或重新发送。',
  truncated: '\n\n…（微信只显示前 14000 字，完整回复请在电脑查看同一会话。）',
  partPrefix: (index, total) => `（${index}/${total}） `,
  invalidContent: '这条消息的内容或时间无效，请重新发送。',
  controlFailed: '这项操作未完成，请在桌面检查当前绑定与状态。',
  answerSubmitted: '回答已提交，Mirally 会在原会话中继续。',
  answerRecorded: remaining => `已记录。还需回答第 ${remaining} 题。`,
  questionExpired: '问题已失效，请在会话中继续。',
  assistantUsage: '发送“助理”进入助理模式，或发送“会话模式”返回直接会话。',
  assistantCurrent: '当前是助理模式。',
  directCurrent: '当前是直接会话模式。',
  modeAssistant: title => `已进入助理模式。发送“会话模式”可返回会话：${title}。`,
  modeAssistantFallback: '已进入助理模式。发送“会话模式”可返回之前的会话。',
  modeDirect: title => `已返回会话：${title}`,
  approved: '已确认该动作。',
  denied: '已拒绝该动作。',
  statusLine: (title, mode, running) => `当前会话：${title}（${mode === 'assistant' ? '助理' : '直接会话'}）\n${running ? '正在回复' : '可以发送文字'}\n\n会话 · 查看历史 · 菜单`,
  sessionsUsage: '发送“会话”查看第一页，或发送“会话 2”查看下一页。',
  sessionListTitle: page => `最近会话 · 第 ${page} 页`,
  currentMark: ' · 当前',
  sessionListHint: '\n\n发送“切换 1”进入对应会话；也可使用完整标题或编号。',
  sessionPagePrevious: page => `上一页：会话 ${page - 1}`,
  sessionPageNext: page => `下一页：会话 ${page + 1}`,
  sessionPageEmpty: page => `这一页没有会话。上一页：会话 ${page - 1}`,
  sessionsEmpty: '暂无会话。发送“新对话”开始。',
  historyUsage: '用法：/history 查看最近正文；更早记录使用上一页给出的 /history 数字游标。',
  historyTitle: '当前会话的文字历史：\n\n',
  historyEmptyItem: '（此条没有可展示的文字）',
  historyClip: '…（本条过长，只显示前 1600 字）',
  historyOlder: seq => `\n更早记录：/history ${seq}`,
  historyStart: '\n已到当前可读取历史的开头。',
  historyGaps: '\n部分历史暂时无法读取，以上并非完整记录。',
  historyActive: '\n当前仍在回复，最新内容可能尚未完整。',
  historyFooter: '\n这里只显示用户和助手的文字。',
  historyEmpty: '没有更早的可读取文字历史。',
  historyGapsShort: '部分历史暂时无法读取。',
  switchUsage: '发送“切换 完整标题或编号”；“会话”查看列表。',
  switchMatches: '有多个匹配的会话，请选择：\n\n',
  switchMatchesHint: '\n\n发送“切换 1”进入。',
  switchMissing: '未找到这项会话。发送“会话”查看列表，或使用完整标题。',
  switchedCurrent: title => `当前已在：${title}`,
  newNameInvalid: '对话名称请使用 512 字以内的单行文字。',
  switched: title => `已切换到：${title}`,
  reconcileUnknown: '上次连接中断，有一条消息未能确认完成；请查看桌面或重新发起，不会自动重跑。',
  approval: ({targetTitle, title, detail, code}) => `${targetTitle ? `会话：${targetTitle}\n` : ''}动作请求：${title}\n\n${detail}\n\n请完整阅读所有分段后决定。\n确认：/approve ${code}\n拒绝：/deny ${code}\n该确认仅用于当前动作，过期或会话切换后失效。`,
  question: ({targetTitle, index, total, detail, multiSelect}) => `${targetTitle ? `会话：${targetTitle}\n` : ''}Mirally 需要你的回答${total > 1 ? `（${index + 1}/${total}）` : ''}：\n\n${detail}\n\n${multiSelect ? '回复选项数字（多选用逗号分隔），或直接填写文字。' : '回复选项数字或直接填写文字。'}`,
  questionOversize: 'Mirally 正在等待一个较长问题或计划的审核。内容无法在微信完整展示，请到电脑中的原会话查看并回答。',
  expiredIndex: LIST_TEXT,
};
const HELP = WEIXIN_TEXT.menu;
const TEXT_CONTROLS = new Map([['菜单', '/help'], ['帮助', '/help'], ['会话', '/sessions'], ['会话列表', '/sessions'],
  ['当前会话', '/status'], ['新对话', '/new'], ['查看历史', '/history'], ['停止回复', '/stop'], ['助理', '/assistant on'], ['会话模式', '/assistant off']]);
const controlText = text => {
  const exact = TEXT_CONTROLS.get(text);
  if (exact) return exact;
  const page = /^(会话|会话列表)[ \t]+([1-9]\d{0,3})$/.exec(text);
  if (page) return `/sessions ${page[2]}`;
  // Explicit, single-line commands only. Ordinary requests keep their exact
  // wording and go to the selected model conversation, without a router model.
  const match = /^(切换会话|切换|新对话)[ \t]+([^\r\n]+)$/.exec(text);
  return match ? `${match[1] === '新对话' ? '/new' : '/use'} ${match[2].trim()}` : null;
};
const conversationLine = (item, index) => `${index + 1}. ${Array.from(String(item.title ?? '').replace(/\s+/g, ' ')).slice(0, 160).join('')}`;
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
const CLIP_SUFFIX = '…' + WEIXIN_TEXT.truncated;
function boundedReply(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  // reply() bounds before enqueueing and delivery bounds again; the length
  // check comes first so a caller-provided clip marker (model or quoted text
  // can contain the same suffix) cannot bypass the 14000-character limit, and
  // only an actually bounded reply gets the marker.
  const chars = Array.from(raw);
  return chars.length > 14000 ? chars.slice(0, 14000).join('') + CLIP_SUFFIX : chars.join('');
}
export function weixinTextParts(value) {
  return splitText(boundedReply(value));
}
function splitText(text) {
  const all = Array.from(text), parts = [];
  for (let start = 0; start < all.length; start += 3500) parts.push(all.slice(start, start + 3500).join(''));
  return parts.length > 1 ? parts.map((part, index) => WEIXIN_TEXT.partPrefix(index + 1, parts.length) + part) : parts;
}

/** Media failures may echo only this repository's own channel wording. */
const MEDIA_FAILURE_PREFIXES = ['weixin_media', 'weixin_voice_', 'speech_'];
export function mediaFailureText(error) {
  const code = error?.code;
  if (typeof code === 'string' && MEDIA_FAILURE_PREFIXES.some(prefix => code.startsWith(prefix))) {
    const message = typeof error.message === 'string' ? error.message : '';
    const chars = Array.from(message);
    if (chars.length >= 1 && chars.length <= 80 && !/[\r\n]/.test(message)) return message;
  }
  return WEIXIN_TEXT.mediaFailedFallback;
}

/**
 * product.send raises these before (or exactly at) command admission: the
 * message never entered the conversation, so "已送出" would point the user at a
 * result that does not exist. Unlisted errors stay conservatively unknown —
 * the at-most-once rule still forbids replaying them automatically.
 */
const PRE_ADMIT_ERROR_CODES = new Set(['confirmation_required', 'conversation_archived', 'conversation_busy',
  'invalid_request', 'message_too_large', 'model_not_configured', 'remote_scope_revoked', 'unsupported_image_input',
  'weixin_scope_revoked']);
const isPreAdmitRejection = error => PRE_ADMIT_ERROR_CODES.has(error?.code);

export class WeixinChannel {
  constructor(product, {baseUrl, allowLoopback = false, apiFactory, now = Date.now} = {}) {
    this.product = product;
    this.allowLoopback = allowLoopback;
    this.baseUrl = validateWeixinBaseUrl(baseUrl ?? 'https://ilinkai.weixin.qq.com', {allowLoopback});
    this.apiFactory = apiFactory ?? (origin => new WeixinApi({baseUrl: origin, allowLoopback, botAgent: 'Mirally/0.1.0'}));
    this.now = now;
    this.text = WEIXIN_TEXT;
    this.phase = 'unbound';
    this.error = '';
    this.jobs = new Set();
    this.receipts = new Set();
    // Commands a /stop actually cancelled, keyed by command/turn id.
    this.cancelledStops = new Map();
    // A /stop whose cancel result is still in flight, so a racing finish can
    // wait for the exact cancelled ids instead of guessing from the conversation.
    this.stoppingStops = new Map();
    this.numberedLists = new WeixinNumberedLists(this.now);
    this.closed = false;
    this.management = Promise.resolve();
    this.calendar = new WeixinCalendar(this);
    this.media = new WeixinInboundMedia(product, {fixtureOrigin: allowLoopback && this.baseUrl.startsWith('http:') ? this.baseUrl : undefined});
    this.outboundMedia = new WeixinOutboundMedia(this, {fixtureOrigin: allowLoopback && this.baseUrl.startsWith('http:') ? this.baseUrl : undefined});
    this.lifecycle = new WeixinLifecycle(this); this.lifecycleCurrent = null;
    this.typing = new WeixinTyping(this);
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
      mode: state.mode, assistant_conversation_id: state.assistant_conversation_id, direct_conversation_id: state.direct_conversation_id,
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
      } else if (method === 'weixin.mode') {
        const old = await this.state();
        if (!Number.isSafeInteger(params.expected_revision) || params.expected_revision !== old.revision) throw blocked();
        if (!['assistant','direct'].includes(params.mode)) throw Object.assign(new Error('请选择助理或会话模式。'),{code:'invalid_request',status:400});
        if (old.mode === params.mode) return this.status();
        if (this.hasActiveInput(old.installation_id)) throw Object.assign(new Error('微信会话正在处理，请结束后切换模式。'),{code:'conversation_busy',status:409});
        await this.disconnectTransport();
        const {state} = await this.store('mode', {expected_revision:old.revision,mode:params.mode});
        if (state.assistant_conversation_id) this.product.broadcast('session-metadata', {session_id:state.assistant_conversation_id});
        if (state.enabled) this.connect();
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
    if (this.lifecycleCurrent) {this.lifecycle.notify(this.lifecycleCurrent, false);this.lifecycleCurrent = null;}
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
        if (!this.lifecycleCurrent) {this.lifecycleCurrent = state;this.lifecycle.notify(state, true);}
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

  hasActiveInput(installationId) {
    for (const [native, commandId] of this.product.activeCommands ?? []) {
      const policy=this.product.commandPolicies?.get(commandId);
      if (policy?.origin==='weixin' && policy.installation_id===installationId && this.product.handles?.get(native)?.agent?.status==='running') return true;
    }
    return false;
  }

  /** An unreadable status still allows stopping; only a confirmed idle turn is refused. */
  async isRunning(conversationId) {
    try {
      const conversation = await this.product.conversation(conversationId);
      return this.product.summary(conversation).status === 'running';
    } catch { return true; }
  }

  /** Remember exactly which commands /stop cancelled so each yields one notice. */
  rememberStoppedCommands(commandIds) {
    if (!Array.isArray(commandIds)) return;
    for (const commandId of commandIds)
      if (typeof commandId === 'string' && commandId) this.cancelledStops.set(commandId, true);
    // Bound memory across a long session; entries are per stop, never a window.
    while (this.cancelledStops.size > 100) this.cancelledStops.delete(this.cancelledStops.keys().next().value);
  }

  /**
   * A cancelled finish suppresses its second notice only when /stop cancelled
   * this exact command/turn. A finish that races with the stop waits for the
   * cancel result instead of matching the whole conversation (which could
   * swallow a newer turn's stop) or a five-second window (which repeated a
   * late one).
   */
  async stoppedByUser(event) {
    if (this.cancelledStops.delete(event.command_id)) return true;
    const pending = this.stoppingStops.get(event.conversation_id);
    if (!pending) return false;
    await pending.promise;
    return this.cancelledStops.delete(event.command_id);
  }

  async receive(state, message) {
    if (!message || message.message_type !== 1 || message.group_id || message.from_user_id !== state.peer_id
      || message.to_user_id && message.to_user_id !== state.bot_id || message.message_state === 1) return;
    const id = eventId(message.message_id ?? message.client_id);
    if (!id) return;
    const items = Array.isArray(message.item_list) ? message.item_list : [];
    let inspected;
    try { inspected = inspectWeixinMedia(items); } catch { inspected = null; }
    const text = inspected?.text ?? '[当前消息包含尚未支持的内容]';
    if (!text || text.length > 100000) return;
    const timestamp = Number.isSafeInteger(message.create_time_ms) && message.create_time_ms >= 0 ? message.create_time_ms : undefined;
    let event;
    try {
      ({event} = await this.store('admit', {installation_id: state.installation_id, revision: state.revision,
        event_id: id, text, ...(inspected?.hasMedia ? {content_identity:inspected.content_identity} : {}), context_token: message.context_token, created_at: timestamp}));
    } catch (error) {
      // A poison event must not prevent later messages in this batch or keep
      // retrying a conflicting identity forever. Storage outages still retry.
      if (!['CONFLICT', 'INVALID_PARAMS'].includes(error.code)) throw error;
      this.error = '忽略了一条格式异常或身份冲突的微信消息。';
      return;
    }
    if (event.duplicate) return;
    if (!inspected || !message.context_token || timestamp === undefined
      || message.create_time_ms > this.now() + 60000 || this.now() - message.create_time_ms > MAX_AGE) {
      await this.store('event', {installation_id: event.installation_id, event_id: id, state: 'rejected'});
      if (message.context_token) await this.reply(event, this.text.invalidContent, {control: true});
      return;
    }
    if (!inspected.hasMedia && (text.startsWith('/') || controlText(text) || this.calendar.handles(text))) {
      try {
        if (this.calendar.handles(text)) await this.calendar.control(event);
        else await this.control(event, state);
      }
      catch { await this.reply(event, this.text.controlFailed, {control: true}); }
      await this.store('event', {installation_id: event.installation_id, event_id: id, state: 'completed'});
      return;
    }
    if (!inspected.hasMedia) try {
      const answer = await this.product.nativeInteractions?.answerActiveRemote(event, text);
      if (answer) {
        if (answer.complete) await this.reply(event, this.text.answerSubmitted, {control:true});
        await this.store('event', {installation_id:event.installation_id, event_id:id, state:'completed'});
        return;
      }
    } catch (error) {
      await this.reply(event, error.message || this.text.questionExpired, {control:true});
      await this.store('event', {installation_id:event.installation_id, event_id:id, state:'rejected'});
      return;
    }
    const stopTyping = this.typing.begin(event);
    let submitted = false;
    let mediaPrepared = false;
    try {
      await this.authorize(event);
      let input = event;
      if (inspected.hasMedia) {
        await this.reply({...event, command_id:`${event.command_id}:media-receipt`}, this.text.mediaReceived(items.length), {control:true});
        void this.pump().catch(() => {});
        const media = await this.media.prepare(event, items, {authorize: scope => this.authorize(scope), signal:this.connection?.signal ?? new AbortController().signal});
        mediaPrepared = true;
        input = {...event, ...media};
      }
      await this.store('event', {installation_id: event.installation_id, event_id: id, state: 'submitted'});
      submitted = true;
      const accepted = await this.product.sendWeixin(input);
      if (!accepted.accepted) throw blocked();
      // This is transport feedback, not another model message or a completed
      // result. A failed receipt must never reject or replay an admitted turn.
      if (!inspected.hasMedia) await this.acknowledge(event).catch(() => { this.error = '消息已接入，但接收反馈未能发送。'; });
      const job = this.finish(event).catch(() => {}).finally(() => {
        stopTyping();
        this.receipts.delete(`received:${event.command_id}`);
        this.jobs.delete(job);
      });
      this.jobs.add(job);
    } catch (error) {
      stopTyping();
      // A definite pre-admit rejection is not an unknown submission: saying the
      // message was sent would send the user to a run that never existed.
      const rejected = !submitted || isPreAdmitRejection(error);
      await this.store('event', {installation_id: event.installation_id, event_id: id, state: rejected ? 'rejected' : 'unknown'});
      await this.reply(event, this.failureText(error, {hasMedia: inspected.hasMedia, mediaPrepared, submitted: submitted && !rejected}), {control: true});
    }
  }

  /**
   * One failure, one actionable sentence: a busy turn, a rejected attachment
   * (only with this repository's own wording) or a message that never reached
   * the conversation are not the same as a submitted turn with an unknown result.
   */
  failureText(error, {hasMedia, mediaPrepared, submitted}) {
    if (error.code === 'conversation_busy') return this.text.busySend;
    // Media that never finished preparing is an attachment problem even when
    // this channel does not know the code; never blame the model connection.
    if (hasMedia && !mediaPrepared) return mediaFailureText(error);
    return submitted ? this.text.unconfirmed : this.text.notSubmitted;
  }

  async control(event, state) {
    const text = event.text.trim();
    const [, name, argument = ''] = /^(\S+)(?:\s+([\s\S]*))?$/.exec(controlText(text) ?? text);
    const value = argument.trim();
    const current = await this.authorize(event);
    if (name === '/assistant') {
      const mode = value === '' || value === 'on' ? 'assistant' : value === 'off' ? 'direct' : null;
      if (!mode) { await this.reply(event, this.text.assistantUsage, {control:true}); return; }
      if (current.mode === mode) {await this.reply(event, mode === 'assistant' ? this.text.assistantCurrent : this.text.directCurrent, {control:true});return;}
      if (this.hasActiveInput(current.installation_id)) {await this.reply(event, this.text.busySwitch, {control:true});return;}
      const {state:selected}=await this.store('mode',{expected_revision:current.revision,mode});
      if (selected.assistant_conversation_id) this.product.broadcast('session-metadata', {session_id:selected.assistant_conversation_id});
      // Switching modes keeps the other conversation; name it so the user can go back.
      const direct = mode === 'assistant' && selected.direct_conversation_id
        ? await this.product.conversation(selected.direct_conversation_id).catch(() => null) : null;
      const conversation = mode === 'direct' ? await this.product.conversation(selected.conversation_id) : null;
      await this.reply({...event,revision:selected.revision,conversation_id:selected.conversation_id},
        mode==='assistant' ? (direct?.title ? this.text.modeAssistant(direct.title) : this.text.modeAssistantFallback)
          : this.text.modeDirect(conversation.title), {control:true});
    } else if (name === '/approve' || name === '/deny') {
      if (!/^(?:\d{9}|[a-f0-9]{16})$/.test(value)) throw blocked();
      await this.product.actionGate.decideRemote(event, value, name === '/approve');
      await this.reply(event, name === '/approve' ? this.text.approved : this.text.denied, {control: true});
    } else if (name === '/answer') {
      const match=/^([a-f0-9]{16})\s+([\s\S]+)$/.exec(value);
      if (!match || !this.product.nativeInteractions) throw blocked();
      try {
        const result=await this.product.nativeInteractions.answerRemote(event,match[1],match[2]);
        await this.reply(event,result.complete?this.text.answerSubmitted:this.text.answerRecorded(result.remaining.join('、')),{control:true});
      } catch(error) { await this.reply(event,error.message || this.text.questionExpired,{control:true}); }
    } else if (name === '/stop') {
      if (!await this.isRunning(event.conversation_id)) {
        await this.reply(event, this.text.notRunning, {control: true});
        return;
      }
      // The cancelled turn must not report a second "stopped" notice. Record the
      // exact command ids this stop cancels; a finish that settles while cancel
      // is still in flight waits for that result instead of consuming a
      // conversation-wide marker.
      let release;
      const pending = {promise: new Promise(resolve => { release = resolve; })};
      this.stoppingStops.set(event.conversation_id, pending);
      try {
        const stopped = await this.product.cancel(event.conversation_id);
        this.rememberStoppedCommands(stopped?.cancelled_command_ids);
      } finally {
        if (this.stoppingStops.get(event.conversation_id) === pending) this.stoppingStops.delete(event.conversation_id);
        release();
      }
      await this.reply(event, this.text.stopped, {control: true});
    } else if (name === '/help') await this.reply(event, HELP, {control: true});
    else if (name === '/status') {
      const conversation = await this.product.conversation(event.conversation_id);
      await this.reply(event, this.text.statusLine(conversation.title, current.mode,
        this.product.summary(conversation).status === 'running'), {control: true});
    } else if (name === '/sessions') {
      if (value && !/^[1-9]\d{0,3}$/.test(value)) {
        await this.reply(event, this.text.sessionsUsage, {control: true});
        return;
      }
      const page = value ? Number(value) : 1;
      const candidates = (await this.product.rpc('conversation.list', {archived_filter: 'active', limit: 21, offset: (page - 1) * 20})).conversations;
      const rows = candidates.slice(0, 20);
      await this.authorize(event);
      this.numberedLists.stage('sessions', event, rows.map(item => item.conversation_id));
      const navigation = [page > 1 ? this.text.sessionPagePrevious(page) : '',
        candidates.length > 20 ? this.text.sessionPageNext(page) : ''].filter(Boolean).join(' · ');
      await this.reply(event, rows.length ? this.text.sessionListTitle(page) + '\n\n' + rows.map((item, index) =>
        `${conversationLine(item, index)}${item.conversation_id === event.conversation_id ? this.text.currentMark : ''}`).join('\n')
        + this.text.sessionListHint + (navigation ? `\n${navigation}` : '')
        : page > 1 ? this.text.sessionPageEmpty(page) : this.text.sessionsEmpty, {control: true});
    } else if (name === '/history') {
      if (value && (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)))) {
        await this.reply(event, this.text.historyUsage, {control: true});
        return;
      }
      const before = value ? Number(value) : undefined;
      const history = await this.product.history(event.conversation_id);
      await this.authorize(event);
      const messages = history.messages.filter(message => ['user', 'assistant'].includes(message.role)
        && Number.isSafeInteger(message.seq) && message.seq >= 0 && (before === undefined || message.seq < before));
      const selected = messages.slice(-6);
      const lines = selected.map(message => {
        const chars = Array.from(String(message.text ?? ''));
        return `${message.role === 'user' ? '你' : 'Mirally'}：${chars.slice(0, 1600).join('') || this.text.historyEmptyItem}${chars.length > 1600 ? this.text.historyClip : ''}`;
      });
      const older = messages.length > selected.length ? this.text.historyOlder(selected[0].seq) : this.text.historyStart;
      await this.reply(event, selected.length ? this.text.historyTitle + lines.join('\n\n') + older
        + (history.gaps?.length ? this.text.historyGaps : '')
        + (history.active_command_ids?.length ? this.text.historyActive : '')
        + this.text.historyFooter
        : this.text.historyEmpty + (history.gaps?.length ? this.text.historyGapsShort : ''), {control: true});
    } else if (name === '/new' || name === '/use') {
      if (current.mode === 'assistant' && this.hasActiveInput(current.installation_id)) { await this.reply(event, this.text.busySwitch, {control:true}); return; }
      let conversationId;
      if (name === '/use') {
        if (!value || Array.from(value).length > 512 || /[\r\n]/.test(value)) {
          await this.reply(event, this.text.switchUsage, {control: true});
          return;
        }
        await this.authorize(event);
        let selector;
        try { selector = this.numberedLists.select('sessions', event, value); }
        catch (error) { await this.reply(event, error.message, {control: true}); return; }
        const {conversations: matches} = await this.product.rpc('internal.conversation.resolve', {selector: selector ?? value});
        await this.authorize(event);
        if (matches.length !== 1) {
          if (matches.length) this.numberedLists.stage('sessions',event,matches.slice(0,8).map(item=>item.conversation_id));
          await this.reply(event, matches.length ? this.text.switchMatches
            + matches.slice(0, 8).map((item,index) => conversationLine(item,index)).join('\n') + this.text.switchMatchesHint
            : this.text.switchMissing, {control: true});
          return;
        }
        conversationId = matches[0].conversation_id;
        if (conversationId === current.conversation_id) {
          await this.reply(event, this.text.switchedCurrent(matches[0].title), {control: true});
          return;
        }
      } else if (value) {
        if (Array.from(value).length > 512 || /[\r\n]/.test(value)) {
          await this.reply(event, this.text.newNameInvalid, {control: true});
          return;
        }
        await this.authorize(event);
        ({conversation_id: conversationId} = await this.product.rpc('conversation.ensure', {
          conversation_id: 'conversation_weixin_' + createHash('sha256').update(event.installation_id + '\0' + event.event_id).digest('hex').slice(0, 32),
          title: value,
        }));
      }
      await this.authorize(event);
      await this.product.cancelWeixinCommands(current.installation_id);
      const {state: selected} = await this.store('select', {expected_revision: current.revision, ...(conversationId ? {conversation_id: conversationId} : {})});
      const conversation = await this.product.conversation(selected.conversation_id);
      await this.reply({...event, revision: selected.revision, conversation_id: selected.conversation_id}, this.text.switched(conversation.title), {control: true});
    } else await this.reply(event, HELP, {control: true});
  }

  async finish(event) {
    const result = await this.product.finishWeixin(event);
    this.receipts.delete(`received:${event.command_id}`);
    try { await this.authorize(event); } catch { return; }
    if (result.state === 'completed' && result.text) {
      await this.reply(event, result.text);
      await this.store('event', {installation_id: event.installation_id, event_id: event.event_id, state: 'completed', result_ref: result.ref});
    } else if (result.state === 'cancelled' && await this.stoppedByUser(event)) {
      // /stop already reported the stop; a second notice would repeat one state twice.
      await this.store('event', {installation_id: event.installation_id, event_id: event.event_id, state: 'unknown'});
    } else {
      await this.reply(event, result.state === 'cancelled' ? this.text.replyStopped : this.text.replyUnknown, {control: true});
      await this.store('event', {installation_id: event.installation_id, event_id: event.event_id, state: 'unknown'});
    }
  }

  async acknowledge(event) {
    const scope = await this.authorize(event);
    const key = `received:${event.command_id}`;
    this.receipts.add(key);
    try {
      await this.store('enqueue', {
        installation_id: event.installation_id, revision: event.revision,
        key, conversation_id: event.conversation_id,
        text: scope.mode === 'assistant' ? this.text.receivedAssistant : this.text.received, context_token: event.context_token,
      });
    } catch (error) { this.receipts.delete(key); throw error; }
    // The network may be slow. Completion and further input must not wait for
    // the receipt to be delivered; the ordered outbox owns both deliveries.
    void this.pump().catch(() => {});
  }

  canDeliverReceipt(delivery) {
    // Waiting feedback belongs to an active turn in this process. Never replay
    // an old "processing" notice after completion, interruption or restart.
    return !delivery.key?.startsWith('received:') || this.receipts.has(delivery.key);
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

  async requestActionApproval(value, scope, code) {
    const state = await this.authorize(scope);
    if (!this.product.actionGate.remoteDeliveryCurrent(value.id, scope) || !state.context_token) throw blocked();
    const target = scope.target_conversation_id ? await this.product.conversation(scope.target_conversation_id) : null;
    if (target) await this.authorize(scope);
    const text = this.text.approval({targetTitle: target?.title, title: value.title, detail: value.detail, code});
    await this.store('enqueue', {installation_id:scope.installation_id, revision:scope.revision,
      conversation_id:scope.conversation_id, key:`action-approval:${value.id}`, text, context_token:state.context_token});
    void this.pump().catch(() => {});
  }

  async requestUserQuestion(value, scope, code, index = 0) {
    const state=await this.authorize(scope);
    if (!this.product.nativeInteractions?.remoteDeliveryCurrent(value.id,scope,index) || !state.context_token) throw blocked();
    const question=value.questions[index];
    if (!question) throw blocked();
    const target = scope.target_conversation_id ? await this.product.conversation(scope.target_conversation_id) : null;
    if (target) await this.authorize(scope);
    const detail=`${question.header?question.header+' · ':''}${question.question}`
      +(question.detail?`\n${question.detail}`:'')
      +(question.options?.length?'\n\n'+question.options.map((item,n)=>`${n+1}. ${item.label}${item.description?'：'+item.description:''}`).join('\n'):'');
    const text=this.text.question({targetTitle: target?.title, index, total: value.questions.length, detail, multiSelect: question.multiSelect});
    if (text.length>24_000) {
      await this.store('enqueue',{installation_id:scope.installation_id,revision:scope.revision,conversation_id:scope.conversation_id,
        key:`native-question:${value.id}:${index}:oversize`,text:this.text.questionOversize,context_token:state.context_token});
    } else await this.store('enqueue',{installation_id:scope.installation_id,revision:scope.revision,conversation_id:scope.conversation_id,
      key:`native-question:${value.id}:${index}`,text,context_token:state.context_token});
    void this.pump().catch(()=>{});
  }

  canDeliverAction(delivery) {
    if (delivery.key?.startsWith('native-question:')) {
      const match=/^native-question:([^:]+):(\d+)(?::oversize)?$/.exec(delivery.key);
      return !!match && this.product.nativeInteractions?.remoteDeliveryCurrent(match[1],delivery,Number(match[2]))===true;
    }
    if (!delivery.key?.startsWith('action-approval:')) return true;
    return this.product.actionGate.remoteDeliveryCurrent(delivery.key.slice('action-approval:'.length), {
      installation_id: delivery.installation_id, revision: delivery.revision, conversation_id: delivery.conversation_id});
  }

  async notifyReminders() { return notifyReminders(this); }

  canDeliverWork(delivery) { return !delivery.key?.startsWith('work-notification:'); }

  async reconcile(state) {
    const {events} = await this.store('events', {installation_id: state.installation_id, states: ['queued', 'unknown'], limit: 200});
    for (const event of events) {
      if (event.revision !== state.revision) continue;
      if (event.state === 'queued') await this.store('event', {installation_id: event.installation_id, event_id: event.event_id, state: 'rejected'});
      try {
        const result = await this.product.weixinResult(event).catch(() => null);
        if (result?.state === 'completed' && result.text) await this.reply(event, result.text);
        else await this.reply(event, this.text.reconcileUnknown, {control: true});
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
    const {deliveries} = await this.store('outbox', {installation_id: state.installation_id, limit: 1000});
    for (const pending of deliveries) {
      if (this.closed || !this.connection || this.connection.signal.aborted) return;
      if (this.outboundMedia.isMedia(pending) && await this.outboundMedia.readiness(pending) === 'wait') continue;
      const {delivery} = await this.store('claim', {delivery_id: pending.delivery_id});
      if (delivery.status !== 'sending' || !delivery.state) continue;
      let status = 'unknown', receipt, acceptedParts = 0, failedPart;
      try {
        await this.authorize({...delivery, conversation_id: delivery.state.conversation_id});
        if (!this.canDeliverReceipt(delivery) || delivery.command_id && !await this.product.canDeliverWeixin(delivery) || !await this.canDeliverWork(delivery) || !await canDeliverReminder(this,delivery) || !this.canDeliverAction(delivery)) {
          status = 'cancelled';
        } else {
          const controller = new AbortController(); this.sendController = controller;
          if (this.outboundMedia.isMedia(delivery)) {
            receipt = await this.outboundMedia.dispatch(delivery, AbortSignal.any([controller.signal, this.connection.signal]));
            status = 'sent';
          } else {
          const parts = /^(action-approval|native-question):/.test(delivery.key??'') ? splitText(delivery.text) : weixinTextParts(delivery.text);
          for (let index = 0; index < parts.length; index++) {
            failedPart = index;
            await this.authorize({...delivery, conversation_id: delivery.state.conversation_id});
            if (delivery.command_id && !await this.product.canDeliverWeixin(delivery) || !await this.canDeliverWork(delivery) || !await canDeliverReminder(this,delivery)
              || !this.canDeliverAction(delivery) || !this.canDeliverReceipt(delivery)) throw blocked();
            await this.api(delivery.state.base_url).sendText({token: delivery.state.token, to: delivery.state.peer_id,
              text: parts[index], contextToken: delivery.context_token || delivery.state.context_token,
              clientId: `${delivery.delivery_id}:${index}`, signal: AbortSignal.any([controller.signal, this.connection.signal])});
            acceptedParts++;
          }
          status = 'sent'; receipt = {accepted: true, client_id: delivery.delivery_id, accepted_parts: acceptedParts};
          }
        }
      } catch (error) {
        status = error.code === 'weixin_scope_revoked' ? 'cancelled' : error.deliveryState === 'rejected' ? 'failed' : 'unknown';
        receipt = {accepted: false, accepted_parts: acceptedParts, ...(failedPart !== undefined ? {failed_part: failedPart} : {}),
          reason: status === 'unknown' ? 'unconfirmed' : status === 'cancelled' ? 'revoked' : 'provider_rejected'};
        if (error.credentialStale) { this.phase = 'login_required'; this.error = '微信登录已失效，请重新扫码。'; this.connection?.abort(); }
        else this.error = status === 'unknown' ? '有回复未能确认发送，请在桌面查看；不会自动重发。' : '有回复未能送出，请在桌面查看发送状态。';
      } finally { this.sendController = null; }
      const acknowledged = await this.store('ack', {delivery_id: delivery.delivery_id, status, ...(receipt ? {receipt} : {})});
      await ackReminder(this,delivery,acknowledged.delivery?.status ?? status);
      this.numberedLists.settle(delivery.key, status === 'sent' && acknowledged.delivery?.status === 'sent');
      if (status === 'sent' && acknowledged.delivery?.status === 'sent' && receipt?.accepted === true
        && receipt.accepted_parts === splitText(delivery.text).length && delivery.key?.startsWith('action-approval:')) this.product.actionGate.remoteDelivered(
        delivery.key.slice('action-approval:'.length), {installation_id:delivery.installation_id, revision:delivery.revision, conversation_id:delivery.conversation_id});
      if (status==='sent' && acknowledged.delivery?.status==='sent' && receipt?.accepted===true
        && receipt.accepted_parts===splitText(delivery.text).length && delivery.key?.startsWith('native-question:') && !delivery.key.endsWith(':oversize')) {
        const match=/^native-question:([^:]+):(\d+)$/.exec(delivery.key);
        if (match) this.product.nativeInteractions?.remoteDelivered(match[1],delivery,Number(match[2]));
      }
      if (status !== 'sent' && !(status === 'cancelled' && delivery.key?.startsWith('received:'))) return;
    }
  }

  async stop() {
    this.typing.stop();
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
