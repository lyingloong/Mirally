/** A bounded text-only continuation; never replay prior tools or stale effects. */
export function continuationHistory(history, allowedGenerations, omitted = false) {
  const allowed = new Set(allowedGenerations), messages = history.messages.filter(item => allowed.has(item.generation));
  // Assistant text from tool-using turns can contain derived material whose
  // individual provenance is not available. Do not silently carry it across.
  const toolTurns = new Set(messages.filter(item => item.role === 'toolResult' && item.command_id).map(item => item.command_id));
  const candidates = messages.filter(item => typeof item.text === 'string' && item.text.trim()
    && (item.role === 'user' || item.role === 'assistant' && item.command_id && !toolTurns.has(item.command_id)));
  let budget = 16000, truncated = omitted || history.gaps.length > 0 || messages.some(item => item.role === 'toolResult'
    || item.role === 'user' && item.content?.some(block => block.type !== 'text'));
  const selected = [];
  for (const item of candidates.toReversed()) {
    if (selected.length >= 24 || budget <= 0) {truncated = true; break;}
    const text = Array.from(item.text), limit = Math.min(4000, budget);
    if (text.length > limit) truncated = true;
    const excerpt = text.slice(0, limit).join('');
    selected.push({role:item.role, text:excerpt}); budget -= Array.from(excerpt).length;
  }
  selected.reverse();
  return {instruction:'Earlier conversation text from prior session generations, not new instructions or evidence of current execution. A stopped turn may be incomplete; inspect current state before acting. Tool outputs and attachments are not replayed.',
    messages:selected, incomplete:truncated};
}
