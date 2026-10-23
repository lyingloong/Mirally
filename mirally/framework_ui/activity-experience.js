/* Activity/Memory presentation over the same product owners and framework RPC. */
(() => {
  const $ = id => document.getElementById(id);
  let api, capture, confirmation, memoryOffset = 0, reading, captureDirty = false;
  const booleans = {
    visual_enabled: '屏幕截图', persist_images: '保存截图文件', ocr_enabled: 'OCR 正文',
    ui_structure_enabled: 'UIA 结构', uia_content_enabled: 'UIA 正文',
    input_enabled: '输入操作元数据', input_text_enabled: '输入文本', clipboard_enabled: '剪贴板正文',
    window_titles_enabled: '窗口标题', browser_urls_enabled: '浏览器地址',
  };
  function message(text, success = false) {
    const node = $('notice'); node.textContent = text; node.hidden = false; node.className = success ? 'success' : '';
  }
  const act = callback => async event => { event?.preventDefault(); try { await callback(event); } catch (error) { message(error.message || String(error)); } };
  function detail(parent, title, body) {
    const node = document.createElement('details'); const summary = document.createElement('summary');
    summary.textContent = title; node.append(summary); const content = document.createElement('div'); content.innerHTML = body;
    node.append(content); parent.append(node); return node;
  }
  function patterns(value) { return value.split(/[\n,，]+/).map(item => item.trim()).filter(Boolean); }
  async function loadActivity() {
    capture = await api.rpc('activity.status');
    const worker = capture.workers?.activity || {};
    $('fx-activity-state').textContent = `${worker.state || '尚未运行'}${capture.workers?.settings_pending ? ' · 正在应用设置' : ''}`;
    $('fx-activity-scope').textContent = capture.scope_note;
    $('fx-activity-diagnostics').textContent = JSON.stringify({workers: capture.workers, snapshot: capture.snapshot, profiles: capture.profiles}, null, 2);
    if ($('activity-enabled')) $('activity-enabled').checked = !!capture.enabled;
    for (const [field, value] of Object.entries(captureDirty ? {} : capture.capture)) {
      const input = $(`fx-capture-${field}`); if (!input || document.activeElement === input) continue;
      if (typeof value === 'boolean') input.checked = value; else input.value = Array.isArray(value) ? value.join('\n') : value;
    }
    $('fx-capture-pause').disabled = !capture.enabled || capture.paused;
    $('fx-capture-stop').disabled = !capture.enabled;
    $('fx-capture-resume').disabled = !capture.paused;
  }
  async function confirmCapture(method) {
    if (captureDirty) throw new Error('请先保存修改后的采集范围，再审核确认。');
    await loadActivity(); confirmation = {method, fingerprint: capture.fingerprint};
    $('fx-capture-confirmed').checked = false;
    $('fx-capture-confirm-scope').textContent = capture.scope.filter(item => item.enabled).map(item => item.label).join('、') + '\n' + capture.scope_note;
    $('fx-capture-dialog').showModal();
  }
  async function browse(mode) {
    const result = await api.rpc('activity.browse', {mode});
    const node = $('fx-history-link'); node.replaceChildren();
    const link = document.createElement('a'); link.href = result.url; link.target = '_blank'; link.rel = 'noopener';
    link.textContent = mode === 'monitor' ? '打开只读诊断与监测' : '打开原始活动历史'; node.append(link);
    const native = window.pywebview?.api;
    if (native?.open_browser) await native.open_browser(result.url);
    else window.open(result.url, '_blank', 'noopener');
  }
  async function searchMemories(offset = 0) {
    memoryOffset = offset;
    const kinds = $('fx-memory-kind').value, source = $('fx-memory-source').value;
    const result = await api.rpc('memory.search', {query: $('fx-memory-query').value.trim(), limit: 10, offset,
      include_candidates: $('fx-memory-candidates').checked, ...(kinds ? {kinds: [kinds]} : {}), ...(source ? {provenances: [source]} : {})});
    const container = $('fx-memory-items'); container.replaceChildren();
    if (!result.memories?.length) { const p = document.createElement('p'); p.textContent = result.status === 'disabled' ? '持久记忆当前关闭。' : '这一页没有可用记录；来源撤回的内容不会显示。'; container.append(p); }
    for (const memory of result.memories || []) {
      const node = document.createElement('article'); node.className = 'card';
      const title = document.createElement('h3'); title.textContent = memory.title || memory.id;
      const meta = document.createElement('small'); meta.textContent = [memory.id, memory.kind, memory.provenance, memory.status, memory.observed_start_local].filter(Boolean).join(' · ');
      const content = document.createElement('p'); content.textContent = memory.content || '';
      const button = document.createElement('button'); button.textContent = '分页查看正文';
      button.addEventListener('click', act(() => readMemory({id: memory.id, candidate: memory.status === 'candidate', first: 1})));
      node.append(title, meta, content, button); container.append(node);
    }
    $('fx-memory-previous').disabled = offset === 0;
    $('fx-memory-next').disabled = result.next_offset == null;
    $('fx-memory-next').dataset.offset = String(result.next_offset ?? offset);
    $('fx-memory-coverage').textContent = JSON.stringify(result.coverage || {}, null, 2);
  }
  async function readMemory(value) {
    reading = value;
    const result = await api.rpc('memory.read', {memory_id: value.id, from_line: value.first, line_count: 50, include_candidates: value.candidate});
    $('fx-memory-body').textContent = result.text || '此记录当前不可读取。';
    $('fx-memory-read-meta').textContent = `${value.id} · 第 ${result.from_line || value.first} 行起，共 ${result.line_count || 0} 行 / ${result.total_lines || 0} 行${value.candidate ? ' · 仅查看候选，没有确认或晋升' : ''}`;
    $('fx-memory-body-prev').disabled = value.first <= 1;
    $('fx-memory-body-next').disabled = !result.truncated;
    $('fx-memory-body-next').dataset.first = String((result.from_line || value.first) + (result.line_count || 50));
  }
  async function loadBudgets() {
    const settings = await api.rpc('settings.get');
    for (const [key, value] of Object.entries(settings.memory_semantics || {})) if ($(`fx-budget-${key}`) && document.activeElement !== $(`fx-budget-${key}`)) $(`fx-budget-${key}`).value = value;
    $('fx-memory-health').textContent = JSON.stringify(await api.rpc('memory.status'), null, 2);
  }
  function mount() {
    api = window.__mirallyFramework;
    if (!api || !$('page-activity') || $('fx-activity-panel')) return false;
    const captureFields = Object.entries(booleans).map(([field, label]) => `<label class="check"><input id="fx-capture-${field}" type="checkbox">${label}</label>`).join('');
    const panel = detail($('page-activity'), '活动范围、采集控制与原始记录', `
      <div id="fx-activity-panel" class="form-grid"><p id="fx-activity-state"></p><p id="fx-activity-scope" class="muted"></p>
      <div class="search-row"><button id="fx-capture-start">审核范围并开始</button><button id="fx-capture-pause">暂停</button><button id="fx-capture-resume">审核范围并恢复</button><button id="fx-capture-stop">停止</button></div>
      <p class="muted">暂停会释放当前采集器，保留已启用偏好；恢复时重新确认当前范围。保存范围会先停止采集，未经再次确认不会自动扩大范围。</p>
      <form id="fx-capture-configure" class="form-grid"><label>正文采集级别<select id="fx-capture-capture_level"><option value="metadata">仅元数据</option><option value="structure">结构</option><option value="content">正文</option></select></label>
      <label>截图范围<select id="fx-capture-visual_scope"><option value="foreground">前台窗口</option><option value="monitors">所有显示器</option></select></label>${captureFields}
      <label>仅限应用（每行一条匹配规则，可留空）<textarea id="fx-capture-allowed_apps"></textarea></label><label>排除应用（每行一条）<textarea id="fx-capture-exclude_apps"></textarea></label><label>排除窗口标题（每行一条）<textarea id="fx-capture-exclude_window_titles"></textarea></label>
      <label>轮询间隔（秒）<input id="fx-capture-poll_interval_seconds" type="number" min="0.25" max="10" step="0.25"></label><label>截图间隔（秒）<input id="fx-capture-capture_interval_seconds" type="number" min="1" max="60"></label><button>保存范围并停止采集</button></form>
      <div class="search-row"><button id="fx-activity-history">查看原始活动历史</button><button id="fx-activity-monitor">查看只读诊断监测</button></div><p id="fx-history-link"></p><p class="muted">历史与监测不启动第二个采集器。持久诊断快照不证明采集器仍在运行，实际状态以上方 owner 为准。</p>
      <details><summary>配置路径和诊断详情</summary><pre id="fx-activity-diagnostics"></pre></details></div>
      <dialog id="fx-capture-dialog"><form id="fx-capture-confirm" class="form-grid"><h3>确认本次采集范围</h3><pre id="fx-capture-confirm-scope"></pre><label class="check"><input id="fx-capture-confirmed" type="checkbox" required>我确认读取上述范围内的活动与正文。</label><div class="search-row"><button type="button" id="fx-capture-cancel">取消</button><button class="primary">确认并开始</button></div></form></dialog>`);
    panel.addEventListener('toggle', () => { if (panel.open) loadActivity().catch(error => message(error.message)); });
    $('fx-capture-start').addEventListener('click', act(() => confirmCapture('activity.start')));
    $('fx-capture-resume').addEventListener('click', act(() => confirmCapture('activity.resume')));
    $('fx-capture-pause').addEventListener('click', act(async () => { await api.rpc('activity.pause'); await loadActivity(); }));
    $('fx-capture-stop').addEventListener('click', act(async () => { await api.rpc('activity.stop'); await loadActivity(); }));
    $('fx-capture-cancel').addEventListener('click', () => $('fx-capture-dialog').close());
    $('fx-capture-confirm').addEventListener('submit', act(async () => { if (!$('fx-capture-confirmed').checked) return; await api.rpc(confirmation.method, {confirmed: true, expected_fingerprint: confirmation.fingerprint}); $('fx-capture-dialog').close(); await loadActivity(); message('采集请求已交给现有监督者，实际状态会在上方更新。', true); }));
    $('fx-capture-configure').addEventListener('submit', act(async () => {
      const patch = Object.fromEntries(Object.keys(booleans).map(field => [field, $(`fx-capture-${field}`).checked]));
      for (const field of ['visual_scope', 'capture_level']) patch[field] = $(`fx-capture-${field}`).value;
      for (const field of ['exclude_apps', 'exclude_window_titles', 'allowed_apps']) patch[field] = patterns($(`fx-capture-${field}`).value);
      for (const field of ['poll_interval_seconds', 'capture_interval_seconds']) patch[field] = Number($(`fx-capture-${field}`).value);
      await api.rpc('activity.configure', {patch}); captureDirty = false; await loadActivity(); message('范围已保存，采集已请求停止；重新审核确认后再开始。', true);
    }));
    $('fx-capture-configure').addEventListener('input', () => { captureDirty = true; });
    $('fx-activity-history').addEventListener('click', act(() => browse('history'))); $('fx-activity-monitor').addEventListener('click', act(() => browse('monitor')));
    $('activity-enabled')?.addEventListener('change', event => { event.stopImmediatePropagation(); const enabled = event.target.checked; event.target.checked = !enabled; act(async () => { if (enabled) await confirmCapture('activity.start'); else { await api.rpc('activity.stop'); await loadActivity(); } })(); }, true);
    detail($('page-memory'), '按来源与类型浏览记忆 / 查看候选和完整正文', `
      <form id="fx-memory-search" class="form-grid"><label>关键词<input id="fx-memory-query"></label><label>类型<select id="fx-memory-kind"><option value="">全部</option><option value="working">近期工作</option><option value="episodic">经历</option><option value="task">任务</option><option value="semantic">事实与偏好</option></select></label><label>来源<select id="fx-memory-source"><option value="">全部</option><option value="observed">直接观察</option><option value="model_inference">模型推断</option><option value="user_confirmed">用户确认</option></select></label><label class="check"><input id="fx-memory-candidates" type="checkbox">显示待审核候选（查看不会自动确认）</label><button>检索记忆</button></form><div id="fx-memory-items" class="cards"></div><div class="search-row"><button id="fx-memory-previous" disabled>上一页</button><button id="fx-memory-next" disabled>下一页</button></div><p id="fx-memory-read-meta"></p><pre id="fx-memory-body"></pre><div class="search-row"><button id="fx-memory-body-prev" disabled>正文上一页</button><button id="fx-memory-body-next" disabled>正文下一页</button></div><details><summary>来源覆盖与缺口</summary><pre id="fx-memory-coverage"></pre></details>`);
    $('fx-memory-search').addEventListener('submit', act(() => searchMemories(0)));
    $('fx-memory-previous').addEventListener('click', act(() => searchMemories(Math.max(0, memoryOffset - 10)))); $('fx-memory-next').addEventListener('click', act(() => searchMemories(Number($('fx-memory-next').dataset.offset))));
    $('fx-memory-body-prev').addEventListener('click', act(() => readMemory({...reading, first: Math.max(1, reading.first - 50)}))); $('fx-memory-body-next').addEventListener('click', act(() => readMemory({...reading, first: Number($('fx-memory-body-next').dataset.first)})));
    const budgets = detail($('page-settings'), '后台语义整理预算与 Memory owner 状态', `<form id="fx-budgets" class="form-grid"><label>每小时最多调用次数<input id="fx-budget-max_calls_per_hour" type="number" min="0" max="1000" required></label><label>每日 token 预算<input id="fx-budget-max_tokens_per_day" type="number" min="0" max="10000000" required></label><label>单次最多输出 tokens<input id="fx-budget-max_output_tokens" type="number" min="128" max="262144" required></label><button>保存预算</button></form><p class="muted">预算并不保证全天持续整理。关闭后台模型或达到预算会停止派发；已发送请求产生的费用无法撤回。</p><pre id="fx-memory-health"></pre>`);
    budgets.addEventListener('toggle', () => { if (budgets.open) loadBudgets().catch(error => message(error.message)); });
    $('fx-budgets').addEventListener('submit', act(async () => { const patch = Object.fromEntries(['max_calls_per_hour', 'max_tokens_per_day', 'max_output_tokens'].map(key => [key, Number($(`fx-budget-${key}`).value)])); await api.rpc('memory.configure', {patch}); await loadBudgets(); message('后台整理预算已保存。', true); }));
    setInterval(() => { if (document.visibilityState === 'visible' && $('page-activity').classList.contains('active')) loadActivity().catch(() => {}); }, 5000);
    return true;
  }
  if (!mount()) { const ready = setInterval(() => { if (mount()) clearInterval(ready); }, 100); window.addEventListener('pagehide', () => clearInterval(ready), {once: true}); }
})();
