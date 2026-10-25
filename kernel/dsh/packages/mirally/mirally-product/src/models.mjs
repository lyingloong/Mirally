import {createHash} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {LlmAdapter, resolveImageAttachmentAccess} from '@deepseek-ai/dsh-llm';
import {PiAiAdapter, resolveProfiles, credentialStoreFrom, authContextFrom} from '@deepseek-ai/dsh-llm-pi-ai';

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
    const model = settings.model ?? {};
    if (!model.model_id || !model.base_url) {
      this.options = undefined;
      return undefined;
    }
    const fingerprint = createHash('sha256').update(JSON.stringify(model)).digest('hex');
    if (fingerprint === this.fingerprint) return this.options;
    const keyReference = `MIRALLY_MODEL_${fingerprint.slice(0, 20).toUpperCase()}`;
    const route = productModelRoute(model);
    const profile = {...route.profile, apiKeyEnv: keyReference};
    const next = resolveProfiles({mirally: profile});
    this.keys.set(keyReference, model.api_key ?? '');
    this.profiles = next;
    if (!this.registration) this.registration = this.ctx.llm.registerAdapter(['mirally'], this.adapter);
    this.fingerprint = fingerprint;
    this.options = route.options;
    return this.options;
  }

  requireOptions() {
    if (!this.options) throw Object.assign(new Error('请先在设置中配置模型、服务地址和凭据'), {code: 'model_not_configured'});
    return {...this.options};
  }

  dispose() { this.registration?.(); this.keys.clear(); }
}
