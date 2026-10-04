/* Companion for the pinned native Control UI. No second client or memory store. */
(() => {
  if (window.top !== window || location.origin !== __MIRALLY_ORIGIN__ || !window.chrome?.webview) return;
  const MARK = __MIRALLY_MARK__;
  const office = window.__mirallyOffice;
  let app, pane, surface, entry, connectedContext, mode = 'workspace', frame, returnAfterAttention, presentationScope, preparing;
  let surfaceMode = 'rail';
  const disposers = [];
  const $ = selector => surface.querySelector(selector);
  const text = (selector, value) => {
    const node = $(selector);
    if (node.textContent !== value) node.textContent = value;
  };
  const post = (action, extra = {}) => chrome.webview.postMessage({type:'mirally-companion', action, ...extra});
  const message = row => row.message ?? row;
  const content = row => {
    const m = message(row);
    if (m.__openclawToolStreamLive) return '';
    return typeof m.content === 'string' ? m.content : (m.content ?? [])
      .filter(part => part.type === 'text').map(part => part.text).join('\n');
  };
  const excerpt = (value, limit = 160) => {
    const clean = String(value || '').replace(/^[#*>\s]+/gm, '').replace(/\*\*|__|`/g, '').trim();
    return clean.length > limit ? clean.slice(0, limit) + '…' : clean;
  };
  const active = () => [...document.querySelectorAll('openclaw-chat-pane.chat-pane-cache__pane--active')]
    .find(candidate => candidate.presented !== false && candidate.state?.handleSendChat);
  const rows = state => state.sessionsResult?.sessions ?? app?.context?.sessions?.state?.result?.sessions ?? [];
  async function ensureReady() {
    if (active()) return active();
    if (preparing) return preparing;
    const page = document.querySelector('openclaw-new-session-page');
    const context = document.querySelector('openclaw-app')?.context;
    const submission = page?.submission, client = context?.gateway.snapshot.client;
    if (!submission || !client || !page.isConnected) return null;
    if (submission.submitting || submission.attachmentDraft.pendingReads) {
      submission.setError('请等材料读取完成，再切换工作方式。');
      return null;
    }
    preparing = (async () => {
      let retained;
      try {
        const params = submission.buildDraftSessionCreateParams({message:'',mentions:[],attachments:[]});
        const result = await context.sessions.createResult(params,{reconciliation:'background'});
        if (!result?.key || result.initialRun?.status === 'rejected') {
          if (page.isConnected) submission.setError('会话尚未准备完成，请查看原生连接与权限设置后重试。');
          return null;
        }
        if (!page.isConnected || context.gateway.snapshot.client !== client) return null;
        retained = submission.callbacks.retainForHandoff?.();
        const handoff = {draft:submission.message,mentions:submission.mentions,attachments:[...submission.attachmentDraft.attachments],send:false};
        document.querySelector('openclaw-app-shell')?.selectChatSession(result.key,params.agentId);
        const deadline = performance.now()+10000;
        let target;
        while (performance.now()<deadline) {
          target = active();
          if (target?.state?.sessionKey === result.key) break;
          await new Promise(resolve=>requestAnimationFrame(resolve));
        }
        if (target?.state?.sessionKey !== result.key || context.gateway.snapshot.client !== client) return null;
        target.applySessionHandoff(result.key,handoff);
        submission.attachmentDraft.take();
        submission.restoreMessage('',[]);
        schedule();
        return target;
      } catch {
        if (page.isConnected) submission.setError('会话暂未准备完成，请在原生页面查看连接与配置后重试。');
        return null;
      } finally { retained?.release(); }
    })();
    try { return await preparing; }
    finally { preparing = null; schedule(); }
  }
  function openOffice() {
    workspace(() => window.__mirallyWork?.open());
  }
  function expand(value) {
    surface.dataset.expanded = String(value);
    $('.mirally-companion__rail').tabIndex = value ? -1 : 0;
    $('.mirally-companion__panel').inert = !value;
  }
  function setMode(value) {
    mode = value;
    document.documentElement.dataset.mirallyMode = value;
    surface.hidden = value !== 'companion';
    if (value === 'companion') expand(!!pane?.state?.chatMessage);
    schedule();
  }
  async function dock() {
    pane = await ensureReady();
    if (!pane) return;
    window.__mirallyWork?.close();
    setMode('companion');
    post('dock', {surfaceMode,expanded:surface.dataset.expanded === 'true'});
  }
  function setForm(value) {
    if (!['rail','badge','pet'].includes(value)) return;
    surfaceMode = value;
    surface.dataset.form = value;
    surface.querySelectorAll('[data-form]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.form===value)));
    if (mode === 'companion') post('surface',{surfaceMode,expanded:surface.dataset.expanded === 'true'});
  }
  function syncShape() {
    if (mode !== 'companion' || !surface) return;
    const rect = surface.getBoundingClientRect();
    if (rect.width && rect.height) post('shape', {rect:[rect.x, rect.y, rect.width, rect.height], viewport:[innerWidth, innerHeight]});
  }
  function workspace(callback) {
    post('workspace');
    setMode('workspace');
    requestAnimationFrame(() => callback?.());
  }
  async function nativeFiles(target = pane) {
    const key = target?.state?.sessionKey;
    const current = () => active() === target && target.state.sessionKey === key;
    if (!current()) return;
    const region = target.querySelector('openclaw-chat-sidebar-region');
    if (region?.callbacks?.openSlot) {
      region.callbacks.openSlot('workspace');
      await target.updateComplete;
      await region.updateComplete;
      if (!current()) return;
      const panel = region.layout?.columns?.flatMap(column => column.panels).find(panel => panel.slot === 'workspace');
      if (panel) region.callbacks.activatePanel(panel.id);
    } else target.querySelector('.chat-side-panel-toggle')?.click();
  }
  function nativeAttention() {
    returnAfterAttention = pane?.state?.sessionKey;
    workspace(() => {
      if (pane?.state?.chatSessionApprovalQueue?.length) {
        document.querySelector('openclaw-app-shell')?.openApprovals?.();
      } else {
        pane?.querySelector('.chat-question-card, .chat-question, .chat-attention')?.scrollIntoView({block:'center'});
      }
    });
  }
  function nativeInput() { return pane?.querySelector('.agent-chat__file-input'); }
  function files(list) {
    const input = nativeInput();
    if (!input) { text('[data-slot="receipt"]', '请在工作台中添加文件'); return; }
    if (!list) return input.click();
    const transfer = new DataTransfer();
    for (const file of list) transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', {bubbles:true}));
    expand(true);
    schedule();
  }
  async function send() {
    const target = pane, state = target?.state, key = state?.sessionKey;
    if (!state?.handleSendChat || state.chatSending || (!state.chatMessage?.trim() && !state.chatAttachments?.length)) return;
    const current = () => active()?.state === state && state.sessionKey === key;
    text('[data-slot="receipt"]', '正在发送…');
    try {
      // Undefined preserves native attachments, mentions and send policy.
      const pending = state.handleSendChat(undefined, target.runActivity?.working ? {followUpMode:'steer'} : undefined);
      schedule();
      await pending;
      if (!current()) return;
      text('[data-slot="receipt"]', state.chatError ? excerpt(state.chatError, 90) :
        state.chatMessage ? '草稿已保留，请查看工作台中的发送状态' : '已发送，发送与补充状态可在工作台查看');
    } catch { if (current()) text('[data-slot="receipt"]', '发送未完成，草稿保留在原生工作台'); }
    schedule();
  }
  function recent(state) {
    const node = $('[data-slot="sessions"]');
    if (!node.parentElement.open) return;
    const sessions = rows(state).filter(row => !row.archived).slice(0, 6);
    const signature = JSON.stringify(sessions.map(row => [row.key, row.displayName, row.label, row.key === state.sessionKey]));
    if (node.dataset.signature === signature) return;
    node.dataset.signature = signature;
    node.replaceChildren(...sessions.map(row => {
      const button = document.createElement('button');
      button.textContent = row.displayName || row.label || row.key;
      button.setAttribute('aria-current', String(row.key === state.sessionKey));
      button.onclick = () => {
        pane?.onPaneSessionChange?.(pane.paneId, row.key);
        text('[data-slot="receipt"]', '');
        schedule();
      };
      return button;
    }));
  }
  function background(state) {
    const sidebar = document.querySelector('openclaw-app-sidebar');
    const resolver = sidebar?.resolveSessionAttention;
    const loaded = app?.context?.sessions?.state?.result?.sessions ?? [];
    const others = loaded.filter(row => row.key !== state.sessionKey && !row.archived).map(row => ({row,
      attention:resolver?.call(sidebar, row)?.kind,
      working:row.hasActiveRun === true || row.hasActiveSubagentRun === true}));
    const pending = others.filter(item => ['question','approval','agent','error'].includes(item.attention));
    const working = others.filter(item => item.working);
    const summary = [working.length ? `${working.length} 个其他会话在工作` : '', pending.length ? `${pending.length} 个会话需要查看` : ''].filter(Boolean).join(' · ');
    const panel = $('[data-slot="background"]');
    panel.hidden = !summary;
    text('[data-slot="background-summary"]', summary);
    surface.dataset.notice = pending.length ? 'attention' : '';
    const items = others.filter(item => item.working || pending.includes(item)).slice(0, 4);
    const node = $('[data-slot="background-sessions"]');
    const signature = JSON.stringify(items.map(item => [item.row.key,item.row.displayName,item.working,item.attention]));
    if (node.dataset.signature !== signature) {
      node.dataset.signature = signature;
      node.replaceChildren(...items.map(({row,working,attention}) => {
        const button = document.createElement('button');
        button.textContent = `${row.displayName || row.label || '未命名会话'} · ${['question','approval','agent','error'].includes(attention) ? '需要查看' : working ? '工作中' : ''}`;
        button.onclick = () => workspace(() => sidebar?.selectSession?.(row.key, sidebar.sessionNavigationAgentId?.(row)));
        return button;
      }));
    }
    return pending.length;
  }
  function deliverables(state) {
    const node = $('[data-slot="deliverables"]');
    const target = pane, key = state.sessionKey, client = target.runActivity?.client;
    const workspaceState = state.sessionWorkspaceState;
    const epoch = workspaceState?.connectionEpoch, agent = workspaceState?.agentId;
    const current = () => active() === target && target.state === state && state.sessionKey === key &&
      target.runActivity?.client === client && state.sessionWorkspaceState?.connectionEpoch === epoch && state.sessionWorkspaceState?.agentId === agent;
    const list = workspaceState?.list;
    const scoped = list?.sessionKey === key && (!workspaceState.agentId || workspaceState.agentId === target.runActivity?.agentId);
    const items = scoped ? [...(list.artifacts ?? []).map(item => ({...item,kind:'artifact',label:item.title})),
      ...(list.files ?? []).filter(file => file.kind === 'modified').map(file => ({
        id:file.path,kind:'modified',label:file.path || file.name,title:'文件 · '+(file.name || file.path?.split(/[\\/]/).at(-1))}))].slice(0,3) : [];
    const signature = JSON.stringify([key, target.paneId, agent, epoch, items.map(item => [item.kind, item.id, item.title])]);
    if (node.dataset.signature === signature && node._nativeClient === client) return;
    node._nativeClient = client;
    node.dataset.signature = signature;
    node.replaceChildren(...items.map(item => {
      const button = document.createElement('button');
      button.textContent = item.title;
      button.title = item.label;
      if (item.kind === 'modified' && items.filter(other => other.title === item.title).length > 1) {
        button.textContent += ' · ' + item.label.split(/[\\/]/).at(-2);
      }
      button.onclick = () => workspace(async () => {
        if (!current()) return;
        await nativeFiles(target);
        if (!current()) return;
        const groups = [...target.querySelectorAll('.chat-workspace-rail__group')];
        // Keep the native artifact opener: it validates session, agent and epoch.
        const group = groups.find(group => {
          const summary = group.querySelector('.chat-workspace-rail__group-summary');
          const heading = [...(summary?.childNodes ?? [])].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim();
          return (item.kind === 'artifact' ? /^(Artifacts|产物)$/ : /^(Changed|Modified|已更改|已修改)$/).test(heading);
        });
        const buttons = [...(group?.querySelectorAll('.chat-workspace-rail__file-open') ?? [])];
        const matches = buttons.filter(button => button.getAttribute('aria-label') === item.label);
        if (matches.length === 1) matches[0].click();
      });
      return button;
    }));
  }
  function sync() {
    frame = null;
    app = document.querySelector('openclaw-app');
    pane = active();
    // Lit can replace its footer during route/session updates. Remount before
    // checking the pane, which is briefly absent during those same updates.
    const footer = document.querySelector('.sidebar-shell__footer');
    const destination = footer || document.body;
    if (entry.parentElement !== destination) destination.prepend(entry);
    entry.hidden = false;
    const newPage = document.querySelector('openclaw-new-session-page');
    const ready = !!pane?.state?.handleChatDraftChange || !!(newPage?.submission && newPage.gateway?.connected);
    for (const button of entry.querySelectorAll('[data-entry="dock"],[data-entry="office"]')) button.disabled = !!preparing || !ready;
    window.__mirallyWork?.sync();
    const context = app?.context ?? pane?.context;
    if (context !== connectedContext) {
      disposers.splice(0).forEach(dispose => dispose());
      connectedContext = context;
      for (const service of [context?.gateway, context?.sessions, context?.overlays]) {
        if (service?.subscribe) disposers.push(service.subscribe(schedule));
      }
      if (context?.gateway?.subscribeEvents) disposers.push(context.gateway.subscribeEvents(schedule));
    }
    if (!pane?.state?.handleChatDraftChange) {
      if (mode === 'companion') text('[data-slot="status"]', '连接恢复后即可继续；完整工作台仍可打开');
      return;
    }
    if (mode !== 'companion' && !returnAfterAttention) return;
    const state = pane.state;
    const scope = `${pane.paneId}:${state.sessionKey}`;
    if (scope !== presentationScope) { presentationScope = scope; text('[data-slot="receipt"]', ''); }
    const row = rows(state).find(row => row.key === state.sessionKey);
    const digest = state.observerDigest || row?.observerDigest;
    const history = state.chatMessages ?? [];
    const reply = [...history].reverse().find(row => office.isFinal(message(row)) && content(row));
    const task = [...history].reverse().find(row => message(row).role === 'user' && content(row));
    const busy = pane.runActivity?.working === true || !!state.chatSending;
    const waiting = (pane.questionProjection?.prompts?.length ?? 0) + (state.chatSessionApprovalQueue?.length ?? 0);
    const error = state.chatError || state.chatRunError;
    if (returnAfterAttention && state.sessionKey !== returnAfterAttention) returnAfterAttention = null;
    if (returnAfterAttention && mode === 'workspace' && !waiting) {
      returnAfterAttention = null;
      dock();
    }
    const finalReply = reply && history.indexOf(reply) > history.indexOf(task);
    const nativeTitle = pane.paneTitle || row?.displayName;
    const generatedTitle = !nativeTitle || nativeTitle === state.sessionKey || nativeTitle === state.sessionKey.split(':').at(-1) || /^(新会话|New session)$/i.test(nativeTitle);
    const title = generatedTitle ? excerpt(task ? content(task).split(/[。？！\n]/)[0] : '', 48) || '我们从这里开始' : nativeTitle;
    const status = waiting ? '需要你决定，工作进展会继续保留' : error ? excerpt(error, 100) : busy ?
      excerpt(digest?.headline || state.chatRunStartup?.message, 120) || '正在推进这件事，你可以继续自己的工作' :
      finalReply ? '回复已经留下，随时可以接着聊' : '把手头的一小部分交给我，我们一起推进';
    surface.dataset.state = waiting ? 'waiting' : error ? 'error' : busy ? 'working' : finalReply ? 'done' : 'idle';
    text('[data-slot="title"]', title);
    text('[data-slot="rail-title"]', excerpt(title, 28));
    const otherAttention = background(state);
    text('[data-slot="state"]', waiting ? '需要你' : busy ? '正在推进' : error ? '需要查看' : finalReply ? '本轮已结束' : '随时在这里');
    text('.mirally-companion__rail-hint', waiting || otherAttention ? '需要你' : busy ? '与你并肩' : error ? '需要查看' : finalReply ? '回复已留下' : '随时在这里');
    text('[data-slot="status"]', status);
    $('[data-action="attention"]').hidden = !waiting;
    $('[data-action="stop"]').hidden = !busy;
    text('[data-action="send"]', busy ? '补充 ↗' : '发送 ↗');
    const activities = (state.toolStreamOrder ?? []).slice(-3).map(id => state.toolStreamById?.get?.(id) ?? state.toolStreamById?.[id])
      .filter(tool => tool && (!tool.sessionKey || tool.sessionKey === state.sessionKey))
      .map(tool => `${tool.isError ? '需要查看' : tool.resultReceived ? '已处理' : '正在处理'} · ${tool.activity?.description ||
        ({read:'读取资料',write:'整理文件',edit:'修改内容',exec:'执行后台操作',browser:'处理网页',web_search:'查找资料',web_fetch:'阅读网页',process:'等待后台操作'}[tool.name]) || '工作步骤'}`);
    const activity = $('[data-slot="activity"]');
    const activityKey = activities.join('\n');
    if (activity.dataset.key !== activityKey) {
      activity.dataset.key = activityKey;
      activity.replaceChildren(...activities.map(value => { const li = document.createElement('li'); li.textContent = value; return li; }));
    }
    const handoff = $('[data-slot="handoff"]');
    handoff.hidden = busy || waiting || error || !finalReply;
    $('.mirally-companion__delivery-actions').hidden = handoff.hidden;
    const report = office.handoff(reply ? content(reply) : '');
    $('[data-action="resolve-report"]').hidden = !report?.attention;
    if (!handoff.hidden && report?.attention) {
      surface.dataset.notice = 'attention';
      text('.mirally-companion__rail-hint', '待你确认');
      text('[data-slot="status"]', '本轮回复已留下，有几处事项等你确认');
    }
    $('.mirally-handoff__sections').hidden = !report;
    $('[data-slot="reply"]').hidden = !!report;
    for (const key of ['completed','attention','next']) {
      const section = $(`[data-handoff="${key}"]`);
      section.hidden = !report?.[key];
      text(`[data-handoff="${key}"] p`, excerpt(report?.[key], 230));
    }
    deliverables(state);
    text('.mirally-handoff__label', report ? '本轮交接' : '回复回顾');
    $('[data-slot="file-label"]').hidden = !$('[data-slot="deliverables"]').children.length;
    text('[data-slot="reply"]', excerpt(reply ? content(reply) : '', 220));
    text('[data-slot="previous"]', excerpt(digest?.assessment || (reply ? content(reply) : ''), 230) || '这个会话还没有进展记录');
    const input = $('[data-slot="draft"]');
    if (input.value !== (state.chatMessage || '')) input.value = state.chatMessage || '';
    const attachments = state.chatAttachments ?? [];
    const materials = $('[data-slot="attachments"]');
    const thumbs = [...pane.querySelectorAll('.chat-attachments-preview .chat-attachment-thumb')].map(node=>node.querySelector('img')?.src || '');
    const materialKey = JSON.stringify(attachments.map((file,index)=>[file.id,file.fileName,file.name,thumbs[index]]));
    if (materials.dataset.signature !== materialKey) {
      materials.dataset.signature = materialKey;
      materials.replaceChildren(...attachments.map((file,index)=>{
        const item = document.createElement('div'); item.className='mirally-companion__material';
        if (file.mimeType?.startsWith('image/') && /^(blob:|data:image\/)/.test(thumbs[index])) { const image=document.createElement('img'); image.src=thumbs[index]; image.alt=file.fileName || '图片材料'; item.append(image); }
        const label=document.createElement('span'); label.textContent=file.fileName||file.name||'附件'; item.append(label);
        return item;
      }));
    }
    $('[data-action="send"]').disabled = state.connected === false || !!state.chatSending || (!input.value.trim() && !attachments.length);
    recent(state);
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(sync); }
  function mount() {
    surface = document.createElement('aside');
    surface.className = 'mirally-companion';
    surface.hidden = true;
    surface.dataset.expanded = 'true';
    surface.dataset.form = surfaceMode;
    surface.dataset.state = 'idle';
    surface.setAttribute('aria-label', 'Mirally 侧边伙伴');
    surface.innerHTML = `
      <div class="mirally-companion__rail" role="button" tabindex="0" aria-label="展开侧边伙伴">
        <span class="mirally-companion__mark"><img alt="Mirally"></span>
        <span class="mirally-companion__pet-face" aria-hidden="true"><i></i><i></i></span>
        <span class="mirally-companion__pulse"></span><span class="mirally-companion__rail-title" data-slot="rail-title"></span>
        <span class="mirally-companion__rail-hint">与你并肩</span>
        <span class="mirally-companion__drop-hint">带上材料</span>
      </div>
      <div class="mirally-companion__panel">
        <header class="mirally-companion__header">
          <span class="mirally-companion__mark"><img alt=""></span><span class="mirally-companion__brand">Mirally</span>
          <button class="mirally-companion__drag-hint" data-action="drag" title="拖动伙伴的位置" aria-label="拖动伙伴的位置">⠿</button>
          <button class="mirally-companion__target-handle" title="拖向页面，带回工作材料" aria-label="拖向页面，带回工作材料">◎</button>
          <div class="mirally-companion__actions">
            <button class="mirally-companion__icon-button" data-action="workspace" title="打开完整工作台" aria-label="打开完整工作台">↗</button>
            <button class="mirally-companion__icon-button" data-action="minimize" title="最小化到任务栏" aria-label="最小化到任务栏">−</button>
          </div>
        </header>
        <nav class="mirally-companion__form-switch" aria-label="伙伴形态"><button data-form="rail" aria-pressed="true">侧栏</button><button data-form="badge" aria-pressed="false">图标</button><button data-form="pet" aria-pressed="false">伙伴</button></nav>
        <section class="mirally-companion__work"><div class="mirally-companion__eyebrow"><span class="mirally-companion__pulse"></span><span data-slot="state"></span></div>
          <h2 class="mirally-companion__title" data-slot="title"></h2><p class="mirally-companion__status" data-slot="status" role="status"></p>
          <button data-action="attention" hidden>查看需要你决定的事 ↗</button>
          <button data-action="stop" hidden>停止本轮工作</button>
        </section>
        <div class="mirally-companion__body">
          <details class="mirally-companion__resume mirally-companion__office"><summary>工作</summary><p>选择工作、添加材料，再一起推进。</p><button data-action="office">打开工作页面 ↗</button></details>
          <ul class="mirally-companion__activity" data-slot="activity"></ul>
          <section class="mirally-companion__handoff" data-slot="handoff" hidden>
            <p class="mirally-handoff__label">本轮交接</p>
            <p class="mirally-handoff__summary" data-slot="reply"></p>
            <div class="mirally-handoff__sections">
              <section data-handoff="attention"><h3>待你确认</h3><p></p></section>
              <section data-handoff="completed"><h3>助手已完成</h3><p></p></section>
              <section data-handoff="next"><h3>下一步建议</h3><p></p></section>
            </div>
            <p class="mirally-handoff__label" data-slot="file-label">本会话的文件</p><div class="mirally-handoff__actions" data-slot="deliverables"></div>
          </section>
          <details class="mirally-companion__resume" data-slot="background" hidden><summary data-slot="background-summary"></summary><div class="mirally-companion__sessions" data-slot="background-sessions"></div></details>
          <details class="mirally-companion__resume"><summary>接着上次</summary><p data-slot="previous"></p><button data-action="continue">接着推进</button></details>
          <details class="mirally-companion__resume"><summary>最近会话</summary><div data-slot="sessions" class="mirally-companion__sessions"></div></details>
          <div class="mirally-companion__drop" data-slot="drop"><button data-action="attach">添加文件，或拖到这里</button><div data-slot="attachments"></div></div>
        </div>
        <div class="mirally-companion__delivery-actions" hidden><button data-action="reply" title="查看完整回复">查看回复</button><button data-action="resolve-report" title="准备确认草稿，修改后发送" hidden>补充确认</button><button data-action="files" title="打开工作区与成果">查看成果 ↗</button></div>
        <form class="mirally-companion__composer">
          <textarea data-slot="draft" aria-label="和 Mirally 说一句，或补充正在进行的工作" placeholder="和我说一句，或补充要求…" rows="2"></textarea>
          <div class="mirally-companion__composer-footer"><span data-slot="receipt" role="status"></span><button class="mirally-companion__send" data-action="send" type="submit">发送 ↗</button></div>
        </form>
      </div>`;
    surface.querySelectorAll('img').forEach(image => { image.src = MARK; });
    document.body.append(surface);
    window.__mirallyContext?.bind($('.mirally-companion__target-handle'),{
      onStart:()=>text('[data-slot="receipt"]','正在读取窗口画面…'),
      onReady:async result=>{
        if(!await window.__mirallyIntake.attach([result.file]))throw new Error('画面暂未加入，请重试。');
        expand(true);text('[data-slot="receipt"]','已带上「'+result.title+'」'+(result.source==='visible-area'?'的可见区域，请核对截图和遮挡。':'的画面，核对后再补充要求。'));schedule();
      },onError:value=>{expand(true);text('[data-slot="receipt"]',value);}
    });
    entry = document.createElement('nav');
    entry.className = 'mirally-companion-entry';
    entry.setAttribute('aria-label', 'Mirally 模式');
    entry.innerHTML = '<button data-entry="chat">对话</button><button data-entry="office">工作</button><button data-entry="dock" title="在屏幕侧边与你并肩工作">侧边伙伴 ↗</button>';
    entry.querySelector('[data-entry="chat"]').onclick = () => workspace(() => window.__mirallyWork?.close());
    entry.querySelector('[data-entry="dock"]').onclick = dock;
    entry.querySelector('[data-entry="office"]').onclick = openOffice;
    document.body.append(entry);
    const holdOpen = () => document.activeElement === $('[data-slot="draft"]') || !!pane?.state?.chatMessage;
    const collapseAfterFocus = () => requestAnimationFrame(() => {
      if (!surface.matches(':hover') && !holdOpen()) expand(false);
    });
    surface.addEventListener('pointerenter', () => { if (surfaceMode==='rail') expand(true); });
    surface.addEventListener('pointerleave', () => {
      if (!holdOpen()) expand(false);
    });
    surface.addEventListener('focusout', collapseAfterFocus);
    $('.mirally-companion__rail').onkeydown = event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); expand(true); $('[data-slot="draft"]').focus(); }
    };
    $('[data-slot="draft"]').oninput = event => pane?.state?.handleChatDraftChange(event.target.value);
    $('[data-slot="draft"]').onkeydown = event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
    };
    $('.mirally-companion__composer').onsubmit = event => { event.preventDefault(); send(); };
    let dragStart, moved = false;
    surface.addEventListener('click', event => {
      if (moved) { moved=false; event.preventDefault(); return; }
      if (event.target.closest('.mirally-companion__rail')) expand(true);
      const form = event.target.closest('[data-form]')?.dataset.form;
      if (form) setForm(form);
      const action = event.target.closest('[data-action]')?.dataset.action;
      if (action === 'workspace' || action === 'reply') workspace(() => window.__mirallyWork?.close());
      if (action === 'minimize') post('minimize');
      if (action === 'attention') nativeAttention();
      if (action === 'stop') pane?.state?.handleAbortChat?.();
      if (action === 'files') { const target = pane; workspace(() => nativeFiles(target)); }
      if (action === 'attach') files();
      if (action === 'office') openOffice();
      if (action === 'resolve-report') {
        const state = pane?.state;
        const reply = [...(state?.chatMessages ?? [])].reverse().find(row => office.isFinal(message(row)));
        const report = reply && office.handoff(content(reply));
        if (report?.attention && !state.chatMessage?.trim()) state.handleChatDraftChange('关于这些待确认事项：\n' + report.attention + '\n\n我的确认：');
        $('[data-slot="draft"]').focus();
        schedule();
      }
      if (action === 'continue') {
        if (!pane?.state?.chatMessage?.trim()) pane?.state?.handleChatDraftChange('接着这个会话的进展，帮我梳理下一步；需要我决定的地方先和我讨论。');
        $('[data-slot="draft"]').focus();
        schedule();
      }
    });
    surface.addEventListener('pointerdown',event=>{
      moved=false;
      if (event.button===0 && (event.target.closest('[data-action="drag"]') || event.altKey)) {
        event.preventDefault(); post('drag');
      } else if (event.button===0 && surfaceMode!=='rail' && surface.dataset.expanded!=='true') {
        dragStart={x:event.screenX,y:event.screenY};
        surface.setPointerCapture(event.pointerId);
      }
    });
    surface.addEventListener('pointermove',event=>{
      if (dragStart && Math.hypot(event.screenX-dragStart.x,event.screenY-dragStart.y)>5) {
        dragStart=null; moved=true;
        if (surface.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
        post('drag');
      }
    });
    surface.addEventListener('pointerup',()=>{ dragStart=null; });
    surface.addEventListener('pointercancel',()=>{ dragStart=null; });
    surface.querySelectorAll('details').forEach(node => node.addEventListener('toggle', schedule));
    surface.addEventListener('dragover', event => {
      if ([...event.dataTransfer?.types||[]].some(type=>['Files','text/plain','text/uri-list'].includes(type))) {
        event.preventDefault(); surface.dataset.dragover='true';
      }
    });
    surface.addEventListener('dragleave', event => { if (!surface.contains(event.relatedTarget)) delete surface.dataset.dragover; });
    surface.addEventListener('drop', async event => {
      const data = window.__mirallyIntake?.readDrop(event.dataTransfer);
      if (!data || (!data.files.length && !data.text && !data.urls.length)) return;
      event.preventDefault(); event.stopPropagation(); delete surface.dataset.dragover;
      const target = await ensureReady();
      if (!target) return;
      if (data.files.length) {
        if (!await window.__mirallyIntake.attach(data.files)) return;
      } else {
        const value = [data.text,...data.urls.filter(url=>!data.text.includes(url))].filter(Boolean).join('\n');
        target.state.handleChatDraftChange([target.state.chatMessage,value].filter(Boolean).join('\n\n'));
      }
      pane=target; expand(true); text('[data-slot="receipt"]','材料已加入，补充要求后再发送。');
      $('[data-slot="draft"]').focus(); schedule();
    });
    new ResizeObserver(syncShape).observe(surface);
    window.addEventListener('resize', syncShape);
    for (const event of ['openclaw-chat-route-ready', 'openclaw-chat-pane-lifecycle-changed', 'openclaw-chat-run-activity-changed']) document.addEventListener(event, schedule);
    const observer = new MutationObserver(records => {
      // The body survives native root replacements and cached pane switches.
      // Ignore our own rendering so updates cannot form a loop.
      if (records.some(record => {
        const target = record.target.nodeType === Node.ELEMENT_NODE ? record.target : record.target.parentElement;
        return target && !target.closest('.mirally-companion, .mirally-companion-entry, .mirally-work');
      })) schedule();
    });
    observer.observe(document.body, {childList:true, subtree:true, characterData:true, attributes:true, attributeFilter:['class']});
    window.__mirallyCompanion = {dock, workspace, expand, setForm, ensureReady, syncShape, refresh:schedule};
    window.addEventListener('pagehide', () => disposers.splice(0).forEach(dispose => dispose()));
    schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, {once:true});
  else mount();
})();
