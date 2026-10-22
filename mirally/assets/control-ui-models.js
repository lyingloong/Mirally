/* Model service settings contribution; uses the native configuration queue. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else if (root.top === root && root.location.origin === __MIRALLY_ORIGIN__ && root.chrome?.webview) api.install(root);
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const FORMATS = ['openai-completions', 'openai-responses', 'anthropic-messages'];
  const THINKING_FORMATS = ['openai','openrouter','deepseek','together','qwen','qwen-chat-template','zai'];
  const THINKING_LEVELS = ['off','minimal','low','medium','high','xhigh','adaptive','max','ultra'];
  const NEW = '__new__';
  const safeProvider = id => /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(id) && !['__proto__','constructor','prototype'].includes(id);
  const clone = value => JSON.parse(JSON.stringify(value));
  const isPage = location => /\/(?:settings\/)?model-providers\/?$/i.test(location?.pathname || '');
  function configOf(runtime) {
    const snapshot = runtime?.state?.configSnapshot;
    return snapshot?.sourceConfig ?? snapshot?.config ?? snapshot?.resolved ?? null;
  }
  async function refreshSnapshot(runtime, options = {}) {
    let applied = false;
    // Native refresh resolves void even when a read is superseded or skipped.
    // This hook confirms that this request actually adopted a snapshot.
    const result = await runtime.refresh({...options, beforeApplySnapshot: () => { applied = true; }});
    return applied && result !== false && runtime.state.connected === true && !runtime.state.lastError && !!configOf(runtime);
  }
  function primary(config) {
    const model = config?.agents?.defaults?.model;
    return typeof model === 'string' ? model : model?.primary || '';
  }
  function fingerprint(config, provider) {
    const defaults = config?.agents?.defaults;
    return JSON.stringify([config?.models?.providers?.[provider] ?? null, primary(config), defaults?.thinkingDefault, defaults?.utilityModel, defaults?.subagents]);
  }
  function validate(draft) {
    if (!safeProvider(draft.provider)) return '提供商名称请使用字母、数字、短横线或下划线。';
    let url;
    try { url = new URL(draft.baseUrl); } catch { return '请输入完整的服务地址。'; }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return '服务地址须使用 HTTP 或 HTTPS，不能包含密钥、查询参数或片段。';
    if (!FORMATS.includes(draft.api)) return '请选择支持的接口格式。';
    const endpoint = /\/(?:responses|chat\/completions|v1\/messages)\/?$/i;
    if (endpoint.test(url.pathname)) {
      const base = url.origin + url.pathname.replace(endpoint, '');
      return `请填写 API 基础地址 ${base}；程序会自动追加接口路径，不能填写完整操作地址。`;
    }
    if (!draft.modelId?.trim() || /\s/.test(draft.modelId) || draft.modelId.split('/').some(part => !part)) return '请输入完整、不含空格的模型 ID。';
    if (draft.isNewProvider && !draft.apiKey?.trim()) return '新增提供商需要填写 API Key。';
    if (draft.thinkingFormatDirty && draft.thinkingFormat && !THINKING_FORMATS.includes(draft.thinkingFormat)) return '请选择支持的推理请求格式。';
    if (draft.maxTokensDirty && (!Number.isSafeInteger(Number(draft.maxTokens)) || Number(draft.maxTokens) <= 0)) return '模型最大输出须为正整数 tokens。';
    if (draft.thinkingDirty && !THINKING_LEVELS.includes(draft.thinkingDefault)) return '请选择支持的默认思考级别。';
    if (draft.utilityDirty && draft.utilityModel && (/\s/.test(draft.utilityModel) || !draft.utilityModel.includes('/') || draft.utilityModel.split('/').some(part=>!part))) return '轻量辅助模型请填写完整的提供商/模型 ID，或留空沿用主模型。';
    if (draft.utilityDirty && draft.utilityMode === 'custom' && !draft.utilityModel?.trim()) return '请填写指定的轻量辅助模型，或选择沿用主模型。';
    return null;
  }
  const references = (value, ref) => typeof value === 'string' && (value === ref || value.startsWith(ref + '@'));
  function otherReference(config, ref) {
    function walk(value, path) {
      if (typeof value === 'string') return references(value, ref) && path !== 'agents.defaults.model.primary' && path !== 'agents.defaults.model';
      return value && typeof value === 'object' && Object.entries(value).some(([key,item]) => references(key, ref) || walk(item, path ? `${path}.${key}` : key));
    }
    return ['agents', 'channels', 'tools', 'hooks'].some(key => walk(config?.[key], key));
  }
  function hasRedactedValue(value) {
    return value === '__OPENCLAW_REDACTED__' || (value && typeof value === 'object' && Object.values(value).some(hasRedactedValue));
  }
  function buildPatch(config, draft, baseline) {
    const error = validate(draft);
    if (error) return {error};
    if (fingerprint(config, draft.provider) !== baseline) return {error:'配置已被其他操作修改，请重新加载后再保存。'};
    const existing = config?.models?.providers?.[draft.provider];
    if (draft.isNewProvider && existing) return {error:'此提供商已存在，请重新加载后选择。'};
    if (!draft.isNewProvider && !existing) return {error:'此提供商已被移除，请重新加载。'};
    const models = clone(existing?.models || []);
    const index = draft.originalId == null ? -1 : models.findIndex(model => model.id === draft.originalId);
    if (draft.originalId != null && index < 0) return {error:'此模型已被修改，请重新加载。'};
    const id = draft.modelId.trim();
    if (models.some((model,i) => model.id === id && i !== index)) return {error:'这个模型 ID 已存在，请选择已有模型。'};
    const oldRef = draft.originalId == null ? null : `${draft.provider}/${draft.originalId}`;
    const newRef = `${draft.provider}/${id}`;
    if (oldRef && oldRef !== newRef && otherReference(config, oldRef)) return {error:'其他模型设置仍在使用旧 ID，请先更新关联的模型选择，再修改 ID。'};
    // The Gateway restores model secrets by ID; a renamed row has no matching
    // original from which it can restore a redacted model-level header.
    if (index >= 0 && oldRef !== newRef && hasRedactedValue(models[index])) return {error:'此模型含有私有设置，无法直接修改 ID。请添加新模型后选择默认主模型。'};
    if (index >= 0) models[index] = {...models[index], id};
    else models.push({id, name:id, input:['text']});
    // Only an explicit capability edit changes existing authored metadata.
    // Omitted input on older rows must survive connection-only edits unchanged.
    if (draft.imageDirty === true) {
      const row=models[index >= 0 ? index : models.length - 1];
      const input=Array.isArray(row.input) ? row.input : ['text'];
      row.input=draft.supportsImages ? [...new Set([...input,'image'])] : input.filter(value=>value!=='image');
    }
    const selected = models[index >= 0 ? index : models.length - 1];
    if (draft.reasoningDirty) selected.reasoning = draft.supportsReasoning === true;
    if (draft.maxTokensDirty) selected.maxTokens = Number(draft.maxTokens);
    if (draft.thinkingFormatDirty) {
      selected.compat = {...selected.compat};
      if (draft.thinkingFormat) selected.compat.thinkingFormat = draft.thinkingFormat;
      else delete selected.compat.thinkingFormat;
    }
    // Do not copy redacted API keys, headers or unrelated provider settings.
    const provider = {baseUrl:draft.baseUrl.trim(), api:draft.api, models};
    if (draft.apiKey?.trim()) { provider.apiKey = draft.apiKey.trim(); provider.auth = 'api-key'; }
    const raw = {models:{providers:{[draft.provider]:provider}}};
    const defaultRef = primary(config);
    if (draft.makeDefault || (oldRef && oldRef !== newRef && references(defaultRef, oldRef))) {
      const defaultValue = newRef + (draft.makeDefault ? '' : defaultRef.slice(oldRef.length));
      raw.agents = {defaults:{model:typeof config?.agents?.defaults?.model === 'string' ? defaultValue : {primary:defaultValue}}};
    }
    if (draft.thinkingDirty) {
      raw.agents ??= {}; raw.agents.defaults ??= {};
      raw.agents.defaults.thinkingDefault = draft.thinkingDefault;
    }
    if (draft.utilityDirty) {
      raw.agents ??= {}; raw.agents.defaults ??= {};
      raw.agents.defaults.utilityModel = draft.utilityModel.trim();
    }
    return {options:{raw, replacePaths:[`models.providers.${draft.provider}.models`], note:'更新模型服务设置'}};
  }
  function install(root) {
    if (root.__mirallyModels) return root.__mirallyModels;
    const document = root.document;
    let panel = null, context = null, runtime = null, client = null, unsubscribe = null, stopRouter = null;
    let draft = null, baseline = null, dirty = false, busy = false, epoch = 0, frame = null, disposed = false, loaded = false;
    let memorySettings = null, memoryBusy = false, memoryDirty = new Set(), memoryRequest = 0;
    const field = name => panel?.querySelector(`[data-field="${name}"]`);
    const message = text => { const node = panel?.querySelector('[data-slot="notice"]'); if (node && node.textContent !== text) node.textContent = text; };
    const current = token => token === epoch && panel && isPage(root.location) && root.document.querySelector('openclaw-app')?.context === context && context?.runtimeConfig === runtime && runtime?.state?.client === client;
    function permitted() { return loaded && runtime?.canPatch === true && runtime?.state?.connected === true && !!runtime?.state?.client && !busy; }
    function buttons() { for (const node of panel?.querySelectorAll('input,select,button') || []) { const memory = node.hasAttribute('data-memory-field') || node.dataset.action === 'save-memory'; const disabled = memory ? memoryBusy || !permitted() || memorySettings?.canSave !== true : busy || (node.dataset.action === 'save' && !permitted()); if (node.disabled !== disabled) node.disabled = disabled; } }
    function clear() { root.__mirallyActivity?.modelSettings?.cancel?.(); memorySettings=null;memoryBusy=false;memoryDirty.clear();memoryRequest++;if (draft) draft.apiKey = ''; if (field('apiKey')) field('apiKey').value = ''; draft = null; baseline = null; dirty = false; loaded = false; busy = false; panel?.remove(); panel = null; epoch++; }
    const memoryField = name => panel?.querySelector(`[data-memory-field="${name}"]`);
    const memoryNotice = text => {const node=panel?.querySelector('[data-slot="memory-notice"]');if(node)node.textContent=text;};
    async function loadMemory(preserveDraft = false) {
      const token=epoch, request=++memoryRequest, api=root.__mirallyActivity?.modelSettings;
      memorySettings=null;memoryBusy=true;buttons();memoryNotice('正在读取活动记忆整理设置…');
      try {
        if(!api)throw Error('当前宿主未提供活动记忆设置接口。');
        const result=await api.read();if(!current(token) || request!==memoryRequest)return;
        if(result?.error)throw Error(result.error);
        memorySettings=result;
        if(!preserveDraft)memoryDirty.clear();
        if(!memoryDirty.has('model'))memoryField('model').value=result.model || '';
        if(!memoryDirty.has('maxOutputTokens'))memoryField('maxOutputTokens').value=result.max_output_tokens ?? '';
        memoryNotice(`整理${result.enabled ? '已开启' : '已关闭'}；实际模型：${result.effective_model || '沿用主对话模型'}。`);
        return true;
      } catch(error) {if(current(token) && request===memoryRequest)memoryNotice(error.message || '活动记忆设置读取失败。');}
      finally {if(current(token) && request===memoryRequest){memoryBusy=false;buttons();}}
    }
    async function saveMemory() {
      if(memoryBusy || memorySettings?.canSave !== true || !permitted())return;
      if(!memoryDirty.size){memoryNotice('没有待保存的整理模型修改。');return;}
      const maxOutputTokens=Number(memoryField('maxOutputTokens').value);
      if(memoryDirty.has('maxOutputTokens') && (!Number.isSafeInteger(maxOutputTokens) || maxOutputTokens < 128 || maxOutputTokens > 16000)){memoryNotice('整理最大输出须为 128–16000 的整数 tokens。');return;}
      const model=memoryField('model').value.trim();
      if(memoryDirty.has('model') && model && (/\s/.test(model) || !model.includes('/') || model.split('/').some(part=>!part))){memoryNotice('请填写完整的提供商/模型 ID，或留空沿用主对话。');return;}
      const token=epoch, request=++memoryRequest;memoryBusy=true;buttons();memoryNotice('正在保存活动记忆整理模型…');
      try {
        const result=await root.__mirallyActivity.modelSettings.save({...memoryDirty.has('model') ? {model} : {},...memoryDirty.has('maxOutputTokens') ? {maxOutputTokens} : {}});
        if(!current(token) || request!==memoryRequest)return;
        if(result.saved !== true)throw Error('未确认设置已保存，请重新加载。');
        memoryDirty.clear();
        const refreshed=await loadMemory(true);
        if(current(token) && refreshed)memoryNotice(result.applied === true ? '活动记忆整理模型已保存并应用。' : `设置已保存，后台尚未应用${result.reload_error ? `：${result.reload_error}` : '。'}`);
      } catch(error){if(current(token) && request===memoryRequest)memoryNotice(error.message || '保存失败，请重试。');}
      finally {if(current(token) && request===memoryRequest){memoryBusy=false;buttons();}}
    }
    function option(select, value, text) { const node=document.createElement('option');node.value=value;node.textContent=text;select.append(node); }
    function readSelection(providerId, modelId = null) {
      const config = configOf(runtime), providers=config?.models?.providers || {};
      const provider = providers[providerId];
      const isNewProvider = providerId === NEW;
      const models = provider?.models || [];
      const model = modelId === NEW ? null : models.find(item=>item.id===modelId) || models.find(item=>item.id===modelId?.replace(/@[^@]+$/, '')) || models[0];
      const id = isNewProvider ? '' : providerId;
      draft = {provider:id, isNewProvider, baseUrl:provider?.baseUrl || '', api:provider?.api || FORMATS[0],
        apiKey:'', originalId:model?.id ?? null, modelId:model?.id || '', makeDefault:false,
        supportsImages:Array.isArray(model?.input) && model.input.includes('image'), imageDirty:false};
      Object.assign(draft,{supportsReasoning:model?.reasoning === true, reasoningDirty:false,
        thinkingFormat:model?.compat?.thinkingFormat || '', thinkingFormatDirty:false,
        maxTokens:model?.maxTokens ?? '', maxTokensDirty:false,
        thinkingDefault:config?.agents?.defaults?.thinkingDefault || '', thinkingDirty:false,
        utilityModel:config?.agents?.defaults?.utilityModel ?? '', utilityDirty:false});
      baseline = fingerprint(config,id);dirty=false;
      const select=field('provider');select.replaceChildren();
      for (const name of Object.keys(providers)) option(select,name,name);
      option(select,NEW,'＋ 新增提供商');select.value=providerId;
      field('providerId').value=id;field('providerId').closest('label').hidden=!isNewProvider;
      const choices=field('model');choices.replaceChildren();
      for (const item of models) option(choices,item.id,item.name && item.name !== item.id ? `${item.name} · ${item.id}` : item.id);
      option(choices,NEW,'＋ 添加模型');choices.value=model?.id || NEW;
      for (const name of ['baseUrl','api','apiKey','modelId']) field(name).value=draft[name];
      field('makeDefault').checked=false;
      field('supportsImages').checked=draft.supportsImages;
      field('supportsReasoning').checked=draft.supportsReasoning;
      for (const name of ['thinkingFormat','maxTokens','thinkingDefault','utilityModel']) field(name).value=draft[name];
      field('utilityMode').value='preserve';
      const defaults=config?.agents?.defaults || {};
      panel.querySelector('[data-slot="routes"]').textContent = `主对话默认：${primary(config) || '未显式配置'}；轻量辅助任务：${defaults.utilityModel === '' ? '沿用主模型（已禁用自动轻量路由）' : defaults.utilityModel || '由宿主按提供商自动选择轻量模型'}；子代理默认：${typeof defaults.subagents?.model === 'string' ? defaults.subagents.model : defaults.subagents?.model?.primary || '未显式配置，按调用方继承'}；子代理思考：${defaults.subagents?.thinking || '未显式配置，按调用方继承'}。Agent、会话和单次任务的显式设置可能覆盖这些默认值。`;
      message(!safeProvider(id) && !isNewProvider ? '此提供商名称暂不支持在这里修改。' : 'API Key 留空保留已保存密钥。');
      buttons();
    }
    function initial() {
      const config=configOf(runtime), providers=config?.models?.providers || {};
      const ref=primary(config), matched=Object.keys(providers).find(id=>ref.startsWith(id+'/'));
      const id=matched || (Object.hasOwn(providers,'mirally') ? 'mirally' : Object.keys(providers)[0]) || NEW;
      readSelection(id,matched ? ref.slice(matched.length+1) : null);
    }
    async function load(refresh = false) {
      const token=epoch, service=runtime;loaded=false;busy=true;buttons();message('正在加载模型服务…');
      try {
        const result = refresh ? await refreshSnapshot(service) : await service.ensureLoaded();
        if (!current(token)) return;
        if (result === false || !configOf(service)) throw Error();
        loaded=true;initial();loadMemory();
      }
      catch { if(current(token)) { loaded=false;message('模型配置加载失败，请检查连接后重新加载。'); } }
      finally { if(current(token)) { busy=false;buttons(); } }
    }
    async function save() {
      if (!permitted() || !draft) { message('当前无法保存，请检查连接及配置权限。');return; }
      const validation=validate(draft);if(validation){message(validation);return;}
      const token=epoch, service=runtime, savedDraft={...draft}, expected=baseline;let buildError=null;
      busy=true;buttons();message('正在保存…');
      try {
        const ok=await service.patchFromSnapshot(config=>{
          if (!current(token) || service.canPatch !== true || service.state.connected !== true) return {error:'连接已变化，请重新加载。'};
          const result=buildPatch(config,savedDraft,expected);buildError=result.error;
          if(result.options) result.options.canDispatch=()=>current(token) && service.canPatch===true && service.state.connected===true;
          return result;
        });
        if (!current(token)) return;
        if (!ok) { message(buildError || (service.state.configAutoSaveStatus === 'conflict' ? '配置已被其他操作修改，请重新加载后再保存。' : '保存失败，请检查连接、配置权限后重试。'));return; }
        draft.apiKey='';field('apiKey').value='';dirty=false;
        let refreshed=false;
        try { refreshed=await refreshSnapshot(service,{background:true}); } catch {}
        if (!current(token)) return;
        if(refreshed){loaded=true;readSelection(savedDraft.provider,savedDraft.modelId.trim());message('模型服务已保存。');loadMemory(true);}
        else {loaded=false;message('模型服务已保存，但刷新未成功。请重新加载后继续修改。');}
      } catch { if(current(token))message('保存失败，请检查连接、配置权限后重试。'); }
      finally {savedDraft.apiKey='';if(current(token)){busy=false;buttons();}}
    }
    function create() {
      panel=document.createElement('section');panel.id='mirally-model-services';panel.className='settings-section mirally-model-services';
      panel.innerHTML='<h2>模型服务</h2><p>设置服务地址、密钥和模型，已有账号与默认模型设置仍可在本页管理。</p>' +
        '<div class="mirally-model-grid">' +
        '<label>提供商<select data-field="provider"></select></label>' +
        '<label hidden>提供商名称<input data-field="providerId" placeholder="例如 my-provider" autocomplete="off"></label>' +
        '<label>API 基础地址（Base URL）<input data-field="baseUrl" type="url" placeholder="https://…/v1" autocomplete="off" aria-describedby="mirally-model-base-help"><span id="mirally-model-base-help" class="mirally-model-help">填写 API 根地址；程序会自动追加接口路径。</span></label>' +
        '<label>API Key<input data-field="apiKey" type="password" autocomplete="new-password" placeholder="留空保留已保存密钥"></label>' +
        '<label>接口格式<select data-field="api"><option value="openai-completions">OpenAI 兼容（Chat Completions）</option><option value="openai-responses">OpenAI Responses</option><option value="anthropic-messages">Anthropic Messages</option></select></label>' +
        '<label>配置模型<select data-field="model"></select></label>' +
        '<label>实际模型 ID<input data-field="modelId" placeholder="填写服务商提供的完整模型 ID" autocomplete="off"></label></div>' +
        '<div class="mirally-model-capability"><label class="mirally-model-default"><input type="checkbox" data-field="supportsImages" aria-describedby="mirally-model-image-help">支持图片输入</label><span id="mirally-model-image-help" class="mirally-model-help">仅在服务商的此模型支持图像时启用，允许对话发送附加图片；不会自动截屏。</span></div>' +
        '<div class="mirally-model-capability"><label class="mirally-model-default"><input type="checkbox" data-field="supportsReasoning">模型支持推理</label><span class="mirally-model-help">这是模型能力声明，不是任务的思考开关。仅在此模型支持推理时启用。</span></div>' +
        '<div class="mirally-model-grid"><label>推理请求格式<select data-field="thinkingFormat"><option value="">自动识别</option>' + THINKING_FORMATS.map(value=>`<option value="${value}">${value}</option>`).join('') + '</select><span class="mirally-model-help">按服务商接口选；Qwen 可使用 qwen（enable_thinking）或 qwen-chat-template（chat_template_kwargs.enable_thinking），OpenAI 使用 openai。需要同时声明模型支持推理，才能传递关闭思考请求。</span></label>' +
        '<label>模型最大输出（tokens）<input data-field="maxTokens" type="number" min="1" step="1" placeholder="未显式声明"><span class="mirally-model-help">此模型的输出上限元数据；任务可请求更小输出，服务商仍可能施加自己的限制。</span></label></div>' +
        '<h3>任务默认与用途</h3><p data-slot="routes"></p>' +
        '<div class="mirally-model-grid"><label>主对话默认思考<select data-field="thinkingDefault"><option value="" disabled>未显式配置（宿主默认）</option>' + THINKING_LEVELS.map(value=>`<option value="${value}">${value === 'off' ? 'off · 关闭思考' : value}</option>`).join('') + '</select><span class="mirally-model-help">未单独设置的主对话使用此级别；会话 /think 或智能体可覆盖。能否关闭仍取决于模型能力和推理请求格式。</span></label>' +
        '<label>轻量辅助任务路由<select data-field="utilityMode"><option value="preserve">保持当前配置</option><option value="primary">沿用主模型（禁用自动替代）</option><option value="custom">指定轻量辅助模型</option></select><input data-field="utilityModel" aria-label="指定轻量辅助模型 ID" placeholder="提供商/模型" autocomplete="off"><span class="mirally-model-help">用于标题等宿主内部短任务，与活动记忆整理和子代理分开。未配置时宿主可自动选轻量模型；选择沿用主模型会禁用自动替代。输出长度由各任务控制。</span></label></div>' +
        '<label class="mirally-model-default"><input type="checkbox" data-field="makeDefault">设为默认主模型</label>' +
        '<div class="mirally-model-actions"><button type="button" data-action="save">保存模型服务</button><button type="button" data-action="reload">重新加载</button></div>' +
        '<p data-slot="notice" role="status" aria-live="polite"></p>';
      const memory=document.createElement('div');memory.className='mirally-model-task';
      memory.innerHTML='<h3>活动记忆整理</h3><p>这是独立的后台小任务，固定请求关闭推理；上方能力声明、请求格式与服务商一致时才能生效。留空模型沿用主对话路由；整理开关、每小时调用上限和每日预算在活动与记忆管理。</p><div class="mirally-model-grid"><label>整理模型<input data-memory-field="model" maxlength="512" placeholder="留空沿用主对话模型" autocomplete="off"></label><label>整理最大输出（tokens）<input data-memory-field="maxOutputTokens" type="number" min="128" max="16000" step="1"><span class="mirally-model-help">仅限制活动记忆整理输出，范围 128–16000；不修改对话或其他小任务。</span></label></div><div class="mirally-model-actions"><button type="button" data-action="save-memory">保存整理模型</button><a data-memory-link>活动与记忆设置</a></div><p data-slot="memory-notice" role="status" aria-live="polite"></p>';
      const link=memory.querySelector('a');link.href=`${context?.basePath || ''}/settings/appearance?mirally=activity`;
      link.addEventListener('click',event=>{if(event.button!==0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || !context?.navigate)return;event.preventDefault();context.navigate('appearance',{search:'?mirally=activity'});});
      panel.append(memory);
      panel.addEventListener('input',event=>{
        if(event.target.hasAttribute('data-memory-field')){memoryDirty.add(event.target.dataset.memoryField);return;}
        const name=event.target.dataset.field;if(!draft || !name || ['provider','model'].includes(name))return;
        const key=name==='providerId'?'provider':name;draft[key]=['makeDefault','supportsImages','supportsReasoning'].includes(name)?event.target.checked:event.target.value;dirty=true;
        if(name==='supportsImages')draft.imageDirty=true;
        if(name==='supportsReasoning')draft.reasoningDirty=true;
        if(name==='thinkingFormat')draft.thinkingFormatDirty=true;
        if(name==='maxTokens')draft.maxTokensDirty=true;
        if(name==='thinkingDefault')draft.thinkingDirty=true;
        if(name==='utilityModel'){draft.utilityDirty=true;draft.utilityMode=event.target.value ? 'custom' : 'primary';field('utilityMode').value=draft.utilityMode;}
        if(name==='utilityMode'){
          draft.utilityDirty=event.target.value!=='preserve';
          if(event.target.value==='primary'){draft.utilityModel='';field('utilityModel').value='';}
          if(event.target.value==='preserve'){draft.utilityModel=configOf(runtime)?.agents?.defaults?.utilityModel ?? '';field('utilityModel').value=draft.utilityModel;}
        }
        if(name==='providerId') baseline=fingerprint(configOf(runtime),draft.provider);
      });
      panel.addEventListener('change',event=>{
        const name=event.target.dataset.field;
        if(name==='provider' || name==='model') {
          const provider=field('provider').value, model=field('model').value;
          if(dirty){event.target.value=name==='provider'?(draft.isNewProvider?NEW:draft.provider):(draft.originalId??NEW);message('尚有未保存的修改，请先保存或重新加载。');return;}
          readSelection(provider,name==='model'?model:null);
        } else if(['makeDefault','supportsImages','supportsReasoning'].includes(name) && draft) {draft[name]=event.target.checked;dirty=true;if(name==='supportsImages')draft.imageDirty=true;if(name==='supportsReasoning')draft.reasoningDirty=true;}
      });
      panel.addEventListener('click',event=>{
        const action=event.target.closest('button')?.dataset.action;
        if(action==='save') save();
        if(action==='save-memory')saveMemory();
        if(action==='reload' && !busy) {if(draft)draft.apiKey='';field('apiKey').value='';dirty=false;load(true);}
      });
    }
    function sync() {
      frame=null;if(disposed)return;
      const next=document.querySelector('openclaw-app')?.context;
      const service=next?.runtimeConfig, nextClient=service?.state?.client;
      if(next!==context || service!==runtime || nextClient!==client) {
        clear();unsubscribe?.();stopRouter?.();context=next;runtime=service;client=nextClient;
        unsubscribe=service?.subscribe?.(schedule);stopRouter=next?.router?.subscribe?.(schedule);
      }
      const page=document.querySelector('openclaw-model-providers-page');
      if(!page || !runtime || !isPage(root.location)){if(panel)clear();return;}
      const body=page.querySelector('.settings-page');
      if(!body)return;
      if(!panel){create();body.prepend(panel);load();}
      else if(panel.parentNode!==body)body.prepend(panel);
      buttons();
    }
    function schedule(){if(frame===null && !disposed)frame=root.requestAnimationFrame(sync);}
    const observer=new root.MutationObserver(schedule);observer.observe(document,{childList:true,subtree:true});
    root.addEventListener('popstate',schedule);
    function dispose(){disposed=true;observer.disconnect();unsubscribe?.();stopRouter?.();if(frame!==null)root.cancelAnimationFrame(frame);clear();}
    root.addEventListener('pagehide',dispose);
    root.__mirallyModels={sync:schedule,dispose};schedule();return root.__mirallyModels;
  }
  return {FORMATS, isPage, safeProvider, configOf, primary, fingerprint, validate, buildPatch, install};
});
