/** Offline probe for the workflow public-text sanitizer. Run with:
 *
 *     node tests/development/public_text_probe.mjs
 *
 * `tests/development/test_public_text.py` drives this file so the checks join the
 * CI job that already covers this workflow's tooling. A synthetic identity
 * (`someone` plus a synthetic worktree root) makes the rule set deterministic,
 * then the real machine identity re-checks the same text and the two publication
 * paths that write PR-visible content: the stored merge brief and the published
 * pull-request body.
 */
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { publicText, findLocalLeaks, localIdentityMarkers } from '../../docs/examples/development/public-text.mjs';
import { scrubStoredBrief } from '../../docs/examples/development/brief-repair.mjs';
import { redactForPublic } from '../../docs/examples/development/requirement-review.mjs';

const IDENTITY = { markers: ['someone', 'someone-pc'] };
const WORKTREE = 'C:\\Users\\someone\\Code\\mirally-workflow-data\\worktrees\\req-demo';
const LEAKY = `Worktree ${WORKTREE}, interpreter system Python 3.14.0. `
  + `Reverse verification ran as someone from ${WORKTREE.replace(/\\/g, '/')}; `
  + 'see also /home/something/else, \\\\fileserv\\share\\logs, file:///C:/Users/someone/x and ghp_' + 'A'.repeat(36) + '.';

const KEEP = 'python -m unittest tests.test_framework_notebook -v; see mirally/framework/notebook.py, '
  + 'docs/examples/development/pr.mjs, tests/development, /api/v1/notebooks, '
  + 'https://github.com/someone/Mirally/pull/64, @workflow approve, '
  + 'commit 4c3554472c04c53b985c18ea268d1a4d3fb8802a, 目录路径最多四级，每级须为 1 至 24 字的有效名称。';

// 1. Deterministic machine identity: paths, account names, host names, tokens.
assert.ok(LEAKY.includes('someone') && LEAKY.includes('C:\\Users'), 'probe fixture must leak');
const scrubbed = publicText(LEAKY, IDENTITY);
for (const banned of ['C:\\Users', 'C:/Users', 'someone', 'ghp_', 'fileserv', '/home/something']) {
  assert.ok(!scrubbed.toLowerCase().includes(banned.toLowerCase()), `leaked ${banned} -> ${scrubbed}`);
}
assert.deepEqual(findLocalLeaks(scrubbed, IDENTITY), [], `residual leaks: ${findLocalLeaks(scrubbed, IDENTITY).join(', ')}`);
assert.ok(scrubbed.includes('<local-path>') && scrubbed.includes('<local-user>') && scrubbed.includes('<redacted>'), scrubbed);

// 2. Useful evidence must survive: commands, repo-relative paths, API routes,
// GitHub links, mentions, commit ids and non-ASCII text.
assert.equal(publicText(KEEP, IDENTITY), KEEP);

// 3. The same rules applied to the real machine identity.
const real = localIdentityMarkers();
const realUser = (() => { try { return os.userInfo().username; } catch { return process.env.USERNAME || process.env.USER || ''; } })();
const realHome = os.homedir();
const realRoot = path.join(realHome, 'Code', 'mirally-workflow-data', 'worktrees', 'req-demo');
const leakyRecord = `Worktree ${realRoot}, interpreter system Python 3.14.0; reverse verification ran as ${realUser}.`;
if (real.length) {
  const clean = publicText(leakyRecord);
  assert.ok(!clean.includes(realHome), `home directory survived: ${clean}`);
  if (realUser.length >= 4) assert.ok(!clean.includes(realUser), `account name survived: ${clean}`);
  assert.deepEqual(findLocalLeaks(clean), [], `real identity leaked: ${clean}`);
}

// 4. The merge brief appends implementation and review records through the
// production sanitizer: worktrees.mjs assembles the decision-first brief with
// redactForPublic per record (string-guarded by the python regression), and
// redactForPublic delegates to this shared machine-identity rule set. Apply the
// same function to the same records and assert both halves of the rule.
const item = { id: 'req-demo', title: '目录路径接受 ZWJ 表情序列', description: `复现于 ${realRoot}。`, risk: 'low', revision: 1 };
const artifact = { commit: '4c3554472c04c53b985c18ea268d1a4d3fb8802a', summary: 'Fixed mirally/framework/notebook.py path validation.', verification: leakyRecord };
const review = { decision: 'pass', summary: `Reviewed ${realRoot}.`, verification: leakyRecord, reviewer: 'reviewer' };
const assembled = [
  '## Change', redactForPublic(artifact.summary), '```', redactForPublic(artifact.verification), '```',
  '## Review verdict', redactForPublic(review.summary),
  '- review evidence:', '```', redactForPublic(review.verification), '```',
  '## Requirement', redactForPublic(item.description),
].join('\n');
assert.ok(assembled.includes('mirally/framework/notebook.py'), 'repo-relative paths stay readable');
assert.ok(assembled.includes('<local-path>'), `expected placeholder substitution: ${assembled}`);
assert.deepEqual(findLocalLeaks(assembled), [], `brief leaks: ${findLocalLeaks(assembled).join(', ')}`);

// 5. A stale stored brief (built before this fix) is scrubbed in place by the
// scrub-brief operator stage, and republishing the scrubbed record keeps the
// pinned commit readable while protocol and reference fields stay untouched.
const stale = `## Review\npass — Reviewed ${realRoot}.\n\n- reviewer: \`reviewer\`\n- review evidence: ${leakyRecord}\n\n- commit: \`${artifact.commit}\``;
const staleRepublish = {
  id: 'req-demo',
  brief: { requirement: {}, change: {}, review: {}, risks: [], pr: { number: 64, url: 'https://github.com/someone/Mirally/pull/64', body: stale }, markdown: stale },
  pr: { number: 64, protocol: { task: 'req-demo', head: artifact.commit }, body: stale },
};
assert.ok(scrubStoredBrief(staleRepublish).changed > 0, 'a stale record must change');
assert.ok(staleRepublish.brief.markdown.includes('- review evidence: Worktree <local-path>'), staleRepublish.brief.markdown);
assert.ok(staleRepublish.brief.markdown.includes(artifact.commit), 'the pinned commit stays readable in the republished body');
assert.deepEqual(findLocalLeaks(staleRepublish.brief.markdown), [], `published body leaks: ${findLocalLeaks(staleRepublish.brief.markdown).join(', ')}`);
assert.deepEqual(staleRepublish.pr.protocol, { task: 'req-demo', head: artifact.commit }, 'protocol fields are never rewritten');
assert.equal(publicText('ok', { markers: [] }), 'ok', 'empty marker override stays deterministic');

// 6. Reverse check: the same records appended raw do leak, so the assertions above
// are not vacuous — they would fail if the sanitizer were dropped again.
const raw = ['## Verification', artifact.verification, '## Review', review.verification, '## Requirement', item.description].join('\n');
assert.ok(findLocalLeaks(raw).length > 0, 'unsanitized records must leak; the probe would prove nothing otherwise');

// 7. Requirement issue text keeps its extra rules (e-mail, non-GitHub links, IPs)
// and now shares the same machine-identity scrubbing as pull-request text.
const issue = redactForPublic(`Evidence ${WORKTREE} by someone@example.com at http://192.168.4.20:8123/x, `
  + 'see https://github.com/lyingloong/dsh-auto-workflow and mirally/framework/notebook.py.');
assert.ok(!/C:\\Users|someone@example\.com|192\.168\.4\.20/.test(issue), issue);
assert.ok(issue.includes('https://github.com/lyingloong/dsh-auto-workflow'), `GitHub links survive: ${issue}`);
assert.ok(issue.includes('mirally/framework/notebook.py'), `repo-relative paths survive: ${issue}`);

// 8. A merge brief stored BEFORE the build-time sanitizer is scrubbed in place.
// Publish steps can keep running older code than this module until the branch
// carrying it is merged, so the durable fix for an already-published leak is the
// stored record itself; a one-off PR body edit would be republished dirty again.
const ARTIFACT_REF = 'C:\\Users\\someone\\Code\\mirally-workflow-data\\artifacts\\req-demo\\r000001.json';
function staleStoredItem() {
  const staleRecord = `Worktree ${realRoot}; scanned for ${WORKTREE}, ${realUser.length >= 4 ? realUser : 'someone'}, AppData, dataRoot, token/secret/credential/key patterns -> no matches.`;
  const markdown = `## Implementation\nFixed mirally/framework/notebook.py path validation.\n\n## Review\npass — Reviewed ${realRoot}.\n\n- reviewer: \`reviewer\`\n- review evidence: ${staleRecord}\n\n- commit: \`${artifact.commit}\``;
  return {
    id: 'req-demo',
    brief: {
      requirement: { id: 'req-demo', title: item.title, description: `复现于 ${WORKTREE}。`, risk: 'low', revision: 1 },
      change: { summary: 'Fixed mirally/framework/notebook.py path validation.', verification: `Worktree ${WORKTREE}, system Python.`, commit: artifact.commit, artifactRef: ARTIFACT_REF },
      review: { decision: 'pass', summary: `Reviewed ${realRoot}.`, verification: staleRecord, reviewer: 'reviewer' },
      risks: [`Reviewed ${realRoot}.`],
      pr: { number: 64, url: 'https://github.com/someone/Mirally/pull/64', body: markdown },
      markdown,
    },
    pr: { number: 64, protocol: { task: 'req-demo', head: artifact.commit }, body: markdown },
  };
}
if (real.length) {
  // Deterministic pass under the synthetic identity first, then the production
  // one (runtime-discovered machine identity) on a fresh copy.
  for (const options of [{ markers: [...IDENTITY.markers, realUser].filter(Boolean) }, {}]) {
    const stale = staleStoredItem();
    const first = scrubStoredBrief(stale, options);
    assert.ok(first.changed > 0, 'a stale record must change');
    assert.deepEqual(findLocalLeaks(stale.brief.markdown, options), [], `scrubbed brief still leaks: ${findLocalLeaks(stale.brief.markdown, options).join(', ')}`);
    assert.ok(stale.brief.markdown.includes('- review evidence: Worktree <local-path>'), stale.brief.markdown);
    assert.ok(stale.brief.markdown.includes(artifact.commit), 'the pinned commit survives in the stored markdown');
    assert.ok(stale.brief.markdown.includes('mirally/framework/notebook.py'), 'repo-relative paths survive in prose');
    assert.equal(stale.brief.change.artifactRef, ARTIFACT_REF, 'stored references are never rewritten');
    assert.equal(stale.brief.change.commit, artifact.commit, 'stored commits are never rewritten');
    assert.equal(stale.brief.pr.url, 'https://github.com/someone/Mirally/pull/64', 'GitHub links are never rewritten');
    assert.deepEqual(stale.pr.body, stale.brief.markdown, 'the stored PR body mirror becomes the same clean text');
    const second = scrubStoredBrief(stale, options);
    assert.equal(second.changed, 0, 'scrubbing is idempotent');
    if (!options.markers) {
      assert.ok(!stale.brief.markdown.includes(realHome), `home directory survived the production identity: ${stale.brief.markdown}`);
      if (realUser.length >= 4) assert.ok(!stale.brief.markdown.includes(realUser), 'account name survived the production identity');
    }
  }
}

console.log('public-text probe OK');
