import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtempSync, writeFileSync, rmSync, readdirSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";
import os from "node:os";
import { spawnSync } from "node:child_process";
import { createMemoryClient, memoryPackageRoot } from "./memory/memory-client.mjs";
import pluginEntry, { completionAgentParams, createMemoryPlugin, createSharedMemoryPlugin, eligibleMemoryContext, GUIDANCE } from "./memory/index.mjs";

function bridgeFake(respond = request => ({ op: request.op, args: request.args })) {
  const processes = [];
  const calls = [];
  const spawnImpl = (_python, args, options) => {
    const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.pid = 0;
    child.stdout.setEncoding = child.stderr.setEncoding = () => {};
    child.stdin = { writable: true, write(text, _encoding, callback) {
      const request = JSON.parse(text); calls.push(request);
      const result = respond(request, child);
      if (result !== undefined) setImmediate(() => child.stdout.emit("data", `${JSON.stringify({ id: request.id, ok: true, result })}\n`));
      if (request.op === "stop") setImmediate(() => child.emit("close", 0));
      callback?.();
    }, end() {} };
    child.kill = () => { child.killed = true; child.emit("close", 0); };
    processes.push({ child, args, options }); return child;
  };
  return { processes, calls, spawnImpl };
}
const context = (allowed = true, overrides = {}) => ({ agentId: "main", sessionKey: "agent:main:main", trigger: "user", messageProvider: "webchat",
  toolAuthority: { assertActive() {}, allows: name => allowed && name === "memory_search" }, hookInvocation: { assertActive() {} }, ...overrides });
function apiFake(overrides = {}) {
  const config = { agents: { ownership: "explicit", defaults: { systemAgent: { agentId: "main" } }, entries: { main: {}, ufo: {} } } };
  return { pluginConfig: { configPath: "C:/temporary/memory.yaml" }, config, logger: { warn() {} },
    runtime: { config: { current: () => config }, llm: { complete: async () => ({ text: '{"summary":"test"}', usage: { totalTokens: 1 }, provider: "test", model: "test" }) } }, ...overrides };
}
function clientFake(settings = { enabled: true, context_token_budget: 1000, scope: "user:local", semantics: { enabled: false } }) {
  const calls = []; let started = 0; let stopped = 0;
  const client = { async start() { started++; return { status: "ready" }; }, async stop() { stopped++; },
    async request(op, args) { calls.push({ op, args });
      if (op === "settings") return settings;
      if (op === "context") return { text: "2026-10-11: worked on MCP; source: obs-1", memories: [{ id: "m1" }] };
      if (op === "search") return { memories: [{ memory_id: "m1", summary: "MCP integration", source_observation_ids: ["obs-1"], kind: "episode", score: 0.8 }] };
      if (op === "read") return { memory: { memory_id: "m1", title: "MCP" }, text: "Line one\nLine two", from_line: args.from_line, line_count: 2 };
      if (op === "complete_job") return { status: "completed", ok: true, memory_ids: ["m1"] };
      return {};
    }, calls, get started() { return started; }, get stopped() { return stopped; } };
  return client;
}

test("worker starts with the service and queries share it without restart", async () => {
  const fake = bridgeFake();
  const client = createMemoryClient({ root: memoryPackageRoot, pythonPath: "test-python", configPath: "test.yaml", spawnImpl: fake.spawnImpl });
  await assert.rejects(client.request("search", { query: "x" }), /unavailable/);
  assert.equal(fake.processes.length, 0);
  await client.start(); await client.request("search", { query: "x" }); await client.request("context", { query: "x" });
  assert.equal(fake.processes.length, 1);
  assert.equal(fake.processes[0].options.windowsHide, true);
  assert.deepEqual(fake.processes[0].args.slice(0, 4), ["-m", "openassist.runtime.memory.worker", "--root", memoryPackageRoot]);
  await client.stop();
});

test("relative memory profiles resolve from the configured product root rather than the Gateway working directory", async () => {
  const fake = bridgeFake(); const root = path.resolve(os.tmpdir(), "mirally-custom-root");
  const client = createMemoryClient({ root, pythonPath: "test-python", configPath: "config/memory.yaml", spawnImpl: fake.spawnImpl });
  await client.start();
  assert.equal(fake.processes[0].args.at(-1), path.resolve(root, "config/memory.yaml"));
  assert.equal(fake.processes[0].options.cwd, root);
  await client.stop();
});

test("timed-out or cancelled queries do not kill background processing or other queries", async () => {
  const fake = bridgeFake(request => request.op === "slow" ? undefined : { done: request.op });
  const client = createMemoryClient({ pythonPath: "test-python", configPath: "test.yaml", spawnImpl: fake.spawnImpl });
  await client.start();
  await assert.rejects(client.request("slow", {}, { timeoutMs: 15 }), /timed out/);
  assert.equal(fake.processes[0].child.killed, undefined);
  const controller = new AbortController(); const pending = client.request("slow", {}, { signal: controller.signal }); controller.abort();
  await assert.rejects(pending, /cancelled/);
  assert.deepEqual(await client.request("health"), { done: "health" });
  assert.equal(fake.processes.length, 1); assert.equal(client.pendingCount, 0);
  await client.stop();
});

test("SDK entry remains an additive ordinary plugin without replacing memory-core or tools", () => {
  const registrations = []; const api = apiFake({ registerService: value => registrations.push(["service", value]),
    registerMemoryCorpusSupplement: value => registrations.push(["corpus", value]), on: (...args) => registrations.push(["hook", ...args]),
    registerGatewayMethod: (...args) => registrations.push(["rpc", ...args]) });
  pluginEntry.register(api);
  assert.equal(pluginEntry.id, "mirally-memory");
  assert.deepEqual(registrations.map(item => item[0]), ["service", "corpus", "hook", "hook", "rpc"]);
  assert.equal(registrations[2][3].registrationId, "mirally-memory-prompt-observation");
  assert.equal(registrations[3][3].requiresToolAuthority, true);
  assert.equal(registrations[4][1], "mirally.memory.status"); assert.equal(registrations[4][3].scope, "operator.read");
  assert.match(GUIDANCE, /corpus='all'/); assert.match(GUIDANCE, /corpus='wiki'/);
});

test("automatic recall only uses current text and obeys exact native authority", async () => {
  const client = clientFake(); const plugin = createMemoryPlugin(apiFake(), { client });
  await plugin.service.start({});
  const before = client.calls.length;
  assert.equal(await plugin.beforePrompt({ currentUserMessage: "", prompt: "old private request" }, context()), undefined);
  assert.equal(await plugin.beforePrompt({ currentUserMessage: "MCP" }, context(false)), undefined);
  assert.equal(await plugin.beforePrompt({ currentUserMessage: "MCP" }, context(true, { agentId: "ufo" })), undefined);
  assert.equal(client.calls.length, before);
  const result = await plugin.beforePrompt({ currentUserMessage: "MCP", prompt: "old request", messages: [{ private: "ignored" }] }, context());
  assert.match(result.prependContext, /source: obs-1/); assert.match(result.prependContext, /untrusted/); assert.match(result.prependContext, /Current collector\/process state is unverified/);
  assert.equal(client.calls.at(-1).args.query, "MCP"); assert.equal(client.calls.at(-1).args.token_budget, 1000);
  assert.equal(client.started, 1); await plugin.service.stop(); assert.equal(client.stopped, 1);
});

test("native registry views share one started worker and counters while retaining their own authorization", async () => {
  const firstClient = clientFake(); const secondClient = clientFake(); const otherClient = clientFake();
  const configPath = path.join(os.tmpdir(), `memory-registries-${Date.now()}.yaml`);
  const firstApi = apiFake({ pluginConfig: { root: memoryPackageRoot, configPath } });
  const secondApi = apiFake({ pluginConfig: { root: memoryPackageRoot, configPath } });
  const first = createSharedMemoryPlugin(firstApi, { client: firstClient });
  const second = createSharedMemoryPlugin(secondApi, { client: secondClient });
  const unrelated = createSharedMemoryPlugin(apiFake({ pluginConfig: { root: memoryPackageRoot, configPath: configPath + ".other" } }), { client: otherClient });
  await first.service.start({}); await second.service.start({});
  assert.equal(firstClient.started, 1); assert.equal(secondClient.started, 0); assert.equal(otherClient.started, 0);
  assert.match((await second.beforePrompt({ prompt: "MCP" }, context())).prependContext, /obs-1/);
  assert.equal((await first.diagnostics()).recallCounters.injected, 1);
  assert.equal(await second.beforePrompt({ prompt: "MCP" }, context(false)), undefined);
  assert.equal((await first.diagnostics()).lastSkip.reason, "recall-tool-denied");
  secondApi.runtime.config.current = () => ({ agents: { entries: { main: { sandbox: { mode: "all" } } } } });
  assert.equal(await second.beforePrompt({ prompt: "MCP" }, context()), undefined);
  assert.equal((await first.diagnostics()).lastSkip.reason, "configured-sandbox");
  assert.equal(await unrelated.beforePrompt({ prompt: "MCP" }, context()), undefined);
  assert.equal((await unrelated.diagnostics()).recallCounters.injected, 0);
  await second.service.stop(); assert.equal(firstClient.stopped, 0);
  await first.service.stop(); assert.equal(firstClient.stopped, 1);
});

test("shared service ownership transfers on disposal and restarts after a full stop", async () => {
  const firstClient = clientFake(); const secondClient = clientFake();
  const configPath = path.join(os.tmpdir(), `memory-handoff-${Date.now()}.yaml`);
  const config = { root: memoryPackageRoot, configPath };
  const first = createSharedMemoryPlugin(apiFake({ pluginConfig: config }), { client: firstClient });
  const second = createSharedMemoryPlugin(apiFake({ pluginConfig: config }), { client: secondClient });
  await first.service.start({}); await second.service.start({});
  await first.service.stop(); assert.equal(firstClient.stopped, 1); assert.equal(secondClient.started, 1);
  assert.match((await first.beforePrompt({ prompt: "MCP" }, context())).prependContext, /obs-1/);
  await second.service.stop(); assert.equal(secondClient.stopped, 1); assert.equal(second.ready, false);
  await second.service.start({}); assert.equal(secondClient.started, 2); assert.equal(second.ready, true);
  await second.service.stop(); assert.equal(secondClient.stopped, 2);
});

test("automatic prompt respects disabled prompt injection and sandbox/harness boundaries", async () => {
  const client = clientFake();
  const liveConfig = { plugins: { entries: { "mirally-memory": { hooks: { allowPromptInjection: false } } } } };
  const api = apiFake({ runtime: { config: { current: () => liveConfig } } });
  const plugin = createMemoryPlugin(api, { client }); await plugin.service.start({});
  assert.equal(await plugin.beforePrompt({ prompt: "MCP" }, context()), undefined);
  assert.equal(eligibleMemoryContext(api, context(true, { sandboxed: true })), false);
  assert.equal(eligibleMemoryContext(api, context(true, { sessionKey: "agent:main:subagent:a" })), false);
  liveConfig.agents = { entries: { main: { sandbox: { mode: "all" } } } };
  assert.equal(eligibleMemoryContext(api, context()), false);
  liveConfig.agents.entries.main = { models: { "test/model": { agentRuntime: { id: "mirally-ufo" } } } };
  assert.equal(eligibleMemoryContext(api, context(true, { modelProviderId: "test", modelId: "model" })), false);
  await plugin.service.stop();
});

test("additive corpus returns SDK search/read formats with line and evidence provenance", async () => {
  const client = clientFake(); const plugin = createMemoryPlugin(apiFake(), { client }); await plugin.service.start({});
  const params = { agentId: "main", agentSessionKey: "agent:main:main", query: "MCP" };
  const results = await plugin.corpus.search(params);
  assert.equal(results[0].corpus, "mirally-memory"); assert.equal(results[0].path, "mirally-memory/m1"); assert.match(results[0].provenanceLabel, /obs-1/);
  const read = await plugin.corpus.get({ ...params, lookup: results[0].path, fromLine: 2, lineCount: 2 });
  assert.equal(read.fromLine, 2); assert.equal(read.lineCount, 2);
  assert.equal(await plugin.corpus.get({ ...params, lookup: "mirally-memory/../config" }), null);
  assert.deepEqual(await plugin.corpus.search({ ...params, sandboxed: true }), []);
  await plugin.service.stop();
});

test("semantic pump uses only host-managed direct completion after worker budget admission", async () => {
  const client = clientFake({ enabled: true, semantics: { enabled: true, interval_seconds: 3600 } });
  const original = client.request; let admitted = true; const modelCalls = [];
  client.request = async (op, args) => {
    if (op === "claim_job") return admitted ? { status: "ready", job: { job_id: "j1", lease_token: "l1", payload: { system_prompt: "Return JSON", prompt: "obs-1", agent_id: "main", max_tokens: 1000 } } } : { status: "idle", job: null };
    return original(op, args);
  };
  const api = apiFake(); api.runtime.llm.complete = async params => { modelCalls.push(params); return { text: '{"summary":"MCP","source_observation_ids":["obs-1"],"facts":[]}', usage: { totalTokens: 50 }, provider: "host", model: "host" }; };
  const plugin = createMemoryPlugin(api, { client }); await plugin.service.start({}); await plugin.pumpOnce();
  assert.equal(modelCalls.length, 1); assert.equal(modelCalls[0].execution, undefined); assert.equal(modelCalls[0].messages.length, 2); assert.equal(modelCalls[0].reasoning, "off");
  assert.equal(modelCalls[0].agentId, undefined);
  assert.equal(modelCalls[0].tools, undefined); assert.equal(client.calls.at(-1).op, "complete_job");
  assert.equal(client.calls.at(-1).args.usage.totalTokens, 50); assert.equal(typeof client.calls.at(-1).args.result, "string");
  admitted = false; await plugin.pumpOnce(); assert.equal(modelCalls.length, 1);
  await plugin.service.stop();
});

test("background completion inherits the configured system agent without bypassing explicit overrides", () => {
  const api = apiFake();
  assert.deepEqual(completionAgentParams(api, "main"), {});
  assert.deepEqual(completionAgentParams(api, "ufo"), { agentId: "ufo" });
  const config = { agents: { ownership: "explicit", defaults: { systemAgent: { agentId: "other" } }, entries: { main: {}, other: {} } } };
  const custom = apiFake({ runtime: { config: { current: () => config } } });
  assert.deepEqual(completionAgentParams(custom, "main"), { agentId: "main" });
  assert.deepEqual(completionAgentParams(custom, "other"), {});
});

test("official unbound LLM capability accepts inherited ownership and preserves its completion allowlist", { timeout: 45000 }, async () => {
  const dist = path.join(memoryPackageRoot, "integrations", "openclaw", "node_modules", "openclaw", "dist");
  const bundle = readdirSync(dist).find(name => name.startsWith("runtime-llm.runtime-") && name.endsWith(".mjs"));
  assert.ok(bundle, "Pinned official OpenClaw LLM runtime must be installed");
  const { createRuntimeLlm } = await import(pathToFileURL(path.join(dist, bundle)).href);
  const config = { agents: { ownership: "explicit", defaults: { systemAgent: { agentId: "main" } },
    entries: { main: { model: "openai/test-model" }, ufo: {} } },
    plugins: { entries: { "mirally-memory": { llm: { allowedCompletionModels: [] } } } } };
  const llm = createRuntimeLlm({ getConfig: () => config, logger: { debug() {}, info() {}, warn() {}, error() {} },
    authority: { caller: { kind: "plugin", id: "mirally-memory" }, pluginIdForPolicy: "mirally-memory" } });
  const request = { messages: [{ role: "user", content: "test only" }], purpose: "memory ownership regression" };
  await assert.rejects(llm.complete({ ...request, agentId: "main" }), /cannot override the target agent/);
  // Empty completion allowlist stops before credential/provider acquisition; this test performs zero API calls.
  await assert.rejects(llm.complete({ ...request, ...completionAgentParams({ config }, "main") }), /allowlist has no valid models/);
});

test("official authorized prompt hook supplies live exact tool authority and a current invocation", { timeout: 45000 }, async () => {
  const dist = path.join(memoryPackageRoot, "integrations", "openclaw", "node_modules", "openclaw", "dist");
  const bundle = (prefix, contains = "") => pathToFileURL(path.join(dist, readdirSync(dist).find(name => name.startsWith(prefix) && name.endsWith(".mjs") && readFileSync(path.join(dist, name), "utf8").includes(contains)))).href;
  const { t: createHookRunner } = await import(bundle("hooks-", "createHookRunner as t"));
  const { t: buildChannels } = await import(bundle("hook-agent-context-"));
  const client = clientFake(); const plugin = createMemoryPlugin(apiFake(), { client }); await plugin.service.start({});
  try {
    const runner = createHookRunner({ plugins: [{ id: "mirally-memory" }], typedHooks: [{ pluginId: "mirally-memory", hookName: "before_prompt_build", handler: plugin.beforePrompt, requiresToolAuthority: true }] });
    const ctx = { agentId: "main", sessionKey: "agent:main:explicit:test", trigger: "user", ...buildChannels({ messageProvider: "webchat", sessionKey: "agent:main:explicit:test" }) };
    const result = await runner.runAuthorizedPromptBuild({ prompt: "MCP", messages: [] }, ctx,
      { toolAuthorityFingerprint: "test-native-turn", activeToolNames: ["memory_search", "tool_call"], assertHostActive() {} });
    assert.match(result.prependContext, /obs-1/);
    const denied = await runner.runAuthorizedPromptBuild({ prompt: "MCP", messages: [] }, ctx,
      { toolAuthorityFingerprint: "test-native-turn", activeToolNames: ["tool_call"], assertHostActive() {} });
    assert.equal(denied, undefined); assert.equal((await plugin.diagnostics()).lastSkip.reason, "recall-tool-denied");
  } finally { await plugin.service.stop(); }
});

test("model failures release a job lease and never spin through more model calls", async () => {
  const client = clientFake({ enabled: true, semantics: { enabled: true, interval_seconds: 3600 } });
  const original = client.request;
  client.request = async (op, args) => op === "claim_job" ? { job: { job_id: "j1", lease_token: "l1", payload: {} } } : original(op, args);
  const api = apiFake(); api.runtime.llm.complete = async () => { throw new Error("provider unavailable"); };
  const plugin = createMemoryPlugin(api, { client }); await plugin.service.start({}); await plugin.pumpOnce();
  assert.equal(client.calls.at(-1).op, "fail_job"); assert.match(client.calls.at(-1).args.error, /provider unavailable/);
  await plugin.service.stop();
});

test("semantic failures remain visible through healthy worker polls until a committed retry", async () => {
  const client = clientFake({ enabled: true, semantics: { enabled: true, interval_seconds: 3600 } });
  const original = client.request; let budgetDenied = false;
  client.request = async (op, args) => op === "claim_job" ? budgetDenied ? { status: "budget_exhausted", job: null }
    : { job: { job_id: "j1", lease_token: "l1", payload: {} } } : op === "health" ? { status: "ready", maintenance_status: "ready" } : original(op, args);
  const api = apiFake(); let fails = true; let clears = 0;
  api.runtime.llm.complete = async () => { if (fails) throw new Error("provider unavailable"); return { text: "{}", usage: { totalTokens: 4 }, provider: "fixture", model: "fixture" }; };
  const plugin = createMemoryPlugin(api, { client });
  await plugin.service.start({ serviceHealth: { reportFailure() {}, clearFailure() { clears++; } } });
  await plugin.pumpOnce(); const clearsBeforePoll = clears;
  let status = await plugin.diagnostics(); assert.equal(clears, clearsBeforePoll);
  assert.equal(status.semantic.state, "retry_pending"); assert.match(status.semantic.lastError, /provider unavailable/);
  assert.equal(status.semantic.lastSuccessUtcIso, null); assert.equal(status.semantic.completedJobs, 0);
  budgetDenied = true; await plugin.pumpOnce(); status = await plugin.diagnostics();
  assert.equal(status.semantic.state, "budget_exhausted"); assert.match(status.semantic.lastError, /provider unavailable/);
  budgetDenied = false; fails = false; await plugin.pumpOnce(); status = await plugin.diagnostics();
  assert.equal(status.semantic.completedJobs, 1); assert.equal(status.semantic.lastSuccessJobId, "j1");
  assert.equal(status.semantic.lastError, null); assert.equal(status.semantic.lastUsage.totalTokens, 4);
  assert.ok(status.semantic.lastSuccessUtcIso); assert.equal(JSON.stringify(status.semantic).includes('"text"'), false);
  await plugin.service.stop();
});

test("stale or rejected completion acknowledgements cannot claim a successful memory synthesis", async () => {
  const client = clientFake({ enabled: true, semantics: { enabled: true, interval_seconds: 3600 } });
  const original = client.request; let staleSource = false;
  client.request = async (op, args) => op === "claim_job" ? { job: { job_id: "j1", lease_token: "l1", payload: {} } }
    : op === "complete_job" ? staleSource ? { status: "stale_source_revision", ok: true, memory_ids: [] } : { status: "stale_lease", ok: false } : original(op, args);
  const plugin = createMemoryPlugin(apiFake(), { client }); await plugin.service.start({}); await plugin.pumpOnce();
  let status = await plugin.diagnostics(); assert.equal(status.semantic.completedJobs, 0);
  assert.match(status.semantic.lastError, /not committed.*stale_lease/); assert.equal(status.semantic.failedJobs, 1);
  assert.equal(client.calls.at(-2).op, "fail_job"); assert.equal(client.calls.at(-2).args.usage.totalTokens, 1);
  staleSource = true; await plugin.pumpOnce(); status = await plugin.diagnostics();
  assert.equal(status.semantic.discardedJobs, 1); assert.equal(status.semantic.completedJobs, 0); assert.equal(status.semantic.lastSuccessUtcIso, null);
  assert.equal(status.semantic.state, "discarded_stale_source"); await plugin.service.stop();
});

test("semantic capability absence is visible in service health while reads remain available", async () => {
  const client = clientFake({ enabled: true, semantics: { enabled: true, interval_seconds: 3600 } });
  const reported = []; const api = apiFake({ runtime: { config: { current: () => ({}) } } });
  const plugin = createMemoryPlugin(api, { client });
  await plugin.service.start({ serviceHealth: { reportFailure: error => reported.push(error.message) } });
  assert.match(reported[0], /host LLM capability/); assert.equal(plugin.ready, true);
  await plugin.service.stop();
});

test("service shutdown cancels the single in-flight model and releases its job before closing worker", async () => {
  const client = clientFake({ enabled: true, semantics: { enabled: true, interval_seconds: 3600 } });
  const original = client.request; let calls = 0; let modelEntered;
  const entered = new Promise(resolve => { modelEntered = resolve; });
  client.request = async (op, args) => op === "claim_job" ? { job: { job_id: "j1", lease_token: "l1", payload: {} } } : original(op, args);
  const api = apiFake(); api.runtime.llm.complete = params => { calls++; modelEntered(); return new Promise((_, reject) => params.signal.addEventListener("abort", () => reject(new Error("cancelled")), { once: true })); };
  const plugin = createMemoryPlugin(api, { client }); await plugin.service.start({});
  const first = plugin.pumpOnce(); const second = plugin.pumpOnce(); await entered;
  assert.equal(calls, 1); await plugin.service.stop(); await Promise.all([first, second]);
  assert.equal(client.calls.at(-1).op, "fail_job"); assert.equal(client.stopped, 1);
});

test("real worker recall retrieves dated evidence while unrelated questions inject no history", { timeout: 45000 }, async () => {
  const temporary = mkdtempSync(path.join(os.tmpdir(), "mirally-memory-context-"));
  const database = path.join(temporary, "memory.sqlite3"); const configPath = path.join(temporary, "memory.yaml");
  const activityPath = path.join(temporary, "activity.yaml");
  writeFileSync(activityPath, `enabled: false\nstorage:\n  database_path: ${JSON.stringify(path.join(temporary, "absent-observations.sqlite3"))}\n`);
  writeFileSync(configPath, `enabled: true\ndatabase_path: ${JSON.stringify(database)}\nactivity_config_path: ${JSON.stringify(activityPath)}\nsemantics:\n  enabled: false\n`);
  const seed = spawnSync(path.join(memoryPackageRoot, ".venv", "Scripts", "python.exe"), ["-c",
    "from pathlib import Path; import sys; from openassist.core.memory import MemoryStore; s=MemoryStore(Path(sys.argv[1])); s.put_memory({'id':'memory_sample','title':'OpenClaw MCP design','content':'Observed project work on MCP integration; source observation obs-1.','kind':'episodic','scope':'user/local','provenance':'observed','status':'active','source_observation_ids':['obs-1'],'observed_start_utc_ms':1791702575297,'observed_end_utc_ms':1791702651320}); s.close()", database],
    { cwd: memoryPackageRoot, encoding: "utf8", windowsHide: true });
  assert.equal(seed.status, 0, seed.stderr);
  const api = apiFake({ pluginConfig: { root: memoryPackageRoot, configPath } }); const plugin = createMemoryPlugin(api);
  try {
    const failures = []; await plugin.service.start({ serviceHealth: { reportFailure: error => failures.push(error.message) } });
    assert.equal(plugin.ready, true, failures.join("; "));
    const recalled = await plugin.beforePrompt({ currentUserMessage: "MCP" }, context());
    assert.match(recalled.prependContext, /2026-10-11/); assert.match(recalled.prependContext, /obs-1/);
    const unrelated = await plugin.beforePrompt({ currentUserMessage: "一小时有多少分钟？" }, context());
    assert.equal(unrelated, undefined);
    const status = await plugin.diagnostics();
    assert.deepEqual(status.recallCounters, { lookups: 2, injected: 1, empty: 1, failed: 0 });
    assert.equal(status.lastRecall.outcome, "empty"); assert.equal(status.lastRecall.sessionKeyHash.length, 24);
    assert.equal(JSON.stringify(status).includes("一小时"), false); assert.equal(JSON.stringify(status).includes("MCP"), false);
  } finally { await plugin.service.stop(); rmSync(temporary, { recursive: true, force: true }); }
});

test("worker maintenance degradation is reported without treating a successful pipe response as healthy", async () => {
  const client = clientFake(); client.start = async () => ({ status: "ready", maintenance_status: "degraded", last_error: "index issue" });
  const reported = []; const plugin = createMemoryPlugin(apiFake(), { client });
  await plugin.service.start({ serviceHealth: { reportFailure: error => reported.push(error.message) } });
  assert.match(reported[0], /degraded.*index issue/); await plugin.service.stop();
});

test("real repository Python worker runs independently of chat and stops on parent EOF", { timeout: 45000 }, async () => {
  const temporary = mkdtempSync(path.join(os.tmpdir(), "mirally-memory-plugin-"));
  const configPath = path.join(temporary, "memory.yaml");
  writeFileSync(configPath, `enabled: false\ndatabase_path: ${JSON.stringify(path.join(temporary, "memory.sqlite3"))}\nsemantics:\n  enabled: false\n`);
  const client = createMemoryClient({ root: memoryPackageRoot, configPath });
  try {
    const health = await client.start(); assert.ok(health && typeof health === "object");
    const settings = await client.request("settings"); assert.equal(settings.enabled, false); assert.equal(settings.starts_collector, false);
    const child = client.process;
    child.stdin.end();
    await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error("Memory worker did not exit after EOF")), 5000); child.once("close", () => { clearTimeout(timer); resolve(); }); });
    assert.equal(client.process, null);
  } finally { await client.stop(); rmSync(temporary, { recursive: true, force: true }); }
});
