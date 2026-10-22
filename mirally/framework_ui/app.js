const $ = id => document.getElementById(id);
const state = {token: '', connected: false, sessions: [], current: null, busy: false, settings: {}, stream: null, page: 'chat', notices: 0};
const titles = {chat: '对话', memory: '记忆', activity: '活动', reminders: '提醒', inbox: '收件箱', actions: '桌面任务', extensions: '扩展', settings: '设置'};
const empty = (element, text) => { element.replaceChildren(); const p = document.createElement('p'); p.className = 'muted'; p.textContent = text; element.append(p); };
function notice(text, success = false) { $('notice').textContent = text; $('notice').className = success ? 'success' : ''; $('notice').hidden = !text; }
function errorText(error) { return error?.message || String(error); }
async function request(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...(state.token ? {Authorization: `Bearer ${state.token}`} : {}), ...options.headers}});
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.error) throw new Error(data.error?.message || data.message || `请求失败（${response.status}）`);
  return data;
}
async function rpc(method, params = {}) {
  const data = await request('/api/rpc', {method: 'POST', body: JSON.stringify({method, params})});
  return Object.hasOwn(data, 'result') ? data.result : data;
}
function handle(action) { return async event => { event?.preventDefault(); try { await action(event); } catch (error) { notice(errorText(error)); } }; }
function card(container, item, options = {}) {
  const node = document.createElement('article'); node.className = 'card';
  const title = document.createElement('h3'); title.textContent = options.title || item.title || item.subject || item.name || item.text?.slice(0, 70) || item.id || '记录'; node.append(title);
  const text = document.createElement('p'); const value = options.text ?? item.content ?? item.body ?? item.text ?? item.summary ?? ''; text.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); node.append(text);
  const meta = document.createElement('small'); meta.textContent = options.meta || [item.id || item.memory_id || item.commitment_id || item.delivery_id, item.status || item.state, item.source_id || item.source, item.observed_at || item.due_at || item.created_at].filter(Boolean).join(' · '); node.append(meta);
  if (options.action) { const button = document.createElement('button'); button.textContent = options.actionLabel || '查看'; button.addEventListener('click', handle(() => options.action(item))); node.append(button); }
  container.append(node); return node;
}
function listFrom(data, keys) { if (Array.isArray(data)) return data; for (const key of keys) if (Array.isArray(data?.[key])) return data[key]; return []; }
function sessionId(session) { return session?.id || session?.session_id; }
function conversationId() { return state.current?.conversation_id || state.current?.conversationId || sessionId(state.current); }
function setBusy(value, message = '') { state.busy = value; $('working').hidden = !value; $('send').disabled = value; $('working-text').textContent = message || '正在处理'; }
function renderSessions() {
  $('sessions').replaceChildren();
  for (const session of state.sessions) { const button = document.createElement('button'); button.textContent = session.title || '新对话'; button.className = sessionId(session) === sessionId(state.current) ? 'selected' : ''; button.addEventListener('click', handle(() => selectSession(session))); $('sessions').append(button); }
}
async function refreshSessions() { const data = await request('/api/sessions'); state.sessions = listFrom(data, ['sessions', 'conversations']); renderSessions(); }
function messageNode(role, text) { const article = document.createElement('article'); article.className = `message ${['user', 'assistant', 'tool'].includes(role) ? role : 'tool'}`; const name = document.createElement('span'); name.className = 'role'; name.textContent = role === 'user' ? '你' : role === 'assistant' ? 'Mirally' : '执行记录'; const body = document.createElement('div'); body.textContent = text; article.append(name, body); $('messages').append(article); return body; }
async function loadHistory() {
  if (!state.current) return;
  const data = await request(`/api/sessions/${encodeURIComponent(sessionId(state.current))}/history`);
  const messages = listFrom(data, ['messages', 'items']);
  $('messages').replaceChildren(); for (const message of messages) messageNode(message.role || 'assistant', message.text ?? message.content ?? '');
  if (data.status) setBusy(['running', 'processing', 'busy'].includes(data.status));
  $('messages').scrollTop = $('messages').scrollHeight;
}
async function selectSession(session) { state.current = session; partialMessage = null; setBusy(false); state.stream?.abort(); renderSessions(); await showPage('chat'); await loadHistory(); connectEvents(); document.body.classList.remove('nav-open'); }
async function createSession() { const data = await request('/api/sessions', {method: 'POST', body: JSON.stringify({title: '新对话'})}); const session = data.session || data; await refreshSessions(); $('messages').replaceChildren(); await selectSession(session); $('message').focus(); return session; }
async function sendMessage() {
  const text = $('message').value.trim(); if (!text || state.busy) return;
  if (!state.current) await createSession();
  if ($('messages').querySelector('.welcome')) $('messages').replaceChildren();
  messageNode('user', text); $('message').value = ''; setBusy(true); notice('');
  const commandId = crypto.randomUUID();
  try { await request(`/api/sessions/${encodeURIComponent(sessionId(state.current))}/messages`, {method: 'POST', body: JSON.stringify({commandId, text})}); }
  catch (error) { setBusy(false); notice(errorText(error)); await loadHistory(); }
}
async function connectEvents() {
  state.stream?.abort(); const controller = new AbortController(); state.stream = controller;
  try {
    const response = await fetch('/api/events', {headers: state.token ? {Authorization: `Bearer ${state.token}`} : {}, signal: controller.signal});
    if (!response.ok || !response.body) throw new Error('实时连接暂不可用');
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    while (!controller.signal.aborted) {
      const {done, value} = await reader.read(); if (done) break;
      buffer += decoder.decode(value, {stream: true}); buffer = buffer.replace(/\r\n/g, '\n');
      let end; while ((end = buffer.indexOf('\n\n')) >= 0) { const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2); const lines = frame.split('\n'); const name = lines.find(line => line.startsWith('event:'))?.slice(6).trim(); const payload = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n'); if (!payload) continue; try { await consumeEvent(JSON.parse(payload), name); } catch (error) { notice(errorText(error)); } }
    }
    if (!controller.signal.aborted) { $('connection').textContent = '正在重连'; setTimeout(connectEvents, 1500); }
  } catch (error) { if (!controller.signal.aborted) { $('connection').textContent = '连接中断'; setTimeout(connectEvents, 2500); } }
}
let partialMessage = null;
async function consumeEvent(event, name) {
  const type = event.type || name;
  if (type === 'inbox' || type === 'delivery') { await refreshInbox(); return; }
  if (type === 'action') { if (state.page === 'actions') await refreshActions(); return; }
  const id = event.session_id || event.sessionId;
  if (id && id !== sessionId(state.current)) return;
  if (['token', 'delta', 'text-delta'].includes(type)) { if (!partialMessage) partialMessage = messageNode('assistant', ''); partialMessage.textContent += event.text || event.delta || ''; $('messages').scrollTop = $('messages').scrollHeight; }
  if (['done', 'idle', 'cancelled', 'completed'].includes(type)) { partialMessage = null; setBusy(false); await loadHistory(); await refreshSessions(); }
  if (['error', 'failed'].includes(type)) { partialMessage = null; setBusy(false); notice(event.text || event.message || event.error?.message || '这次处理未完成，请重试。'); await loadHistory(); }
  if (type === 'session' || type === 'status') { const status = event.status || event.state; if (status) setBusy(['running', 'processing', 'busy'].includes(status), event.message); if (status === 'idle') { partialMessage = null; await loadHistory(); } }
}
async function showPage(page) {
  state.page = page; document.querySelectorAll('.page').forEach(node => node.classList.toggle('active', node.id === `page-${page}`)); document.querySelectorAll('nav button').forEach(node => node.classList.toggle('active', node.dataset.page === page)); $('page-title').textContent = titles[page]; document.body.classList.remove('nav-open');
  if (page === 'settings' || page === 'activity') await loadSettings();
  if (page === 'reminders') await refreshReminders();
  if (page === 'inbox') await refreshInbox();
  if (page === 'extensions') await refreshExtensions();
  if (page === 'actions') await refreshActions();
}
async function loadSettings() {
  if (state.settingsLoad) return state.settingsLoad;
  const controls = [...document.querySelectorAll('#page-settings input, #page-settings select, #page-settings textarea, #page-settings button, #activity-enabled')];
  controls.forEach(control => { control.disabled = true; });
  state.settingsLoad = loadSettingsData().finally(() => { controls.forEach(control => { control.disabled = false; }); state.settingsLoad = null; });
  return state.settingsLoad;
}
async function loadSettingsData() {
  state.settings = await rpc('settings.get'); const model = state.settings.model || {};
  for (const [id, key] of [['activity-enabled', 'activity_enabled'], ['memory-enabled', 'memory_enabled'], ['background-enabled', 'background_enabled'], ['initiative-enabled', 'general_initiative_enabled'], ['dnd-enabled', 'do_not_disturb']]) $(id).checked = !!state.settings[key];
  for (const [id, key] of [['model-provider', 'provider'], ['model-format', 'api_format'], ['model-url', 'base_url'], ['model-id', 'model_id'], ['model-max-tokens', 'max_tokens']]) if (model[key] !== undefined) $(id).value = model[key];
  $('model-images').checked = !!model.image_enabled; $('model-reasoning').checked = !!model.reasoning_enabled; $('model-key').value = ''; $('key-status').textContent = model.api_key_configured ? '已保存，留空保留' : '尚未配置';
  const status = await rpc('system.status'); $('system-status').textContent = JSON.stringify(status, null, 2); $('activity-status').replaceChildren(); card($('activity-status'), {}, {title: $('activity-enabled').checked ? '活动采集已启用' : '活动采集已关闭', text: status.workers ? JSON.stringify(status.workers, null, 2) : '开启或关闭后，运行状态会在这里更新。'});
  const prefs = await rpc('preference.list'); $('preferences').replaceChildren(); for (const pref of listFrom(prefs, ['preferences', 'items'])) card($('preferences'), pref, {title: pref.key || pref.name, text: pref.value});
}
async function searchContext(kind) {
  const query = $(`${kind}-query`).value.trim();
  const data = await rpc('context.read', {query, include_memory: kind === 'memory', include_activity: kind === 'activity', limit: 30});
  const results = data[kind]; const items = listFrom(results, ['memories', 'items', 'results']); const container = $(`${kind}-results`); container.replaceChildren();
  if (!items.length) empty(container, results?.status === 'disabled' ? '这项能力当前关闭，可在设置中开启。' : '没有找到匹配资料。资料覆盖可能不完整。');
  for (const item of items) card(container, item);
  for (const gap of data.gaps || []) { const p = document.createElement('p'); p.className = 'muted'; p.textContent = typeof gap === 'string' ? gap : JSON.stringify(gap); container.append(p); }
}
async function refreshReminders() {
  const data = await rpc('commitment.list'); const container = $('reminders'); container.replaceChildren(); const reminders = listFrom(data, ['commitments', 'items']);
  if (!reminders.length) empty(container, '还没有提醒。');
  for (const item of reminders) card(container, item, {actionLabel: '取消提醒', action: async reminder => { await rpc('commitment.cancel', {commitment_id: reminder.commitment_id || reminder.id}); await refreshReminders(); }});
}
async function refreshInbox() {
  const data = await rpc('inbox.list'); const container = $('inbox-items'); container.replaceChildren(); const items = listFrom(data, ['items', 'messages', 'deliveries']); const unread = items.filter(item => item.state === 'unread'); $('inbox-count').textContent = unread.length ? String(unread.length) : '';
  if (!items.length) empty(container, '目前没有新消息。');
  for (const item of items) card(container, item, {actionLabel: '标记已读', action: async message => { await rpc('inbox.ack', {delivery_id: message.delivery_id || message.id, state: 'read'}); await refreshInbox(); }});
}
async function refreshExtensions() {
  const data = await rpc('extensions.list'); const container = $('extension-items'); container.replaceChildren(); const items = listFrom(data, ['extensions', 'items']);
  if (!items.length) empty(container, '尚未添加扩展。');
  for (const item of items) card(container, item, {title: item.name || item.id, text: item.description || item.error || item.status || item.type, actionLabel: '停用', action: async extension => { await rpc('extensions.disable', {id: extension.id}); await refreshExtensions(); }});
}
async function showEcosystem(method) { const data = await rpc(method); $('ecosystem-results').replaceChildren(); const items = listFrom(data, ['skills', 'tools', 'resources', 'prompts', 'items', 'servers']); if (!items.length) empty($('ecosystem-results'), '没有可用项目。请先配置并启用对应扩展。'); for (const item of items) card($('ecosystem-results'), item, {text: JSON.stringify(item, null, 2)}); }
async function refreshActions() {
  const {actions} = await rpc('desktop.list'); const container = $('action-items'); container.replaceChildren();
  if (!actions.length) empty(container, '还没有桌面任务。');
  for (const action of actions) {
    const active = ['starting', 'running', 'waiting_for_user', 'stopping'].includes(action.state);
    const node = card(container, action, {title: action.text, text: action.result?.message || action.result?.text || '', meta: `${action.state} · ${action.started_at}`, ...(active ? {actionLabel: '停止任务', action: async () => { await rpc('desktop.cancel', {id: action.id}); await refreshActions(); }} : {})});
    if (!action.question) continue;
    const question = action.question; const p = document.createElement('p'); p.textContent = question.text || JSON.stringify(question.action); node.append(p);
    const respond = async values => { await rpc('desktop.respond', {id: action.id, request_id: question.id, ...values}); await refreshActions(); };
    if (question.type === 'confirm') { for (const [label, confirmed] of [['允许这次操作', true], ['拒绝', false]]) { const button = document.createElement('button'); button.textContent = label; button.addEventListener('click', handle(() => respond({confirmed}))); node.append(button); } }
    else { const form = document.createElement('form'); const input = document.createElement('input'); input.required = true; input.setAttribute('aria-label', '回答桌面任务问题'); const button = document.createElement('button'); button.textContent = '发送回答'; form.append(input, button); form.addEventListener('submit', handle(() => respond({text: input.value}))); node.append(form); }
  }
}
for (const button of document.querySelectorAll('nav button')) button.addEventListener('click', handle(() => showPage(button.dataset.page)));
$('new-chat').addEventListener('click', handle(createSession)); $('composer').addEventListener('submit', handle(sendMessage));
$('message').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); handle(sendMessage)(); } });
$('stop').addEventListener('click', handle(async () => { const result = await request(`/api/sessions/${encodeURIComponent(sessionId(state.current))}/cancel`, {method: 'POST', body: '{}'}); partialMessage = null; setBusy(false); notice(result.status === 'unknown' ? '停止请求已发出，仍有结果待核实。' : '已请求停止。', true); await loadHistory(); }));
$('toggle-nav').addEventListener('click', () => document.body.classList.toggle('nav-open')); $('companion').addEventListener('click', () => document.body.classList.toggle('companion'));
$('refresh').addEventListener('click', handle(async () => { await refreshSessions(); if (state.page === 'chat') await loadHistory(); else await showPage(state.page); }));
for (const kind of ['memory', 'activity']) $(`${kind}-search`).addEventListener('submit', handle(() => searchContext(kind)));
$('model-settings').addEventListener('submit', handle(async () => { const model = {provider: $('model-provider').value, api_format: $('model-format').value, base_url: $('model-url').value.trim(), model_id: $('model-id').value.trim(), max_tokens: Number($('model-max-tokens').value), image_enabled: $('model-images').checked, reasoning_enabled: $('model-reasoning').checked}; if ($('model-key').value) model.api_key = $('model-key').value; await rpc('settings.update', {patch: {model}}); await loadSettings(); notice('模型连接已保存。下一次对话使用新配置。', true); }));
for (const [id, key] of [['activity-enabled', 'activity_enabled'], ['memory-enabled', 'memory_enabled'], ['background-enabled', 'background_enabled'], ['initiative-enabled', 'general_initiative_enabled'], ['dnd-enabled', 'do_not_disturb']]) $(id).addEventListener('change', handle(async () => { const previous = !$(id).checked; try { await rpc('settings.update', {patch: {[key]: $(id).checked}}); await loadSettings(); notice('设置已保存。', true); } catch (error) { $(id).checked = previous; throw error; } }));
$('preference-save').addEventListener('submit', handle(async () => { await rpc('preference.set', {key: $('preference-key').value.trim(), value: $('preference-value').value.trim()}); $('preference-value').value = ''; await loadSettings(); notice('偏好已保存。', true); }));
$('memory-correction').addEventListener('submit', handle(async () => { await rpc('memory.correct', {content: $('correction-text').value.trim(), memory_id: $('correction-id').value.trim() || undefined}); $('correction-text').value = ''; notice('纠正已保存。', true); await searchContext('memory'); }));
$('reminder-create').addEventListener('submit', handle(async () => { await rpc('commitment.create', {title: $('reminder-text').value.trim(), body: $('reminder-text').value.trim(), due_at_utc_ms: new Date($('reminder-time').value).getTime(), channel: 'inbox', request_id: crypto.randomUUID()}); $('reminder-text').value = ''; await refreshReminders(); notice('提醒已创建。', true); }));
$('extension-configure').addEventListener('submit', handle(async () => { const config = JSON.parse($('extension-json').value); await rpc('extensions.configure', {extension: config}); await refreshExtensions(); notice('扩展配置已应用。', true); }));
$('skills-list').addEventListener('click', handle(() => showEcosystem('skills.list'))); $('mcp-list').addEventListener('click', handle(() => showEcosystem('mcp.resources.list'))); $('prompts-list').addEventListener('click', handle(() => showEcosystem('mcp.prompts.list')));
$('script-run').addEventListener('submit', handle(async () => { const args = JSON.parse($('script-args').value); if (!Array.isArray(args) || args.some(value => typeof value !== 'string')) throw new Error('脚本参数应为字符串组成的 JSON 数组。'); if (!state.current) await createSession(); const data = await rpc('scripts.run', {extension_id: $('script-extension').value.trim(), path: $('script-path').value.trim(), args, confirmed: $('script-confirm').checked, conversation_id: conversationId(), command_id: crypto.randomUUID()}); $('script-result').textContent = JSON.stringify(data, null, 2); $('script-confirm').checked = false; }));
$('desktop-start').addEventListener('submit', handle(async () => { if (!$('desktop-confirm').checked) throw new Error('请先确认执行范围。'); if (!state.current) await createSession(); await rpc('desktop.start', {conversation_id: conversationId(), command_id: crypto.randomUUID(), text: $('desktop-text').value.trim(), confirmed: true}); $('desktop-confirm').checked = false; await showPage('actions'); }));
$('ecosystem-read').addEventListener('submit', handle(async () => { const kind = $('ecosystem-kind').value; const server = $('ecosystem-server').value.trim(); const name = $('ecosystem-name').value.trim(); const params = kind === 'skills.read' ? {name} : kind === 'mcp.resources.read' ? {server, uri: name} : {server, name, arguments: JSON.parse($('ecosystem-args').value)}; $('ecosystem-detail').textContent = JSON.stringify(await rpc(kind, params), null, 2); }));
$('legacy-list').addEventListener('click', handle(async () => { $('legacy-history').textContent = JSON.stringify(await rpc('legacy.history'), null, 2); }));
$('mcp-tools-list').addEventListener('click', handle(() => showEcosystem('mcp.tools.list')));
$('mcp-call').addEventListener('submit', handle(async () => { if (!$('mcp-confirm').checked) throw new Error('需要明确确认这次 MCP 调用。'); if (!state.current) await createSession(); const result = await rpc('mcp.tools.call', {server: $('mcp-tool-server').value.trim(), name: $('mcp-tool-name').value.trim(), arguments: JSON.parse($('mcp-tool-args').value), confirmed: true, conversation_id: conversationId(), command_id: crypto.randomUUID()}); $('mcp-tool-result').textContent = JSON.stringify(result, null, 2); $('mcp-confirm').checked = false; }));
$('quit').addEventListener('click', handle(async () => { await request('/api/shutdown', {method: 'POST', body: '{}'}); notice('Mirally 正在退出。', true); state.connected = false; state.stream?.abort(); if (window.pywebview?.api?.quit) await window.pywebview.api.quit(); }));
async function boot() {
  const fragment = new URLSearchParams(location.hash.slice(1)); state.token = fragment.get('token') || '';
  history.replaceState(null, '', location.pathname); // The token remains in memory only.
  try { if (state.token) await request('/api/auth', {method: 'POST', body: '{}'}); const health = await request('/api/health'); state.connected = true; $('connection').textContent = '本机已连接'; $('connection').classList.add('ready'); $('runtime-state').textContent = health.version ? `Mirally · ${health.version}` : 'Mirally 本机内核'; await refreshSessions(); if (state.sessions.length) { state.current = state.sessions[0]; renderSessions(); await loadHistory(); } connectEvents(); await refreshInbox(); const settings = await rpc('settings.get'); if (!settings.model?.model_id) notice('先在「设置」连接你的模型服务，然后开始对话。'); }
  catch (error) { notice(errorText(error)); $('connection').textContent = '连接失败'; }
}
window.addEventListener('beforeunload', () => state.stream?.abort());
setInterval(() => { if (state.connected) { refreshInbox().catch(() => {}); if (state.page === 'actions') refreshActions().catch(() => {}); } }, 15_000);
document.addEventListener('focusin', () => window.pywebview?.api?.editing(true));
document.addEventListener('focusout', () => window.pywebview?.api?.editing(false));
boot();
