/* A task preparation page over the native chat canvas. Execution stays native. */
(() => {
  if (window.top !== window || location.origin !== __MIRALLY_ORIGIN__ || !window.chrome?.webview) return;
  const office = window.__mirallyOffice, intake = window.__mirallyIntake;
  const drafts = new Map(); // Unsaved form values only, scoped to each native conversation.
  let page, shown = false, scope, observed, sending = false;
  const active = () => intake.active();
  const $ = selector => page.querySelector(selector);
  const icon = kind => {
    const paths = {
      meeting:'M7 10h10M7 14h6M5 4h14v14H9l-4 3Z', brief:'M4 4h16v12H4ZM12 16v5M8 21h8M8 12V9m4 3V7m4 5V6',
      compare:'M8 4v16m8-16v16M4 8h8m0 8h8M5 5l3 3 3-3m2 14 3-3 3 3', read:'M12 5v15M3 4c4-1 6 0 9 2 3-2 5-3 9-2v15c-4-1-6 0-9 2-3-2-5-3-9-2Z',
      write:'m5 15 10-10 4 4L9 19l-5 1ZM13 7l4 4M4 4h5M4 8h3', custom:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',
      web:'M3 12h18M12 3c6 6 6 12 0 18-6-6-6-12 0-18ZM21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
      visual:'M3 4h18v16H3Zm0 13 5-5 4 4 3-3 6 5M15 8h.01', screen:'M3 4h18v13H3ZM8 21h8m-4-4v4M7 8h10M7 12h6',
      upload:'M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5', file:'M6 3h8l4 4v14H6ZM14 3v5h4M9 12h6M9 16h6'
    };
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[kind] || paths.custom}"/></svg>`;
  };
  const key = pane => `${pane.paneId}:${pane.state.sessionKey}`;
  const materials = pane => pane.chatState.attachmentReads.project(pane.state.chatAttachments || []);
  function form(pane = active()) {
    if (!pane) return null;
    const id = key(pane);
    if (!drafts.has(id)) drafts.set(id, {workflow:'', goal:'', output:'reply', source:'', audience:'', focus:''});
    return drafts.get(id);
  }
  function notice(value) { $('[data-work="notice"]').textContent = value; }
  function close() {
    shown = false;
    document.documentElement.dataset.mirallyView = 'chat';
    if (page) { page.hidden = true; sync(); }
  }
  async function open() {
    shown = true;
    document.documentElement.dataset.mirallyView = 'work';
    sync();
    await intake.ensureReady();
    sync();
  }
  function position() {
    if (!shown || page.hidden) return;
    const rect = document.querySelector('#control-ui-main')?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return;
    page.style.left = `${rect.left}px`; page.style.top = `${rect.top}px`;
    page.style.width = `${rect.width}px`; page.style.height = `${rect.height}px`;
  }
  function sync() {
    if (!page) return;
    const pane = active(), state = pane?.state;
    // A transient pane replacement must not remove the mode navigation.
    const main = document.querySelector('#control-ui-main');
    const chatRoute = main?.classList.contains('content--chat') === true || !!main?.querySelector('openclaw-new-session-page');
    if (main) main.inert = shown && chatRoute && !!pane && document.documentElement.dataset.mirallyMode !== 'companion';
    const entry = document.querySelector('.mirally-companion-entry');
    if (entry) {
      entry.hidden = !chatRoute;
      for (const button of entry.querySelectorAll('[data-entry]')) {
        if (button.dataset.entry === (shown && chatRoute ? 'office' : 'chat')) button.setAttribute('aria-current','page');
        else button.removeAttribute('aria-current');
      }
    }
    page.hidden = !shown || !chatRoute || !pane || document.documentElement.dataset.mirallyMode === 'companion';
    if (page.hidden) return;
    if (observed !== main) { resize.disconnect(); resize.observe(main); observed = main; }
    position();
    const draft = form(pane), changed = scope !== key(pane);
    if (changed) { scope = key(pane); notice(''); }
    const selected = office.workflows.find(item => item.id === draft.workflow);
    const kind = selected?.kind;
    page.dataset.kind = kind || '';
    page.dataset.preparing = String(!!selected);
    for (const button of page.querySelectorAll('[data-workflow]')) button.setAttribute('aria-pressed',String(button.dataset.workflow === draft.workflow));
    $('[data-work="setup"]').hidden = !selected;
    $('[data-work="choose"]').hidden = !!selected;
    const goal = $('[data-work="goal"]');
    if (goal.value !== draft.goal) goal.value = draft.goal;
    goal.placeholder = selected?.placeholder || '';
    $('[data-work="task-title"]').textContent = selected?.title || '准备你的工作';
    const sources = $('[data-work="sources"]'), source = $('[data-work="source"]');
    sources.hidden = !['web','writing','compare','custom'].includes(kind);
    $('[data-work="source-label"]').textContent = kind === 'web' ? '提供网页链接' : kind === 'compare' ? '方案背景或比较材料' : '想法、文稿或背景';
    source.placeholder = kind === 'web' ? '拖入网页链接，或每行粘贴一个网址。' : '可以直接写想法、粘贴文稿，也可以拖入文件。';
    if (source.value !== draft.source) source.value = draft.source;
    $('[data-work="target"]').hidden = !['screen','visual'].includes(kind);
    $('[data-work="media-note"]').hidden = kind !== 'meeting';
    $('[data-work="audience-row"]').hidden = kind !== 'writing';
    if ($('[data-work="audience"]').value !== draft.audience) $('[data-work="audience"]').value = draft.audience;
    const choices = $('[data-work="choices"]');
    if (choices.dataset.workflow !== draft.workflow) {
      choices.dataset.workflow = draft.workflow;
      choices.replaceChildren(...(selected?.choices || []).map(value=>{
        const button=document.createElement('button');button.type='button';button.textContent=value;button.dataset.focus=value;
        button.onclick=()=>{ const current=form();if(current) {current.focus=value;sync();} };return button;
      }));
    }
    for (const button of choices.querySelectorAll('button')) button.setAttribute('aria-pressed',String(button.dataset.focus===draft.focus));
    for (const button of page.querySelectorAll('[data-output]')) button.setAttribute('aria-pressed',String(button.dataset.output === draft.output));
    $('[data-work="session"]').textContent = pane.paneTitle || '当前会话';
    const attachments = state.chatAttachments || [];
    const projected = materials(pane);
    const thumbs = [...pane.querySelectorAll('.chat-attachments-preview .chat-attachment-thumb')].map(node=>node.querySelector('img')?.src || '');
    const pendingReads = pane.chatState?.attachmentReads?.pendingReads || 0;
    const readErrors = projected.some(item => item.state === 'error');
    const node = $('[data-work="materials"]');
    const signature = JSON.stringify(projected.map((item,index) => [item.attachment.id,item.state,item.progress,thumbs[index]]));
    if (node.dataset.signature !== signature || changed) {
      node.dataset.signature = signature;
      node.replaceChildren(...projected.map(({attachment:item,state:reading,progress},index) => {
        const chip = document.createElement('div'); chip.className = 'mirally-work__file';
        const mark = document.createElement('span'); mark.innerHTML = icon('file');
        if (item.mimeType?.startsWith('image/') && /^(blob:|data:image\/)/.test(thumbs[index] || '')) {
          const image=document.createElement('img');image.src=thumbs[index];image.alt=item.fileName || '图片材料';mark.replaceChildren(image);chip.classList.add('mirally-work__file--image');
        }
        const label = document.createElement('span'); label.textContent = item.fileName || item.name || '附件'; label.title = label.textContent;
        const status = document.createElement('small'); status.textContent = reading === 'error' ? '读取失败，请重新添加' : ['reading','uploading'].includes(reading) ? `读取中${typeof progress === 'number' ? ' '+Math.round(progress*100)+'%' : '…'}` : item.status === 'unsupported' ? '已保存，未解析' : '已就绪';
        if (item.note) status.title = item.note;
        status.dataset.state = reading;
        const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label',`移除 ${label.textContent}`);
        remove.onclick = () => { if (active() === pane && key(pane) === scope) { removeAttachment(pane,item.id); sync(); } };
        chip.append(mark,label,status,remove); return chip;
      }));
    }
    $('[data-work="material-count"]').textContent = pendingReads ? `正在读取 ${pendingReads} 份材料…` : readErrors ? '部分材料未能读取，请移除后重新添加。' : attachments.length ? `${attachments.length} 份材料 · 与当前对话共用` : '文件、图片，或在右侧直接写下背景';
    $('[data-work="draft-note"]').hidden = !state.chatMessage?.trim();
    $('[data-work="run-note"]').hidden = !pane.runActivity?.working;
    const urlsValid = kind !== 'web' || !draft.source.trim() || draft.source.split(/\r?\n/).filter(line=>line.trim()).every(line=>{
      try { const url=new URL(line.trim());return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password; } catch { return false; }
    });
    $('[data-work="source-error"]').hidden = urlsValid;
    const hasSource = !sources.hidden && !!draft.source.trim();
    $('[data-work="start"]').disabled = sending || pendingReads > 0 || readErrors || !urlsValid || state.chatSending || state.connected === false || !selected || (!draft.goal.trim() && !hasSource && !attachments.length);
    $('[data-work="start"]').textContent = sending ? '正在发送…' : pane.runActivity?.working ? '发送补充 ↗' : '发送 ↗';
  }
  function removeAttachment(pane,id) {
    // Native preview and projection have the same order, including duplicate names.
    // Wait for matching counts rather than risk removing another file mid-render.
    const items = materials(pane), index = items.findIndex(item => item.attachment.id === id);
    const buttons = [...pane.querySelectorAll('.chat-attachments-preview .chat-attachment-remove')];
    if (index >= 0 && buttons.length === items.length) buttons[index].click();
  }
  async function attach(list) {
    const input = active()?.querySelector('.agent-chat__file-input');
    if (!input) { notice('材料入口正在准备，请稍后再试。'); return; }
    if (!list) { input.click(); return; }
    if (!await intake.attach(list)) { notice('材料暂未加入，请稍后再试。');return; }
    notice('材料正在加入，请核对下面的预览与读取状态。'); sync();
  }
  async function start(event) {
    event.preventDefault();
    const pane = active(), state = pane?.state, draft = form(pane), session = pane && key(pane);
    if (!state || sending || $('[data-work="start"]').disabled || !draft?.workflow) return;
    const attachments = [...(state.chatAttachments || [])];
    const kind = office.workflows.find(item=>item.id===draft.workflow)?.kind;
    const request = office.compose(draft.workflow,draft.goal,draft.output,{source:['web','writing','compare','custom'].includes(kind)?draft.source:'',audience:kind==='writing'?draft.audience:'',focus:draft.focus});
    const specification = JSON.stringify(draft);
    sending = true; notice('正在交给 Mirally…'); sync();
    let admitted = false;
    const current = () => active() === pane && key(pane) === session;
    const accepted = () => {
      admitted = true;
      if (!current()) return;
      // Capture native handlers before removals update the attachment projection.
      const items = materials(pane), buttons = [...pane.querySelectorAll('.chat-attachments-preview .chat-attachment-remove')];
      if (buttons.length === items.length) items.forEach((item,index)=>{ if (attachments.some(file=>file.id===item.attachment.id)) buttons[index].click(); });
      if (JSON.stringify(draft) === specification) drafts.delete(session);
      close(); // Native outbox admission is the acknowledgement, not model completion.
    };
    try {
      // A message override keeps the native chat draft. Attachments must be
      // explicit: the native override deliberately does not inherit them.
      const result = await state.handleSendChat(request,{attachmentsOverride:attachments,mentionsOverride:[],onOutboxAdmitted:accepted,
        ...(window.__mirallyHost ? {mirallyAllowFileWrite: draft.output !== 'reply'} : {}),
        ...(pane.runActivity?.working ? {followUpMode:'steer'} : {})});
      if (!admitted && result === true) accepted();
      if (!admitted && current()) notice('尚未提交，请到对话中查看原因；要求和材料已保留。');
    } catch {
      if (!admitted && current()) notice('尚未提交，请到对话中查看原因；要求和材料已保留。');
    }
    finally { sending = false; sync(); window.__mirallyCompanion?.refresh(); }
  }
  const resize = new ResizeObserver(position);
  function mount() {
    page = document.createElement('section'); page.className = 'mirally-work'; page.hidden = true;
    page.setAttribute('aria-label','快捷开始');
    page.innerHTML = `
      <header class="mirally-work__top"><nav aria-label="快捷开始"><button data-work="chat">返回对话</button><button aria-current="page">快捷开始</button></nav><span data-work="session"></span></header>
      <div class="mirally-work__scroll"><div class="mirally-work__content">
        <header class="mirally-work__hero"><div><p class="mirally-work__eyebrow">MIRALLY / 与你并肩</p><h1>把手头的事，<br>一起向前推进。</h1><p>带上材料和想法，从一件具体的工作开始。</p></div><div class="mirally-work__emblem"><img alt="" src=${JSON.stringify(__MIRALLY_MARK__)}></div></header>
        <div class="mirally-work__cards" aria-label="选择一个起点"></div>
        <p class="mirally-work__choose" data-work="choose">选择一个起点，也可以自由开始。</p>
        <form data-work="setup" hidden>
          <div class="mirally-work__setup-head"><h2 data-work="task-title"></h2><button type="button" data-work="reselect">换个起点 ↑</button></div>
          <div class="mirally-work__grid">
            <section class="mirally-work__material-panel"><h3>01 <span>从材料或想法开始</span></h3>
              <div data-work="sources" hidden><label data-work="source-label"></label><textarea data-work="source" aria-label="网页、想法或原始文稿" rows="5"></textarea><p data-work="source-error" class="mirally-work__error" hidden>请每行填写一个 HTTP 或 HTTPS 网页链接。</p></div>
              <div data-work="target" class="mirally-work__target" hidden><button type="button" class="mirally-work__target-handle">${icon('screen')}<span>把 Mirally 拖向要看的窗口 ↗</span></button><p>松开后只采集目标窗口画面。先核对预览，再告诉我想做什么。</p><button type="button" data-work="badge">切成小图标，去页面旁边 ↗</button></div>
              <button type="button" class="mirally-work__drop" data-work="attach">${icon('upload')}<strong>把文件拖到这里</strong><span>或点击选择文件</span></button>
              <p data-work="material-count"></p><div data-work="materials" class="mirally-work__materials"></div>
              <p data-work="media-note" class="mirally-work__note" hidden>可以带来会议记录、录音或视频。无法解析的材料会说明，不把未读取内容当成已听到。</p>
              <p class="mirally-work__note">也可以在要求中提供本机文件路径、网页链接或直接粘贴内容。</p>
            </section>
            <section class="mirally-work__requirements"><h3>02 <span>告诉我你想做成什么</span></h3><div data-work="choices" class="mirally-work__choices"></div>
              <label data-work="audience-row" class="mirally-work__audience" hidden>给谁看？<input data-work="audience" aria-label="受众" placeholder="如：老师、团队、客户"></label>
              <textarea data-work="goal" aria-label="目标、背景与具体要求" rows="5"></textarea>
              <h3>03 <span>选择交付方式</span></h3><div class="mirally-work__outputs"></div>
            </section>
          </div>
          <footer class="mirally-work__submit"><div><p data-work="draft-note" hidden>对话中的文字草稿会保留，材料随本次工作一起发送。</p><p data-work="run-note" hidden>当前会话正在工作，新要求会通过原生补充机制提交。</p><p data-work="notice" role="status"></p></div><button data-work="start" type="submit">开始这项工作 ↗</button></footer>
        </form>
      </div></div>`;
    $('.mirally-work__cards').replaceChildren(...office.workflows.map(item => {
      const button = document.createElement('button'); button.type='button'; button.dataset.workflow=item.id;
      button.innerHTML=`<span class="mirally-work__card-icon">${icon(item.icon)}</span><strong>${item.title}</strong><span>${item.description}</span><i aria-hidden="true">↗</i>`;
      button.onclick=()=>{
        const draft=form(); if (!draft) return;
        draft.workflow=item.id; draft.focus=item.choices?.[0]||''; notice(''); sync();
        $('[data-work="setup"]').scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
      };
      return button;
    }));
    $('.mirally-work__outputs').replaceChildren(...office.outputs.map(item => {
      const button=document.createElement('button'); button.type='button'; button.dataset.output=item.id; button.textContent=item.title;
      button.onclick=()=>{ const draft=form(); if (draft) { draft.output=item.id; sync(); } }; return button;
    }));
    $('[data-work="chat"]').onclick=close;
    $('[data-work="reselect"]').onclick=()=>{const draft=form();if(draft){draft.workflow='';sync();$('.mirally-work__scroll').scrollTop=0;}};
    $('[data-work="badge"]').onclick=()=>{window.__mirallyCompanion.setForm('badge');window.__mirallyCompanion.dock();};
    $('[data-work="attach"]').onclick=()=>attach();
    $('[data-work="goal"]').oninput=event=>{ const draft=form(); if (draft) { draft.goal=event.target.value; sync(); } };
    for (const field of ['source','audience']) $('[data-work="'+field+'"]').oninput=event=>{ const draft=form();if(draft){draft[field]=event.target.value;sync();} };
    $('[data-work="setup"]').onsubmit=start;
    page.addEventListener('dragover',event=>{ if ([...event.dataTransfer?.types||[]].some(type=>['Files','text/plain','text/uri-list'].includes(type))) { event.preventDefault(); page.dataset.dragging='true'; } });
    page.addEventListener('dragleave',event=>{ if (!page.contains(event.relatedTarget)) delete page.dataset.dragging; });
    page.addEventListener('drop',async event=>{
      const data=intake.readDrop(event.dataTransfer);
      if (!data.files.length&&!data.urls.length&&!data.text) return;
      event.preventDefault();event.stopPropagation();delete page.dataset.dragging;
      await intake.ensureReady();const draft=form();if(!draft) return;
      if(data.files.length){if(!draft.workflow)draft.workflow=data.files.every(file=>file.type.startsWith('image/'))?'visual':'custom';await attach(data.files);}
      else{
        if(!draft.workflow)draft.workflow=data.urls.length?'web':'create';
        const value=data.urls.length ? data.urls.join('\n') : data.text;
        // URL fields stay machine-readable; a dragged link's title is optional background.
        const field=['web','writing','compare','custom'].includes(office.workflows.find(item=>item.id===draft.workflow)?.kind)?'source':'goal';
        draft[field]=[draft[field],value].filter(Boolean).join('\n\n');
        if(data.urls.length&&data.text&&!data.urls.some(url=>data.text.includes(url)))draft.goal=[draft.goal,data.text].filter(Boolean).join('\n');
      }
      if(!draft.focus)draft.focus=office.workflows.find(item=>item.id===draft.workflow)?.choices?.[0]||'';
      sync();$('[data-work="setup"]').scrollIntoView({block:'start'});
    });
    document.body.append(page);
    window.__mirallyContext?.bind($('.mirally-work__target-handle'),{
      onStart:()=>notice('正在读取窗口画面…'),
      onReady:async result=>{
        if(!await intake.attach([result.file]))throw new Error('画面暂未加入，请重试。');
        notice('已带上「'+result.title+'」'+(result.source==='visible-area'?'的可见区域，可能包含遮挡，请核对预览。':'的画面，请核对预览并填写要求。'));sync();
      },onError:notice
    });
    window.addEventListener('resize',position);
    window.__mirallyWork={open,close,sync};
  }
  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',mount,{once:true}); else mount();
})();
