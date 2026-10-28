import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadMirallyComposition, BASE_PATCH} from '../kernel/dsh/packages/mirally/mirally-product/composition.mjs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const require = createRequire(new URL('../kernel/dsh/packages/mirally/mirally-product/package.json', import.meta.url));
const {loadOverlayPatches, composeEntries} = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-app-boot')).href);

test('Mirally composes every shipped native DSH base row through the native patch engine', () => {
  const inherited = composeEntries([loadOverlayPatches('test', BASE_PATCH)]);
  const {entries} = loadMirallyComposition();
  const rows = new Map(entries.map(entry => [entry.id, entry]));
  assert.equal(rows.size, entries.length, 'no duplicate providers/ids');
  for (const entry of inherited) assert.ok(rows.has(entry.id), `native base row dropped: ${entry.id}`);
  assert.equal(rows.get('fs-sandbox').disabled, undefined);
  assert.equal(rows.get('compaction-basic').config.headroomTokens, 0);
  for (const id of ['token-meter','compaction-basic','tool-result-pruner','image-offload','session-query-sqlite',
    'subagent','tool-subagent','tool-subagent-fork','workflow-ptc','tool-todo','tool-goal','plan-mode',
    'approval','permission','tool-fs','tool-fs-search','tool-pwsh','tool-ask-user']) {
    assert.notEqual(rows.get(id)?.disabled, true, `${id} is a live native capability`);
    assert.match(rows.get(id)?.name ?? '', /^file:/);
  }
  for (const id of ['credentials','authorization','deepseek-account','session-log-deepseek','session-telemetry-otel',
    'llm-deepseek','llm-deepseek-account','llm-pi-ai','web-search-deepseek','web-deepseek-api']) {
    assert.equal(rows.get(id).disabled, true, `external owner must be explicit: ${id}`);
  }
  assert.equal(rows.get('tool-ask-user').config.mode, 'legacy');
  assert.equal(rows.get('tool-ask-user').config.timeout, -1);
  assert.equal(rows.get('system-prompt').config.includeRuntimeContext, true);
});
