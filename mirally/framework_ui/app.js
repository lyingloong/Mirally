const $ = id => document.getElementById(id);
const state = {token: '', connected: false, sessions: [], current: null, busy: false, submitting: false, historyLoading: false,
  messages: [], attachments: [], actions: [], artifacts: [], tools: [], uiPreferences: {}, settings: {}, stream: null, page: 'chat', notices: 0};
const drafts = new Map(), listeners = new Set(); let creatingSession, stateFrame;
function emitState() { if (!stateFrame) stateFrame = requestAnimationFrame(() => { stateFrame = null; for (const listener of listeners) listener(); }); }
function rememberDraft() { const id = conversationId(); if (id) drafts.set(id, {text: $('message').value, attachments: state.attachments}); }
const titles = {chat: '对话', memory: '记忆', activity: '活动', reminders: '提醒', inbox: '收件箱', actions: '桌面任务', extensions: '扩展', settings: '设置'};
const empty = (element, text) => { element.replaceChildren(); const p = document.createElement('p'); p.className = 'muted'; p.textContent = text; element.append(p); };
function notice(text, success = false) { $('notice').textContent = text; $('notice').className = success ? 'success' : ''; $('notice').hidden = !text; if (!success) state.lastError = text; emitState(); }
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
function setBusy(value, message = '') { state.busy = value; $('working').hidden = !value; $('send').disabled = state.submitting; $('send').textContent = value ? '补充' : '发送'; $('working-text').textContent = message || '正在处理'; emitState(); }
function renderSessions() {
  $('sessions').replaceChildren();
  for (const session of state.sessions) { const button = document.createElement('button'); button.textContent = session.title || '新对话'; button.className = sessionId(session) === sessionId(state.current) ? 'selected' : ''; button.addEventListener('click', handle(() => selectSession(session))); $('sessions').append(button); }
}
async function refreshSessions() { const data = await request('/api/sessions'); state.sessions = listFrom(data, ['sessions', 'conversations']); if (state.current) state.current = state.sessions.find(item => sessionId(item) === sessionId(state.current)) || state.current; renderSessions(); emitState(); }
function messageNode(role, text) { const article = document.createElement('article'); article.className = `message ${['user', 'assistant', 'tool'].includes(role) ? role : 'tool'}`; const name = document.createElement('span'); name.className = 'role'; name.textContent = role === 'user' ? '你' : role === 'assistant' ? 'Mirally' : '执行记录'; const body = document.createElement('div'); body.textContent = text; article.append(name, body); $('messages').append(article); return body; }
async function loadHistory() {
  if (!state.current) return;
  // The native transcript commits at attempt end. A manual refresh must keep
  // the current provisional reply attached until its ordered SSE end arrives.
  if (state.busy && partialMessage) return;
  const id = conversationId(); state.historyLoading = true; emitState();
  let data;
  try { data = await request(`/api/sessions/${encodeURIComponent(id)}/history`); }
  finally { if (conversationId() === id) { state.historyLoading = false; emitState(); } }
  if (conversationId() !== id) return;
  if (state.busy && partialMessage) return;
  const messages = listFrom(data, ['messages', 'items']);
  partialMessage = null; partialRecord = null; partialAttempt = null;
  state.messages = messages; state.historyLoading = false; emitState();
  $('messages').replaceChildren(); for (const message of messages) messageNode(message.role || 'assistant', message.text ?? message.content ?? '');
  if (data.status) setBusy(['running', 'processing', 'busy'].includes(data.status));
  $('messages').scrollTop = $('messages').scrollHeight;
}
async function selectSession(session) { if (typeof session === 'string') session = state.sessions.find(item => sessionId(item) === session); if (!session) return; rememberDraft(); state.current = session; state.messages = []; state.artifacts = []; state.tools = []; state.runId = null; state.lastCompletion = null; state.lastError = ''; const draft = drafts.get(conversationId()); $('message').value = draft?.text || ''; state.attachments = draft?.attachments || []; renderAttachments(); partialMessage = null; setBusy(['working', 'running', 'processing', 'executing', 'busy'].includes(session.status)); state.stream?.abort(); renderSessions(); await showPage('chat'); await loadHistory(); connectEvents(); await refreshArtifacts(); document.body.classList.remove('nav-open'); }
async function createSession() { if (creatingSession) return creatingSession; creatingSession = (async () => { const data = await request('/api/sessions', {method: 'POST', body: JSON.stringify({title: '新对话'})}); const session = data.session || data; await refreshSessions(); $('messages').replaceChildren(); await selectSession(session); $('message').focus(); return session; })(); try { return await creatingSession; } finally { creatingSession = null; } }
async function sendMessage(override, options = {}) {
  const supplied = typeof override === 'string'; const text = (supplied ? override : $('message').value).trim(); if (state.submitting) return;
  if (!state.current) await createSession();
  const attachments = [...(options.attachmentsOverride ?? state.attachments)];
  if (!text && !attachments.length) return;
  if (attachments.some(item => ['reading', 'uploading', 'error'].includes(item.readingState))) throw new Error('请先等待材料准备完成，或移除读取失败的材料。');
  const id = conversationId(); state.submitting = true; const commandId = crypto.randomUUID(); state.runId = commandId; state.lastCompletion = null; state.lastError = ''; state.tools = [];
  const submittedText = $('message').value;
  if ($('messages').querySelector('.welcome')) $('messages').replaceChildren();
  const message = text || '请查看我提供的材料。';
  messageNode('user', message); state.messages.push({role: 'user', text: message, id: commandId}); setBusy(true); notice('');
  try {
    const result = await request(`/api/sessions/${encodeURIComponent(id)}/messages`, {method: 'POST', body: JSON.stringify({commandId, text: message, materials: attachments.map(item => item.id), allow_file_write: options.allow_file_write === true || (!supplied && $('allow-files').checked)})});
    options.onOutboxAdmitted?.(result);
    const draft = conversationId() === id ? {text: $('message').value, attachments: state.attachments} : drafts.get(id) || {text: submittedText, attachments: []};
    draft.attachments = draft.attachments.filter(item => !attachments.some(sent => sent.id === item.id));
    if (!supplied && !options.preserveDraft && draft.text === submittedText) draft.text = '';
    drafts.set(id, draft);
    if (conversationId() === id) { state.attachments = draft.attachments; $('message').value = draft.text; renderAttachments(); }
    return result;
  } catch (error) { if (conversationId() === id) { setBusy(false); notice(errorText(error)); await loadHistory(); } throw error; }
  finally { state.submitting = false; $('send').disabled = false; emitState(); }
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
let partialMessage = null, partialRecord = null, partialAttempt = null;
async function consumeEvent(event, name) {
  const type = event.type || name;
  if (type === 'inbox' || type === 'delivery') { await refreshInbox(); return; }
  if (type === 'action') { await refreshActions(); return; }
  if (type === 'artifact') { if (event.artifact?.conversation_id === conversationId()) await refreshArtifacts(); return; }
  const id = event.session_id || event.sessionId;
  if (id && id !== sessionId(state.current)) return;
  if (type === 'assistant-start') { partialMessage = null; partialRecord = null; partialAttempt = event.attempt_id; return; }
  if (type === 'assistant-end') { if (!partialAttempt || partialAttempt === event.attempt_id) { partialMessage = null; partialRecord = null; partialAttempt = null; } if (event.outcome === 'abandoned') await loadHistory(); return; }
  if (type === 'tool') { state.tools.push({...event, runId: event.runId || state.runId}); emitState(); return; }
  if (['token', 'delta', 'text-delta'].includes(type)) {
    if (event.attempt_id && partialAttempt !== event.attempt_id) { partialMessage = null; partialRecord = null; partialAttempt = event.attempt_id; }
    if (!partialMessage) { partialMessage = messageNode('assistant', ''); partialRecord = {role: 'assistant', text: '', id: `stream-${partialAttempt || state.runId}`}; state.messages.push(partialRecord); }
    partialMessage.textContent += event.text || event.delta || ''; partialRecord.text = partialMessage.textContent; emitState(); $('messages').scrollTop = $('messages').scrollHeight;
  }
  if (['done', 'idle', 'cancelled', 'completed'].includes(type)) { state.lastCompletion = {phase: event.cancelled || event.failed ? 'interrupted' : 'done', sessionKey: conversationId(), runId: state.runId}; partialMessage = null; setBusy(false); await loadHistory(); await refreshSessions(); await refreshArtifacts(); }
  if (['error', 'failed'].includes(type)) { state.lastCompletion = {phase: 'interrupted', sessionKey: conversationId(), runId: state.runId}; partialMessage = null; setBusy(false); notice(event.text || event.message || event.error?.message || '这次处理未完成，请重试。'); await loadHistory(); }
  if (type === 'session' || type === 'status') { const status = event.status || event.state; if (status) setBusy(['running', 'processing', 'busy'].includes(status), event.message); if (status === 'idle') { partialMessage = null; await loadHistory(); } }
}
async function showPage(page) {
  if (page === 'work') { await showPage('chat'); if (!state.current) await createSession(); await window.__mirallyWork?.open(); return; }
  window.__mirallyWork?.close();
  state.page = page; document.querySelectorAll('.page').forEach(node => node.classList.toggle('active', node.id === `page-${page}`)); document.querySelectorAll('#sidebar>nav button').forEach(node => node.classList.toggle('active', node.dataset.page === page)); $('page-title').textContent = titles[page]; document.body.classList.remove('nav-open');
  if (page === 'settings' || page === 'activity') await loadSettings();
  if (page === 'reminders') await refreshReminders();
  if (page === 'inbox') await refreshInbox();
  if (page === 'extensions') await refreshExtensions();
  if (page === 'actions') await refreshActions();
  emitState();
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
  const {actions} = await rpc('desktop.list'); state.actions = actions; emitState(); const container = $('action-items'); container.replaceChildren();
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
function renderAttachments() {
  const container = $('attachments'); container.replaceChildren();
  for (const item of state.attachments) {
    const chip = document.createElement('div'); chip.className = 'attachment chat-attachment-thumb';
    if (item.previewUrl) { const img = document.createElement('img'); img.src = item.previewUrl; img.alt = item.fileName; chip.append(img); }
    const label = document.createElement('span'); label.textContent = `${item.fileName} · ${item.readingState === 'uploading' ? '读取中' : item.readingState === 'error' ? '读取失败' : item.status === 'unsupported' ? '已保存，未解析' : '已准备'}`; chip.append(label);
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'chat-attachment-remove'; remove.textContent = '×'; remove.setAttribute('aria-label', `移除 ${item.fileName}`); remove.onclick = () => removeAttachment(item.id); chip.append(remove); container.append(chip);
  }
  rememberDraft(); emitState();
}
function removeAttachment(id) { const item = state.attachments.find(value => value.id === id); if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl); state.attachments = state.attachments.filter(value => value.id !== id); renderAttachments(); }
async function attachFiles(files) {
  if (!state.current) await createSession();
  const id = conversationId(), attachments = state.attachments;
  if (attachments.length + files.length > 12) throw new Error('一次最多准备 12 份材料。');
  for (const file of files) {
    if (file.size > 8 * 1024 * 1024 || !file.size) throw new Error('单份材料应为 1 字节至 8 MiB。');
    const item = {id: crypto.randomUUID(), fileName: file.name, name: file.name, mimeType: file.type, readingState: 'uploading', previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : ''};
    attachments.push(item); if (conversationId() === id) renderAttachments();
    try {
      const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file); });
      const uploaded = await request('/api/materials', {method: 'POST', body: JSON.stringify({conversation_id: id, name: file.name, media_type: file.type, data})}); Object.assign(item, uploaded);
    } catch (error) { item.readingState = 'error'; item.error = errorText(error); if (conversationId() === id) notice(item.error); }
    if (conversationId() === id) renderAttachments();
  }
  return attachments.every(item => item.readingState !== 'error');
}
async function refreshArtifacts() {
  if (!state.current) return;
  const id = conversationId(); const data = await request(`/api/artifacts?conversation_id=${encodeURIComponent(id)}`);
  if (id !== conversationId()) return;
  state.artifacts = data.artifacts; const container = $('artifacts'); container.replaceChildren();
  for (const artifact of state.artifacts) card(container, artifact, {text: `${artifact.bytes} 字节`, actionLabel: '查看与下载', action: () => openArtifact(artifact.id)});
  $('artifacts-section').hidden = !state.artifacts.length; emitState();
}
async function openArtifact(id) { const data = await request(`/api/artifacts/${encodeURIComponent(id)}/preview`); $('artifact-name').textContent = data.artifact.name; $('artifact-text').textContent = data.text + (data.truncated ? '\n[预览已截短]' : ''); $('artifact-download').href = data.artifact.download_url; $('artifact-download').download = data.artifact.name; $('artifact-dialog').showModal(); }
function continueDraft() { const last = state.messages.filter(item => item.role === 'assistant').at(-1); const handoff = window.__mirallyOffice?.handoff(last?.text || ''); $('message').value = handoff?.next ? `请接着这一步继续：${handoff.next}\n\n我的补充：` : '请接着上次的工作继续。\n\n我的补充：'; rememberDraft(); emitState(); $('message').focus(); }
async function stopCurrent() { if (!state.current) return; await request(`/api/sessions/${encodeURIComponent(conversationId())}/cancel`, {method: 'POST', body: '{}'}); state.lastCompletion = {phase: 'interrupted', sessionKey: conversationId(), runId: state.runId}; partialMessage = null; setBusy(false); await loadHistory(); notice('已请求停止，未确认的外部结果仍需核实。', true); }
async function loadExperience() {
  const sources = ['/experience-adapter.js', '/experience/control-ui-office.js', '/experience/control-ui-intake.js', '/experience/control-ui-context.js', '/experience/control-ui-work.js', '/experience/control-ui-companion.js', '/activity-experience.js'];
  for (const source of sources) await new Promise((resolve, reject) => { const script = document.createElement('script'); script.src = source; script.onload = resolve; script.onerror = () => reject(new Error('Mirally 体验资源未能加载，请重新连接。')); document.body.append(script); });
}
window.__mirallyFramework = {
  state, rpc, request, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  ensureSession: async () => state.current || await createSession(), selectSession, showPage, send: sendMessage,
  getDraft: () => $('message').value, setDraft(value) { $('message').value = value; rememberDraft(); emitState(); },
  attachFiles, removeAttachment, nativeInput: () => $('file-input'), openFiles: async () => { await showPage('chat'); await refreshArtifacts(); $('artifacts-section').scrollIntoView({block: 'nearest'}); },
  getHistory: () => state.messages, getActions: () => state.actions, getArtifacts: () => state.artifacts, openArtifact, continueDraft, stop: stopCurrent, notify: notice,
  getUiPreference: key => state.uiPreferences[key],
  setUiPreference(key, value) { state.uiPreferences[key] = value; emitState(); request('/api/ui-preferences', {method: 'POST', body: JSON.stringify({key, value})}).catch(error => notice(errorText(error))); },
};
for (const button of document.querySelectorAll('#sidebar>nav button')) button.addEventListener('click', handle(() => showPage(button.dataset.page)));
$('new-chat').addEventListener('click', handle(createSession)); $('composer').addEventListener('submit', handle(sendMessage));
$('message').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); handle(sendMessage)(); } });
$('stop').addEventListener('click', handle(stopCurrent));
$('toggle-nav').addEventListener('click', () => document.body.classList.toggle('nav-open')); $('companion').addEventListener('click', handle(() => window.__mirallyCompanion?.dock()));
$('attach').addEventListener('click', () => $('file-input').click());
$('file-input').addEventListener('change', handle(async () => { const files = [...$('file-input').files]; $('file-input').value = ''; await attachFiles(files); }));
$('message').addEventListener('input', () => { rememberDraft(); emitState(); });
$('continue-work').addEventListener('click', continueDraft);
$('artifact-close').addEventListener('click', () => $('artifact-dialog').close());
$('theme').addEventListener('change', () => { document.documentElement.dataset.theme = $('theme').value; window.__mirallyFramework.setUiPreference('theme', $('theme').value); });
$('page-chat').addEventListener('dragover', event => { event.preventDefault(); });
$('page-chat').addEventListener('drop', handle(async event => { const data = window.__mirallyIntake?.readDrop(event.dataTransfer); if (data?.files.length) await attachFiles(data.files); else if (data) { $('message').value = [$('message').value, ...data.urls, data.text].filter(Boolean).join('\n\n'); rememberDraft(); emitState(); } }));
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
  try {
    if (state.token) await request('/api/auth', {method: 'POST', body: '{}'});
    const health = await request('/api/health'); state.connected = true;
    state.uiPreferences = (await request('/api/ui-preferences')).preferences;
    $('theme').value = state.uiPreferences.theme || 'system'; document.documentElement.dataset.theme = $('theme').value;
    await loadExperience(); await refreshSessions();
    if (state.sessions.length) await selectSession(state.sessions[0]); else connectEvents();
    await refreshActions(); await refreshInbox(); emitState();
    $('connection').textContent = '本机已连接'; $('connection').classList.add('ready'); $('runtime-state').textContent = health.version ? `Mirally · ${health.version}` : 'Mirally 本机内核';
    const settings = await rpc('settings.get'); if (!settings.model?.model_id) notice('先在「设置」连接你的模型服务，然后开始对话。');
  }
  catch (error) { notice(errorText(error)); $('connection').textContent = '连接失败'; }
}
window.addEventListener('beforeunload', () => state.stream?.abort());
setInterval(() => { if (state.connected) { refreshInbox().catch(() => {}); if (state.page === 'actions') refreshActions().catch(() => {}); } }, 15_000);
document.addEventListener('focusin', () => window.pywebview?.api?.editing(true));
document.addEventListener('focusout', () => window.pywebview?.api?.editing(false));
boot();
