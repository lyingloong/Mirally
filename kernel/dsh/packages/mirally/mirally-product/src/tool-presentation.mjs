/** Bounded, display-only projections of authoritative native tool events. */
export const TOOL_DISPLAY_BYTE_LIMIT = 32768;
const CLIPPED = '… [显示已截短]';
const SECRET_KEY = /^(?:api[-_]?key|authorization|password|secret|access[-_]?token|refresh[-_]?token|client[-_]?secret)$/i;

/** No executable/plugin objects, binary payloads or deferred contexts cross this view. */
export function toolDisplayValue(input) {
  let remaining = 12000, nodes = 0, truncated = false;
  const seen = new Set();
  const clip = value => {
    const limit = Math.min(8192, Math.max(0, remaining));
    remaining -= Math.min(value.length, limit);
    if (value.length <= limit) return value;
    truncated = true;
    return value.slice(0, limit) + CLIPPED;
  };
  const visit = (value, depth = 0) => {
    if (++nodes > 256 || depth > 6 || remaining <= 0) { truncated = true; return CLIPPED; }
    if (value == null) return null;
    if (typeof value === 'string') return clip(value);
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return Number.isFinite(value) ? value : String(value);
    if (typeof value !== 'object') { truncated = true; return '[非 JSON 内容未显示]'; }
    if (seen.has(value)) { truncated = true; return '[循环引用未显示]'; }
    seen.add(value);
    try {
      if (Array.isArray(value)) {
        const output = value.slice(0, 64).map(item => visit(item, depth + 1));
        if (value.length > 64) { truncated = true; output.push(CLIPPED); }
        return output;
      }
      const output = Object.create(null), keys = Object.keys(value);
      for (const key of keys.slice(0, 64)) {
        const displayKey = clip(key);
        // A display projection must not disclose credentials supplied to a tool.
        if (SECRET_KEY.test(key)) output[displayKey] = '[凭据已隐藏]';
        else {
          try { output[displayKey] = visit(value[key], depth + 1); }
          catch { truncated = true; output[displayKey] = '[内容无法显示]'; }
        }
        if (remaining <= 0) break;
      }
      if (keys.length > 64 || remaining <= 0) { truncated = true; output.__mirally_display_note = CLIPPED; }
      return output;
    } finally { seen.delete(value); }
  };
  let value;
  try { value = visit(input); }
  catch { value = '[内容无法显示]'; truncated = true; }
  const encoded = JSON.stringify(value);
  if (Buffer.byteLength(encoded, 'utf8') > TOOL_DISPLAY_BYTE_LIMIT) {
    // JSON escapes can exceed the traversal character budget. Keep a valid,
    // explicit preview rather than slicing an invalid JSON/network frame.
    value = {preview: encoded.slice(0, 4096), note: CLIPPED}; truncated = true;
  }
  return {value, truncated};
}

export function toolResultDisplay(result) {
  const parts = [];
  let length = 0, omitted = false;
  const content = Array.isArray(result?.content) ? result.content : [];
  if (content.length > 64) omitted = true;
  for (const block of content.slice(0, 64)) {
    if (block?.type !== 'text' || typeof block.text !== 'string') { omitted = true; continue; }
    const available = Math.max(0, 12000 - length);
    parts.push(block.text.slice(0, available)); length += Math.min(block.text.length, available);
    if (block.text.length > available) { omitted = true; break; }
    if (parts.length >= 64) { omitted = true; break; }
  }
  if (!parts.length && result?.isError && typeof result.error?.message === 'string') parts.push(result.error.message);
  if (omitted) parts.push('[部分内容未显示：二进制内容或超出显示上限。]');
  const projection = toolDisplayValue(parts.join('\n'));
  const text = typeof projection.value === 'string' ? projection.value : JSON.stringify(projection.value);
  return {value: {content: [{type: 'text', text}]}, truncated: omitted || projection.truncated};
}
