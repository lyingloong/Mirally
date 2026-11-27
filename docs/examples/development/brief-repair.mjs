/** One-shot repair of a merge brief stored BEFORE the public-text sanitizer
 * rules existed. The brief markdown is republished verbatim by publish steps,
 * which can run older code than this module until the branch carrying the
 * sanitizer is merged, so the durable fix for an already-published leak is to
 * clean the stored record itself — a one-off PR body edit is overwritten by the
 * next gate cycle. Only human-readable prose is rewritten (requirement title
 * and summary/why/acceptance/evidence, implementation and review records, risks
 * and the mirrored PR body); store references, protocol fields and
 * machine-local artifact paths inside the store JSON stay byte-valid.
 * Idempotent: a second pass over clean text changes nothing. The operator runs
 * it through `driver.mjs scrub-brief` (claim-guarded store mutation that
 * asserts cleanliness before saving); the offline regression probe imports
 * scrubStoredBrief directly. Rule source: the shared public-text module. */
import { publicText, assertPublicText } from './public-text.mjs';

export function scrubStoredBrief(item, options = {}) {
  const proseFields = {
    requirement: ['title', 'description', 'summary', 'evidence'],
    change: ['summary', 'verification'],
    review: ['summary', 'verification', 'reviewer'],
  };
  const proseLists = { requirement: ['why', 'acceptance'] };
  let changed = 0;
  const scrub = value => {
    if (typeof value !== 'string') return value;
    const clean = publicText(value, options);
    if (clean !== value) changed += 1;
    return clean;
  };
  const brief = item.brief;
  if (!brief || typeof brief !== 'object') return { changed: 0 };
  for (const [group, names] of Object.entries(proseFields)) {
    const record = brief[group];
    if (record && typeof record === 'object') for (const name of names) record[name] = scrub(record[name]);
  }
  for (const [group, names] of Object.entries(proseLists)) {
    const record = brief[group];
    if (!record || typeof record !== 'object') continue;
    for (const name of names) if (Array.isArray(record[name])) record[name] = record[name].map(scrub);
  }
  if (Array.isArray(brief.risks)) brief.risks = brief.risks.map(scrub);
  if (brief.pr && typeof brief.pr === 'object') brief.pr.body = scrub(brief.pr.body);
  brief.markdown = scrub(brief.markdown);
  if (item.pr && typeof item.pr === 'object') item.pr.body = scrub(item.pr.body);
  return { changed };
}

export async function scrubBrief(store, id, claimId) {
  return store.mutate(id, claimId, async item => {
    if (!item.brief || typeof item.brief !== 'object' || typeof item.brief.markdown !== 'string' || !item.brief.markdown.trim()) throw new Error('Requirement has no stored merge brief to scrub');
    const { changed } = scrubStoredBrief(item);
    // Fail closed BEFORE saving: the scrubbed record must satisfy the same
    // assertion every publication applies to its output.
    assertPublicText(item.brief.markdown, 'stored merge brief');
    item.brief.scrubbedAt = new Date().toISOString();
    return { item, changed };
  });
}
