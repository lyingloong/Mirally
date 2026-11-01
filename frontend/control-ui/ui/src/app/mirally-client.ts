/** HTTP/SSE transport for the retained Control UI. Mirally/DSH own every fact. */
import type { GatewayBrowserClient, GatewayBrowserClientOptions, GatewayHelloOk, GatewayEventFrame } from "../api/gateway.ts";

import {readMirallyModel, MIRALLY_READ_METHODS, selectedProductModel} from "./mirally-read-models.ts";
import {DesktopReminders} from './desktop-reminders.ts';

type Data = Record<string, any>;
const key = (id: string) => `agent:main:${id}`;
const identity = (value: string) => String(value ?? "").replace(/^agent:main:/, "");
const unsupported = (method: string): never => { throw Object.assign(new Error(`当前 Mirally 内核尚未提供此操作：${method}`), {code: "METHOD_NOT_FOUND", gatewayCode: "METHOD_NOT_FOUND"}); };
const METHODS = [...MIRALLY_READ_METHODS, "themes.list", "themes.get", "health", "status", "agents.list", "agents.identity.get", "agent.identity.get", "sessions.list", "sessions.resolve", "sessions.create", "sessions.get", "sessions.abort", "sessions.subscribe", "sessions.unsubscribe", "sessions.messages.subscribe", "sessions.messages.unsubscribe", "chat.history", "chat.startup", "chat.metadata", "chat.send", "chat.abort", "config.get", "config.patch", "models.list", "models.authStatus", "sessions.describe", "sessions.files.list", "sessions.files.get", "artifacts.list", "artifacts.get", "artifacts.download"];

METHODS.push('sessions.patch');
METHODS.push('sessions.fork', 'sessions.compact');
const METADATA_FIELDS = new Set(['title', 'label', 'pinned', 'archived', 'model']);
const PATCH_CONTEXT = new Set(['key', 'sessionKey', 'agentId', 'expectedSessionId', 'expectedMetadataVersion']);
const modelProvider = (connectionId: string) => `mirally-${connectionId}`;
function selectionFromReference(value: unknown): Data | null {
  if (value == null || value === '') return null;
  if (typeof value !== 'string') throw new Error('无效的模型选择。');
  const match = /^mirally-([A-Za-z0-9_-]{1,64})\/(.+)$/.exec(value);
  if (!match) throw new Error('请选择已配置的模型。');
  return {connection_id: match[1], model_id: match[2]};
}
// Retained media components interpret bare slash paths as old host filesystem
// references. Only our exact authenticated receipt routes become same-origin
// HTTP resources; no old media resolver or machine path is involved.
function historyMedia(block: Data): Data {
  const resource = (value: unknown) => typeof value === 'string'
    && /^\/api\/sessions\/[^/]+\/attachments\/(?:image|file)\/[a-f0-9]{64}$/.test(value)
    ? new URL(value, location.origin).href : value;
  if (block.type === 'image') return {...block, url: resource(block.url)};
  if (block.type === 'attachment' && block.attachment) return {...block, attachment: {...block.attachment, url: resource(block.attachment.url)}};
  return block;
}

export class MirallyBrowserClient {
  readonly instanceId = crypto.randomUUID();
  readonly gatewayUrl = location.origin;
  readonly recoveryScope = `mirally:${location.origin}`;
  recoveryScopeReady = false;
  connected = false;
  readonly scopeUpgradeReady = false;
  private stopped = true;
  private generation = 0;
  private stream?: AbortController;
  private retry?: ReturnType<typeof setTimeout>;
  private token: string;
  private eventListeners = new Set<(event: GatewayEventFrame) => void>();
  private runs = new Map<string, Set<string>>();
  private partials = new Map<string, string>();
  private assistantAttempts = new Map<string, {id: string; fresh: boolean; closed: boolean}>();
  private reasoningStreams = new Map<string, Map<string, {text: string; timestamp: number; nativeSessionId?: string; nativeSeq?: number}>>();
  private sequence = 0;
  private agentSequence = 0;
  private configHash = "";
  private preferences: Data = {};
  private metadataRows = new Map<string, Data>();
  private desktopReminders = new DesktopReminders((method,params)=>this.rpc(method,params),
    () => (window as any).pywebview?.api);
  supportsSessionPatch(params: unknown) {
    if (!params || typeof params !== 'object' || Array.isArray(params)) return false;
    const fields = Object.keys(params).filter(field => !PATCH_CONTEXT.has(field));
    return fields.length > 0 && fields.every(field => METADATA_FIELDS.has(field));
  }
  constructor(private opts: GatewayBrowserClientOptions) {
    this.token = opts.token || new URLSearchParams(location.hash.slice(1)).get("token") || "";
    const host = {request: this.http.bind(this), rpc: this.rpc.bind(this),
      conversationIdForSession: identity,
      getUiPreference: (name: string) => this.preferences[name],
      setUiPreference: (name: string, value: string) => { this.preferences[name] = value; void this.http('/api/ui-preferences', {method:'POST', body:JSON.stringify({key:name,value})}); },
      client: this};
    Object.assign(window, {__mirallyHost: host});
    window.dispatchEvent(new CustomEvent("mirally-host-ready"));
  }
  get needsWakeReconnect() { return !this.connected; }
  start() { if (!this.stopped) return; this.stopped = false; const generation = ++this.generation; void this.connect(generation); }
  stop() { this.stopped = true; this.desktopReminders.stop(); ++this.generation; this.connected = false; this.stream?.abort(); clearTimeout(this.retry); this.reasoningStreams.clear(); this.assistantAttempts.clear(); this.partials.clear(); }

  /** Render-only rows: they never enter the transcript, outbox or message cache. */
  getReasoningStream(sessionKey: string, runId?: string | null, history: Data[] = []) {
    if (!runId) return [];
    const entries = this.reasoningStreams.get(`${identity(sessionKey)}:${runId}`);
    if (!entries) return [];
    return [...entries].filter(([, entry]) => entry.text && !history.some(message => entry.nativeSeq !== undefined
      && message.native_session_id === entry.nativeSessionId && message.native_seq === entry.nativeSeq))
      .map(([attemptId, entry]) => ({id: `mirally-reasoning:${attemptId}`, role: 'assistant', timestamp: entry.timestamp,
        __openclaw: {runId}, __mirallyTransient: true, content: [{type: 'thinking', thinking: entry.text}]}));
  }
  requestScopeUpgrade() { return Promise.reject(new Error("本机 Mirally 使用产品授权确认，不提供 Gateway scope 升级。")); }
  cancelScopeUpgrade() {}
  private scheduleReconnect(generation: number, delayMs: number) {
    if (this.stopped || generation !== this.generation) return false;
    this.connected = false; this.stream?.abort(); clearTimeout(this.retry);
    const nextGeneration = ++this.generation;
    this.retry = setTimeout(() => { if (!this.stopped && nextGeneration === this.generation) void this.connect(nextGeneration); }, delayMs);
    return true;
  }
  private disconnected(cause: unknown, generation: number, wasConnected: boolean) {
    const error = Object.assign(new Error(`gateway disconnected: ${cause instanceof Error ? cause.message : String(cause)}`, {cause}),
      {code: 'GATEWAY_DISCONNECTED', gatewayCode: 'GATEWAY_DISCONNECTED'});
    if (wasConnected && this.connected && this.scheduleReconnect(generation, 1500)) {
      this.opts.onClose?.({code: 1006, reason: error.message, willRetry: true});
    }
    return error;
  }
  private async http(path: string, options: RequestInit = {}) {
    const target = new URL(path, location.origin);
    if (!path.startsWith("/") || target.origin !== location.origin || target.username || target.password) throw new Error("Mirally requests must stay on this origin");
    const generation = this.generation, wasConnected = this.connected;
    let response: Response;
    try { response = await fetch(target, {...options, redirect: "error", headers: {"Content-Type": "application/json", ...(this.token ? {Authorization: `Bearer ${this.token}`} : {}), ...options.headers}}); }
    catch (cause) { if (options.signal?.aborted) throw cause; throw this.disconnected(cause, generation, wasConnected); }
    let result: Data;
    try { result = await response.json(); }
    catch (cause) {
      if (options.signal?.aborted) throw cause;
      if (response.ok) throw this.disconnected(cause, generation, wasConnected);
      result = {};
    }
    if (!response.ok || result.error) throw Object.assign(new Error(result.error?.message || `请求失败 (${response.status})`), {code: result.error?.code, gatewayCode: result.error?.code});
    return result;
  }
  private async rpc(method: string, params: Data = {}) { return (await this.http("/api/rpc", {method: "POST", body: JSON.stringify({method, params})})).result; }
  private activeRuns(id: string) { return [...(this.runs.get(id) || [])]; }
  private rememberRun(id: string, runId: string) { const runs = this.runs.get(id) || new Set<string>(); runs.add(runId); this.runs.set(id, runs); }
  private forgetRun(id: string, runId: string) { const runs = this.runs.get(id); runs?.delete(runId); if (!runs?.size) this.runs.delete(id); }
  addEventListener(listener: (event: GatewayEventFrame) => void) {
    this.eventListeners.add(listener); return () => this.eventListeners.delete(listener);
  }
  private emit(event: string, payload: Data) {
    if (this.stopped) return;
    const frame: GatewayEventFrame = {type: "event", event, payload, seq: ++this.sequence};
    for (const listener of [this.opts.onEvent, ...this.eventListeners]) {
      try { listener?.(frame); } catch (error) { console.warn('Mirally UI event handler failed', error); }
    }
  }
  private async connect(generation: number) {
    try {
      if (this.token) await this.http("/api/auth", {method: "POST", body: "{}"});
      const health = await this.http("/api/health");
      this.preferences = (await this.http('/api/ui-preferences')).preferences || {};
      await this.loadPresentation();
      if (this.stopped || generation !== this.generation) return;
      this.refreshNativeCapturePreference();
      this.connected = true; this.recoveryScopeReady = true; this.desktopReminders.start();
      this.opts.onHello?.({type: "hello-ok", protocol: 3, server: {version: "Mirally", connId: this.instanceId, controlUiBuildSource: "configured"},
        features: {methods: METHODS, events: ["chat", "agent", "sessions.changed", "config.changed"]},
        snapshot: {presence: [], health: health.product, stateVersion: {presence: 0, health: 0}, uptimeMs: 0,
          sessionDefaults: {defaultAgentId: "main", mainKey: "main"}},
        auth: {role: "operator", scopes: ["operator.read", "operator.write", "operator.admin"], recoveryScope: this.recoveryScope},
        policy: {maxPayload: 16 * 1024 * 1024, maxBufferedBytes: 16 * 1024 * 1024, tickIntervalMs: 30_000},
      } as unknown as GatewayHelloOk);
      void this.events(generation);
    } catch (error) {
      if (this.stopped || generation !== this.generation) return;
      if (this.scheduleReconnect(generation, 2000)) this.opts.onClose?.({code: 1006, reason: String(error), willRetry: true});
    }
  }
  private async loadPresentation() {
    for (const [id, src] of [["mirally-native-bridge", "/mirally-native-bridge.js"], ["mirally-original-presentation", "/mirally-presentation.js"]]) {
      if (document.getElementById(id)) continue;
      await new Promise<void>((resolve, reject) => { const script = document.createElement("script"); script.id = id; script.src = src; script.onload = () => resolve(); script.onerror = () => {script.remove(); reject(new Error("原界面资源未能加载"));}; document.head.append(script); });
    }
    (window as any).__mirallyFramework = {showPage: async (page: string) => {
      const context = (document.querySelector("openclaw-app") as any)?.context;
      if (page === "work") return (window as any).__mirallyWork?.open();
      if (page === "activity") return (window as any).__mirallyActivity?.open();
      const route = ({chat: "new-session", settings: "config", model: "model-providers", memory: "memory", extensions: "plugins", reminders: "cron", inbox: "custodian"} as Data)[page];
      if (!route) throw new Error("该产品页面尚未完成原界面接入");
      context?.navigate(route, {search: ""});
    }};
  }
  private async events(generation: number) {
    const controller = new AbortController(); this.stream?.abort(); this.stream = controller;
    try {
      const response = await fetch("/api/events", {headers: this.token ? {Authorization: `Bearer ${this.token}`} : {}, signal: controller.signal});
      if (!response.ok || !response.body) throw new Error("实时连接暂不可用");
      const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
      while (!this.stopped && generation === this.generation) {
        const {value, done} = await reader.read(); if (done) break;
        if (this.stopped || generation !== this.generation) return;
        buffer += decoder.decode(value, {stream: true}); buffer = buffer.replace(/\r\n/g, "\n");
        let end;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
          const data = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
          if (data) await this.consume(JSON.parse(data), generation);
        }
      }
      if (!controller.signal.aborted && !this.stopped) throw new Error("实时连接已结束");
    } catch (error) {
      if (controller.signal.aborted || this.stopped || generation !== this.generation) return;
      if (this.scheduleReconnect(generation, 1500)) this.opts.onClose?.({code: 1006, reason: String(error), willRetry: true});
    }
  }
  private async sessions() {
    const sessions: Data[] = [];
    let page = await this.http('/api/sessions');
    for (;;) {
      sessions.push(...page.sessions);
      if (!page.has_more) break;
      page = await this.http(`/api/sessions?offset=${page.next_offset}`);
    }
    const settings = await this.rpc('settings.get');
    const model = selectedProductModel(settings);
    const defaultConnection = settings.model_routes?.chat ?? 'default';
    for (const session of sessions) this.metadataRows.set(session.id, session);
    return sessions.map((s: Data) => ({...s, key: key(s.id), sessionId: s.session_id, displayName: s.title, label: s.title,
      ...(s.parent_conversation_id ? {parentSessionKey:key(s.parent_conversation_id)} : {}),
      metadataVersion: s.metadata_version, pinnedAt: s.pinned ? s.pinned_at : undefined, archivedAt: s.archived ? s.archived_at : undefined,
      // The retained UI separates display policy from generation effort. Allow
      // real thinking blocks (including old history) through its second gate;
      // chatShowThinking remains the user's visibility toggle.
      reasoningLevel: 'on', agentId: "main", kind: "direct", updatedAt: s.updated_at, hasActiveRun: s.status === "running", activeRunIds: [...new Set([...(s.active_command_ids || []), ...this.activeRuns(s.id)])],
      backgroundJobCount: typeof s.background_job_count === 'number' ? s.background_job_count : undefined,
      modelProvider: modelProvider(s.model_selection?.connection_id ?? defaultConnection), model: s.model_selection?.model_id ?? model.model_id,
      modelOverrideSource: s.model_selection ? 'user' : null}));
  }
  private message(message: Data) { return {id: message.id, role: message.role, timestamp: message.time, __openclaw: message.__openclaw,
    ...(['desktop', 'weixin', 'mobile', 'schedule'].includes(message.input_origin) && message.role === 'user' ? {input_origin: message.input_origin} : {}),
    native_session_id: message.native_session_id, native_seq: message.native_seq, ...(message.toolCallId ? {toolCallId: message.toolCallId} : {}),
    content: Array.isArray(message.content) ? message.content.filter((block: Data) => block.type === 'text' && typeof block.text === 'string'
      || message.role === 'assistant' && block.type === 'thinking' && typeof block.thinking === 'string'
      || message.role === 'toolResult' && ['toolcall', 'toolresult'].includes(block.type)
      || message.role === 'user' && ['image', 'attachment', 'attachment_error'].includes(block.type)).map(historyMedia) : [{type: "text", text: message.text || ""}], stopReason: message.stopReason ?? "stop"}; }
  private async history(id: string): Promise<Data> {
    const session = (await this.sessions()).find((item: Data) => identity(item.key) === id);
    if (!session) return {sessionKey: key(id), messages: [], sessionId: undefined, hasMore: false};
    const result = await this.http(`/api/sessions/${encodeURIComponent(id)}/history`);
    return {sessionKey: key(id), sessionId: session.sessionId, sessionInfo:session, messages: result.messages.map(this.message), hasMore: false,
      ...(result.in_flight ? {inFlightRun:{runId:result.in_flight.command_id, startedAt:result.in_flight.started_at, sessionAbortable:true}} : {}),
      activeRunIds: session.activeRunIds, model: session.model};
  }
  private async consume(event: Data, generation = this.generation) {
    if (this.stopped || generation !== this.generation) return;
    if (event.type === 'ui-preference') {
      this.preferences[event.key] = event.value;
      if (event.key === 'mirally.companion.hideFromCapture') this.refreshNativeCapturePreference();
      return;
    }
    if (event.type === 'configuration') { this.emit('config.changed', {paths:event.fields || []}); return; }
    const id = event.session_id; if (!id) { if (["artifact", "action", "inbox", "interaction", "work", "calendar", "schedule"].includes(event.type)) window.dispatchEvent(new CustomEvent("mirally-product-event", {detail: event})); return; }
    const runId = event.command_id || this.activeRuns(id).at(-1);
    const partialKey = `${id}:${runId || ""}`;
    if (event.type === 'session-metadata') {this.emit('sessions.changed', {key: key(id), agentId: 'main', reason: 'metadata'}); return;}
    if (event.type === 'session' && event.command_id) {
      // Only publish the persisted user row owned by this command. Assistant
      // streaming and terminal reconciliation remain on their existing path.
      try {
        const history = await this.history(id);
        if (this.stopped || generation !== this.generation) return;
        const message = history.messages.find((item: Data) => item.role === 'user' && item.__openclaw?.runId === event.command_id);
        if (message) this.emit('session.message', {key: key(id), agentId: 'main', message});
      } catch { /* History will be recovered on the next explicit refresh. */ }
      return;
    }
    if (event.type === "assistant-start") {
      this.partials.set(partialKey, "");
      this.assistantAttempts.set(partialKey, {id: event.attempt_id || 'unframed', fresh: true, closed: false});
    }
    if (["token", "reasoning"].includes(event.type) && typeof event.text === 'string') {
      const attempt = this.assistantAttempts.get(partialKey);
      if (attempt?.closed || event.attempt_id && attempt && event.attempt_id !== attempt.id) return;
      const attemptId = event.attempt_id || attempt?.id || 'unframed';
      const replace = attempt?.fresh === true;
      if (attempt) attempt.fresh = false;
      if (event.type === 'reasoning') {
        const entries = this.reasoningStreams.get(partialKey) || new Map();
        const previous = entries.get(attemptId);
        entries.set(attemptId, {...previous, text: (previous?.text || '') + event.text,
          timestamp: previous?.timestamp || Date.now(), nativeSessionId: event.native_session_id});
        this.reasoningStreams.set(partialKey, entries);
      } else this.partials.set(partialKey, (this.partials.get(partialKey) || '') + event.text);
      const text = this.partials.get(partialKey) || '';
      this.emit("chat", {sessionKey: key(id), agentId: "main", runId, state: "delta", ...(replace ? {replace: true} : {}),
        deltaText: event.type === 'token' ? event.text : '', message: {role: "assistant", content: [{type: "text", text}]}});
    }
    if (event.type === 'assistant-end') {
      const attempt = this.assistantAttempts.get(partialKey);
      const entry = this.reasoningStreams.get(partialKey)?.get(event.attempt_id);
      if (entry && Number.isSafeInteger(event.native_seq)) entry.nativeSeq = event.native_seq;
      if (event.outcome === 'abandoned') this.reasoningStreams.get(partialKey)?.delete(event.attempt_id);
      if (attempt?.id === event.attempt_id) {
        attempt.closed = true;
        if (event.outcome === 'abandoned') {
          this.partials.delete(partialKey);
          this.emit('chat', {sessionKey: key(id), agentId: 'main', runId, state: 'delta', replace: true, deltaText: '', message: {role: 'assistant', content: []}});
        }
      }
    }
    if (event.type === "tool") this.emit("agent", {runId, sessionKey: key(id), seq: ++this.agentSequence, ts: Date.now(), stream: "tool", data: {
      phase: event.state === "running" ? "start" : "result", name: event.name, toolCallId: event.id, isError: event.state === "failed",
      ...(Object.hasOwn(event, "args") ? {args: event.args} : {}),
      ...(event.state !== "running" && Object.hasOwn(event, "result") ? {result: event.result} : {}),
    }});
    if (["done", "error"].includes(event.type)) {
      const history = await this.history(id), message = history.messages.filter((m: Data) => m.role === "assistant" && (!runId || m.__openclaw?.runId === runId)).at(-1);
      if (this.stopped || generation !== this.generation) return;
      // A queued input may only become durable after the original session event.
      const userMessage = runId && history.messages.find((item: Data) => item.role === 'user' && item.__openclaw?.runId === runId);
      if (userMessage) this.emit('session.message', {key: key(id), agentId: 'main', message: userMessage});
      if (runId) this.forgetRun(id, runId);
      this.reasoningStreams.delete(partialKey); this.assistantAttempts.delete(partialKey);
      this.emit("chat", {sessionKey: key(id), agentId: "main", runId, state: event.type === "error" || event.failed ? "error" : event.cancelled ? "aborted" : "final", message, errorMessage: event.text || event.message || (event.failed ? "这次处理没有完成。" : undefined)});
      const activeRunIds = this.activeRuns(id);
      this.emit("sessions.changed", {key:key(id), agentId:"main", hasActiveRun:!!history.sessionInfo?.hasActiveRun || activeRunIds.length > 0, activeRunIds, reason:"completed"}); this.partials.delete(partialKey);
    }
  }
  private refreshNativeCapturePreference() {
    // The native shell reads the persisted product preference itself. A wire
    // event only invalidates it; no incoming value controls a native window.
    const native = (window as Window & {chrome?: {webview?: {__mirallyBrowserOnly?: boolean; postMessage(value: object): void}}}).chrome?.webview;
    if (native && native.__mirallyBrowserOnly !== true) native.postMessage({type: 'mirally-framework', action: 'refresh-capture-preference'});
  }
  async request<T = unknown>(method: string, params: Data = {}, options?: {signal?: AbortSignal}): Promise<T> {
    options?.signal?.throwIfAborted();
    const result = await this.dispatch(method, params); options?.signal?.throwIfAborted(); return result as T;
  }
  private async dispatch(method: string, p: Data): Promise<any> {
    const id = identity(p.sessionKey || p.key || p.reference?.key || "main");
    switch (method) {
      case "health": case "status": return (await this.http("/api/health")).product;
      case "themes.list": case "themes.get": {
        const {BUILTIN_THEMES} = await import("../../../packages/gateway-protocol/src/theme.ts");
        const prefs = this.preferences['control-ui'] || {}, selected = prefs.theme || 'claw';
        const theme = BUILTIN_THEMES.find(item => item.id === (p.id || selected));
        if (!theme) return unsupported('themes.get: custom/plugin theme');
        return {current:{id:selected, mode:prefs.themeMode || 'system', scope:'gateway', overrides:{
          ...(prefs.theme ? {id:prefs.theme} : {}), ...(prefs.themeMode ? {mode:prefs.themeMode} : {})}},
          theme, ...(method === 'themes.list' ? {themes:BUILTIN_THEMES} : {})};
      }
      case "agents.list": {const settings=await this.rpc('settings.get'); const model=selectedProductModel(settings); return {defaultId: "main", mainKey: "main", scope: "per-sender", agents: [{id: "main", name: "Mirally", model: {primary:model.model_id ? `${modelProvider(settings.model_routes?.chat ?? 'default')}/${model.model_id}` : ''}, identity: {name: "Mirally", avatar: "/experience/mirally-128.png"}}]};}
      case "agent.identity.get": case "agents.identity.get": return {agentId: "main", name: "Mirally", avatar: "/experience/mirally-128.png"};
      case "sessions.list": {
        let sessions = await this.sessions();
        if (p.archived !== 'all') sessions = sessions.filter((s: Data) => (s.archived === true) === (p.archived === true));
        if (p.search) sessions = sessions.filter((s: Data) => s.title.toLocaleLowerCase().includes(String(p.search).toLocaleLowerCase()));
        const count = sessions.length;
        if (Number.isSafeInteger(p.limit) && p.limit > 0) sessions = sessions.slice(0, p.limit);
        const settings=await this.rpc('settings.get'); const model=selectedProductModel(settings);
        return {ts: Date.now(), count, defaults: {modelProvider: modelProvider(settings.model_routes?.chat ?? 'default'), model: model.model_id}, sessions};
      }
      case 'sessions.patch': {
        if (!this.supportsSessionPatch(p)) throw new Error('当前 Mirally 不支持此对话设置；本次未修改。');
        if (p.agentId && p.agentId !== 'main') throw new Error('当前 Mirally 仅支持主伙伴的对话。');
        if (!this.metadataRows.has(id)) await this.sessions();
        const observed = this.metadataRows.get(id);
        if (!observed) throw new Error('会话不存在，请刷新。');
        if (p.expectedSessionId && p.expectedSessionId !== observed.session_id) throw new Error('会话已变更，请刷新后重试。');
        const patch: Data = {};
        if (Object.hasOwn(p, 'model')) patch.model_selection = selectionFromReference(p.model);
        for (const field of ['pinned', 'archived']) if (Object.hasOwn(p, field)) patch[field] = p[field];
        if (Object.hasOwn(p, 'title')) patch.title = p.title;
        if (Object.hasOwn(p, 'label')) {
          const title = p.label === null || p.label === '' ? '新对话' : p.label;
          if (Object.hasOwn(patch, 'title') && patch.title !== title) throw new Error('title 和 label 不能指定不同名称。');
          patch.title = title;
        }
        await this.http(`/api/sessions/${encodeURIComponent(id)}`, {method: 'PATCH', body: JSON.stringify({
          expected_version: p.expectedMetadataVersion ?? observed.metadata_version, patch})});
        const entry = (await this.sessions()).find((s: Data) => identity(s.key) === id);
        this.emit('sessions.changed', {key: key(id), agentId: 'main', reason: 'metadata'});
        return {ok: true, key: key(id), entry};
      }
      case "sessions.describe": return {session: (await this.sessions()).find((s: Data) => identity(s.key) === id) || null};
      case "sessions.get": case "sessions.resolve": { const session = (await this.sessions()).find((s: Data) => identity(s.key) === id); if (!session) { if (p.allowMissing) return {ok:false}; throw new Error("会话不存在"); } return {ok:true, key: session.key, entry: session, sessionId: session.sessionId, agentId:"main"}; }
      case 'sessions.fork': return this.dispatch('sessions.create',{...p,fork:true,parentSessionKey:p.parentSessionKey || p.sessionKey || p.key});
      case 'sessions.compact': {
        const result=await this.http(`/api/sessions/${encodeURIComponent(id)}/native/commands`,{method:'POST',body:JSON.stringify({line:'/compact'})});
        return {ok:result.result.kind==='success',compacted:result.result.kind==='success'&&result.result.sourceEventSeq!==undefined,reason:result.result.text};
      }
      case "sessions.create": {
        if (p.permissionMode != null && p.permissionMode !== '') throw new Error("当前 Mirally 尚未支持会话权限模式；本次没有创建会话。请重新打开新对话，以实际操作的逐条授权为准。");
        if (p.incognito) throw new Error("当前内核尚未支持无痕会话，请使用普通会话；本次没有创建或保存会话。");
        if ((p.parentSessionKey || p.parentSessionId) && p.fork !== true || p.fork && p.fork !== true)
          throw new Error("请明确选择分叉会话及其来源。");
        if (p.agentId && p.agentId !== 'main') throw new Error("当前 Mirally 仅支持主伙伴的会话；本次没有创建会话。");
        const initialModel = selectionFromReference(p.model);
        let parentId: string | undefined;
        if (p.fork === true) {
          if (p.parentSessionKey) parentId=identity(p.parentSessionKey);
          else if (p.parentSessionId) parentId=(await this.sessions()).find((s:Data)=>s.sessionId===p.parentSessionId)?.id;
          if (!parentId) throw new Error('请选择要分叉的来源会话。');
        }
        const session = await this.http(parentId ? `/api/sessions/${encodeURIComponent(parentId)}/fork` : "/api/sessions", {method: "POST", body: JSON.stringify({title: p.displayName || p.label || p.title || undefined,
          requestId:p.idempotencyKey, ...(!parentId && initialModel ? {model_selection: initialModel} : {})})});
        if (parentId && initialModel) await this.http(`/api/sessions/${encodeURIComponent(session.id)}`,{method:'PATCH',body:JSON.stringify({expected_version:session.metadata_version,patch:{model_selection:initialModel}})});
        const entry = (await this.sessions()).find((s: Data) => identity(s.key) === session.id);
        if (p.message || p.attachments?.length) {
          try {
            const sent = await this.dispatch("chat.send", {...p, sessionKey: key(session.id), message: p.message});
            return {key: key(session.id), entry, runStarted: sent.status !== 'ok' && sent.status !== 'error', runId: sent.runId,
              ...(sent.status === 'error' ? {runError: {message: '这次提交没有继续执行，请查看历史后重新发送。'}} : {})};
          }
          catch (error) { return {key:key(session.id), entry, runStarted:false, runError:{message:String(error)}}; }
        }
        return {key: key(session.id), entry};
      }
      case "sessions.messages.subscribe": case "sessions.subscribe": return {subscribed: true, subscriptionId: key(id), sessionKey: key(id)};
      case "sessions.messages.unsubscribe":
      case "sessions.unsubscribe": return {ok: true};
      case "chat.history": return this.history(id);
      case "chat.startup": { const history = await this.history(id); return {...history, history, metadata: await this.dispatch("chat.metadata", p)}; }
      case "chat.metadata": { const catalog = await this.dispatch('models.list', {}); return {commands:[], models:catalog.models}; }
      case "sessions.abort": case "chat.abort": {
        const runIds = this.activeRuns(id);
        const result = await this.http(`/api/sessions/${encodeURIComponent(id)}/cancel`, {method: "POST", body: JSON.stringify({commandId: p.runId})});
        if (result.stopped) this.runs.delete(id);
        return {ok: true, aborted: result.stopped, runIds,
          ...(method === 'sessions.abort' ? {status:result.stopped ? 'aborted' : 'no-active-run'} : {})};
      }
      case "chat.send": {
        if (id === 'main') await this.http('/api/sessions', {method:'POST',body:JSON.stringify({conversationId:'main',title:'对话'})});
        if (this.metadataRows.get(id)?.archived) throw new Error('请先恢复已归档的对话，再发送消息或材料。');
        const runId = p.idempotencyKey || crypto.randomUUID();
        const materials = [];
        for (const [index, attachment] of (p.attachments || []).entries()) {
          const uploaded = await this.http("/api/materials", {method: "POST", body: JSON.stringify({conversation_id: id, request_id:`${runId}:${index}`, name: attachment.fileName || attachment.name || "material", media_type: attachment.mimeType, data: attachment.content || attachment.data})});
          if (uploaded.readingState === "error") throw new Error(uploaded.note); materials.push(uploaded.id);
        }
        this.rememberRun(id, runId);
        try {
          const result = await this.http(`/api/sessions/${encodeURIComponent(id)}/messages`, {method: "POST", body: JSON.stringify({commandId: runId, text: p.message || "请查看我提供的材料。", materials, allow_file_write: p.mirallyAllowFileWrite === true})});
          if (result.replay && ['completed','cancelled','rejected','unknown'].includes(result.command?.state)) {
            this.forgetRun(id, runId);
            const state = result.command.state === 'completed' ? 'final' : result.command.state === 'cancelled' ? 'aborted' : 'error';
            queueMicrotask(() => { this.emit('chat', {sessionKey:key(id), agentId:'main', runId, state}); const activeRunIds = this.activeRuns(id); this.emit('sessions.changed', {key:key(id), agentId:'main', reason:'command-replay', hasActiveRun:activeRunIds.length > 0, activeRunIds}); });
            return {runId, status:state === 'final' ? 'ok' : 'error', sessionKey:key(id)};
          }
          return {runId, status: result.accepted ? "started" : "unknown", sessionKey: key(id), messageSeq: result.message_seq};
        } catch (error) { this.forgetRun(id, runId); throw error; }
      }
      case "sessions.files.list": case "sessions.files.get": case "artifacts.list": case "artifacts.get": case "artifacts.download": {
        const bridge = (window as any).__mirallyNativeBridge;
        if (!bridge) throw new Error("成果入口尚未准备完成"); return bridge.requestNative(method, p);
      }
      case "models.authStatus": {
        const model = selectedProductModel(await this.rpc("settings.get"));
        return {ts: Date.now(), providers: model.model_id ? [{provider: model.provider, displayName: model.provider,
          status: model.api_key_configured ? 'static' : 'missing', profiles: [], ...(model.api_key_configured ? {apiKey:{source:'config'}} : {})}] : [], providerCapabilities: []};
      }
      case "models.list": {
        const catalog = await this.rpc('model.connections.list');
        Object.assign(globalThis, {__mirallyModelProviderLabels: Object.fromEntries((catalog.connections || [])
          .map((connection: Data) => [modelProvider(connection.id).toLowerCase(), connection.name || '模型连接']))});
        return {models: (catalog.connections || []).flatMap((connection: Data) =>
          (connection.models || [connection.model]).filter((model: Data) => model?.model_id).map((model: Data) => ({id: model.model_id,
            name: model.model_id, provider: modelProvider(connection.id), available: !!connection.model?.base_url,
            input: model.image_enabled ? ['text', 'image'] : ['text'], reasoning: !!model.reasoning_enabled})))};
      }
      case "config.get": return this.config();
      case "config.patch": return this.patchConfig(p);
      default: {const result = await readMirallyModel(method, p, {rpc:this.rpc.bind(this)}); return result.handled ? result.value : unsupported(method);}
    }
  }
  private async config() {
    const settings = await this.rpc("settings.get"), model = selectedProductModel(settings), provider = modelProvider(settings.model_routes?.chat ?? 'default');
    const config = {ui: {prefs: this.preferences['control-ui'] || {}}, models: {providers: model.model_id ? {[provider]: {baseUrl: model.base_url, api: model.api_format === "openai-chat" ? "openai-completions" : model.api_format, models: [{id: model.model_id, name: model.model_id, input: model.image_enabled ? ["text", "image"] : ["text"], reasoning: model.reasoning_enabled, maxTokens: model.max_tokens}]}} : {}},
      agents: {defaults: {model: {primary: model.model_id ? `${provider}/${model.model_id}` : ""}}, list: [{id: "main", name: "Mirally"}]}};
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(config))))).map(x => x.toString(16).padStart(2, "0")).join(""); this.configHash = hash;
    return {path: "Mirally 产品配置", exists: true, valid: true, hash, config, resolved: config, raw: JSON.stringify(config, null, 2), issues: [], warnings: [], legacyIssues: []};
  }
  private async patchConfig(p: Data) {
    const current = await this.config();
    if (p.baseHash && p.baseHash !== current.hash) throw new Error("配置已变化，请重新加载后保存。");
    const patch = typeof p.raw === "string" ? JSON.parse(p.raw) : p.patch;
    if (patch && Object.keys(patch).length === 1 && patch.ui?.prefs && Object.keys(patch.ui).length === 1) {
      const value = {...this.preferences['control-ui'], ...patch.ui.prefs};
      const result = await this.http('/api/ui-preferences', {method:'POST',body:JSON.stringify({key:'control-ui',value})});
      this.preferences = result.preferences; return {ok:true, config:(await this.config()).config};
    }
    if (!patch || Object.keys(patch).some(name => !["models", "agents"].includes(name))) return unsupported("config.patch fields");
    const defaults = patch.agents?.defaults;
    if (defaults && Object.keys(defaults).some(name => name !== 'model')) return unsupported('config.patch agent defaults');
    const providers = patch.models?.providers || {};
    const names = Object.keys(providers); if (names.length > 1) throw new Error("此兼容入口一次只修改一个模型连接；更多连接请在模型设置中管理。");
    const name = names[0], provider = providers[name], model = provider?.models?.[0];
    if (model?.compat && Object.keys(model.compat).length) return unsupported('config.patch model compatibility');
    if (!provider || !model?.id || provider.models.length !== 1) throw new Error("当前内核需要一个明确的服务和模型连接。");
    const settings = await this.rpc("settings.get"), connectionId = settings.model_routes?.chat || "default";
    const value: Data = {provider: name === modelProvider(connectionId) ? selectedProductModel(settings).provider : name, base_url: provider.baseUrl, model_id: model.id, api_format: provider.api === "openai-completions" ? "openai-chat" : provider.api, image_enabled: model.input?.includes("image") || false, reasoning_enabled: !!model.reasoning};
    if (model.maxTokens) value.max_tokens = model.maxTokens;
    if (provider.apiKey) value.api_key = provider.apiKey;
    if (connectionId === "default") await this.rpc("settings.update", {patch: {model: value}});
    else await this.rpc("model.connection.save", {connection_id: connectionId, model: value});
    return {ok: true, config: (await this.config()).config};
  }
}
export function createMirallyBrowserClient(opts: GatewayBrowserClientOptions): GatewayBrowserClient { return new MirallyBrowserClient(opts) as unknown as GatewayBrowserClient; }
