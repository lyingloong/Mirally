import {createHash} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {LlmAdapter, resolveImageAttachmentAccess} from '@deepseek-ai/dsh-llm';
import {PiAiAdapter, resolveProfiles, credentialStoreFrom, authContextFrom} from '@deepseek-ai/dsh-llm-pi-ai';

/** Resolve one product-owned connection; never fall back from a missing explicit route. */
export function resolveProductConnection(settings, {purpose = 'chat', connectionId, modelId} = {}) {
  if (!['chat', 'background'].includes(purpose)) throw Object.assign(new Error('Unsupported model purpose'), {code: 'invalid_model_purpose'});
  const id = connectionId ?? settings.model_routes?.[purpose] ?? 'default';
  const connection = id === 'default' ? {name: '默认连接', model: settings.model ?? {}} : settings.model_connections?.[id];
  if (!connection) throw Object.assign(new Error('模型连接不存在，请刷新设置。'), {code: 'model_connection_not_found'});
  const base = connection.model ?? {};
  const chosen = modelId === undefined || modelId === base.model_id ? undefined
    : settings.model_catalogs?.[id]?.find(item => item.model_id === modelId);
  if (modelId !== undefined && modelId !== base.model_id && !chosen)
    throw Object.assign(new Error('所选模型已不可用，请在模型连接中检查。'), {code: 'model_not_found'});
  return {id, name: connection.name, model: {...base, ...chosen}};
}

/** Protocol facts, not a second model loop. Keep service-default distinct from off. */
export function productModelRoute(model) {
  const protocols = {'openai-chat': 'openai-completions', 'openai-responses': 'openai-responses',
    'anthropic': 'anthropic-messages', 'anthropic-messages': 'anthropic-messages'};
  const api = protocols[model.api_format] ?? model.api_format ?? 'openai-completions';
  let officialDeepSeek = false;
  try { officialDeepSeek = new URL(model.base_url).hostname === 'api.deepseek.com'; } catch {}
  const deepseek = officialDeepSeek || model.provider === 'deepseek';
  const configured = model.reasoning_effort || '';
  const effort = configured === 'default' ? undefined : configured || (model.reasoning_enabled ? (deepseek ? 'high' : 'medium') : deepseek ? 'off' : undefined);
  const reasoning = deepseek || model.reasoning_enabled || (configured && configured !== 'default');
  // In PiAI, absent off means "do not override the service default"; null
  // explicitly enables Off and lets each protocol send its correct disable field.
  const reasoningEfforts = {
    ...(configured === 'default' ? {} : {off: null}), low: 'low',
    medium: deepseek ? 'high' : 'medium', high: 'high', max: 'max',
  };
  return {
    profile: {api, baseURL: model.base_url,
      ...(deepseek && api === 'openai-completions' ? {compat: {thinkingFormat: 'deepseek', supportsDeveloperRole: false, requiresReasoningContentOnAssistantMessages: true}} : {}),
      models: [{id: model.model_id, contextWindow: model.context_window ?? 128000,
        maxTokens: model.max_tokens ?? 2048, input: model.image_enabled ? ['text', 'image'] : ['text'],
        ...(reasoning ? {reasoningEfforts} : {})}],
      retryPolicy: {mode: 'normal', maxRetries: 1}},
    options: {provider: 'mirally', model: model.model_id, maxTokens: model.max_tokens ?? 2048,
      ...(effort && effort !== 'off' ? {} : {temperature: model.temperature ?? 0.7}),
      ...(effort ? {reasoningEffort: effort} : {})},
  };
}

class ExplicitTestAdapter extends LlmAdapter {
  constructor(tokenDelay) { super(); this.tokenDelay = tokenDelay; }
  async listModels() { return [{id: 'test-model', name: 'Explicit offline test model', contextWindow: 65536, maxTokens: 4096}]; }
  async *stream(options) {
    const prompt = options.messages.filter(message => message.role === 'user'
      && !message.content?.some(block => block.type === 'text' && block.text.startsWith('Current runtime context.'))).at(-1);
    const input = prompt?.content?.filter(block => block.type === 'text').map(block => block.text).join('') ?? '';
    const text = `Mirally 测试模型已收到：${input}`;
    yield {type: 'block-start', index: 0, blockType: 'text'};
    for (const character of text) {
      options.signal?.throwIfAborted();
      if (this.tokenDelay > 0) await delay(this.tokenDelay, undefined, {signal: options.signal});
      yield {type: 'text-delta', index: 0, text: character};
    }
    yield {type: 'block-end', index: 0, block: {type: 'text', text}};
    yield {type: 'usage', usage: {inputTokens: Math.ceil(input.length / 2), outputTokens: text.length}};
    yield {type: 'finish', reason: {kind: 'stop'}};
  }
}

/** Keeps existing native provider implementations and freezes credential generations per profile. */
export class ProductModels {
  constructor(ctx, {testMode, testTokenDelayMs}) {
    this.ctx = ctx;
    this.testMode = testMode;
    this.profiles = new Map();
    this.keys = new Map();
    if (testMode) {
      this.registration = ctx.llm.registerAdapter(['mirally-test'], new ExplicitTestAdapter(testTokenDelayMs));
      this.options = {provider: 'mirally-test', model: 'test-model', maxTokens: 4096};
    } else {
      this.adapter = new PiAiAdapter({
        profiles: () => this.profiles,
        resolveApiKey: async (_provider, profile) => this.keys.get(profile.apiKeyEnv),
        auth: {credentials: credentialStoreFrom(ctx), authContext: authContextFrom(ctx)},
        resolveAttachments: () => ctx.get('attachments'),
        resolveImageAccess: (attachments, ref) => resolveImageAttachmentAccess(attachments,
          hostPath => ctx.get('fs')?.processPathFromHostPath(hostPath), ref),
      });
    }
  }

  sync(settings) {
    if (this.testMode) return this.options;
    const snapshot = {model: settings.model ?? {}, connections: settings.model_connections ?? {}, routes: settings.model_routes ?? {}, catalogs: settings.model_catalogs ?? {}};
    const fingerprint = createHash('sha256').update(JSON.stringify(snapshot)).digest('hex');
    if (fingerprint === this.fingerprint) return this.options;
    const declared = {}, options = new Map(), selections = new Map(), keys = new Map();
    for (const id of ['default', ...Object.keys(snapshot.connections)]) {
      const {model} = resolveProductConnection(settings, {connectionId: id});
      if (!model.model_id || !model.base_url) continue;
      const provider = id === 'default' ? 'mirally' : `mirally-${id}`;
      const keyReference = `MIRALLY_MODEL_${createHash('sha256').update(JSON.stringify([id, model])).digest('hex').slice(0, 24).toUpperCase()}`;
      const route = productModelRoute(model);
      declared[provider] = {...route.profile, apiKeyEnv: keyReference};
      options.set(id, {...route.options, provider});
      selections.set(JSON.stringify([id, model.model_id]), {...route.options, provider});
      for (const entry of snapshot.catalogs[id] ?? []) {
        if (!entry.model_id || entry.model_id === model.model_id) continue;
        const extra = productModelRoute({...model, ...entry});
        declared[provider].models.push(...extra.profile.models);
        selections.set(JSON.stringify([id, entry.model_id]), {...extra.options, provider});
      }
      keys.set(keyReference, model.api_key ?? '');
    }
    const next = resolveProfiles(declared);
    for (const [ref, key] of keys) this.keys.set(ref, key);
    this.profiles = next;
    if (!this.registration && next.size) this.registration = this.ctx.llm.registerAdapter([...next.keys()], this.adapter);
    else this.registration?.replace([...next.keys()]);
    this.fingerprint = fingerprint;
    this.connectionOptions = options;
    this.selectionOptions = selections;
    this.settings = settings;
    this.options = options.get(resolveProductConnection(settings).id);
    return this.options;
  }

  requireOptions(purpose = 'chat', connectionId, modelId) {
    const resolved = this.testMode ? null : resolveProductConnection(this.settings ?? {}, {purpose, connectionId, modelId});
    const options = this.testMode ? this.options : modelId === undefined
      ? this.connectionOptions?.get(resolved.id) : this.selectionOptions?.get(JSON.stringify([resolved.id, modelId]));
    if (!options) throw Object.assign(new Error('请先为该用途在设置中配置模型、服务地址和凭据'), {code: 'model_not_configured'});
    return {...options};
  }

  assertImageInput(purpose = 'chat', connectionId, modelId) {
    if (this.testMode) return;
    const {model} = resolveProductConnection(this.settings ?? {}, {purpose, connectionId, modelId});
    if (!model.image_enabled) throw Object.assign(new Error('当前用途的模型连接未启用图片输入，请选择支持视觉的连接后再发送图片。'), {code: 'unsupported_image_input', status: 400});
  }

  dispose() { this.registration?.(); this.keys.clear(); }
}
