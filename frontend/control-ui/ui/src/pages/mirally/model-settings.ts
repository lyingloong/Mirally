export type ModelSettings = {
  provider?: string; base_url?: string; api_format?: string; model_id?: string;
  api_key_configured?: boolean; max_tokens?: number; temperature?: number; context_window?: number;
  image_enabled?: boolean; reasoning_enabled?: boolean; reasoning_effort?: string;
};

/** Credentials live only in the password input, never in a retained draft. */
export type ModelDraft = {
  provider: string; base_url: string; api_format: string; model_id: string; max_tokens: string;
  context_window: string; temperature: string; image_enabled: boolean; reasoning_effort: string;
};

export type DiscoveredModel = {
  id: string; name: string; input_modalities?: string[]; context_window?: number; max_output_tokens?: number;
  effort?: {supported_levels: string[]; default_level?: string};
};

export function isDeepSeekEndpoint(base: string) {
  try { return new URL(base).hostname.toLowerCase() === 'api.deepseek.com'; } catch { return false; }
}

export function modelDraft(model: ModelSettings): ModelDraft {
  return {
    provider: model.provider || 'openai-compatible', base_url: model.base_url || '', model_id: model.model_id || '',
    api_format: ({'openai-completions': 'openai-chat', anthropic: 'anthropic-messages'} as Record<string, string>)[model.api_format || ''] || model.api_format || 'openai-chat',
    max_tokens: String(model.max_tokens ?? 2048), context_window: String(model.context_window ?? 128000),
    temperature: String(model.temperature ?? 0.7), image_enabled: model.image_enabled === true,
    reasoning_effort: model.reasoning_effort || (model.reasoning_enabled ? (isDeepSeekEndpoint(model.base_url || '') ? 'high' : 'medium') : 'off'),
  };
}

/** An explicit choice fills a public preset; editing existing settings never applies this silently. */
export function deepSeekPreset(draft: ModelDraft): ModelDraft {
  return {...draft, provider: 'deepseek', base_url: 'https://api.deepseek.com', api_format: 'openai-responses',
    model_id: 'deepseek-flash', image_enabled: true, reasoning_effort: 'default'};
}

export function adoptDiscoveredModel(draft: ModelDraft, item: DiscoveredModel): ModelDraft {
  const next = {...draft, model_id: item.id};
  if (item.context_window && item.context_window <= 10000000) next.context_window = String(item.context_window);
  if (item.max_output_tokens) next.max_tokens = String(Math.min(Number(draft.max_tokens), item.max_output_tokens));
  if (item.input_modalities) next.image_enabled = item.input_modalities.includes('image');
  if (item.effort && !['default', 'off'].includes(next.reasoning_effort) && !item.effort.supported_levels.includes(next.reasoning_effort)) next.reasoning_effort = 'default';
  return next;
}

export function modelCapabilityText(item: DiscoveredModel) {
  return [item.input_modalities ? `输入：${item.input_modalities.join('、')}` : '服务未声明输入类型',
    item.context_window ? `上下文 ${item.context_window.toLocaleString('zh-CN')} token` : '',
    item.max_output_tokens ? `最多输出 ${item.max_output_tokens.toLocaleString('zh-CN')} token` : '',
    item.effort ? `推理：${item.effort.supported_levels.join(' / ')}${item.effort.default_level ? `（默认 ${item.effort.default_level}）` : ''}` : '',
  ].filter(Boolean).join('；');
}
