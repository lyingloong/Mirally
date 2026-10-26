import {randomBytes} from 'node:crypto';

// Independently implemented from Tencent's public iLink protocol; see README.md.
export const WEIXIN_PROTOCOL_REFERENCE = Object.freeze({
  version: '2.4.10', commit: '874637a0dbc24050f72d1385add4fc6170a2d726',
});
const DEFAULT_BASE_URL = 'https://ilinkai.weixin.qq.com';
const QR_STATES = new Set(['wait', 'scaned', 'confirmed', 'expired', 'need_verifycode',
  'verify_code_blocked', 'scaned_but_redirect', 'binded_redirect']);
const ID_FIELDS = new Set(['message_id', 'msg_id', 'svr_id']);
const MAX_RESPONSE_BYTES = 1024 * 1024;

/** Sanitized wire errors never retain request URLs, response bodies or fetch causes. */
export class WeixinApiError extends Error {
  constructor(message, {code = 'protocol', kind = 'protocol', deliveryState, httpStatus} = {}) {
    super(message);
    this.name = 'WeixinApiError';
    this.code = code;
    this.kind = kind;
    this.credentialStale = code === -14;
    if (deliveryState) this.deliveryState = deliveryState;
    if (httpStatus !== undefined) this.httpStatus = httpStatus;
  }
}

function invalid(field, kind = 'schema') {
  return new WeixinApiError(`微信接口 ${field} 无效`, {code: kind, kind});
}

function textValue(value, field, {max = 16384, empty = false} = {}) {
  if (typeof value !== 'string' || (!empty && !value.trim()) || Buffer.byteLength(value) > max
    || /[\u0000-\u001f\u007f]/.test(value)) throw invalid(field);
  return value;
}

function officialHost(host) {
  return host === 'weixin.qq.com' || host.endsWith('.weixin.qq.com');
}

/**
 * Validate a credential-bearing iLink origin, including server-issued IDC origins.
 * @param {string} value Origin only; no userinfo, path, search or fragment.
 * @param {{allowLoopback?: boolean}} options Explicit local HTTP fixture permission.
 * @returns {string} Canonical origin without a trailing slash.
 */
export function validateWeixinBaseUrl(value, {allowLoopback = false} = {}) {
  if (typeof value !== 'string' || value.length > 2048 || /[\s\\]/.test(value)
    || !/^https?:\/\/[^/?#]+\/?$/i.test(value)) throw invalid('服务地址', 'configuration');
  let url;
  try { url = new URL(value); } catch { throw invalid('服务地址', 'configuration'); }
  if (url.username || url.password || url.pathname !== '/' || url.search || url.hash)
    throw invalid('服务地址', 'configuration');
  if (url.protocol === 'https:' && !url.port && officialHost(url.hostname)) return url.origin;
  // Check the literal authority too: URL normalizes 127.1, integer and hex IPs.
  if (allowLoopback && url.protocol === 'http:' && /^(?:127\.0\.0\.1|localhost)(?::\d+)?$/i.test(
    value.replace(/^http:\/\//i, '').replace(/\/$/, ''))
    && ['127.0.0.1', 'localhost'].includes(url.hostname)) return url.origin;
  throw invalid('服务地址', 'configuration');
}

function qrContent(value) {
  textValue(value, '二维码内容', {max: 8192});
  if (/[<>]/.test(value) || value.startsWith('//')) throw invalid('二维码内容');
  if (/^[A-Za-z][A-Za-z\d+.-]*:/.test(value)) {
    let url;
    try { url = new URL(value); } catch { throw invalid('二维码内容'); }
    if (url.protocol !== 'https:' || url.username || url.password || url.port
      || !officialHost(url.hostname) || /[\s\\]/.test(value)) throw invalid('二维码内容');
  }
  return value; // Opaque QR payload for local encoding; never fetched or executed here.
}

function parseResponse(raw) {
  let result;
  try {
    result = JSON.parse(raw, (key, value, context) => {
      if (!ID_FIELDS.has(key) || typeof value !== 'number') return value;
      // Node >=22.19 preserves the original JSON token in the reviver context.
      if (context?.source && /^\d+$/.test(context.source)) return context.source;
      if (Number.isSafeInteger(value) && value >= 0) return String(value);
      throw invalid('消息标识');
    });
  } catch { throw invalid('JSON 响应'); }
  if (!result || typeof result !== 'object' || Array.isArray(result)) throw invalid('JSON 响应');
  for (const field of ['ret', 'errcode']) {
    if (result[field] !== undefined && !Number.isSafeInteger(result[field])) throw invalid('业务返回码');
  }
  const code = [result.ret, result.errcode].includes(-14) ? -14 : (result.ret || result.errcode || 0);
  if (code) throw new WeixinApiError(code === -14 ? '微信凭据已失效，请重新连接' : `微信接口拒绝请求（${code}）`,
    {code, kind: 'business', deliveryState: 'rejected'});
  return result;
}

function validateMessages(msgs) {
  if (!Array.isArray(msgs) || msgs.length > 1000) throw invalid('消息列表');
  for (const msg of msgs) {
    if (!msg || typeof msg !== 'object' || Array.isArray(msg)) throw invalid('入站消息');
    for (const name of ['message_id', 'from_user_id', 'to_user_id', 'client_id', 'group_id', 'session_id', 'context_token']) {
      if (msg[name] !== undefined) textValue(msg[name], '消息字段', {empty: true});
    }
    for (const name of ['message_type', 'message_state']) {
      if (msg[name] !== undefined && !Number.isSafeInteger(msg[name])) throw invalid('消息类型');
    }
    if (msg.item_list !== undefined) {
      if (!Array.isArray(msg.item_list) || msg.item_list.length > 256) throw invalid('消息内容列表');
      for (const item of msg.item_list) {
        if (!item || typeof item !== 'object' || Array.isArray(item)) throw invalid('消息内容');
        if (item.type !== undefined && !Number.isSafeInteger(item.type)) throw invalid('消息内容类型');
        if (item.text_item !== undefined && (!item.text_item || typeof item.text_item !== 'object'
          || Array.isArray(item.text_item) || (item.text_item.text !== undefined && typeof item.text_item.text !== 'string')))
          throw invalid('消息文字');
      }
    }
  }
  return msgs;
}

/** Bounded HTTP-only iLink client. It has no retry, login persistence or message routing. */
export class WeixinApi {
  #fetch;
  #allowLoopback;
  #baseInfo;
  #clientVersion;
  #requestTimeoutMs;
  #pollTimeoutMs;

  constructor({baseUrl = DEFAULT_BASE_URL, fetch: fetchImpl = globalThis.fetch, allowLoopback = false,
    version = WEIXIN_PROTOCOL_REFERENCE.version, botAgent = 'Mirally/0.1.0',
    requestTimeoutMs = 15000, pollTimeoutMs = 35000} = {}) {
    this.#allowLoopback = allowLoopback === true;
    this.baseUrl = validateWeixinBaseUrl(baseUrl, {allowLoopback: this.#allowLoopback});
    if (typeof fetchImpl !== 'function') throw invalid('HTTP 客户端', 'configuration');
    this.#fetch = fetchImpl;
    if (typeof version !== 'string' || !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(version)
      || version.split('.').some(part => Number(part) > 255)) throw invalid('客户端版本', 'configuration');
    const parts = version.split('.').map(Number);
    this.#clientVersion = String((parts[0] << 16) | (parts[1] << 8) | parts[2]);
    if (typeof botAgent !== 'string' || botAgent.length > 256
      || !/^[A-Za-z\d_.-]{1,32}\/[A-Za-z\d_.+-]{1,32}(?: \([\x20-\x27\x2a-\x7e]{1,64}\))?$/.test(botAgent))
      throw invalid('客户端标识', 'configuration');
    this.#baseInfo = Object.freeze({channel_version: version, bot_agent: botAgent});
    for (const value of [requestTimeoutMs, pollTimeoutMs]) {
      if (!Number.isSafeInteger(value) || value < 1 || value > 120000) throw invalid('请求超时', 'configuration');
    }
    this.#requestTimeoutMs = requestTimeoutMs;
    this.#pollTimeoutMs = pollTimeoutMs;
  }

  async #request(path, {body, token, signal, baseUrl = this.baseUrl, poll = false, sending = false} = {}) {
    const base = validateWeixinBaseUrl(baseUrl, {allowLoopback: this.#allowLoopback});
    if (token !== undefined) textValue(token, '凭据');
    if (signal?.aborted) throw new WeixinApiError('微信请求已取消',
      {code: 'aborted', kind: 'aborted', deliveryState: sending ? 'rejected' : undefined});
    const headers = {'iLink-App-Id': 'bot', 'iLink-App-ClientVersion': this.#clientVersion};
    if (body !== undefined) Object.assign(headers, {'Content-Type': 'application/json', AuthorizationType: 'ilink_bot_token',
      'X-WECHAT-UIN': Buffer.from(String(randomBytes(4).readUInt32BE(0))).toString('base64')});
    if (token !== undefined) headers.Authorization = `Bearer ${token}`;
    const controller = new AbortController();
    let timedOut = false, issued = false;
    const onAbort = () => controller.abort();
    signal?.addEventListener('abort', onAbort, {once: true});
    const timer = setTimeout(() => {timedOut = true; controller.abort();}, poll ? this.#pollTimeoutMs : this.#requestTimeoutMs);
    let abortListener;
    const interrupted = new Promise((resolve, reject) => {
      abortListener = () => reject(new WeixinApiError(timedOut ? '微信请求超时' : '微信请求已取消',
        {code: timedOut ? 'timeout' : 'aborted', kind: timedOut ? 'timeout' : 'aborted'}));
      controller.signal.addEventListener('abort', abortListener, {once: true});
    });
    try {
      return await Promise.race([interrupted, (async () => {
        issued = true;
        const response = await this.#fetch(`${base}${path}`, {method: body === undefined ? 'GET' : 'POST',
          headers, ...(body === undefined ? {} : {body: JSON.stringify(body)}), redirect: 'manual', signal: controller.signal});
        if (response.redirected || (response.status >= 300 && response.status < 400)) {
          void response.body?.cancel().catch(() => {});
          throw new WeixinApiError('微信接口拒绝 HTTP 重定向', {code: 'redirect', kind: 'http'});
        }
        if (!response.ok) {
          void response.body?.cancel().catch(() => {});
          throw new WeixinApiError(`微信接口 HTTP 错误（${response.status}）`,
            {code: 'http', kind: 'http', httpStatus: response.status});
        }
        const declared = response.headers.get('content-length');
        if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_RESPONSE_BYTES)) {
          void response.body?.cancel().catch(() => {}); throw invalid('响应大小');
        }
        const reader = response.body?.getReader();
        if (!reader) throw invalid('响应正文');
        const chunks = []; let length = 0;
        try {
          for (;;) {
            const part = await reader.read();
            if (part.done) break;
            length += part.value.byteLength;
            if (length > MAX_RESPONSE_BYTES) {void reader.cancel().catch(() => {}); throw invalid('响应大小');}
            chunks.push(part.value);
          }
        } finally {reader.releaseLock();}
        return parseResponse(Buffer.concat(chunks, length).toString('utf8'));
      })()]);
    } catch (err) {
      const safe = err instanceof WeixinApiError ? err : new WeixinApiError('微信网络请求失败', {code: 'network', kind: 'network'});
      if (sending && !safe.deliveryState) safe.deliveryState = issued ? 'unknown' : 'rejected';
      throw safe;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      controller.signal.removeEventListener('abort', abortListener);
      controller.abort();
    }
  }

  /** Obtain QR payload. Callers encode qrcode_img_content locally, never as HTML. */
  async getQr({tokens = [], signal} = {}) {
    if (!Array.isArray(tokens) || tokens.length > 10) throw invalid('本地凭据列表');
    tokens.forEach(token => textValue(token, '凭据'));
    const result = await this.#request('/ilink/bot/get_bot_qrcode?bot_type=3', {body: {local_token_list: tokens}, signal});
    return {qrcode: textValue(result.qrcode, '二维码标识', {max: 8192}), qrcode_img_content: qrContent(result.qrcode_img_content)};
  }

  /** Poll once. Returned IDC origins are validated; the caller controls subsequent polls. */
  async pollQr({qrcode, verifyCode, baseUrl = this.baseUrl, signal} = {}) {
    textValue(qrcode, '二维码标识', {max: 8192});
    const query = new URLSearchParams({qrcode});
    if (verifyCode !== undefined) {textValue(verifyCode, '验证码', {max: 64}); query.set('verify_code', verifyCode);}
    const result = await this.#request(`/ilink/bot/get_qrcode_status?${query}`, {baseUrl, signal, poll: true});
    if (!QR_STATES.has(result.status)) throw invalid('二维码状态');
    const clean = {status: result.status};
    for (const field of ['bot_token', 'ilink_bot_id', 'ilink_user_id']) {
      if (result[field] !== undefined) clean[field] = textValue(result[field], '登录字段');
    }
    if (result.baseurl !== undefined) clean.baseurl = validateWeixinBaseUrl(result.baseurl, {allowLoopback: this.#allowLoopback});
    if (result.redirect_host !== undefined) {
      const host = textValue(result.redirect_host, 'IDC 地址', {max: 253});
      // Official redirect_host is an authority without scheme, path or userinfo.
      if (!/^[a-zA-Z\d.-]+$/.test(host)) throw invalid('IDC 地址');
      clean.redirect_host = new URL(validateWeixinBaseUrl(`https://${host}`)).hostname;
    }
    if (clean.status === 'confirmed' && (!clean.bot_token || !clean.ilink_bot_id || !clean.ilink_user_id))
      throw invalid('登录确认凭据');
    if (clean.status === 'scaned_but_redirect' && !clean.redirect_host) throw invalid('IDC 地址');
    return clean;
  }

  /** Receive one update batch; retains uint64 identifiers and never advances the caller's cursor. */
  async updates({token, cursor = '', signal} = {}) {
    textValue(token, '凭据'); textValue(cursor, '消息游标', {max: 262144, empty: true});
    const result = await this.#request('/ilink/bot/getupdates', {token, signal, poll: true,
      body: {get_updates_buf: cursor, base_info: this.#baseInfo}});
    if (result.ret === undefined && result.errcode === undefined && result.msgs === undefined
      && result.get_updates_buf === undefined) throw invalid('消息响应');
    const msgs = validateMessages(result.msgs ?? []);
    if (result.get_updates_buf !== undefined) textValue(result.get_updates_buf, '消息游标', {max: 262144, empty: true});
    if (result.longpolling_timeout_ms !== undefined && (!Number.isSafeInteger(result.longpolling_timeout_ms)
      || result.longpolling_timeout_ms < 0 || result.longpolling_timeout_ms > 120000)) throw invalid('轮询超时');
    return {...result, msgs};
  }

  /** Send once. unknown delivery must never be automatically retried by the caller. */
  async sendText({token, to, text, contextToken, clientId, signal} = {}) {
    textValue(token, '凭据'); textValue(to, '收件人'); textValue(clientId, '发送标识', {max: 256});
    if (typeof text !== 'string' || !text.trim() || Buffer.byteLength(text) > 16384 || text.includes('\0')) throw invalid('发送文字');
    textValue(contextToken, '消息上下文凭据');
    const result = await this.#request('/ilink/bot/sendmessage', {token, signal, sending: true, body: {
      msg: {from_user_id: '', to_user_id: to, client_id: clientId, message_type: 2, message_state: 2,
        context_token: contextToken, item_list: [{type: 1, text_item: {text}}]}, base_info: this.#baseInfo,
    }});
    try {
      if (result.message_id !== undefined) textValue(result.message_id, '发送回执标识', {max: 256});
      if (result.ret !== 0 && !result.message_id) throw invalid('发送回执');
    } catch {
      throw new WeixinApiError('微信发送回执无效', {code: 'schema', kind: 'schema', deliveryState: 'unknown'});
    }
    return result;
  }
}
