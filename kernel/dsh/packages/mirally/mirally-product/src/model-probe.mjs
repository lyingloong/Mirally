/** Explicit settings-page probes. They never change configuration or send conversation data. */
const MAX_BYTES = 2 * 1024 * 1024;
const MAX_MODELS = 1000;
const EFFORTS = new Set(['none', 'off', 'low', 'medium', 'high', 'max']);
const fail = (message, code = 'model_discovery_failed') => Object.assign(new Error(message), {code, status: 400});
const positive = value => Number.isSafeInteger(value) && value > 0 ? value : undefined;

export function modelListing(body) {
  if (!Array.isArray(body?.data)) throw fail('服务未返回有效的模型列表；仍可手动填写模型 ID。');
  const seen = new Set();
  return body.data.slice(0, MAX_MODELS).flatMap(raw => {
    if (typeof raw?.id !== 'string' || !raw.id.trim() || raw.id.length > 2048 || seen.has(raw.id)) return [];
    seen.add(raw.id);
    const modalities = raw.input_modalities ?? raw.input;
    const input = Array.isArray(modalities) ? modalities.filter(item => ['text', 'image', 'audio', 'video'].includes(item)) : undefined;
    const effort = raw.effort;
    const supported = Array.isArray(effort?.supported_levels) ? effort.supported_levels.filter(level => EFFORTS.has(level)) : [];
    return [{id: raw.id, name: typeof raw.name === 'string' ? raw.name.slice(0, 2048) : raw.id,
      ...(input ? {input_modalities: [...new Set(input)]} : {}),
      ...(positive(raw.context_window ?? raw.context_length) ? {context_window: positive(raw.context_window ?? raw.context_length)} : {}),
      ...(positive(raw.max_output_tokens ?? raw.max_tokens) ? {max_output_tokens: positive(raw.max_output_tokens ?? raw.max_tokens)} : {}),
      ...(supported.length ? {effort: {supported_levels: [...new Set(supported)],
        ...(supported.includes(effort.default_level) ? {default_level: effort.default_level} : {})}} : {})}];
  });
}

export async function discoverProductModels(model, signal) {
  if (!model?.base_url) throw fail('请先保存服务地址和凭据。', 'model_not_configured');
  let url;
  try { url = new URL(model.base_url); } catch { throw fail('模型服务地址无效。'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw fail('读取模型列表需要不含凭据、查询参数或片段的 HTTP(S) 服务地址。');
  }
  const anthropic = ['anthropic', 'anthropic-messages'].includes(model.api_format);
  const base = url.pathname.replace(/\/+$/, '');
  url.pathname = anthropic ? `${base.endsWith('/v1') ? base.slice(0, -3) : base}/v1/models` : `${base}/models`;
  const headers = {accept: 'application/json'};
  if (anthropic) headers['anthropic-version'] = '2023-06-01';
  if (model.api_key) headers[anthropic ? 'x-api-key' : 'authorization'] = anthropic ? model.api_key : `Bearer ${model.api_key}`;
  let response;
  try { response = await fetch(url, {headers, redirect: 'error', signal}); }
  catch (error) {
    if (signal?.aborted) throw fail('读取模型列表已取消或超时。', 'model_discovery_timeout');
    throw fail('无法连接模型列表接口，请检查地址、网络和服务状态。');
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw fail(`模型列表接口返回 HTTP ${response.status}${[401, 403].includes(response.status) ? '，请检查 API Key' : response.status === 404 ? '；该服务可能不提供模型列表，可手动填写模型 ID' : ''}。`);
  }
  let bytes = 0;
  const chunks = [], reader = response.body?.getReader();
  if (!reader) throw fail('模型列表接口返回空响应。');
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_BYTES) throw fail('模型列表超过 2 MiB，已停止读取。');
      chunks.push(value);
    }
  } catch (error) {
    if (error?.code === 'model_discovery_failed') throw error;
    if (signal?.aborted) throw fail('读取模型列表已取消或超时。', 'model_discovery_timeout');
    throw fail('模型列表读取中断，请检查网络和服务状态后重试。');
  } finally { await reader.cancel().catch(() => {}); }
  let body;
  try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw fail('模型列表接口未返回有效 JSON。'); }
  return {models: modelListing(body), truncated: body.data?.length > MAX_MODELS, source: 'provider'};
}
