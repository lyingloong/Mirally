import test, {after} from "node:test";
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {randomUUID} from "node:crypto";
import {Readable} from "node:stream";
import {mkdtempSync, readFileSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import path from "node:path";
import {diagnosticMetadata} from "../integrations/openclaw/ufo/diagnostics.mjs";
import {createUfoHarness, preparedRoute, routeSupport, taskContext, readFrames, workerMode, HARNESS_ID, WORKER_MODES} from "../integrations/openclaw/ufo/harness.mjs";

const model = {api: "openai-completions", baseUrl: "http://127.0.0.1:1234/v1", input: ["text", "image"]};
const testRoot = mkdtempSync(path.join(tmpdir(), "mirally-ufo-harness-"));
after(() => rmSync(testRoot, {recursive:true, force:true}));
const route = {requestedRuntime: HARNESS_ID, modelProvider: {...model, requestTransportOverrides: "none",
  preparedAuth: {mode: "api-key"}}};

test("explicit supported transport only; auth and request overrides never bypass policy", () => {
  assert.equal(routeSupport(route).supported, true);
  assert.equal(routeSupport({...route, requestedRuntime: "auto"}).supported, false);
  assert.equal(routeSupport({...route, modelProvider: {...route.modelProvider, requestTransportOverrides: "present"}}).supported, false);
  assert.equal(routeSupport({...route, modelProvider: {...route.modelProvider, preparedAuth: {mode: "oauth"}}}).supported, false);
});
const compatibleModel = {...model, id: "vision", reasoning: true, compat: {
  thinkingFormat: "openai", supportsReasoningEffort: true,
  supportedReasoningEfforts: ["none", "low", "medium", "xhigh"],
  reasoningEffortMap: {minimal: "low", high: "xhigh"},
}};
function compatibilityConfig() {
  return {models: {providers: {synthetic: {...model, models: [structuredClone(compatibleModel)]}}}};
}
const flaggedRoute = {...route, provider: "synthetic", modelId: "vision",
  modelProvider: {...route.modelProvider, requestTransportOverrides: "present"}};
test("known reasoning compatibility is supported without bypassing transport restrictions", () => {
  const config = compatibilityConfig();
  assert.equal(routeSupport(flaggedRoute, config).supported, true);
  assert.equal(routeSupport(flaggedRoute).supported, false);
  for (const change of [
    cfg => cfg.models.providers.synthetic.headers = {"x-test": "test"},
    cfg => cfg.models.providers.synthetic.request = {proxy: {url: "http://127.0.0.1"}},
    cfg => cfg.models.providers.synthetic.timeoutSeconds = 5,
    cfg => cfg.models.providers.synthetic.models[0].params = {extra: true},
    cfg => cfg.models.providers.synthetic.models[0].compat.supportsStore = false,
    cfg => cfg.models.providers.synthetic.models[0].compat.thinkingFormat = "qwen",
    cfg => cfg.agents = {defaults: {params: {extra: true}}},
  ]) {
    const modified = compatibilityConfig(); change(modified);
    assert.equal(routeSupport(flaggedRoute, modified).supported, false);
  }
});
test("thinking off and high are mapped exactly for the prepared model", () => {
  const params = {modelRegistry: {find: () => compatibleModel}, resolvedApiKey: "test-only", modelId: "vision"};
  assert.equal(preparedRoute({...params, thinkLevel: "off"}).parameters.reasoning_effort, "none");
  assert.equal(preparedRoute({...params, thinkLevel: "high"}).parameters.reasoning_effort, "xhigh");
  assert.throws(() => preparedRoute({...params, thinkLevel: "ultra"}));
});
test("context preserves exact prior text and refuses overflow", () => {
  const exact = "邮件正文：您好\n\n保留全部段落";
  assert.equal(taskContext([{role: "user", content: "写邮件"}, {role: "assistant", content: exact}]).previous_tasks[0].result, exact);
  assert.throws(() => taskContext([{role: "user", content: "x".repeat(33000)}]));
});
test("prepared route honors generation controls and refuses unsupported settings", () => {
  const params = {modelRegistry: {find: () => model}, resolvedApiKey: "test-only", modelId: "vision", thinkLevel: "off"};
  assert.equal(preparedRoute(params).key, "test-only");
  assert.throws(() => preparedRoute({...params, thinkLevel: "high"}));
  assert.throws(() => preparedRoute({...params, streamParams: {headers: {}}}));
});
test("JSON framing handles split UTF-8 and rejects partial shutdown", async () => {
  const bytes = Buffer.from('{"text":"中文"}\n');
  const values = [];
  for await (const frame of readFrames(Readable.from([bytes.subarray(0, 11), bytes.subarray(11)]))) values.push(frame);
  assert.deepEqual(values, [{text: "中文"}]);
  await assert.rejects(async () => {for await (const frame of readFrames(Readable.from(['{"broken":']))) {}});
});

test("direct UFO and host-created child sessions select the desktop subtask mode", () => {
  assert.equal(workerMode({}), WORKER_MODES.desktopSubtask);
  assert.equal(workerMode({spawnedBy: "agent:main:session:123"}), WORKER_MODES.desktopSubtask);
  assert.equal(workerMode({spawnedBy: ""}), WORKER_MODES.desktopSubtask);
});

function params(overrides = {}) {
  const id = randomUUID();
  return {...{sessionId: id, sessionKey: "agent:ufo:test-" + id, sessionFile: "unused", agentId: "ufo",
    runId: randomUUID(), prompt: "合成测试", provider: "test", modelId: "vision", timeoutMs: 3000,
    modelRegistry: {find: () => model}, resolvedApiKey: "test-only", thinkLevel: "off",
    hostCapabilities: {assertActive() {}, runBeforeToolCall: async () => ({blocked: false})},
    observeToolTerminal: () => ({}), workspaceDir: process.cwd(), config: {}}, ...overrides};
}
function child(program) {
  return spawn(process.execPath, ["--input-type=module", "-e", `
    import readline from 'node:readline';
    const send = value => process.stdout.write(JSON.stringify(value)+'\\n');
    send({type:'ready',version:1});
    const lines = readline.createInterface({input:process.stdin});
    lines.on('close',()=>process.exit(0));
    lines.on('line', line=>{ const frame=JSON.parse(line); ${program} });
  `], {stdio: ["pipe", "pipe", "pipe"], windowsHide: true});
}
function harness(program, extra = {}) {
  return createUfoHarness({}, {root:testRoot, spawnWorker: () => child(program), contextFor: async () => ({}),
    persistAssistant: async () => ({kind: "result"}), ...extra});
}

test("diagnostics follow startup, model and terminal events without private content", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "mirally-ufo-diagnostics-"));
  const input = params({prompt: "PRIVATE_PROMPT", resolvedApiKey: "PRIVATE_KEY"});
  try {
    const result = await harness(`
      if(frame.type==='run') {
        send({type:'event',event:{type:'phase',phase:'preparing',stage:'model_binding',summary:'PRIVATE_SUMMARY'}});
        send({type:'event',event:{type:'model_attempt',agent:'HOST_AGENT',attempt:1}});
        send({type:'event',event:{type:'waiting',stage:'model',elapsed_seconds:12,remaining_seconds:78}});
        send({type:'result',result:{status:'failed',reason:'TimeoutError',message:'PRIVATE_RESULT'}});
      }
    `, {root}).runAttempt(input);
    assert.equal(result.terminal.kind, "failed");
    const content = readFileSync(path.join(root, ".artifacts/ufo-runs", input.runId + ".jsonl"), "utf8");
    assert.doesNotMatch(content, /PRIVATE_/);
    const events = content.trim().split("\n").map(JSON.parse);
    assert.equal(events[0].type, "harness_entered");
    assert.ok(events.some(event => event.stage === "model_binding"));
    assert.ok(events.some(event => event.agent === "HOST_AGENT" && event.attempt === 1));
    assert.ok(events.some(event => event.stage === "model" && event.elapsed_seconds === 12));
    assert.equal(events.at(-1).status, "failed");
    assert.deepEqual(diagnosticMetadata({arguments:{secret:"PRIVATE_ARG"}, route:{key:"PRIVATE_KEY"},
      stack:[{file:"worker.py",function:"main",line:20,locals:"PRIVATE_LOCAL"}]}),
      {stack:[{file:"worker.py",function:"main",line:20}]});
  } finally { rmSync(root, {recursive:true, force:true}); }
});
test("business answer survives real worker IPC and transcript handoff", async () => {
  const result = await harness(`if(frame.type==='run') send({type:'result', result:{status:'partial',message:frame.mode+':第一部分已完成'}});`).runAttempt(params());
  assert.equal(result.terminal.kind, "ok");
  assert.equal(result.assistantTranscriptOwned, true);
  assert.match(result.assistantTexts[0], /部分完成.*desktop_subtask.*第一部分已完成/s);
});
test("a spawned OpenClaw child is sent directly to the desktop executor", async () => {
  const result = await harness(`if(frame.type==='run') send({type:'result', result:{status:'completed',message:frame.mode}});`).runAttempt(
    params({spawnedBy: "agent:main:session:parent"}));
  assert.equal(result.terminal.kind, "ok");
  assert.equal(result.assistantTexts[0], "desktop_subtask");
});
test("stop during a pending authorization revokes it and drains the process", async () => {
  const input = params(), controller = new AbortController(); input.abortSignal = controller.signal;
  input.hostCapabilities.runBeforeToolCall = () => new Promise(resolve => setTimeout(() => resolve({blocked: false}), 80));
  const instance = harness(`
    if(frame.type==='run') send({type:'request',id:'tool',operation:'authorize',name:'click',arguments:{},call_id:'c'});
    if(frame.type==='cancel') send({type:'result',result:{status:'cancelled',message:'stopped'}});
    if(frame.type==='response'&&frame.ok) {send({type:'result',result:{status:'completed',message:'BAD_ACTION'}});}
  `);
  const pending = instance.runAttempt(input);
  setTimeout(() => controller.abort(), 35);
  const result = await pending;
  assert.equal(result.terminal.kind, "aborted");
  assert.equal(result.assistantTranscriptOwned, false);
  assert.doesNotMatch(result.assistantTexts.join(), /BAD_ACTION/);
});
test("a non-interruptible native tool timeout force-stops the worker", async () => {
  const started = Date.now();
  const result = await harness(`
    if(frame.type==='run') send({type:'event',event:{type:'waiting',stage:'tool',timed_out:true,in_flight:true}});
  `, {registerRun: (_, value) => value}).runAttempt(params({timeoutMs: 10000}));
  assert.equal(result.terminal.kind, "timeout");
  assert.match(result.assistantTexts[0], /桌面操作超时/);
  assert.ok(Date.now() - started < 7000);
});
test("a worker that sends no post-handshake progress is stopped", async () => {
  const started = Date.now();
  const result = await harness(``).runAttempt(params({timeoutMs: 10000, ufoStartupTimeoutMs: 100}));
  assert.equal(result.terminal.kind, "failed");
  assert.ok(Date.now() - started < 5000);
});

test("startup heartbeats cannot hide a stuck native import from the deadline", async () => {
  const result = await harness(`
    if(frame.type==='run') {
      send({type:'event',event:{type:'phase',phase:'preparing',stage:'native_compatibility'}});
      setInterval(()=>send({type:'event',event:{type:'startup_wait',stage:'native_compatibility'}}),20);
    }
    if(frame.type==='cancel') send({type:'result',result:{status:'failed',message:'startup stopped'}});
  `).runAttempt(params({timeoutMs:10000,ufoStartupTimeoutMs:150}));
  assert.equal(result.terminal.kind, "failed");
  assert.equal(result.terminal.source, "startup");
});

test("startup timeout persists a useful reply when the worker reports late cancellation", async () => {
  let persisted;
  const result = await harness(`
    if(frame.type==='run') {
      send({type:'event',event:{type:'phase',phase:'preparing',stage:'native_compatibility'}});
      // This cleanup event arrives after the harness deadline.  It must not
      // replace the stage captured at the timeout boundary.
      setTimeout(()=>send({type:'event',event:{type:'waiting',stage:'initialization',stopping:true}}),130);
      setTimeout(()=>send({type:'result',result:{status:'cancelled',message:'late cancellation'}}),180);
    }
  `, {persistAssistant: async ({message}) => {persisted = message; return {kind:'result'};}})
    .runAttempt(params({timeoutMs:10000,ufoStartupTimeoutMs:100}));
  assert.equal(result.terminal.kind, "failed");
  assert.equal(result.terminal.source, "startup");
  assert.equal(result.assistantTranscriptOwned, true);
  assert.match(result.assistantTexts[0], /初始化超时/);
  assert.match(result.assistantTexts[0], /native_compatibility/);
  assert.doesNotMatch(result.assistantTexts[0], /阶段：initialization/);
  assert.match(JSON.stringify(persisted), /native_compatibility/);
});
test("question answers use the native nested answer schema", async () => {
  const instance = harness(`
    if(frame.type==='run') send({type:'request',operation:'ask',id:'q',text:'测试问题'});
    if(frame.type==='response') send({type:'result',result:{status:frame.ok?'completed':'failed',message:frame.value?.text??'failed'}});
  `, {question: async ({questions}) => ({status: "answered", answers: {answers: {[questions[0].id]: ["实际答案"]}}})});
  const result = await instance.runAttempt(params());
  assert.equal(result.assistantTexts[0], "实际答案");
});

test("ordinary authority remains required after a supplement was adopted", async () => {
  let handle, current = true, beforeCalls = 0;
  const input = params();
  input.hostCapabilities.runBeforeToolCall = async () => {beforeCalls++;return {blocked: false};};
  const instance = harness(`
    if(frame.type==='supplement') {
      send({type:'event',event:{type:'context_applied',receipt_ids:[frame.receipt_id]}});
      setTimeout(()=>send({type:'request',operation:'authorize',id:'next',name:'click',call_id:'next',arguments:{}}),40);
    }
    if(frame.type==='response') send({type:'result',result:{status:frame.ok?'completed':'failed',message:frame.ok?'BAD_ACTION':'revoked'}});
  `, {registerRun: (_, value) => {handle=value;}, unregisterRun() {}});
  const running = instance.runAttempt(input);
  while (!handle) await new Promise(resolve => setTimeout(resolve, 1));
  await handle.messageInjectionV2.queueMessage("Changed requirement", undefined, () => {
    if (!current) throw new Error("source revoked");
  }, "run");
  current = false;
  const result = await running;
  assert.equal(beforeCalls, 0);
  assert.equal(result.terminal.kind, "failed");
  assert.doesNotMatch(result.assistantTexts.join(), /BAD_ACTION/);
});

test("source-bound steering refuses before worker queue dispatch", async () => {
  let handle;
  const instance = harness(`
    if(frame.type==='supplement') send({type:'result',result:{status:'completed',message:'BAD_QUEUE'}});
    if(frame.type==='cancel') send({type:'result',result:{status:'cancelled',message:'stopped'}});
  `, {registerRun: (_, value)=>{handle=value;}, unregisterRun() {}});
  const running=instance.runAttempt(params());
  while(!handle) await new Promise(resolve=>setTimeout(resolve,1));
  assert.throws(()=>handle.messageInjectionV2.queueMessage("scoped",undefined,()=>{},"source-bound"));
  handle.abort();
  const result=await running;
  assert.doesNotMatch(result.assistantTexts.join(),/BAD_QUEUE/);
});

test("a confirmation waits for a correlated native decision", async () => {
  const input = params();let waits=0;
  input.hostCapabilities.requestApproval = async () => ({id:"approval"});
  input.hostCapabilities.waitForApproval = async ({approvalId}) => {
    assert.equal(approvalId,"approval");waits++;return {decision:"allow-once"};
  };
  const instance=harness(`
    if(frame.type==='run') send({type:'request',operation:'confirm',id:'confirm',text:'Synthetic only',action:'test'});
    if(frame.type==='response') send({type:'result',result:{status:'completed',message:frame.value?'confirmed':'denied'}});
  `);
  const result=await instance.runAttempt(input);
  assert.equal(waits,1);assert.equal(result.assistantTexts[0],"confirmed");
});
