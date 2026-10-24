/* Mirally product messages for the retained Control UI; no Gateway or model owner. */
(function (root, factory) {
  const exported = factory();
  if (typeof module === 'object' && module.exports) module.exports = exported;
  else if (root.top === root) {
    root.createLegacyNativeBridge = exported.createLegacyNativeBridge;
    root.__mirallyLegacyNativeBridge ||= exported.createLegacyNativeBridge(root);
    root.__mirallyLegacyNativeBridge.install();
  }
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const actions = new Set(['status', 'browse', 'monitor', 'memory-search', 'memory-read', 'start', 'pause', 'resume', 'stop', 'set-enabled', 'set-semantics', 'model-settings', 'set-semantic-model']);
  const mutations = new Set(['start', 'pause', 'resume', 'stop', 'set-enabled', 'set-semantics', 'set-semantic-model']);
  const fields = {
    status: [], browse: [], monitor: [], start: [], pause: ['session_id'], resume: ['session_id'], stop: ['session_id'],
    'set-enabled': ['enabled'], 'set-semantics': ['enabled', 'model', 'maxCallsPerHour', 'maxTokensPerDay'],
    'model-settings': [], 'set-semantic-model': ['model', 'maxOutputTokens'],
    'memory-search': ['query', 'include_candidates', 'limit', 'offset', 'provenances', 'kinds', 'start_utc_ms', 'end_utc_ms'],
    'memory-read': ['memory_id', 'from_line', 'line_count'],
  };
  const fileMethods = new Set(['sessions.files.list', 'sessions.files.get', 'artifacts.list', 'artifacts.get', 'artifacts.download']);
  const stateName = value => ({failed: 'error', stop_unconfirmed: 'unknown', cancelling: 'stopping'}[value] || value || 'unknown');
  const workersOf = value => value?.status && typeof value.status === 'object' ? value.status : value || {};
  const mime = name => ({md: 'text/markdown', txt: 'text/plain', csv: 'text/csv', json: 'application/json', html: 'text/html', css: 'text/css', svg: 'text/plain'}[String(name).split('.').at(-1).toLowerCase()] || 'text/plain');
  const filePath = artifact => `${artifact.id}-${artifact.name}`;
  const artifactSummary = (artifact, sessionKey) => ({id: artifact.id, type: 'file', title: artifact.name,
    mimeType: mime(artifact.name), sizeBytes: artifact.bytes, sessionKey, source: 'mirally', download: {mode: 'bytes'}});

  function projectStatus(activity, memory, settings) {
    const workers = workersOf(activity.workers), memoryWorkers = workersOf(memory.workers);
    const worker = workers.activity || {}, health = memoryWorkers.memory?.health || memory;
    const verified = memoryWorkers.running === true && !!memoryWorkers.memory;
    const actualState = worker.state;
    const runtime = {verified, status: verified ? stateName(memoryWorkers.memory?.state) : 'unknown',
      running: verified ? memoryWorkers.memory?.state !== 'disabled' : null,
      ready: verified ? !['disabled', 'starting', 'failed', 'stop_unconfirmed'].includes(memoryWorkers.memory?.state) : null,
      maintenance_running: verified && typeof health.maintenance_running === 'boolean' ? health.maintenance_running : null,
      maintenance_status: health.maintenance_status || stateName(memoryWorkers.memory?.state),
      semantic: {...memoryWorkers.semantic, state: stateName(memoryWorkers.semantic?.state)},
      semantic_maintenance: health.semantic_maintenance, error: memoryWorkers.memory?.error || health.last_error};
    const sessionId = `mirally-activity:${activity.fingerprint}`;
    const pending = workers.settings_pending === true;
    const capture = {enabled: activity.enabled === true, configured: true,
      config_path: activity.profiles?.activity, database: activity.profiles?.database,
      database_exists: activity.database_exists ?? !!activity.snapshot?.health,
      collection_level: activity.capture?.capture_level, capture: activity.scope || [], scope_note: activity.scope_note,
      config_fingerprint: activity.fingerprint, session_id: sessionId,
      live_enabled: workers.running === true && ['running', 'degraded', 'starting'].includes(actualState),
      collector: actualState ? {...activity.snapshot?.collector, status: stateName(actualState), snapshot: false,
        pause_reason: activity.snapshot?.pause?.reason} : {...activity.snapshot?.collector, status: 'unknown', snapshot: true},
      health: activity.snapshot?.health, diagnostics: activity.snapshot?.diagnostics,
      controls: {'set-enabled': !pending, start: activity.enabled && !activity.paused && !pending && ['disabled', 'stopped', 'failed'].includes(actualState),
        pause: activity.enabled && !activity.paused && !pending && ['running', 'degraded', 'starting'].includes(actualState),
        resume: activity.enabled && activity.paused && !pending, stop: activity.enabled && !pending},
      warning: worker.error || (pending ? '正在应用采集设置；请等待当前 owner 更新。' : null)};
    return {activity: capture, memory: {enabled: settings.memory_enabled === true, configured: true,
      config_path: memory.profiles?.memory, database: memory.profiles?.database,
      activity_config_path: health.settings?.activity_config_path,
      health, runtime, semantics: {...memory.semantics, ...settings.memory_semantics,
        enabled: settings.background_enabled === true, model: '', effective_model: settings.model?.model_id || null},
      raw_retention: health.raw_retention || memory.raw_retention,
      controls: {'set-semantics': true}}, profiles: {owner: 'mirally-framework', warning: ''}};
  }

  function createLegacyNativeBridge(root, suppliedHost) {
    let disposed = false, installed = false, controlling = false, original, wrapper, createdWebview = false;
    const candidates = new Set(), notices = new Set();
    async function host() {
      for (let attempt = 0; attempt < 200; attempt++) {
        if (disposed) throw new Error('Mirally 页面已关闭。');
        const api = suppliedHost || root.__mirallyLegacyHost;
        if (api?.rpc && api?.request) return api;
        await new Promise(resolve => root.setTimeout(resolve, 100));
      }
      throw new Error('Mirally 产品连接尚未就绪，请重新连接。');
    }
    const rpc = async (method, params = {}) => (await host()).rpc(method, params);
    const request = async (path, options) => (await host()).request(path, options);
    function notify(text) {
      if (notices.has(text)) return;
      notices.add(text);
      const api = suppliedHost || root.__mirallyLegacyHost;
      if (api?.notify) api.notify(text);
      else if (root.CustomEvent) root.dispatchEvent?.(new root.CustomEvent('mirally-experience-notice', {detail: {message: text}}));
    }
    async function status() {
      const [activity, memory, settings] = await Promise.all([rpc('activity.status'), rpc('memory.status'), rpc('settings.get')]);
      return projectStatus(activity, memory, settings);
    }
    async function confirmStart(method) {
      const activity = await rpc('activity.status');
      if (typeof activity.fingerprint !== 'string' || !activity.fingerprint) throw new Error('无法核对采集范围，请刷新后重试。');
      const api = await host(), scope = (activity.scope || []).filter(item => item.enabled).map(item => item.label).join('、');
      const confirmed = api.confirmCapture ? await api.confirmCapture({method, fingerprint: activity.fingerprint, scope: activity.scope, scope_note: activity.scope_note})
        : root.confirm?.(`确认读取这些电脑活动？\n\n${scope}\n${activity.scope_note || ''}\n\n仅在你确认后开始；配置变化会要求重新确认。`);
      if (confirmed !== true) return {cancelled: true};
      const result = await rpc(method, {confirmed: true, expected_fingerprint: activity.fingerprint});
      return {...result, saved: true};
    }
    async function checkSession(message) {
      const activity = await rpc('activity.status');
      if (message.session_id !== `mirally-activity:${activity.fingerprint}`) throw new Error('采集范围或会话已变化，请刷新后再操作。');
    }
    async function openHistory(mode) {
      const result = await rpc('activity.browse', {mode});
      const url = new URL(result.url);
      if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.username || url.password || url.pathname !== '/' || [...url.searchParams].length !== 1 || !['', '#diagnostics'].includes(url.hash) || !/^[A-Za-z0-9_-]{24,128}$/.test(url.searchParams.get('token') || '')) throw new Error('宿主未返回有效的本机活动历史地址。');
      const api = await host();
      let opened;
      if (api.openBrowser) opened = await api.openBrowser(result.url);
      else if (root.pywebview?.api?.open_browser) opened = await root.pywebview.api.open_browser(result.url);
      else if (!root.open(result.url, '_blank', 'noopener')) throw new Error('浏览器阻止了新窗口，请允许打开本机活动历史后重试。');
      if (opened === false || opened?.opened === false) throw new Error('本机活动历史尚未打开，请重试。');
      return {...result, opened: true};
    }
    async function saveSemantics(message, modelOnly = false) {
      // The owned framework currently routes all background semantics through
      // the configured main model. Do not pretend an unimplemented route saved.
      if (Object.hasOwn(message, 'model') && message.model !== '') throw new Error('当前内核的活动记忆整理沿用主对话模型；独立整理模型路由尚未实现。请留空。');
      const patch = {}, budget = {};
      if (!modelOnly) {
        if (typeof message.enabled !== 'boolean') throw new Error('整理开关必须是布尔值。');
        patch.background_enabled = message.enabled;
      }
      for (const [field, key, minimum, maximum] of [['maxCallsPerHour', 'max_calls_per_hour', 0, 1000], ['maxTokensPerDay', 'max_tokens_per_day', 0, 10000000], ['maxOutputTokens', 'max_output_tokens', 128, 16000]]) {
        if (!Object.hasOwn(message, field)) continue;
        const value = message[field];
        if (!Number.isInteger(value) || value < minimum || value > maximum) throw new Error('整理预算超出当前内核支持范围。');
        budget[key] = value;
      }
      if (Object.keys(budget).length) patch.memory_semantics = budget;
      const result = Object.keys(patch).length ? await rpc('settings.update', {patch}) : await rpc('settings.get');
      const workers = workersOf(result.workers);
      return {saved: true, applied: workers.running === true && workers.settings_pending === false,
        ...(workers.running === true ? {} : {reload_error: '尚未确认后台监督者已应用设置。'})};
    }
    async function execute(message) {
      const allowed = new Set(['type', 'action', 'requestId', ...(fields[message.action] || [])]);
      if (!actions.has(message.action) || Object.keys(message).some(key => !allowed.has(key))) throw new Error('不支持的 Mirally 产品操作或字段。');
      switch (message.action) {
        case 'status': return status();
        case 'browse': return openHistory('history');
        case 'monitor': return openHistory('monitor');
        case 'start': return confirmStart('activity.start');
        case 'resume': await checkSession(message); return confirmStart('activity.resume');
        case 'pause': case 'stop': await checkSession(message); return rpc(`activity.${message.action}`);
        case 'set-enabled': {
          if (typeof message.enabled !== 'boolean') throw new Error('活动记录开关必须是布尔值。');
          if (message.enabled) return confirmStart('activity.start');
          return {...await rpc('activity.stop'), saved: true};
        }
        case 'memory-search': {
          const params = Object.fromEntries(fields['memory-search'].filter(key => Object.hasOwn(message, key)).map(key => [key, message[key]]));
          const result = await rpc('memory.search', params);
          if (!params.include_candidates) candidates.clear();
          else for (const row of result.memories || []) if (row.status === 'candidate') candidates.add(row.id);
          return result;
        }
        case 'memory-read': return rpc('memory.read', {memory_id: message.memory_id, from_line: message.from_line ?? 1, line_count: message.line_count ?? 100, include_candidates: candidates.has(message.memory_id)});
        case 'model-settings': {
          const settings = await rpc('settings.get');
          return {enabled: settings.background_enabled === true, model: '', effective_model: settings.model?.model_id || null, ...settings.memory_semantics, canSave: true};
        }
        case 'set-semantics': return saveSemantics(message);
        case 'set-semantic-model': return saveSemantics(message, true);
      }
    }
    function receive(message, payload) { if (!disposed) root.__mirallyActivity?.receive({requestId: message.requestId, ...payload}); }
    function onNativeMessage(message) {
      if (!message || typeof message !== 'object' || message.type !== 'mirally-activity') return false;
      if (typeof message.requestId !== 'string' || !message.requestId || message.requestId.length > 256) return true;
      const mutation = mutations.has(message.action);
      if (mutation && controlling) { receive(message, {error: '上一项产品操作尚未完成，请等待后再试。'}); return true; }
      if (mutation) controlling = true;
      Promise.resolve().then(() => execute(message)).then(result => receive(message, {result}), error => receive(message, {error: error.message || String(error)}))
        .finally(() => { if (mutation) controlling = false; });
      return true;
    }
    async function files(params) {
      if (params.agentId && params.agentId !== 'main') throw new Error('当前仅支持 Mirally 主会话的成果。');
      if (params.runId || params.taskId) throw new Error('当前成果回执按明确会话归属，尚未支持按原宿主回合或任务筛选。');
      if (typeof params.sessionKey !== 'string' || !params.sessionKey || params.sessionKey.length > 512) throw new Error('缺少有效会话。');
      const api = await host(), id = await api.conversationIdForSession?.(params.sessionKey);
      if (typeof id !== 'string' || !id) throw new Error('无法核对这份文件所属的 Mirally 会话。');
      const result = await request(`/api/artifacts?conversation_id=${encodeURIComponent(id)}`);
      return {id, artifacts: (result.artifacts || []).filter(item => item.conversation_id === id)};
    }
    async function requestNative(method, params = {}) {
      if (!fileMethods.has(method)) throw new Error('未实现此原生文件操作；只可读取明确保存的 Mirally 成果。');
      const {artifacts} = await files(params), rootName = 'Mirally 成果';
      if (method === 'artifacts.list') return {artifacts: artifacts.map(item => artifactSummary(item, params.sessionKey))};
      if (method === 'artifacts.get' || method === 'artifacts.download') {
        if (!/^[a-f0-9]{36}$/.test(params.artifactId || '')) throw new Error('成果回执无效。');
        const artifact = artifacts.find(item => item.id === params.artifactId);
        if (!artifact) throw new Error('当前会话没有这份已保存成果。');
        if (method === 'artifacts.get') return {artifact: artifactSummary(artifact, params.sessionKey)};
        const content = await request(`/api/artifacts/${artifact.id}/content`);
        if (content.artifact?.id !== artifact.id || content.artifact.conversation_id !== artifact.conversation_id ||
          content.encoding !== 'base64' || typeof content.data !== 'string' || content.data.length > 1398104)
          throw new Error('成果字节回执无效，请刷新。');
        return {artifact: artifactSummary(artifact, params.sessionKey), encoding: 'base64', data: content.data, url: `/api/artifacts/${artifact.id}/download`};
      }
      if (method === 'sessions.files.list') {
        if (params.path && params.path !== '.') throw new Error('仅提供本会话成果根目录，不浏览任意本机目录。');
        if (params.search != null && (typeof params.search !== 'string' || params.search.length > 512)) throw new Error('文件搜索条件无效。');
        const entries = artifacts.filter(item => !params.search || item.name.toLowerCase().includes(params.search.toLowerCase())).map(item => ({path: filePath(item), name: item.name, kind: 'file', sessionKind: 'modified', size: item.bytes, updatedAtMs: Date.parse(item.created_at)}));
        return {sessionKey: params.sessionKey, root: rootName, files: entries.map(item => ({...item, kind: 'modified', missing: false})), browser: {path: '', entries}};
      }
      if (typeof params.path !== 'string' || !params.path || params.path.length > 4096 || /^[\\/]|^[A-Za-z]:/.test(params.path) || params.path.replaceAll('\\', '/').split('/').includes('..')) throw new Error('仅允许读取当前会话已登记的成果路径。');
      const matches = artifacts.filter(item => filePath(item) === params.path || item.name === params.path);
      if (matches.length !== 1) throw new Error(matches.length ? '存在同名成果，请在文件侧栏选择明确文件。' : '当前会话没有这份已保存成果。');
      const artifact = matches[0];
      if (!/^[a-f0-9]{36}$/.test(artifact.id)) throw new Error('成果回执无效。');
      const preview = await request(`/api/artifacts/${artifact.id}/preview`);
      if (preview.artifact?.id !== artifact.id || preview.artifact.conversation_id !== artifact.conversation_id) throw new Error('成果所属会话已变化，请刷新。');
      return {sessionKey: params.sessionKey, root: rootName, file: {path: filePath(artifact), workspacePath: filePath(artifact), name: artifact.name,
        kind: 'modified', missing: false, size: artifact.bytes, updatedAtMs: Date.parse(artifact.created_at),
        previewKind: 'text', contentEncoding: 'utf8', mimeType: mime(artifact.name),
        content: (preview.text || '') + (preview.truncated ? '\n\n[预览已截短；完整内容请从成果下载入口读取。]' : '')}};
    }
    function browserMessage(message) {
      if (message?.type === 'mirally-ufo-input' || message?.type === 'mirally-startup-painted') return;
      if (message?.type === 'mirally-companion') {
        if (message.action === 'capture-target') root.__mirallyContext?.receive({requestId: message.requestId, error: '窗口截图需要 Mirally 桌面窗口；浏览器中请添加图片文件。'});
        else if (['drag', 'minimize'].includes(message.action)) notify('移动和最小化伙伴需要 Mirally 桌面窗口。');
        return; // Shape/dock remain page presentation, with no claimed OS effect.
      }
      throw new Error('浏览器模式没有此原生窗口操作。');
    }
    function install() {
      if (installed || disposed) return bridge;
      root.chrome ||= {};
      if (!root.chrome.webview) {
        const BrowserEvents = root.EventTarget || globalThis.EventTarget;
        const events = new BrowserEvents();
        root.chrome.webview = {__mirallyBrowserOnly: true,
          addEventListener: events.addEventListener.bind(events),
          removeEventListener: events.removeEventListener.bind(events)};
        createdWebview = true;
      }
      const native = root.chrome.webview;
      original = typeof native.postMessage === 'function' ? native.postMessage : null;
      wrapper = message => { if (!onNativeMessage(message)) { if (original) return original.call(native, message); return browserMessage(message); } };
      native.postMessage = wrapper; installed = true;
      return bridge;
    }
    function dispose() {
      disposed = true; candidates.clear();
      if (installed && root.chrome?.webview?.postMessage === wrapper) {
        if (createdWebview) delete root.chrome.webview;
        else root.chrome.webview.postMessage = original;
      }
    }
    const bridge = {install, dispose, onNativeMessage, requestNative, supports: method => fileMethods.has(method), status};
    root.addEventListener?.('pagehide', dispose, {once: true});
    return bridge;
  }
  return {createLegacyNativeBridge, projectStatus};
});
