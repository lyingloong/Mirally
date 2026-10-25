/* Runtime adapter for Mirally's retained companion, intake and work surfaces.
 * This projects the owned UI's existing state. It creates no transport or history.
 */
(function (root, factory) {
  const createExperienceAdapter = factory();
  if (typeof module === 'object' && module.exports) module.exports = {createExperienceAdapter};
  else if (root.top === root && root.__mirallyFramework) {
    root.__mirallyExperience = createExperienceAdapter(root.__mirallyFramework, root);
  }
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const liveStates = new Set(['starting', 'running', 'waiting_for_user', 'stopping']);
  const idOf = session => session?.conversation_id || session?.id || session?.session_id;
  const values = value => Array.isArray(value) ? value : [];
  const toolLabels = {
    mirally_context: '整理相关记忆与活动', mirally_preferences: '读取你的偏好',
    mirally_reminders: '查看提醒', mirally_set_preference: '保存你的偏好',
    mirally_create_reminder: '创建提醒', mirally_create_file: '保存交付文件',
    mirally_workspace_roots: '查看授权文件夹', mirally_list_files: '查看文件目录',
    mirally_read_file: '阅读文件', mirally_search_files: '查找文件内容',
    mirally_write_workspace_file: '保存工作区文件', mirally_web_search: '搜索网页资料', mirally_web_fetch: '阅读网页正文',
    read: '读取资料', write: '整理文件', edit: '修改内容', exec: '执行后台操作',
    browser: '处理网页', web_search: '查找资料', web_fetch: '阅读网页',
  };

  function createExperienceAdapter(api, root) {
    if (!api?.state || typeof api.subscribe !== 'function') throw new Error('Mirally UI state is required');
    const entries = new Map();
    const native = typeof root.chrome?.webview?.postMessage === 'function' && !root.chrome.webview.__mirallyBrowserOnly;
    const report = message => {
      if (typeof api.notify === 'function') api.notify(message);
      else if (root.CustomEvent) root.dispatchEvent?.(new root.CustomEvent('mirally-experience-notice', {detail: {message}}));
    };
    if (!native) {
      root.chrome ??= {};
      root.chrome.webview = {
        __mirallyBrowserOnly: true,
        postMessage(message) {
          if (message?.type !== 'mirally-companion') return;
          if (message.action === 'capture-target') {
            root.__mirallyContext?.receive({requestId: message.requestId, error: '窗口画面采集需要 Mirally 桌面窗口；浏览器中请添加截图文件。'});
          } else if (message.action === 'drag' || message.action === 'minimize') {
            report('移动和最小化伙伴需要 Mirally 桌面窗口。');
          }
          // Dock/workspace/shape remain local page presentation. No native
          // window operation is claimed by this browser-only bridge.
        },
      };
    }

    const actions = () => values(api.getActions?.() ?? api.state.actions);
    const artifacts = () => values(api.getArtifacts?.() ?? api.state.artifacts);
    const currentId = () => idOf(api.state.current);
    const nativeScope = () => `${api.state.current?.session_id || ''}:${api.state.current?.generation ?? ''}`;
    const currentActions = id => actions().filter(action => action.conversation_id === id && liveStates.has(action.state));
    const thumbnails = () => values(api.state.attachments).map(item => item.previewUrl || item.preview_url || item.dataUrl || '');
    function requireCurrent(id, scope) {
      if (currentId() !== id || (scope !== undefined && nativeScope() !== scope)) throw new Error('会话已经切换，请在当前会话中继续。');
    }
    function sessions() {
      return values(api.state.sessions).map(session => {
        const id = idOf(session);
        const pending = currentActions(id);
        return {...session, key: id, displayName: session.title || '新对话',
          hasActiveRun: pending.length > 0 || ['working', 'running', 'processing', 'executing', 'busy'].includes(session.status)
            || (id === currentId() && api.state.busy)};
      });
    }
    function update() {
      const id = currentId();
      if (!id) return null;
      const scope = nativeScope();
      let entry = entries.get(id);
      if (!entry || entry.nativeScope !== scope) {
        const state = {sessionKey: id};
        const element = () => root.document?.querySelector('#page-chat');
        const pane = {state, paneId: `mirally:${id}`, presented: true, runActivity: {},
          questionProjection: {prompts: []}, chatState: {attachmentReads: {
            get pendingReads() { return state._pendingReads || 0; },
            project(list) { return values(list).map(attachment => ({attachment,
              state: attachment.readingState || attachment.reading_state || attachment.state || attachment.status || 'ready', progress: attachment.progress})); },
          }},
          querySelector(selector) { return selector === '.agent-chat__file-input' ? api.nativeInput?.() : element()?.querySelector(selector) ?? null; },
          querySelectorAll(selector) { return element()?.querySelectorAll(selector) ?? []; },
          onPaneSessionChange(_paneId, key) { return api.selectSession(key); },
        };
        state.handleChatDraftChange = text => { requireCurrent(id, scope); api.setDraft(text); };
        state.handleSendChat = async (override, options = {}) => {
          requireCurrent(id, scope);
          const result = await api.send(override, {...options, ...(override !== undefined ? {preserveDraft: true} : {})});
          update();
          return result;
        };
        state.handleAbortChat = () => { requireCurrent(id, scope); if (!api.stop) throw new Error('停止入口尚未连接，请在工作台中停止。'); return api.stop(); };
        entry = {state, pane, nativeScope: scope};
        entries.set(id, entry);
      }
      const {state, pane} = entry;
      const active = currentActions(id);
      const questions = active.filter(action => action.question);
      const history = values(api.getHistory?.() ?? api.state.messages);
      const toolStreamById = new Map();
      for (const [index, event] of values(api.state.tools).entries()) {
        if (event.session_id && event.session_id !== id) continue;
        const toolId = event.id || event.callId || `tool-${index}`;
        const ended = ['completed', 'failed', 'cancelled'].includes(event.state);
        toolStreamById.set(toolId, {name: event.name, sessionKey: id,
          runId: event.runId || event.command_id || api.state.runId,
          resultReceived: ended, isError: event.state === 'failed' || !!event.error,
          activity: [{phase: ended ? 'end' : 'start', status: event.state,
            title: event.progressText || event.title || root.__mirallyOffice?.toolLabel(event.name) || toolLabels[event.name] || '处理工作材料'}]});
      }
      Object.assign(state, {
        sessionKey: id, connected: api.state.connected !== false,
        chatMessage: api.getDraft?.() ?? '', chatSending: !!api.state.submitting,
        chatLoading: !!api.state.historyLoading,
        chatMessages: history.map(message => ({...message, content: message.content ?? message.text ?? '',
          timestamp: message.time ?? message.timestamp, stopReason: message.stopReason ?? 'stop'})),
        chatAttachments: values(api.state.attachments).map(item => ({...item,
          mimeType: item.mimeType || item.mediaType || item.media_type || '',
          fileName: item.fileName || item.name || '附件'})), chatError: api.state.lastError || null,
        chatSessionApprovalQueue: [], sessionsResult: {sessions: sessions()},
        chatRunId: api.state.runId || history.filter(message => message.role === 'user').at(-1)?.id || null,
        toolStreamOrder: [...toolStreamById.keys()], toolStreamById,
        _pendingReads: api.state.attachmentReads?.pendingReads ?? values(api.state.attachments).filter(item => ['reading', 'uploading'].includes(item.readingState || item.state || item.status)).length,
      });
      pane.paneTitle = api.state.current?.title || '新对话';
      pane.runActivity.working = !!api.state.busy || active.length > 0;
      pane.runActivity.completion = api.state.lastCompletion?.sessionKey === id ? api.state.lastCompletion : null;
      pane.questionProjection.prompts = questions.map(action => action.question);
      const progress = active.at(-1);
      state.observerDigest = progress ? {sessionKey: id, runId: state.chatRunId, headline: progress.question?.text || progress.progress?.message || progress.progress?.text || progress.text} : null;
      return pane;
    }
    async function ensureReady() {
      if (!currentId()) await api.ensureSession();
      return update();
    }
    function renderDeliverables(container) {
      const id = currentId();
      const items = artifacts().filter(item => !item.conversation_id || item.conversation_id === id).slice(0, 3);
      const signature = JSON.stringify([id, items.map(item => [item.id, item.title, item.name, item.path])]);
      if (container.dataset.signature === signature) return;
      container.dataset.signature = signature;
      container.replaceChildren(...items.map(item => {
        const button = root.document.createElement('button');
        button.textContent = item.title || item.name || item.path || '已生成文件';
        button.title = item.path || item.name || button.textContent;
        button.addEventListener('click', async () => {
          if (currentId() !== id) return;
          try { await api.openArtifact(item.id ?? item.path); }
          catch (error) { report(error.message); }
        });
        return button;
      }));
    }
    const adapter = {
      native, active: update, ensureReady, sessions,
      getPreference(key) { return api.getUiPreference?.(key) ?? api.state.uiPreferences?.[key]; },
      setPreference(key, value) { return api.setUiPreference ? api.setUiPreference(key, value) : root.localStorage?.setItem(key, value); },
      subscribe(listener) { return api.subscribe(() => { update(); listener(); }); },
      entryDestination() { return root.document?.querySelector('#sidebar footer') || root.document?.body; },
      main() { return root.document?.querySelector('#page-chat'); },
      chatRoute() { return api.state.page === 'chat' || !api.state.page; },
      showChat() { return api.showPage('chat'); },
      nativeInput() { return api.nativeInput?.(); },
      attachFiles(files) { return api.attachFiles(files); },
      removeAttachment(_pane, id) { return api.removeAttachment(id); },
      attachmentPreviews: thumbnails,
      openFiles() { return api.openFiles?.(); },
      openAttention() { return api.showPage('actions'); },
      selectSession(id) { return api.selectSession(id); },
      background(state) {
        return sessions().filter(row => row.key !== state.sessionKey).map(row => ({row,
          working: row.hasActiveRun, attention: currentActions(row.key).some(action => action.question) ? 'question' : null}));
      },
      renderDeliverables,
      requestContextUnavailable: report,
    };
    return adapter;
  }
  return createExperienceAdapter;
});
