/* Mirally settings contribution for the pinned native Control UI. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else if (root.top === root && root.location.origin === __MIRALLY_ORIGIN__ && root.chrome?.webview) {
    api.install(root);
  }
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const ACTIONS = new Set(['status', 'browse', 'monitor', 'memory-search', 'memory-read', 'start', 'pause', 'resume', 'stop', 'set-enabled', 'set-semantics', 'model-settings', 'set-semantic-model']);
  const CAPTURE_ACTIONS = new Set(['start', 'pause', 'resume', 'stop', 'set-enabled']);
  const STATUS_LABELS = {running: '运行中', ready: '已就绪', paused: '已暂停', stopped: '已停止',
    disabled: '已关闭', degraded: '部分采集源不可用', error: '异常', starting: '正在启动',
    not_ready: '尚未就绪', unavailable: '暂不可用', unknown: '未确认', active: '已保存', candidate: '待审核'};
  const KIND_LABELS = {episodic: '活动经历', semantic: '事实与线索', working: '近期工作'};
  const RETENTION_LABELS = {scheduled: '等待下次清理', waiting: '等待整理完成', waiting_memory: '等待记忆落库',
    blocked: '等待整理完成', running: '正在清理', cleaning: '正在清理', complete: '已完成', ready: '已完成',
    error: '清理遇到问题', degraded: '清理未完成，等待重试', not_ready: '等待后台运行',
    partial: '部分完成，等待下次清理', disabled: '已关闭', not_due: '等待下次清理'};
  const RETENTION_REASONS = {source_generation_changed: '活动来源已更换，旧批次清理已暂停，请检查来源配置。',
    memory_checkpoint_unavailable: '等待记忆整理进度保存后再清理。',
    asset_cleanup_pending: '部分截图或临时文件暂时无法删除，后台会重试。',
    raw_compaction_pending: '原始数据已删除，等待数据库释放空间。',
    projection_compaction_pending: '处理缓存已清理，等待数据库释放空间。',
    unmemorized_or_unstable_observations_retained: '尚未整理完成的记录已保留，后续清理会再次检查。'};
  const asText = value => value == null ? '' : String(value);
  const CAPTURE_LABELS = {'前台窗口元数据':'正在使用的应用', '输入操作元数据':'键鼠操作', 'UIA 结构':'窗口与控件',
    'OCR 正文':'画面中的文字', 'UIA 正文':'窗口中的文字', '剪贴板正文':'剪贴板内容'};
  const SEMANTIC_LABELS = {disabled:'已关闭',idle:'等待新活动',waiting:'等待整理',running:'正在总结',
    waiting_merge:'分片已整理，等待汇总',consolidation_blocked:'汇总受阻，等待处理',
    budget_exhausted:'等待本地整理预算恢复',host_unavailable:'模型服务暂不可用',retry_pending:'总结失败，等待重试',error:'总结遇到问题',
    stopped:'已停止',discarded_stale_source:'来源已变化，等待新活动'};
  const statusLabel = value => STATUS_LABELS[value] || '未确认';
  function semanticDraft(model, hour, day) {
    const maxCallsPerHour = Number(hour), maxTokensPerDay = Number(day);
    if (String(hour).trim() === '' || !Number.isInteger(maxCallsPerHour) || maxCallsPerHour < 0 || maxCallsPerHour > 10000) return {error:'每小时整理次数须为 0–10,000 的整数。'};
    if (String(day).trim() === '' || !Number.isInteger(maxTokensPerDay) || maxTokensPerDay < 0 || maxTokensPerDay > 10000000) return {error:'每日 tokens 上限须为 0–10,000,000 的整数。'};
    const value = asText(model).trim();
    if (value && (/\s/.test(value) || value.length > 512)) return {error:'请输入不含空格的完整模型 ID，或留空沿用对话模型。'};
    return {model:value,maxCallsPerHour,maxTokensPerDay};
  }
  function semanticLabel(memory, runtime) {
    if (!memory.semantics?.enabled) return '已关闭';
    const semantic = runtime.semantic || {};
    if (runtime.semantic_maintenance?.last_failure?.exhausted === true && [undefined,'idle','waiting','retry_pending'].includes(semantic.state)) return '总结重试已达上限';
    return SEMANTIC_LABELS[semantic.state] || '已开启，运行未确认';
  }
  const localTime = value => {
    if (value == null || value === '') return '';
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString() : '';
  };
  function semanticFeedback(memory, now = Date.now()) {
    const semantic=memory.runtime?.semantic || {}, maintenance=memory.runtime?.semantic_maintenance || {};
    const failure=maintenance.last_failure || {}, usage=maintenance.usage || {}, budget=memory.semantics || {};
    const next=maintenance.next_attempt_utc_ms || failure.next_attempt_utc_ms;
    const error=semantic.lastError || failure.error;
    const failedTime=localTime(failure.failed_at_utc_ms || semantic.lastAttemptUtcIso);
    const parseError=/expecting value|jsondecodeerror|unexpected end.*json|unexpected token.*json|json.*unexpected/i.test(asText(error));
    const explanation=parseError ? '模型未返回可用的记忆内容，响应可能为空或格式无法解析。原始错误可在详细信息与诊断查看。' : asText(error);
    const latest=error ? `最近一次失败（${failedTime || '时间未记录'}）：${explanation}${failure.exhausted === true ? ' 该次任务已达 3 次尝试上限，原始记录仍保留。' : ''}` : '无已报告的失败';
    const valid=value=>Number.isFinite(value) && value>=0;
    const hourReset=localTime((Math.floor(now/3600000)+1)*3600000), dayReset=localTime((Math.floor(now/86400000)+1)*86400000);
    const quota=(used,cap,unit,reset)=>`${valid(used) ? used.toLocaleString() : '未确认'} / ${valid(cap) ? cap.toLocaleString() : '未确认'} ${unit}${cap===0 ? '；上限为 0，需调整预算后继续' : `；${reset}（本地时间）恢复`}`;
    const usageText=`本小时调用已用或预留 ${quota(usage.requests_hourly,budget.max_calls_per_hour,'次',hourReset)}；本预算日 tokens 已用或预留 ${quota(usage.tokens_daily,budget.max_tokens_per_day,'tokens',dayReset)}。这是本地整理预算，不是服务商余额；按 UTC 小时/日期统计。`;
    const reasons=[];
    if (valid(usage.requests_hourly) && valid(budget.max_calls_per_hour) && usage.requests_hourly>=budget.max_calls_per_hour) reasons.push('每小时整理次数已达上限');
    if (valid(usage.tokens_daily) && valid(budget.max_tokens_per_day) && usage.tokens_daily>=budget.max_tokens_per_day) reasons.push('每日 tokens 已达上限');
    const wait=semantic.state==='budget_exhausted' ? `当前等待：${reasons.join('；') || '剩余本地预算不足以预留下一次整理'}。${localTime(next) ? `预计 ${localTime(next)}（本地时间）再尝试。` : '等待后台调度或调整预算。'}不代表服务商余额不足。` : '';
    return {latest,usage:usageText,wait};
  }
  function maintenanceLabel(memory, runtime) {
    if (memory.enabled === false || runtime.maintenance_status === 'disabled') return '已关闭';
    if (runtime.verified !== true) return '运行未确认';
    if (runtime.maintenance_status === 'not_ready') return '等待活动记录';
    if (runtime.maintenance_status === 'degraded' || runtime.maintenance_status === 'error') return '整理遇到问题';
    if (runtime.maintenance_status === 'other_writer') return '等待其他整理进程';
    if (runtime.maintenance_running === false) return '已关闭';
    if (runtime.maintenance_running === true) return '运行中';
    // Older hosts lack worker fields. Preserve their explicitly verified state.
    return statusLabel(runtime.status);
  }
  function isPage(location) {
    return /\/settings\/appearance\/?$/.test(location?.pathname || '') &&
      new URLSearchParams(location?.search || '').get('mirally') === 'activity';
  }
  function createClient(root, timeoutMs = 20000) {
    const pending = new Map();
    let sequence = 0;
    function request(action, params = {}) {
      if (!ACTIONS.has(action)) return Promise.reject(new Error('不支持的活动操作。'));
      const requestId = root.crypto?.randomUUID?.() || `activity-${Date.now()}-${++sequence}`;
      return new Promise((resolve, reject) => {
        // Native confirmation and collector shutdown can take time. A timeout
        // must not invite another capture control while an earlier one runs.
        const timer = CAPTURE_ACTIONS.has(action) || ['set-semantics', 'set-semantic-model'].includes(action) ? null : root.setTimeout(() => {
          pending.delete(requestId);
          reject(new Error('操作等待超时，请重试。'));
        }, timeoutMs);
        pending.set(requestId, {resolve, reject, timer});
        try {
          root.chrome.webview.postMessage({...params, type: 'mirally-activity', action, requestId});
        } catch (error) {
          if (timer !== null) root.clearTimeout(timer);
          pending.delete(requestId);
          reject(error);
        }
      });
    }
    function receive(message) {
      const item = pending.get(message?.requestId);
      if (!item) return false;
      pending.delete(message.requestId);
      if (item.timer !== null) root.clearTimeout(item.timer);
      if (message.error) item.reject(new Error(asText(message.error)));
      else item.resolve(message.result);
      return true;
    }
    function cancel() {
      for (const item of pending.values()) {
        if (item.timer !== null) root.clearTimeout(item.timer);
        item.reject(new Error('已离开活动与记忆设置。'));
      }
      pending.clear();
    }
    return {request, receive, cancel};
  }
  function statusView(result = {}) {
    const activity = result.activity || {}, memory = result.memory || {};
    const collector = activity.collector || {}, runtime = memory.runtime || {};
    const memoryHealth = memory.health?.store || memory.health || {};
    const retention = memory.raw_retention || {};
    const retentionEnabled = (retention.configured_enabled ?? retention.enabled) === true;
    const interval = retention.interval_seconds;
    const rawState = retention.state || retention.status;
    const lastCleanup = retention.last_cleanup_utc_ms || retention.last_success_utc_ms;
    const released = retention.reclaimed_bytes;
    return {
      activityEnabled: activity.error ? '配置读取失败' : !activity.configured ? activity.enabled === false ? '默认已关闭' : '默认已开启' : activity.enabled ? '已启用' : '已关闭',
      activityCurrent: activity.live_enabled ? '本次已开启' : '本次未采集',
      collector: collector.snapshot ? `最近记录状态 · ${statusLabel(collector.status)}` : statusLabel(collector.status),
      collectorNote: collector.snapshot ? '来自最近一次保存的采集记录，不代表采集器此刻仍在运行。' : '以当前采集状态为准。',
      history: activity.error ? '暂不可用' : activity.database_exists ? '已有本机历史' : '尚无本机历史',
      level: {content: '正文与画面', structure: '窗口与控件结构', metadata: '基础元数据', off: '关闭'}[activity.collection_level] || '未确认',
      memoryEnabled: memory.error ? '配置读取失败' : !memory.configured ? memory.enabled === false ? '默认已关闭' : '默认已开启' : memory.enabled ? '已启用' : '已关闭',
      memoryRuntime: maintenanceLabel(memory,runtime),
      semantics: semanticLabel(memory,runtime),
      activeCount: Number.isFinite(memoryHealth.active_count) ? String(memoryHealth.active_count) : '—',
      candidateCount: Number.isFinite(memoryHealth.statuses?.candidate) ? String(memoryHealth.statuses.candidate) : '—',
      retentionInterval: retentionEnabled && Number.isFinite(interval) ? interval === 3600 ? '每小时' : `每 ${interval / 60} 分钟` : retentionEnabled ? '每小时' : '已关闭',
      retentionState: !retentionEnabled ? '已关闭' : !memory.enabled ? '等待记忆启用' : RETENTION_LABELS[rawState] || '等待后台运行',
      retentionLast: Number.isFinite(lastCleanup) ? `上次清理：${new Date(lastCleanup).toLocaleString()}` : '尚无已完成的清理记录',
      retentionReleased: Number.isFinite(released) && (Number.isFinite(lastCleanup) || ['cleaning', 'running', 'degraded'].includes(rawState))
        ? `${['cleaning', 'running', 'degraded'].includes(rawState) ? '本次' : '上次'}实际释放 ${(released / (1024 * 1024)).toFixed(1)} MiB` : '',
      retentionWarning: RETENTION_REASONS[retention.blocked_reason] || (retention.error ? '清理遇到问题，后台会重试；可在高级信息查看诊断。' : ''),
    };
  }
  function preserveOutlet(outlet) {
    const saved = {hidden: outlet.hidden, inert: outlet.inert, ariaHidden: outlet.getAttribute('aria-hidden')};
    if (!outlet.hidden) outlet.hidden = true;
    if (!outlet.inert) outlet.inert = true;
    if (outlet.getAttribute('aria-hidden') !== 'true') outlet.setAttribute('aria-hidden', 'true');
    return () => {
      outlet.hidden = saved.hidden;
      outlet.inert = saved.inert;
      if (saved.ariaHidden === null) outlet.removeAttribute('aria-hidden');
      else outlet.setAttribute('aria-hidden', saved.ariaHidden);
    };
  }
  function install(root) {
    if (root.__mirallyActivity) return root.__mirallyActivity;
    const document = root.document, client = createClient(root), modelClient = createClient(root), browserClient = createClient(root);
    const outlets = new Map();
    let panel, entry, visible = false, epoch = 0, frame = null, refreshEpoch = 0;
    let lastStatus, presentationCache, subscribedContext, stopRouter, controlling = false, semanticSaving = false, semanticDirty = false;
    const $ = selector => panel?.querySelector(selector);
    const set = (selector, text) => { const node = $(selector); if (node && node.textContent !== asText(text)) node.textContent = asText(text); };
    const showError = value => {
      set('[data-slot="error"]', value || '');
      const node = $('[data-slot="error"]');
      if (node) node.hidden = !value;
    };
    const context = () => document.querySelector('openclaw-app')?.context;
    function open() {
      const native = context();
      if (!native?.navigate) return false;
      root.__mirallyWork?.close?.();
      // The legacy `config` route redirects to language settings and discards
      // its query. The canonical appearance loader preserves our marker.
      native.navigate('appearance', {search: '?mirally=activity'});
      schedule();
      return true;
    }
    function renderStatus(result, authoritative = true) {
      if (authoritative) {
        lastStatus = result;
        presentationCache = {context: context(), result, updatedAt: Date.now()};
      }
      const view = statusView(result), activity = result?.activity || {}, memory = result?.memory || {};
      for (const [name, text] of Object.entries(view)) set(`[data-status="${name}"]`, text);
      const timestamp = activity.collector?.updated_at_utc_ms;
      set('[data-slot="collector-time"]', Number.isFinite(timestamp) ? `记录时间：${new Date(timestamp).toLocaleString()}` : '');
      const capture = $('[data-slot="capture"]');
      capture.replaceChildren();
      for (const source of Array.isArray(activity.capture) ? activity.capture : []) {
        const span = document.createElement('span');
        span.className = `mirally-activity__capture-source${source.enabled ? ' is-enabled' : ''}`;
        span.textContent = `${CAPTURE_LABELS[source.label] || asText(source.label)} · ${source.enabled ? '开启' : '关闭'}`;
        capture.append(span);
      }
      $('[data-action="browse"]').disabled = !activity.database_exists || Boolean(activity.error);
      $('[data-action="monitor"]').disabled = !activity.database_exists || Boolean(activity.error);
      $('[data-action="open-memory"]').disabled = false;
      for (const action of ['start', 'pause', 'resume', 'stop']) {
        const button = $(`[data-action="${action}"]`);
        const available = activity.controls?.[action] === true && (action !== 'start' || activity.enabled === true);
        button.hidden = !available;
        button.disabled = controlling || !available;
      }
      const toggle = $('[data-action="set-enabled"]');
      toggle.setAttribute('aria-checked', activity.enabled === true ? 'true' : 'false');
      toggle.disabled = controlling || activity.controls?.['set-enabled'] !== true;
      set('[data-slot="preference-note"]', (activity.enabled === true ? '每次打开 Mirally 自动开始记录。暂停只影响本次，关闭开关会保存为关闭。' : '活动记录已关闭；打开开关后开始记录，并在下次启动时自动开启。') + (!activity.configured && !activity.error ? ' 使用默认记录设置。' : ''));
      const live = activity.collector?.snapshot === false && ['running', 'degraded', 'paused'].includes(activity.collector?.status);
      set('[data-slot="monitor-note"]', live ? '活动历史只查看已有记录；网页版监测可查看本次采集并暂停、恢复或停止。' : '在默认浏览器查看本机记录、截图和采集诊断；两个查看入口都不会启动采集器。');
      set('[data-slot="capture-note"]', activity.scope_note || '本机已配置的采集来源与内容级别。');
      set('[data-slot="activity-warning"]', [activity.error, activity.warning, activity.auto_start_error,
        activity.collector?.pause_reason === 'semantic_memory_pending_storage_quota' ? '原始记录存储已满，活动采集已暂停。完成语义整理并清理释放空间后，请点击继续。' : '',
        activity.config_changed ? '本机配置已变化，本次采集继续使用启动时的范围。停止后重新开始，可应用新范围。' : '',
        !activity.database_exists ? '产生记录后，可查看活动历史与网页版监测。' : ''].filter(Boolean).join('\n'));
      set('[data-slot="profile-warning"]', result?.profiles?.warning || '');
      set('[data-slot="memory-warning"]', [memory.error, memory.runtime?.error,
        memory.enabled === false ? '活动记忆当前关闭；开启后可检索已整理保存的记忆。' : '',
        memory.source_matches_activity === false ? '活动记忆目前读取另一份活动来源，可在高级信息查看来源配置。' : ''].filter(Boolean).join('\n'));
      set('[data-slot="retention-warning"]', view.retentionWarning);
      const budgets = memory.semantics || {};
      const budgetText = [Number.isFinite(budgets.max_calls_per_hour) ? `每小时最多 ${budgets.max_calls_per_hour} 次整理` : '',
        Number.isFinite(budgets.max_tokens_per_day) ? `每日模型用量上限 ${budgets.max_tokens_per_day.toLocaleString()} tokens` : ''].filter(Boolean).join('；');
      set('[data-slot="semantic-budget"]', budgetText ? `整理预算：${budgetText}。` : '');
      set('[data-slot="semantic-description"]', budgets.enabled ? '使用模型理解活动内容，提炼事实与线索。' : '目前只做本地整理，不调用模型生成总结。');
      const semantic = memory.runtime?.semantic || {}, maintenance = memory.runtime?.semantic_maintenance || {};
      const semanticToggle = $('[data-action="set-semantics"]');
      semanticToggle.setAttribute('aria-checked', budgets.enabled === true ? 'true' : 'false');
      const canSaveSemantics = memory.controls?.['set-semantics'] === true;
      semanticToggle.disabled = semanticSaving || !canSaveSemantics;
      $('[data-action="save-semantics"]').disabled = semanticSaving || !canSaveSemantics;
      for (const input of panel.querySelectorAll('[data-semantic-field]')) input.disabled = semanticSaving || !canSaveSemantics;
      if (!semanticDirty) {
        $('[data-semantic-field="model"]').value = budgets.model || '';
        $('[data-semantic-field="hour"]').value = Number.isFinite(budgets.max_calls_per_hour) ? budgets.max_calls_per_hour : 24;
        $('[data-semantic-field="day"]').value = Number.isFinite(budgets.max_tokens_per_day) ? budgets.max_tokens_per_day : 60000;
      }
      set('[data-slot="semantic-model"]', budgets.model ? `指定模型：${budgets.model}` : `沿用对话模型${budgets.effective_model ? `：${budgets.effective_model}` : semantic.lastModel ? `（最近使用 ${semantic.lastProvider ? semantic.lastProvider + '/' : ''}${semantic.lastModel}）` : ''}`);
      const timeLabel = value => value ? new Date(value).toLocaleString() : '尚无记录';
      set('[data-slot="semantic-success"]', timeLabel(semantic.lastSuccessUtcIso));
      set('[data-slot="semantic-pending"]', Number.isFinite(maintenance.pending_windows) ? `${maintenance.pending_windows} 个窗口` : '运行未确认');
      const lastFailure = maintenance.last_failure || {}, nextAttempt = maintenance.next_attempt_utc_ms || lastFailure.next_attempt_utc_ms;
      set('[data-slot="semantic-next"]', nextAttempt ? `${timeLabel(nextAttempt)}（本地时间）` : view.semantics === '总结重试已达上限' ? '已达尝试上限，等待处理' : '等待新活动或后台调度');
      const feedback=semanticFeedback(memory);
      set('[data-slot="semantic-failure"]',feedback.latest);
      set('[data-slot="semantic-usage"]',feedback.usage);
      set('[data-slot="semantic-wait"]',feedback.wait);
      set('[data-slot="activity-config"]', activity.config_path || '—');
      set('[data-slot="activity-database"]', activity.database || '—');
      set('[data-slot="memory-config"]', memory.config_path || '—');
      set('[data-slot="memory-database"]', memory.database || '—');
      set('[data-slot="memory-source-config"]', memory.activity_config_path || '—');
      set('[data-slot="memory-source-database"]', memory.activity_database || '—');
      set('[data-slot="diagnostics"]', JSON.stringify({activity: {health: activity.health, diagnostics: activity.diagnostics}, memory: {health: memory.health, runtime: memory.runtime}}, null, 2));
    }
    async function refresh() {
      if (!visible) return;
      const scope = epoch, request = ++refreshEpoch;
      const button = $('[data-action="refresh"]');
      button.disabled = true;
      showError('');
      set('[data-slot="receipt"]', !lastStatus && presentationCache?.context === context()
        ? `上次状态（${new Date(presentationCache.updatedAt).toLocaleTimeString()}），正在更新…` : '正在读取本机状态…');
      try {
        const result = await client.request('status');
        if (!visible || scope !== epoch || request !== refreshEpoch) return;
        renderStatus(result);
        set('[data-slot="receipt"]', '状态已更新');
        return true;
      } catch (error) {
        if (visible && scope === epoch && request === refreshEpoch) { showError(error.message); set('[data-slot="receipt"]', !lastStatus && presentationCache?.context === context() ? '上次状态，更新失败；操作暂不可用，请刷新重试' : '状态读取失败'); }
        return false;
      } finally { if (visible && scope === epoch && request === refreshEpoch) button.disabled = controlling; }
    }
    async function launch(action) {
      if (!visible || !lastStatus || !['browse', 'monitor'].includes(action)) return;
      const scope = epoch, button = $(`[data-action="${action}"]`);
      button.disabled = true;
      showError('');
      set('[data-slot="receipt"]', '正在打开本机页面…');
      try {
        await client.request(action);
        if (visible && scope === epoch) set('[data-slot="receipt"]', '已在浏览器打开');
      } catch (error) {
        if (visible && scope === epoch) { showError(error.message); set('[data-slot="receipt"]', '页面暂未打开'); }
      } finally { if (visible && scope === epoch) button.disabled = false; }
    }
    async function control(action) {
      if (!visible || controlling || !['start', 'pause', 'resume', 'stop'].includes(action) || lastStatus?.activity?.controls?.[action] !== true || (action === 'start' && lastStatus?.activity?.enabled !== true)) return;
      const sessionId = lastStatus?.activity?.session_id;
      if (action !== 'start' && (typeof sessionId !== 'string' || !sessionId)) {
        showError('采集会话尚未确认，请刷新状态后重试。');
        return;
      }
      const scope = epoch;
      controlling = true;
      refreshEpoch += 1;
      $('[data-action="refresh"]').disabled = true;
      for (const item of ['start', 'pause', 'resume', 'stop']) $(`[data-action="${item}"]`).disabled = true;
      showError('');
      set('[data-slot="receipt"]', '正在更新采集状态…');
      try {
        const result = await client.request(action, action === 'start' ? {} : {session_id: sessionId});
        if (!visible || scope !== epoch) return;
        if (result?.cancelled) {
          set('[data-slot="receipt"]', '未开始采集');
          renderStatus(lastStatus);
        } else await refresh();
      } catch (error) {
        if (visible && scope === epoch) { showError(error.message); renderStatus(lastStatus); set('[data-slot="receipt"]', '采集状态未能更新'); }
      } finally { controlling = false; if (visible && scope === epoch && lastStatus) { renderStatus(lastStatus); $('[data-action="refresh"]').disabled = false; } }
    }
    async function toggleEnabled() {
      const state = lastStatus?.activity;
      if (!visible || controlling || state?.controls?.['set-enabled'] !== true) return;
      const scope = epoch, enabled = state.enabled !== true;
      controlling = true;
      refreshEpoch += 1;
      $('[data-action="refresh"]').disabled = true;
      renderStatus(lastStatus);
      showError('');
      set('[data-slot="receipt"]', enabled ? '正在开启活动记录…' : '正在关闭活动记录…');
      try {
        const result = await client.request('set-enabled', {enabled});
        if (!visible || scope !== epoch) return;
        if (result?.cancelled) {
          set('[data-slot="receipt"]', '活动记录设置未更改');
          return;
        }
        if (result?.saved !== true) {
          showError('未能确认活动记录设置已保存，请刷新后重试。');
          set('[data-slot="receipt"]', '活动记录设置未能保存');
          return;
        }
        const refreshed = await refresh();
        if (visible && scope === epoch) {
          if (result?.capture_error) { showError(result.capture_error); set('[data-slot="receipt"]', '设置已保存，但采集状态未能更新'); }
          else if (!refreshed) set('[data-slot="receipt"]', '设置已保存，但刷新失败，请重新读取状态');
          else set('[data-slot="receipt"]', '活动记录设置已保存');
        }
      } catch (error) {
        if (visible && scope === epoch) { showError(error.message); set('[data-slot="receipt"]', '活动记录设置未能保存'); }
      } finally { controlling = false; if (visible && scope === epoch && lastStatus) { renderStatus(lastStatus); $('[data-action="refresh"]').disabled = false; } }
    }
    async function saveSemantics(toggle = false) {
      if (!visible || semanticSaving || lastStatus?.memory?.controls?.['set-semantics'] !== true) return;
      const enabled = lastStatus?.memory?.semantics?.enabled === true;
      const draft = toggle ? {enabled:!enabled} : {...semanticDraft($('[data-semantic-field="model"]').value,
        $('[data-semantic-field="hour"]').value, $('[data-semantic-field="day"]').value), enabled};
      if (draft.error) { showError(draft.error); return; }
      const scope = epoch;
      refreshEpoch += 1;
      semanticSaving = true;
      renderStatus(lastStatus);
      showError('');
      set('[data-slot="semantic-receipt"]', '正在保存模型语义整理设置…');
      try {
        const result = await client.request('set-semantics', draft);
        if (!visible || scope !== epoch) return;
        if (result?.saved !== true) { showError('未能确认模型语义整理设置已保存，请刷新后重试。'); set('[data-slot="semantic-receipt"]', '设置未能保存'); return; }
        if (!toggle) semanticDirty = false;
        const refreshed = await refresh();
        if (!visible || scope !== epoch) return;
        if (result.applied !== true) set('[data-slot="semantic-receipt"]', `设置已保存，后台尚未应用${result.reload_error ? `：${result.reload_error}` : '；请启动或重新连接后台后刷新。'}`);
        else set('[data-slot="semantic-receipt"]', refreshed ? '模型语义整理设置已保存并应用' : '设置已保存并应用，但状态刷新失败');
      } catch (error) {
        if (visible && scope === epoch) { showError(error.message); set('[data-slot="semantic-receipt"]', '模型语义整理设置未能保存'); }
      } finally {
        if (visible && scope === epoch) { semanticSaving = false; if (lastStatus) renderStatus(lastStatus); $('[data-action="refresh"]').disabled = controlling; }
      }
    }
    function createPanel() {
      panel = document.createElement('section');
      panel.className = 'mirally-activity settings-workspace';
      panel.setAttribute('aria-label', '活动与记忆');
      panel.innerHTML = `
        <div class="settings-workspace__body mirally-activity__body">
          <header class="mirally-activity__header"><div><h1>活动与记忆</h1><p>记录电脑上的活动，在需要时找回相关经历。</p></div><button class="btn" type="button" data-action="refresh">刷新</button></header>
          <p class="mirally-activity__receipt" role="status" aria-live="polite" data-slot="receipt"></p>
          <p class="mirally-activity__warning" data-slot="profile-warning"></p>
          <p class="mirally-activity__error" role="alert" data-slot="error" hidden></p>
          <section class="settings-section mirally-activity__section mirally-activity__recording"><div class="settings-row mirally-activity__row mirally-activity__main-toggle"><div class="settings-row__text"><h2 class="settings-section__heading">活动记录</h2><div class="settings-row__desc">按下方范围记录活动；暂停后可随时继续。</div></div><button class="mirally-activity__switch" type="button" role="switch" aria-checked="false" aria-label="活动记录" data-action="set-enabled" disabled><span></span></button></div>
            <div class="mirally-activity__running"><strong data-status="collector">—</strong><div class="mirally-activity__capture-controls"><button class="btn" type="button" data-action="start" hidden>重新开始</button><button class="btn" type="button" data-action="pause" hidden>暂停</button><button class="btn" type="button" data-action="resume" hidden>继续</button><button class="btn" type="button" data-action="stop" hidden>停止本次</button></div></div>
            <p class="mirally-activity__muted" data-status="collectorNote"></p><p class="mirally-activity__muted" data-slot="preference-note"></p>
            <p class="mirally-activity__warning" data-slot="activity-warning"></p>
            <div class="mirally-activity__actions mirally-activity__links"><button class="btn" type="button" data-action="browse" disabled>活动历史 ↗</button><button class="btn" type="button" data-action="monitor" disabled>网页版监测 ↗</button></div>
            <div class="mirally-activity__sources"><div class="mirally-activity__section-label">记录内容 <span data-status="level">—</span></div><div class="mirally-activity__capture" data-slot="capture"></div></div>
          </section>
          <section class="settings-section mirally-activity__section"><div class="settings-section__header"><h2 class="settings-section__heading">活动记忆</h2></div>
            <p class="settings-section__desc">把活动整理为可查找的经历与事实。已有记忆会保留，原始记录定期清理。</p>
            <div class="mirally-activity__memory-summary"><div><span>已保存记忆</span><strong data-status="activeCount">—</strong></div><div><span>本地整理</span><strong data-status="memoryRuntime">—</strong></div><div><span>模型总结</span><strong data-status="semantics">—</strong></div></div>
            <p class="mirally-activity__muted" data-slot="semantic-description"></p>
            <p class="mirally-activity__warning" data-slot="memory-warning"></p>
            <div class="mirally-activity__semantic-settings">
              <div class="settings-row mirally-activity__row"><div class="settings-row__text"><div class="settings-row__title">模型语义整理</div><div class="settings-row__desc">每 5 分钟整理活动窗口，将文字内容发送到配置的模型，提炼总结与事实。独立于活动采集开关。</div></div><button class="mirally-activity__switch" type="button" role="switch" aria-checked="false" aria-label="模型语义整理" data-action="set-semantics" disabled><span></span></button></div>
              <p class="mirally-activity__muted" data-slot="semantic-model">沿用对话模型</p>
              <form data-slot="semantic-form"><div class="mirally-activity__semantic-fields"><label>整理模型<input data-semantic-field="model" maxlength="512" autocomplete="off" placeholder="留空沿用对话模型" disabled></label><label>每小时最多整理次数<input data-semantic-field="hour" type="number" min="0" max="10000" step="1" value="24" disabled></label><label>每日 tokens 上限<input data-semantic-field="day" type="number" min="0" max="10000000" step="1" value="60000" disabled></label></div><p class="mirally-activity__muted">填写完整模型 ID，例如提供商/模型；0 表示没有可用预算，未完成的窗口会等待。调整后保存整理设置，可重新尝试失败任务，仍受预算限制。</p><button class="btn" type="submit" data-action="save-semantics" disabled>保存整理设置</button></form>
              <p class="mirally-activity__muted" data-slot="semantic-budget"></p>
              <p class="mirally-activity__muted" data-slot="semantic-usage">用量暂未确认（UTC 预算统计）</p>
              <p class="mirally-activity__muted" data-slot="semantic-wait"></p><dl class="mirally-activity__semantic-status"><dt>最近成功</dt><dd data-slot="semantic-success">尚无记录</dd><dt>待整理</dt><dd data-slot="semantic-pending">运行未确认</dd><dt>下次尝试</dt><dd data-slot="semantic-next">运行未确认</dd><dt>最近一次失败</dt><dd data-slot="semantic-failure">运行未确认</dd></dl>
              <p class="mirally-activity__receipt" role="status" aria-live="polite" data-slot="semantic-receipt"></p>
            </div>
            <div class="settings-row mirally-activity__row"><div class="settings-row__text"><div class="settings-row__title">原始数据清理</div><div class="settings-row__desc">记忆保存完成后，删除对应的原始记录、截图及处理缓存。尚未整理完成的内容继续保留。</div></div><div class="settings-row__control" data-status="retentionInterval">—</div></div>
            <p class="mirally-activity__muted"><span data-status="retentionState">—</span> · <span data-status="retentionLast">—</span><br><span data-status="retentionReleased"></span></p>
            <p class="mirally-activity__warning" data-slot="retention-warning"></p>
            <div class="mirally-activity__actions"><button class="btn" type="button" data-action="open-memory">打开记忆界面</button></div>
            <p class="mirally-activity__muted">在智能体的记忆页查看模型总结、用户确认和规则观测，搜索正文并追溯来源；这里仅管理采集与整理。</p>
          </section>
          <details class="mirally-activity__advanced"><summary>详细信息与诊断</summary><dl><dt>记录配置</dt><dd data-status="activityEnabled">—</dd><dt>本次采集</dt><dd data-status="activityCurrent">—</dd><dt>活动历史</dt><dd data-status="history">—</dd><dt>采集范围</dt><dd data-slot="capture-note">—</dd><dt>状态时间</dt><dd data-slot="collector-time">—</dd><dt>记忆维护</dt><dd data-status="memoryEnabled">—</dd><dt>待审核记忆</dt><dd data-status="candidateCount">—</dd><dt>活动配置</dt><dd data-slot="activity-config">—</dd><dt>活动数据库</dt><dd data-slot="activity-database">—</dd><dt>记忆配置</dt><dd data-slot="memory-config">—</dd><dt>记忆数据库</dt><dd data-slot="memory-database">—</dd><dt>记忆来源配置</dt><dd data-slot="memory-source-config">—</dd><dt>记忆来源数据库</dt><dd data-slot="memory-source-database">—</dd></dl><p data-slot="monitor-note"></p><pre data-slot="diagnostics"></pre></details>
        </div>`;
      $('[data-action="refresh"]').addEventListener('click', () => { if (!controlling) refresh(); });
      $('[data-action="set-enabled"]').addEventListener('click', toggleEnabled);
      $('[data-action="set-semantics"]').addEventListener('click', () => saveSemantics(true));
      $('[data-slot="semantic-form"]').addEventListener('submit', event => { event.preventDefault(); saveSemantics(); });
      for (const input of panel.querySelectorAll('[data-semantic-field]')) input.addEventListener('input', () => { semanticDirty = true; set('[data-slot="semantic-receipt"]', '有未保存的整理设置'); });
      for (const action of ['browse', 'monitor']) $(`[data-action="${action}"]`).addEventListener('click', () => launch(action));
      for (const action of ['start', 'pause', 'resume', 'stop']) $(`[data-action="${action}"]`).addEventListener('click', () => control(action));
      $('[data-action="open-memory"]').addEventListener('click',()=>{
        const native=context();
        if(native?.navigate)native.navigate('memory',{pathname:`${native.basePath || ''}/settings/memory/memories`,search:''});
      });
    }
    function mountEntry() {
      const nav = document.querySelector('.settings-sidebar__nav');
      if (!nav) return;
      if (!entry) {
        entry = document.createElement('div');
        entry.className = 'settings-sidebar__group mirally-activity__nav-group';
        entry.innerHTML = '<div class="settings-sidebar__group-label">Mirally</div><a class="settings-sidebar__item mirally-activity__entry"><span class="settings-sidebar__item-icon" aria-hidden="true">◷</span><span class="settings-sidebar__item-label">活动与记忆</span></a>';
        entry.querySelector('a').addEventListener('click', event => {
          if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          open();
        });
      }
      const base = context()?.basePath || '';
      const link = entry.querySelector('a');
      const href = `${base}/settings/appearance?mirally=activity`;
      if (link.getAttribute('href') !== href) link.setAttribute('href', href);
      link.classList.toggle('settings-sidebar__item--active', visible);
      if (visible && link.getAttribute('aria-current') !== 'page') link.setAttribute('aria-current', 'page');
      else if (!visible && link.hasAttribute('aria-current')) link.removeAttribute('aria-current');
      if (entry.parentNode !== nav) nav.prepend(entry);
    }
    function sync() {
      frame = null;
      const native = context();
      if (native !== subscribedContext) {
        if (subscribedContext) { modelClient.cancel(); browserClient.cancel(); }
        presentationCache = null;
        if (visible) {
          visible = false; epoch += 1;
          client.cancel(); panel?.remove(); panel = null; lastStatus = null;
        }
        stopRouter?.();
        subscribedContext = native;
        stopRouter = native?.router?.subscribe?.(schedule);
      }
      const next = isPage(root.location), main = document.querySelector('#control-ui-main');
      if (next && main) {
        if (!panel) createPanel();
        const outlet = main.querySelector('openclaw-router-outlet');
        if (outlet && !outlets.has(outlet)) outlets.set(outlet, preserveOutlet(outlet));
        if (outlet && !outlet.hidden) outlet.hidden = true;
        if (panel.parentNode !== main) main.append(panel);
        if (!visible) {
          visible = true;
          epoch += 1;
          lastStatus = null;
          semanticSaving = false; semanticDirty = false;
          for (const node of panel.querySelectorAll('[data-status]')) node.textContent = '—';
          if (presentationCache?.context === native) renderStatus(presentationCache.result, false);
          for (const action of ['start', 'pause', 'resume', 'stop']) {
            $(`[data-action="${action}"]`).hidden = true;
            $(`[data-action="${action}"]`).disabled = true;
          }
          $('[data-action="set-enabled"]').disabled = true;
          for (const action of ['browse', 'monitor']) $(`[data-action="${action}"]`).disabled = true;
          // Cached presentation never grants permission to operate a collector
          // or save settings. Only this visit's status response can authorize it.
          for (const node of panel.querySelectorAll('button, input, select, textarea')) node.disabled = true;
          $('[data-action="refresh"]').disabled = false;
          refresh();
        }
      } else if (visible) {
        visible = false;
        epoch += 1;


        client.cancel();
        panel?.remove();
        panel = null;
        lastStatus = null;
        for (const restore of outlets.values()) restore();
        outlets.clear();
      }
      const html = document.documentElement;
      if (visible && !html.hasAttribute('data-mirally-activity')) html.setAttribute('data-mirally-activity', '');
      else if (!visible && html.hasAttribute('data-mirally-activity')) html.removeAttribute('data-mirally-activity');
      mountEntry();
    }
    function schedule() { if (frame === null) frame = root.requestAnimationFrame(sync); }
    const observer = new root.MutationObserver(schedule);
    observer.observe(document, {childList: true, subtree: true});
    root.addEventListener('popstate', schedule);
    root.addEventListener('pagehide', () => {
      observer.disconnect();
      stopRouter?.();
      if (frame !== null) root.cancelAnimationFrame(frame);
      client.cancel();
      modelClient.cancel(); browserClient.cancel();
    });
    root.__mirallyActivity = {open, receive: message => client.receive(message) || modelClient.receive(message) || browserClient.receive(message), refresh,
      memoryBrowser: {
        search: (params = {}) => {
          const allowed=['query','include_candidates','limit','offset','provenances','kinds','start_utc_ms','end_utc_ms'];
          return browserClient.request('memory-search',Object.fromEntries(allowed.filter(key=>Object.hasOwn(params,key)).map(key=>[key,params[key]])));
        },
        read: ({memory_id,from_line=1,line_count=100} = {}) => browserClient.request('memory-read',{memory_id,from_line,line_count}),
        cancel: browserClient.cancel,
      },
      modelSettings: {
        read: () => modelClient.request('model-settings'),
        save: (values = {}) => {
          if(!values || typeof values !== 'object')return Promise.reject(new Error('后台整理模型或输出上限无效。'));
          const hasModel=Object.hasOwn(values,'model'), hasMax=Object.hasOwn(values,'maxOutputTokens');
          const {model,maxOutputTokens}=values;
          if ((!hasModel && !hasMax) || (hasModel && (typeof model !== 'string' || /\s/.test(model) || model.length > 512)) ||
              (hasMax && (!Number.isInteger(maxOutputTokens) || maxOutputTokens < 128 || maxOutputTokens > 16000))) {
            return Promise.reject(new Error('后台整理模型或输出上限无效。'));
          }
          return modelClient.request('set-semantic-model', {...hasModel ? {model} : {}, ...hasMax ? {maxOutputTokens} : {}});
        },
        cancel: modelClient.cancel,
      }};
    schedule();
    return root.__mirallyActivity;
  }
  return {ACTIONS, isPage, createClient, statusView, semanticDraft, semanticFeedback, preserveOutlet, install};
});
