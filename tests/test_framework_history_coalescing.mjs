/** Execute the actual retained UI coalescer and all its real dependencies. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {projectMessages} from '../kernel/dsh/packages/mirally/mirally-product/src/history-presentation.mjs';
const root = fileURLToPath(new URL('../', import.meta.url));

test('original UI coalesces durable and late live tool projections by the exact native invocation', {timeout: 30000}, async () => {
  const require = createRequire(join(root, 'frontend/control-ui/package.json'));
  const {build} = await import(pathToFileURL(require.resolve('vite')));
  const result = await build({configFile: join(root, 'frontend/control-ui/vite.config.ts'), logLevel: 'error',
    build: {write: false, minify: false, lib: {entry: join(root, 'frontend/control-ui/ui/src/pages/chat/chat-tool-activity-coalesce.ts'), formats: ['es']},
      rolldownOptions: {output: {codeSplitting: false}}}});
  const code = (Array.isArray(result) ? result[0] : result).output.find(file => file.type === 'chunk' && file.isEntry).code;
  const {coalesceToolActivityMessages} = await import('data:text/javascript;base64,' + Buffer.from(code + '\n//# sourceURL=original-tool-coalescer-test.mjs').toString('base64'));
  const log = [{seq: 0, time: 1, type: 'user/message', data: {id: 'user', source: {kind: 'user'}, content: [{type: 'text', text: '查看提醒'}]}},
    {seq: 1, time: 2, type: 'tool/call', data: {turn: 1, step: 1, callId: 'provider-reused', name: 'mirally_reminders', arguments: '{}'}},
    {seq: 2, time: 3, type: 'tool/result', sourceEventSeqs: [1], data: {turn: 1, step: 1, message: {toolCallId: 'provider-reused', isError: false, content: [{type: 'text', text: '真实结果'}]}}}];
  const rows = projectMessages(log, [{command_id: 'run', runtime_request_id: 'user'}], 0, {nativeSessionId: 'native-one'});
  const durable = rows[1], live = {role: 'assistant', runId: 'run', toolCallId: durable.toolCallId, timestamp: 2,
    __openclawToolStreamLive: true, __openclawToolStreamResultReceived: false,
    content: [{type: 'toolcall', name: 'mirally_reminders', arguments: {}}]};
  const item = (message, key) => ({kind: 'message', key, message});
  for (const sequence of [[live, durable], [durable, live]]) {
    const merged = coalesceToolActivityMessages([item(rows[0], 'user'), ...sequence.map((message, i) => item(message, String(i)))]);
    const tools = merged.filter(value => value.kind === 'message' && value.message.content.some(block => ['toolcall', 'tool_call'].includes(block.type)));
    assert.equal(tools.length, 1, 'History and a late live start must remain one card');
    const result = tools[0].message.content.filter(block => ['toolresult', 'tool_result'].includes(block.type));
    assert.equal(result.length, 1); assert.equal(result[0].text, '真实结果');
    assert.equal(tools[0].message.__openclawToolStreamResultReceived, true, 'A late running echo cannot resurrect the spinner');
  }
  const other = projectMessages(log, [{command_id: 'run', runtime_request_id: 'user'}], 10, {nativeSessionId: 'native-two'})[1];
  const both = coalesceToolActivityMessages([item(rows[0], 'user'), item(durable, 'old'), item(other, 'new')]);
  assert.equal(both.filter(value => value.kind === 'message' && value.message.toolCallId).length, 2, 'Provider call IDs repeated in another native generation do not collapse');
});
