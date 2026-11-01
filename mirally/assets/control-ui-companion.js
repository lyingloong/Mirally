/* Companion for the pinned native Control UI. No second client or memory store. */
(() => {
  if (window.top !== window || location.origin !== __MIRALLY_ORIGIN__ || !window.chrome?.webview) return;
  const MARK = __MIRALLY_MARK__;
  const PETS = __MIRALLY_PETS__;
  const PET_KEY = 'mirally.companion.pet';
  const FORM_KEY = 'mirally.companion.form';
  let petType = 'otter';
  try { const saved = window.__mirallyHost?.getUiPreference(PET_KEY) ?? localStorage.getItem(PET_KEY); if (Object.hasOwn(PETS, saved)) petType = saved; } catch {}
  const office = window.__mirallyOffice;
  const trackReply = office.createReplyTracker();
  let app, pane, surface, entry, connectedContext, mode = 'workspace', frame, returnAfterAttention, presentationScope, hydratedHistoryScope, preparing;
  let replyNotice, dismissedReply;
  let surfaceMode = 'rail';
  let pinned = false, shapeSignature = '', presentationAnimations = [];
  let tip, tipAnchor, tipTimer, tipLeaveTimer;
  try { const saved=window.__mirallyHost?.getUiPreference(FORM_KEY) ?? localStorage.getItem(FORM_KEY); if (['rail','badge','pet'].includes(saved)) surfaceMode=saved; } catch {}
  const savePreference = (key, value) => {
    try {
      const pending = window.__mirallyHost?.setUiPreference ? window.__mirallyHost.setUiPreference(key, value) : localStorage.setItem(key, value);
      pending?.catch?.(() => {});
    } catch { /* Presentation remains usable if this source cannot save preferences. */ }
  };
  const disposers = [];
  const $ = selector => surface.querySelector(selector);
  const text = (selector, value) => {
    const node = $(selector);
    if (node.textContent !== value) node.textContent = value;
  };
  const post = (action, extra = {}) => chrome.webview.postMessage({type:'mirally-companion', action, ...extra});
  function hideTip({sync = true} = {}) {
    clearTimeout(tipTimer);clearTimeout(tipLeaveTimer);
    if (tipAnchor) {
      const ids=(tipAnchor.getAttribute('aria-describedby')||'').split(/\s+/).filter(id=>id && id!=='mirally-companion-tip');
      if(ids.length)tipAnchor.setAttribute('aria-describedby',ids.join(' '));else tipAnchor.removeAttribute('aria-describedby');
    }
    tipAnchor=null;
    if(tip&&!tip.hidden){tip.hidden=true;if(sync)syncShape();}
  }
  function showTip(target) {
    if(mode!=='companion'||tipAnchor!==target||!target.isConnected||!target.getClientRects().length ||
        !(target.matches(':hover')||target.matches(':focus-visible')))return hideTip();
    tip.textContent=target.dataset.tip;
    const r=target.getBoundingClientRect();
    const beside=target.classList.contains('mirally-companion__rail')&&r.left-18>=120;
    tip.style.maxWidth=Math.min(276,beside?r.left-18:innerWidth-16)+'px';
    tip.style.left='0px';tip.style.top='0px';
    tip.hidden=false;
    const box=tip.getBoundingClientRect();
    const clamp=(value,maximum)=>Math.max(8,Math.min(value,maximum-8));
    let x=beside?r.left-box.width-10:r.left+(r.width-box.width)/2;
    let y=beside?r.top+(r.height-box.height)/2:r.bottom+8;
    if(!beside && y+box.height>innerHeight-8)y=r.top-box.height-8;
    tip.style.left=clamp(x,innerWidth-box.width)+'px';
    tip.style.top=clamp(y,innerHeight-box.height)+'px';
    const ids=new Set((target.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean));
    ids.add(tip.id);target.setAttribute('aria-describedby',[...ids].join(' '));
    syncShape();
  }
  function queueTip(target) {
    if(!target?.dataset.tip)return;
    if(target===tipAnchor&&tip&&!tip.hidden){clearTimeout(tipLeaveTimer);return;}
    hideTip();tipAnchor=target;
    tipTimer=setTimeout(()=>showTip(target),450);
  }
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
  const attachmentsLabel = state => state.chatAttachments?.length ? `${state.chatAttachments.length} 份材料已带上` : 'Mirally · 随时在这里';
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
  function setPinned(value) {
    pinned = !!value;
    surface.dataset.pinned = String(pinned);
    surface.dataset.preview = String(!pinned && ['quick','detail'].includes(surface.dataset.view));
    surface.querySelectorAll('[data-action="pin"]').forEach(button => {
      button.setAttribute('aria-pressed',String(pinned));
      button.dataset.tip = pinned ? '取消固定，移开后自动收起' : '固定面板，移开后保持展开';
      button.setAttribute('aria-label',button.dataset.tip);
      if(tipAnchor===button&&tip&&!tip.hidden&&tip.textContent!==button.dataset.tip)showTip(button);
    });
  }
  function animatePresentation(value) {
    presentationAnimations.splice(0).forEach(animation=>animation.cancel());
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const node = value === 'compact' ? $('.mirally-companion__rail') : $('.mirally-companion__panel');
    // Layout and the native Region reach their final geometry once. Only
    // compositor properties animate; neither one chases the other's frames.
    const animation=node.animate([{opacity:value === 'compact' ? .6 : 0},{opacity:1}],
      {duration:value === 'compact' ? 83 : 167,easing:'cubic-bezier(0,0,0,1)'});
    presentationAnimations.push(animation);
    animation.finished.catch(()=>{});
    if (value !== 'compact') for (const child of node.children) {
      if (getComputedStyle(child).display === 'none') continue;
      const motion=child.animate([{translate:'6px 0'},{translate:'0px 0'}],{duration:167,easing:'cubic-bezier(0,0,0,1)'});
      presentationAnimations.push(motion);motion.finished.catch(()=>{});
    }
  }
  function setView(value, {preview = false} = {}) {
    if (!['compact','quick','menu','detail','reply'].includes(value)) return;
    if (value === 'compact' && replyNotice) value = 'reply';
    if (['quick','detail'].includes(value)) setPinned(!preview);
    else if (value === 'compact' || value === 'reply') setPinned(false);
    if (surface.dataset.view === value) { schedule(); return; }
    hideTip({sync:false});
    const ownedFocus=surface.contains(document.activeElement);
    surface.dataset.fromView = surface.dataset.view;
    surface.dataset.view = value;
    surface.dataset.expanded = String(value !== 'compact');
    $('.mirally-companion__rail').tabIndex = value === 'detail' ? -1 : 0;
    $('.mirally-companion__rail').setAttribute('aria-expanded',String(value !== 'compact'));
    $('.mirally-companion__panel').inert = value === 'compact';
    surface.dataset.preview = String(!pinned && ['quick','detail'].includes(value));
    if (!preview && ownedFocus && ['quick','detail'].includes(value) &&
        (!document.activeElement.getClientRects().length || value==='detail' && document.activeElement===$('.mirally-companion__rail'))) {
      $('[data-slot="draft"]').focus({preventScroll:true});
    }
    syncShape();
    animatePresentation(value);
    schedule();
  }
  function expand(value) {
    setView(value ? surfaceMode === 'rail' ? 'detail' : 'quick' : 'compact');
  }
  function dismissReply() {
    if (replyNotice) dismissedReply = replyNotice.key;
    replyNotice = null;
  }
  function showReply() {
    if (!replyNotice || mode !== 'companion') return;
    const report = office.handoff(replyNotice.text);
    const body = report ? [report.completed, report.attention && '待你确认：\n'+report.attention,
      report.next && '下一步：'+report.next].filter(Boolean).join('\n\n') : office.plain(replyNotice.text);
    text('[data-slot="notice-title"]', report ? '这一轮有结果了' : replyNotice.text ? 'Mirally 回复你' : '本轮已结束');
    text('[data-slot="notice-text"]', body || '这一轮已结束。打开会话查看结果。');
    const state = pane?.state;
    // Keep the user's current interaction; the receipt appears on returning
    // to compact. An empty composer after sending may show the answer directly.
    if (surface.dataset.view === 'reply') syncShape();
    else if (['compact','quick'].includes(surface.dataset.view) &&
        !state?.chatMessage?.trim() && !state?.chatAttachments?.length && surface.dataset.dragover !== 'true') {
      setView('reply');
    }
  }
  function setMode(value) {
    hideTip({sync:false});
    mode = value;
    document.documentElement.dataset.mirallyMode = value;
    surface.hidden = value !== 'companion';
    if (value === 'companion') expand(false);
    schedule();
  }
  function workspaceBackground() {
    // The native backplate must match the actual page while WebView2 resizes.
    // Transparent pixels reveal the host, so never use the companion's clear
    // background or assume a color from a theme name.
    for (const node of [document.body, document.documentElement]) {
      const color = getComputedStyle(node).backgroundColor;
      const match = /^rgba?\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)(?:\s*,\s*(\d+(?:\.\d+)?))?\s*\)$/.exec(color);
      if (!match || match[4] !== undefined && Number(match[4]) !== 1) continue;
      const channels = match.slice(1, 4).map(Number);
      if (channels.every(value => value >= 0 && value <= 255)) return {background:channels.map(Math.round)};
    }
    return {};
  }
  async function dock() {
    pane = await ensureReady();
    if (!pane) return;
    window.__mirallyWork?.close();
    const background = workspaceBackground();
    setMode('companion');
    post('dock', {...background,surfaceMode,view:surface.dataset.view,expanded:surface.dataset.expanded === 'true',viewport:[innerWidth,innerHeight]});
  }
  function setForm(value, remember = true) {
    if (!['rail','badge','pet'].includes(value)) return;
    hideTip({sync:false});
    surfaceMode = value;
    surface.dataset.form = value;
    surface.querySelectorAll('[data-form]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.form===value)));
    if (remember) savePreference(FORM_KEY,value);
    if (mode === 'companion') post('surface',{surfaceMode,view:surface.dataset.view,expanded:surface.dataset.expanded === 'true'});
  }
  function setPet(value, remember = true) {
    if (!Object.hasOwn(PETS, value)) return;
    petType = value;
    surface.dataset.pet = value;
    surface.querySelector('.mirally-companion__rail .mirally-companion__pet').innerHTML = PETS[value];
    $('.mirally-companion__pet--header').innerHTML = PETS[value].replaceAll('pet-g-', 'pet-header-g-');
    surface.querySelectorAll('button[data-pet]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.pet === value)));
    if (remember) savePreference(PET_KEY,value);
  }
  function syncShape() {
    if (mode !== 'companion' || !surface) return;
    let rect = surface.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const nodes = ['quick','menu','reply'].includes(surface.dataset.view) ?
      [$('.mirally-companion__rail'),$('.mirally-companion__panel')] : [surface];
    if(tip&&!tip.hidden){
      nodes.push(tip);
      const r=tip.getBoundingClientRect(),x=Math.min(rect.x,r.x),y=Math.min(rect.y,r.y);
      rect={x,y,width:Math.max(rect.right,r.right)-x,height:Math.max(rect.bottom,r.bottom)-y};
    }
    const parts = nodes.map(node => {
      const r = node.getBoundingClientRect();
      return [r.x,r.y,r.width,r.height,parseFloat(getComputedStyle(node).borderTopLeftRadius) || 0];
    });
    const shape={rect:[rect.x, rect.y, rect.width, rect.height], viewport:[innerWidth, innerHeight],parts,view:surface.dataset.view};
    const signature=JSON.stringify(shape);
    if (signature === shapeSignature) return;
    shapeSignature=signature;post('shape',shape);
  }
  function workspace(callback) {
    dismissReply();
    setMode('workspace');
    post('workspace', workspaceBackground());
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
    dismissReply();
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
      button.dataset.tip = item.label;
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
    const state = pane.state;
    const scope = `${pane.paneId}:${state.sessionKey}`;
    if (scope !== presentationScope) {
      presentationScope = scope; replyNotice = null; dismissedReply = null;
      hydratedHistoryScope = null;
      if (surface.dataset.view === 'reply') setView('compact');
      text('[data-slot="receipt"]', '');
    }
    // Native pane histories can be retained briefly while another session is
    // loading. They are not evidence of that session's current task.
    if (state.chatLoading !== true) hydratedHistoryScope = scope;
    const loading = state.chatLoading === true && hydratedHistoryScope !== scope;
    const history = loading ? [] : state.chatMessages ?? [];
    const userRows = history.filter(row => message(row).role === 'user');
    const task = userRows.at(-1);
    const reply = [...history].reverse().find(row => office.isFinal(message(row)) && content(row));
    const finalReply = reply && history.indexOf(reply) > history.indexOf(task);
    const identity = row => {
      const m = message(row);
      return [m.id, m.__openclaw?.runId, m.sequence, m.timestamp ?? m.ts, content(row)];
    };
    const busy = !loading && (pane.runActivity?.working === true || !!state.chatSending);
    const waiting = loading ? 0 : (pane.questionProjection?.prompts?.length ?? 0) + (state.chatSessionApprovalQueue?.length ?? 0);
    const error = loading ? null : state.chatError || state.chatRunError;
    const delivered = trackReply({scope, turn:JSON.stringify([userRows.length, task && identity(task)]),
      reply:finalReply ? {key:JSON.stringify(identity(reply)), text:content(reply)} : null,
      completion:pane.runActivity?.completion, working:busy, waiting, error, loading:state.chatLoading});
    if (busy || waiting || error) {
      replyNotice = null;
      if (surface.dataset.view === 'reply') setView('compact');
    } else if (delivered && delivered.key !== dismissedReply &&
        (mode === 'companion' || returnAfterAttention === state.sessionKey)) {
      replyNotice = delivered;
    }
    // Observe replies in the full workspace too, so docking or reloading
    // history never announces an answer that was already shown there.
    if (mode !== 'companion' && !returnAfterAttention) return;
    showReply();
    const row = rows(state).find(row => row.key === state.sessionKey);
    const digests = [state.observerDigest,row?.observerDigest].filter(digest => digest && (!digest.sessionKey || digest.sessionKey === state.sessionKey)).sort((a,b) =>
      (b.revision || 0)-(a.revision || 0) || (Number(b.updatedAt) || Date.parse(b.updatedAt) || 0)-(Number(a.updatedAt) || Date.parse(a.updatedAt) || 0));
    const runId = loading ? null : state.chatRunId || (row?.hasActiveRun ? digests.find(digest => row.activeRunIds?.includes(digest.runId))?.runId : null);
    const currentDigest = runId ? digests.find(digest => digest.runId === runId) : null;
    const runStartup = !loading && (!state.chatRunStartup?.runId || state.chatRunStartup.runId === runId) &&
      (!state.chatRunStartup?.sessionKey || state.chatRunStartup.sessionKey === state.sessionKey) ? state.chatRunStartup : null;
    if (returnAfterAttention && state.sessionKey !== returnAfterAttention) returnAfterAttention = null;
    if (returnAfterAttention && mode === 'workspace' && !waiting) {
      returnAfterAttention = null;
      dock();
    }
    const nativeTitle = pane.paneTitle || row?.displayName;
    const title = loading ? '正在读取会话' : task ? excerpt(content(task).split(/[。？！\n]/)[0],48) || '处理本轮附带的材料' :
      nativeTitle && nativeTitle !== state.sessionKey && nativeTitle !== state.sessionKey.split(':').at(-1) ? nativeTitle : '我们从这里开始';
    const status = loading ? '正在读取当前会话的活动' : waiting ? '需要你决定，工作进展会继续保留' : error ? excerpt(error, 100) : busy ?
      excerpt(currentDigest?.headline || runStartup?.message, 120) || '正在推进这件事，你可以继续自己的工作' :
      finalReply ? '回复已经留下，随时可以接着聊' : '把手头的一小部分交给我，我们一起推进';
    surface.dataset.state = waiting ? 'waiting' : error ? 'error' : busy ? 'working' : finalReply ? 'done' : 'idle';
    text('[data-slot="title"]', title);
    text('[data-slot="peek-title"]', title);
    text('[data-slot="rail-title"]', excerpt(title, 28));
    const otherAttention = background(state);
    text('[data-slot="state"]', waiting ? '需要你' : busy ? '正在推进' : error ? '需要查看' : finalReply ? '本轮已结束' : '随时在这里');
    text('[data-slot="rail-state"]', loading ? '读取中' : waiting ? '待确认' : busy ? '工作中' : error ? '需查看' : finalReply ? '已完成' : '待命');
    text('.mirally-companion__rail-hint', waiting || otherAttention ? '需要你' : busy ? '与你并肩' : error ? '需要查看' : finalReply ? '回复已留下' : '悬停速览');
    $('[data-action="attention"]').hidden = !waiting;
    $('[data-action="stop"]').hidden = !busy;
    text('[data-action="send"]', busy ? '补充 ↗' : '发送 ↗');
    const tools = (loading ? [] : state.toolStreamOrder ?? []).map(id => state.toolStreamById?.get?.(id) ?? state.toolStreamById?.[id])
      .filter(tool => tool && (!tool.sessionKey || tool.sessionKey === state.sessionKey));
    const currentTools = runId ? tools.filter(tool => tool.runId === runId) : [];
    const step = tool => {
      const items = (Array.isArray(tool.activity) ? tool.activity : []).filter(item => !item.hideFromChannelProgress);
      const item = items.filter(item => item.phase !== 'end' && !['completed','failed'].includes(item.status)).at(-1) || items.at(-1);
      return {label:excerpt(item?.progressText || item?.title || tool.activity?.description || office.toolLabel(tool.name),60),
        error:tool.isError || item?.status === 'failed', ended:tool.resultReceived || item?.phase === 'end' || item?.status === 'completed'};
    };
    const liveStep = currentTools.map(step).filter(item => !item.ended && !item.error).at(-1);
    const recentStep = currentTools.map(step).filter(item => item.ended || item.error).at(-1) || (!busy && currentTools.length ? step(currentTools.at(-1)) : null);
    const startup = runStartup?.state !== 'activity' ? ({preparing_workspace:'准备工作空间',
      naming_worktree:'准备工作目录',creating_worktree:'准备工作目录',running_setup:'准备工作环境',
      provisioning_environment:'准备工作环境',preparing_context:'整理上下文',memory_flushing:'整理会话记忆',
      starting_model:'思考下一步'})[runStartup?.phase] : null;
    const currentStep = loading ? '正在读取会话' : waiting ? '等待你的确认' : error ? '工作需要查看' : busy ?
      liveStep?.label || startup || excerpt(currentDigest?.headline || runStartup?.message,60) || '正在思考下一步' :
      finalReply ? '这一轮已结束' : '随时与你并肩';
    text('[data-slot="rail-current"]', currentStep);
    text('[data-slot="status"]', busy && !waiting && !error ? currentStep : status);
    text('[data-slot="rail-previous"]', recentStep && recentStep.label !== currentStep ? (recentStep.ended ? '最近处理 · ' : '最近活动 · ')+recentStep.label : '');
    text('[data-slot="mini-status"]', busy || waiting || error ? currentStep : replyNotice ? '新回复到了' : attachmentsLabel(state));
    const activities = currentTools.slice(-3).map(tool => {
      const item = step(tool);
      return `${item.error ? '需要查看' : !currentTools.length ? '最近处理' : item.ended ? '已处理' : '正在处理'} · ${item.label}`;
    });
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
    text('[data-slot="previous"]', excerpt(currentDigest?.assessment || (reply ? content(reply) : ''), 230) || '这个会话还没有进展记录');
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
    if(tipAnchor&&!tipAnchor.isConnected)hideTip();
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(sync); }
  function mount() {
    surface = document.createElement('aside');
    surface.className = 'mirally-companion';
    surface.hidden = true;
    surface.dataset.expanded = 'false';
    surface.dataset.view = 'compact';
    surface.dataset.form = surfaceMode;
    surface.dataset.pinned = 'false';
    surface.dataset.preview = 'false';
    surface.dataset.state = 'idle';
    surface.setAttribute('aria-label', 'Mirally 侧边伙伴');
    surface.innerHTML = `
      <div class="mirally-companion__rail" role="button" tabindex="0" aria-label="展开侧边伙伴" aria-expanded="false" aria-controls="mirally-companion-panel" title="悬停快速查看，单击保持展开；右键更多操作">
        <span class="mirally-companion__mark"><img alt="Mirally"></span>
        <span class="mirally-companion__pet" aria-hidden="true"></span>
        <span class="mirally-companion__rail-state"><span class="mirally-companion__pulse"></span><span data-slot="rail-state"></span></span>
        <span class="mirally-companion__rail-title" data-slot="rail-title"></span>
        <span class="mirally-companion__rail-progress"><strong data-slot="rail-current" aria-live="polite"></strong><span data-slot="rail-previous"></span></span>
        <span class="mirally-companion__rail-hint">与你并肩</span>
        <span class="mirally-companion__drop-hint">带上材料</span>
      </div>
      <div class="mirally-companion__panel" id="mirally-companion-panel" inert>
        <section class="mirally-companion__notice" aria-label="新回复">
          <div class="mirally-companion__notice-header"><span data-slot="notice-title" role="status" aria-live="polite"></span>
            <button class="mirally-companion__icon-button" data-action="notice-close" title="关闭回复气泡" aria-label="关闭回复气泡">×</button>
          </div>
          <p data-slot="notice-text"></p>
          <div class="mirally-companion__notice-actions"><button data-action="reply">查看完整回复 ↗</button><button data-action="reply-compose">接着聊</button></div>
        </section>
        <header class="mirally-companion__header">
          <span class="mirally-companion__mark"><img alt=""></span><span class="mirally-companion__brand">Mirally</span>
          <button type="button" class="mirally-companion__pet mirally-companion__pet--header" data-action="pet" aria-label="和伙伴打个招呼"></button>
          <button class="mirally-companion__drag-hint" data-action="drag" title="拖动伙伴的位置" aria-label="拖动伙伴的位置">⠿</button>
          <div class="mirally-companion__actions">
            <button class="mirally-companion__icon-button" data-action="pin" aria-pressed="false" title="固定面板，移开后保持展开" aria-label="固定面板，移开后保持展开"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6v6l3 4v2H6v-2l3-4zM12 15v6"/></svg></button>
            <button class="mirally-companion__text-button" data-action="collapse" title="收起到侧边，保留草稿和工作" aria-label="收起到侧边">收起 <span aria-hidden="true">›</span></button>
          </div>
        </header>
        <div class="mirally-companion__quick-header"><span data-slot="mini-status"></span><div class="mirally-companion__actions">
          <button class="mirally-companion__icon-button" data-action="pin" aria-pressed="false" title="固定面板，移开后保持展开" aria-label="固定面板，移开后保持展开"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6v6l3 4v2H6v-2l3-4zM12 15v6"/></svg></button>
          <button class="mirally-companion__icon-button" data-action="menu" title="更多操作和伙伴外观" aria-label="更多操作">⋯</button>
          <button class="mirally-companion__text-button" data-action="collapse" title="收起到侧边，保留草稿和工作" aria-label="收起到侧边">收起 <span aria-hidden="true">›</span></button>
        </div></div>
        <p class="mirally-companion__peek-title" data-slot="peek-title"></p>
        <div class="mirally-companion__menu-actions" role="group" aria-label="伙伴操作">
          <button data-action="quick">快速对话 <span>↵</span></button>
          <button data-action="detail">展开面板 <span>‹</span></button>
          <button data-action="workspace">返回主窗口 <span>↗</span></button>
          <button data-action="minimize" title="最小化到任务栏，点击任务栏或托盘图标恢复">最小化到任务栏 <span>−</span></button>
        </div>
        <div class="mirally-companion__appearance">
          <nav class="mirally-companion__form-switch" aria-label="伙伴形态"><button data-form="rail" aria-pressed="true">侧栏</button><button data-form="badge" aria-pressed="false">图标</button><button data-form="pet" aria-pressed="false">伙伴</button></nav>
          <div class="mirally-companion__pet-switch" role="group" aria-label="伙伴造型">
            <button type="button" data-pet="otter" aria-pressed="false"><span aria-hidden="true">${PETS.otter.replaceAll('pet-g-', 'pet-choice-otter-g-')}</span>小海獭<i aria-hidden="true"></i></button>
            <button type="button" data-pet="fox" aria-pressed="false"><span aria-hidden="true">${PETS.fox.replaceAll('pet-g-', 'pet-choice-fox-g-')}</span>小飞狐<i aria-hidden="true"></i></button>
          </div>
        </div>
        <section class="mirally-companion__work"><div class="mirally-companion__eyebrow"><span class="mirally-companion__pulse"></span><span data-slot="state"></span></div>
          <h2 class="mirally-companion__title" data-slot="title"></h2><p class="mirally-companion__status" data-slot="status" role="status"></p>
          <button data-action="attention" hidden>查看需要你决定的事 ↗</button>
          <button data-action="stop" hidden>停止本轮工作</button>
        </section>
        <div class="mirally-companion__body">
          <details class="mirally-companion__resume mirally-companion__office"><summary>快捷开始</summary><p>选择一个起点，添加需要的材料。</p><button data-action="office">打开快捷开始 ↗</button></details>
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
          <div class="mirally-companion__composer-footer"><button type="button" data-action="attach" class="mirally-companion__icon-button" title="添加文件或图片" aria-label="添加材料">＋</button><button type="button" class="mirally-companion__target-handle" title="按住并拖到目标窗口，截图后核对再发送" aria-label="拖到目标窗口截图">◎</button><span data-slot="receipt" role="status"></span><button class="mirally-companion__send" data-action="send" type="submit">发送 ↗</button></div>
        </form>
        <nav class="mirally-companion__navigation" aria-label="伙伴窗口操作"><button data-action="workspace">返回主窗口 <span aria-hidden="true">↗</span></button><button data-action="detail">展开面板 <span aria-hidden="true">‹</span></button><button data-action="menu">更多 <span aria-hidden="true">⋯</span></button></nav>
      </div>`;
    surface.querySelectorAll('img').forEach(image => { image.src = MARK; });
    surface.querySelectorAll('[title]').forEach(node=>{node.dataset.tip=node.title;node.removeAttribute('title');});
    setForm(surfaceMode, false);
    setPet(petType, false);
    document.body.append(surface);
    tip=document.createElement('div');tip.id='mirally-companion-tip';tip.className='mirally-companion-tip';tip.hidden=true;tip.setAttribute('role','tooltip');document.body.append(tip);
    window.__mirallyContext?.bind($('.mirally-companion__target-handle'),{
      onStart:()=>{setPinned(true);text('[data-slot="receipt"]','正在读取窗口画面…');},
      onReady:async result=>{
        if(!await window.__mirallyIntake.attach([result.file]))throw new Error('画面暂未加入，请重试。');
        setView('detail');text('[data-slot="receipt"]','已带上「'+result.title+'」'+(result.source==='visible-area'?'的可见区域，请核对截图和遮挡。':'的画面，核对后再补充要求。'));schedule();
      },onError:value=>{expand(true);text('[data-slot="receipt"]',value);}
    });
    entry = document.createElement('nav');
    entry.className = 'mirally-companion-entry';
    entry.setAttribute('aria-label', 'Mirally 模式');
    entry.innerHTML = '<button data-entry="chat">对话</button><button data-entry="office">快捷开始</button><button data-entry="dock" title="在屏幕侧边与你并肩工作">侧边伙伴 ↗</button>';
    entry.querySelector('[data-entry="chat"]').onclick = () => workspace(() => window.__mirallyWork?.close());
    entry.querySelector('[data-entry="dock"]').onclick = dock;
    entry.querySelector('[data-entry="office"]').onclick = openOffice;
    document.body.append(entry);
    let leaveTimer, enterTimer, reactionTimer, keyboardFocus=false, restoringAnchorFocus=false, hoverSuppressed=false, suppressedPointer, menuPointer, lastPointer, menuMoved=false;
    const rememberPointer = event => {
      if (Number.isFinite(event.screenX) && Number.isFinite(event.screenY)) lastPointer={x:event.screenX,y:event.screenY,space:'screen'};
      if (surface.dataset.view!=='menu' || !menuPointer) return;
      const point=menuPointer.space==='client' ? {x:event.clientX,y:event.clientY} : lastPointer;
      if (point && Number.isFinite(point.x) && Number.isFinite(point.y) && Math.hypot(point.x-menuPointer.x,point.y-menuPointer.y)>2) menuMoved=true;
    };
    const react = value => {
      clearTimeout(reactionTimer);
      surface.dataset.reaction=value;
      reactionTimer=setTimeout(()=>delete surface.dataset.reaction,value==='drag'?900:1100);
    };
    const holdOpen = () => surface.dataset.view === 'reply' || (surface.dataset.view === 'menu' ? !menuMoved : pinned) ||
      (tip&&!tip.hidden&&tip.matches(':hover')) ||
      (document.hasFocus() && ((!getSelection()?.isCollapsed && surface.contains(getSelection()?.anchorNode)) || document.activeElement === $('[data-slot="draft"]') ||
      (keyboardFocus && surface.contains(document.activeElement)))) || surface.dataset.dragover==='true';
    const cancelLeave = () => { clearTimeout(leaveTimer); leaveTimer=null; };
    const cancelEnter = () => { clearTimeout(enterTimer); enterTimer=null; };
    const compact = ({restoreFocus = true} = {}) => {
      cancelEnter();cancelLeave(); keyboardFocus=false; hoverSuppressed=surface.matches(':hover'); suppressedPointer=lastPointer;menuPointer=null; menuMoved=false;
      dismissReply();
      expand(false);
      if (restoreFocus && mode === 'companion') {
        restoringAnchorFocus=true;
        $('.mirally-companion__rail').focus({preventScroll:true});
        restoringAnchorFocus=false;
      }
    };
    const openMenu = nativePoint => {
      if (mode !== 'companion') return false;
      hoverSuppressed=false; cancelEnter();cancelLeave();
      if (surface.dataset.view !== 'menu') {
        menuPointer=lastPointer || nativePoint;menuMoved=false;
        setView('menu');
        $('.mirally-companion__menu-actions button').focus({preventScroll:true});
      }
      return true;
    };
    const acceptsContextMenu = target => {
      if (mode!=='companion' || !target || !surface.contains(target)) return false;
      if (target.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return false;
      const selected=window.getSelection?.();
      if (selected?.toString() && selected.containsNode?.(target,true)) return false;
      return true;
    };
    const requestContextMenu = (x,y) => {
      if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
      const target=document.elementFromPoint(x,y);
      if (!acceptsContextMenu(target)) return false;
      return openMenu({x,y,space:'client'});
    };
    const openPetMenu = () => {
      if (mode === 'companion' && surfaceMode === 'pet' && surface.dataset.view === 'compact') openMenu();
    };
    const leave = () => {
      cancelLeave();
      // Crossing the transparent gap, reading or visiting a system dialog must
      // not race a 100ms close. Only an uncommitted preview auto-hides.
      leaveTimer=setTimeout(()=>{ if (!surface.matches(':hover') && !holdOpen()) expand(false); },400);
    };
    const collapseAfterFocus = () => {
      if (mode !== 'companion') return;
      requestAnimationFrame(() => { if (!surface.matches(':hover') && !holdOpen()) leave(); });
    };
    surface.addEventListener('pointerenter', event => {
      rememberPointer(event);
      if (hoverSuppressed && suppressedPointer && lastPointer && Math.hypot(lastPointer.x-suppressedPointer.x,lastPointer.y-suppressedPointer.y)>2) hoverSuppressed=false;
      cancelLeave();
      cancelEnter();
      if (event.pointerType === 'mouse' && !hoverSuppressed) {
        if (surfaceMode === 'pet') react('hello');
        else if (surface.dataset.view === 'compact') enterTimer=setTimeout(()=>{
          enterTimer=null;
          if (mode==='companion' && surface.matches(':hover') && !hoverSuppressed && surface.dataset.view==='compact') setView('quick',{preview:true});
        },140);
      }
    });
    surface.addEventListener('pointerleave', event => {
      rememberPointer(event);
      cancelEnter();
      if (!suppressedPointer || !lastPointer || Math.hypot(lastPointer.x-suppressedPointer.x,lastPointer.y-suppressedPointer.y)>2) hoverSuppressed=false;
      dragStart=null;
      if (!holdOpen()) leave();
    });
    surface.addEventListener('focusin',event=>{cancelLeave();if(event.target===$('[data-slot="draft"]'))setPinned(true);});
    surface.addEventListener('keydown',()=>{ keyboardFocus=true; },true);
    surface.addEventListener('keydown',event=>{ if (event.key==='Escape') { event.preventDefault(); compact(); } });
    surface.addEventListener('focusout', collapseAfterFocus);
    document.addEventListener('selectionchange', collapseAfterFocus);
    surface.addEventListener('pointerover',event=>{
      if(event.pointerType==='mouse'&&!event.buttons)queueTip(event.target.closest('[data-tip]'));
    });
    surface.addEventListener('pointerout',event=>{
      if(!tipAnchor||tipAnchor.contains(event.relatedTarget)||tip.contains(event.relatedTarget))return;
      clearTimeout(tipTimer);clearTimeout(tipLeaveTimer);tipLeaveTimer=setTimeout(()=>hideTip(),180);
    });
    surface.addEventListener('focusin',event=>{if(!restoringAnchorFocus&&event.target.matches('[data-tip]:focus-visible'))queueTip(event.target);});
    surface.addEventListener('focusout',()=>hideTip());
    tip.addEventListener('pointerenter',()=>{clearTimeout(tipLeaveTimer);cancelLeave();});
    tip.addEventListener('pointerleave',()=>{hideTip();if(!surface.matches(':hover')&&!holdOpen())leave();});
    document.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&tip&&!tip.hidden){hideTip();event.preventDefault();event.stopPropagation();}
    },true);
    document.addEventListener('keydown',event=>{
      if(mode==='companion'&&event.key==='Escape'&&!event.defaultPrevented){event.preventDefault();compact();}
    });
    document.addEventListener('scroll',event=>{if(!tip.contains(event.target))hideTip();},true);
    window.addEventListener('pointerdown',()=>hideTip(),true);
    window.addEventListener('blur',()=>hideTip());
    window.addEventListener('resize',()=>hideTip());
    $('.mirally-companion__rail').onkeydown = event => {
      if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
        event.preventDefault(); openMenu(); $('.mirally-companion__menu-actions button').focus();
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (surfaceMode === 'pet') react('poke');
        expand(true); $('[data-slot="draft"]').focus();
      }
    };
    $('[data-slot="draft"]').oninput = event => {setPinned(true);pane?.state?.handleChatDraftChange(event.target.value);};
    $('[data-slot="draft"]').onkeydown = event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
    };
    $('.mirally-companion__composer').onsubmit = event => { event.preventDefault(); send(); };
    let dragStart, movedAt=0;
    // Open on mouse release even if the native host suppresses contextmenu.
    surface.addEventListener('pointerup',event=>{
      if (event.button !== 2 || !acceptsContextMenu(event.target)) return;
      rememberPointer(event);
      event.preventDefault(); event.stopPropagation(); openMenu();
    },true);
    surface.addEventListener('contextmenu',event=>{
      if (!acceptsContextMenu(event.target)) return;
      rememberPointer(event);
      event.preventDefault(); event.stopPropagation(); openMenu();
    });
    surface.addEventListener('click', event => {
      if (event.detail && movedAt && performance.now()-movedAt<350) { movedAt=0; event.preventDefault(); return; }
      movedAt=0;
      if (event.target.closest('.mirally-companion__rail')) {
        hoverSuppressed=false; cancelEnter();cancelLeave();
        if (surfaceMode === 'pet') react('poke');
        expand(true); $('[data-slot="draft"]').focus();
      }
      const form = event.target.closest('button[data-form]')?.dataset.form;
      if (form) { setForm(form); compact(); }
      const pet = event.target.closest('button[data-pet]')?.dataset.pet;
      if (pet) { setPet(pet); compact(); }
      const action = event.target.closest('[data-action]')?.dataset.action;
      if (action && !['pin','menu','collapse','notice-close','minimize'].includes(action)) setPinned(true);
      if (action === 'pet') react('poke');
      if (action === 'quick') { setView('quick'); $('[data-slot="draft"]').focus(); }
      if (action === 'reply-compose') { dismissReply(); setView('quick'); $('[data-slot="draft"]').focus(); }
      if (action === 'detail') setView('detail');
      if (action === 'workspace' || action === 'reply') workspace(() => window.__mirallyWork?.close());
      if (action === 'pin') {setPinned(!pinned);if(!pinned&&!surface.matches(':hover'))leave();}
      if (action === 'menu') {if(surface.dataset.view==='menu')compact();else openMenu();}
      if (action === 'minimize') {compact({restoreFocus:false});post('minimize');}
      if (action === 'collapse' || action === 'notice-close') compact();
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
      rememberPointer(event);
      keyboardFocus=false; movedAt=0; cancelLeave();
      if (event.button===0 && (event.target.closest('[data-action="drag"]') || event.altKey)) {
        event.preventDefault(); react('drag'); post('drag');
      } else if (event.button===0 && !event.target.closest('button,a,input,textarea,select,summary,[role="button"]:not(.mirally-companion__rail)') &&
        (event.target.closest('.mirally-companion__header') ||
          (surface.dataset.view==='detail' && event.clientY<=$('.mirally-companion__header').getBoundingClientRect().bottom) ||
          (surfaceMode!=='rail' && event.target.closest('.mirally-companion__rail')))) {
        dragStart={x:event.screenX,y:event.screenY};
        react('poke');
      }
      if (event.target.closest('[data-action="pet"]')) react('poke');
    });
    surface.addEventListener('pointermove',event=>{
      rememberPointer(event);
      if (event.buttons!==1) { dragStart=null; return; }
      if (dragStart && Math.hypot(event.screenX-dragStart.x,event.screenY-dragStart.y)>5) {
        dragStart=null; movedAt=performance.now(); react('drag'); post('drag');
      }
    });
    for (const event of ['pointerup','pointercancel','blur']) window.addEventListener(event,()=>{ dragStart=null; });
    window.addEventListener('blur',()=>{ cancelEnter();keyboardFocus=false; menuMoved=true; if (mode==='companion' && surface.dataset.view==='menu') compact({restoreFocus:false}); else if (mode==='companion'&&!pinned) leave(); });
    surface.querySelectorAll('details').forEach(node => node.addEventListener('toggle', schedule));
    surface.addEventListener('dragover', event => {
      if ([...event.dataTransfer?.types||[]].some(type=>['Files','text/plain','text/uri-list'].includes(type))) {
        event.preventDefault(); cancelLeave(); surface.dataset.dragover='true'; expand(true);
      }
    });
    surface.addEventListener('dragleave', event => {
      if (!surface.contains(event.relatedTarget)) { delete surface.dataset.dragover; if (!holdOpen()) leave(); }
    });
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
        return target && !target.closest('.mirally-companion, .mirally-companion-tip, .mirally-companion-entry, .mirally-work');
      })) schedule();
    });
    observer.observe(document.body, {childList:true, subtree:true, characterData:true, attributes:true, attributeFilter:['class']});
    window.__mirallyCompanion = {dock, workspace, expand, setView, setForm, setPet, openMenu, requestContextMenu, openPetMenu, ensureReady, syncShape, refresh:schedule};
    window.addEventListener('pagehide', () => disposers.splice(0).forEach(dispose => dispose()));
    schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, {once:true});
  else mount();
})();
