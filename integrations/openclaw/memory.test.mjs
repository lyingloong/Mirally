import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtempSync, writeFileSync, rmSync, readdirSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";
import os from "node:os";
import { WebSocketServer } from "ws";
import { spawnSync } from "node:child_process";
import { createMemoryClient, memoryPackageRoot } from "./memory/memory-client.mjs";
import pluginEntry, { completionAgentParams, createMemoryPlugin as createMemoryPluginNative, createSharedMemoryPlugin, eligibleMemoryContext, memoryContextSkipReason, requestMemoryConsolidation, GUIDANCE } from "./memory/index.mjs";

const createMemoryPlugin = (api, options = {}) => createMemoryPluginNative(api,
  { dispatchOperation: run => run(), ...options });

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
  assert.deepEqual(fake.processes[0].args.slice(0, 4), ["-m", "mirally.runtime.memory.worker", "--root", memoryPackageRoot]);
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
  assert.deepEqual(registrations.map(item => item[0]), ["service", "corpus", "hook", "hook", "rpc", "rpc", "rpc"]);
  assert.equal(registrations[2][3].registrationId, "mirally-memory-prompt-observation");
  assert.equal(registrations[3][3].requiresToolAuthority, true);
  assert.equal(registrations[4][1], "mirally.memory.status"); assert.equal(registrations[4][3].scope, "operator.read");
  assert.equal(registrations[5][1], "mirally.memory.reloadSettings"); assert.equal(registrations[5][3].scope, "operator.admin");
  assert.equal(registrations[6][1], "mirally.memory.consolidate"); assert.equal(registrations[6][3].scope, "operator.admin");
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

const dashboardKey = "agent:main:dashboard:45d95984-2321-4cfd-8445-3a7f54334d60";
const ownerDashboardEntry = () => ({ createdVia: "operator",
  createdActor: { type: "human", source: "profile", id: "gateway-owner" },
  delivery: { kind: "internal" }, chatType: "direct", spawnDepth: 0,
  parentSessionKey: "agent:main:main" });
function dashboardApi(entry, calls = []) {
  const api = apiFake();
  api.runtime.agent = { session: { getSessionEntry(params) { calls.push(params); return entry; } } };
  return api;
}

test("official owner dashboard chats can inject context and search/read the native corpus", async () => {
  const calls = []; const entry = ownerDashboardEntry();
  entry.owner = { actor: { type: "human", id: "gateway-owner" } };
  const api = dashboardApi(entry, calls); const client = clientFake();
  const plugin = createMemoryPlugin(api, { client }); await plugin.service.start({});
  try {
    const result = await plugin.beforePrompt({ currentUserMessage: "MCP" }, context(true, { sessionKey: dashboardKey }));
    assert.match(result.prependContext, /obs-1/);
    const params = { agentId: "main", agentSessionKey: dashboardKey, query: "MCP" };
    const [hit] = await plugin.corpus.search(params);
    assert.equal(hit.path, "mirally-memory/m1");
    assert.equal((await plugin.corpus.get({ ...params, lookup: hit.path })).content, "Line one\nLine two");
    assert.deepEqual(client.calls.filter(call => ["context", "search", "read"].includes(call.op)).map(call => call.op), ["context", "search", "read"]);
    assert.equal(calls.length, 3);
    for (const call of calls) assert.deepEqual(call, { agentId: "main", sessionKey: dashboardKey, readConsistency: "latest" });
    // A native owner dashboard is linked to main, but is not a spawned agent.
    assert.equal(eligibleMemoryContext(dashboardApi(ownerDashboardEntry()), context(true, { sessionKey: dashboardKey })), true);
  } finally { await plugin.service.stop(); }
});

test("dashboard keys require authoritative local owner creation records for both recall paths", async t => {
  const variants = [
    ["missing record", undefined],
    ["wrong creator", { createdActor: { type: "human", source: "profile", id: "another-owner" } }],
    ["agent creator", { createdActor: { type: "agent", source: "profile", id: "gateway-owner" } }],
    ["untrusted creator source", { createdActor: { type: "human", source: "channel", id: "gateway-owner" } }],
    ["nonoperator creation", { createdVia: "plugin" }],
    ["external delivery", { delivery: { kind: "external" } }],
    ["group chat", { chatType: "group" }],
    ["spawned child", { spawnDepth: 1 }],
    ["missing spawn depth", { spawnDepth: undefined }],
    ["invalid spawn depth", { spawnDepth: "0" }],
    ["spawned marker", { spawnedBy: "agent:main:main" }],
    ["required sandbox", { sandbox: "required" }],
    ["incognito", { incognito: true }],
    ["external harness", { agentHarnessId: "external" }],
    ["plugin owner", { pluginOwnerId: "other-plugin" }],
    ["assigned another owner", { owner: { actor: { type: "human", id: "someone-else" } } }],
    ["assigned agent owner", { owner: { actor: { type: "agent", id: "gateway-owner" } } }],
    ["assigned external source", { owner: { actor: { type: "human", id: "gateway-owner", source: "channel" } } }],
    ["locked model", { modelSelectionLocked: true }],
  ];
  for (const [name, overrides] of variants) await t.test(name, async () => {
    const api = dashboardApi(overrides === undefined ? undefined : { ...ownerDashboardEntry(), ...overrides });
    const client = clientFake(); const plugin = createMemoryPlugin(api, { client }); await plugin.service.start({});
    try {
      // Even plausible native fields in hook context cannot vouch for a forged key.
      const ctx = context(true, { ...ownerDashboardEntry(), sessionKey: dashboardKey });
      assert.equal(await plugin.beforePrompt({ currentUserMessage: "MCP" }, ctx), undefined);
      const params = { agentId: "main", agentSessionKey: dashboardKey, query: "MCP", lookup: "mirally-memory/m1" };
      assert.deepEqual(await plugin.corpus.search(params), []);
      assert.equal(await plugin.corpus.get(params), null);
      assert.equal(client.calls.some(call => ["context", "search", "read"].includes(call.op)), false);
    } finally { await plugin.service.stop(); }
  });
  for (const api of [apiFake(), dashboardApi(ownerDashboardEntry())]) {
    if (api.runtime.agent) api.runtime.agent.session.getSessionEntry = () => { throw new Error("unavailable store"); };
    assert.equal(eligibleMemoryContext(api, context(true, { sessionKey: dashboardKey })), false);
  }
});

test("owner dashboard eligibility retains every existing context and native authority boundary", () => {
  const api = dashboardApi(ownerDashboardEntry());
  const ctx = context(true, { sessionKey: dashboardKey });
  const denied = [
    [{ agentId: "ufo" }, "agent-not-main"], [{ sandboxed: true }, "sandboxed"],
    [{ channel: "telegram" }, "channel-not-webchat"], [{ messageProvider: "telegram" }, "provider-not-webchat"],
    [{ inputProvenance: { kind: "inter_session" } }, "inter-session-input"],
    [{ sessionKey: "agent:main:dashboard:forged" }, "session-not-owner-main"],
    [{ sessionKey: dashboardKey.replace("dashboard:", "dashboard:incognito-") }, "session-not-owner-main"],
    [{ sessionKey: "agent:main:subagent:child" }, "session-not-private-main"],
    [{ toolAuthority: undefined }, "missing-native-authority"],
    [{ toolAuthority: { assertActive() {}, allows: () => false } }, "recall-tool-denied"],
    [{ hookInvocation: { assertActive() { throw new Error("expired"); } } }, "expired-or-unavailable-authority"],
  ];
  for (const [overrides, reason] of denied) assert.equal(memoryContextSkipReason(api, { ...ctx, ...overrides }, { authority: true }), reason);
  api.config.agents.entries.main.sandbox = { mode: "all" };
  assert.equal(memoryContextSkipReason(api, ctx), "configured-sandbox");
  api.config.agents.entries.main = { runtime: { type: "external" } };
  assert.equal(memoryContextSkipReason(api, ctx), "runtime-not-openclaw");
  for (const sessionKey of ["agent:main:main", "agent:main:explicit:user-created"]) {
    assert.equal(eligibleMemoryContext(apiFake(), context(true, { sessionKey })), true);
  }
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

test("native corpus preserves explicit local calendar dates across the UTC midnight boundary", async () => {
  const client = clientFake();
  const request = client.request;
  const dates = { observed_start_utc_ms: Date.parse("2026-10-14T20:30:00Z"),
    observed_end_utc_ms: Date.parse("2026-10-14T20:40:00Z"),
    observed_start_local: "2026-10-15T04:30:00+08:00", observed_end_local: "2026-10-15T04:40:00+08:00" };
  client.request = async (op, args) => {
    const result = await request(op, args);
    if (op === "search") result.memories = result.memories.map(memory => ({ ...memory, ...dates }));
    if (op === "read") result.memory = { ...result.memory, ...dates };
    return result;
  };
  const plugin = createMemoryPlugin(apiFake(), { client });
  await plugin.service.start({});
  try {
    const params = { agentId: "main", agentSessionKey: "agent:main:main", query: "MCP" };
    const [hit] = await plugin.corpus.search(params);
    assert.match(hit.snippet, /Observed local calendar time: 2026-10-15T04:30:00\+08:00/);
    const read = await plugin.corpus.get({ ...params, lookup: hit.path, fromLine: 2, lineCount: 2 });
    assert.match(read.provenanceLabel, /2026-10-15T04:30:00\+08:00/);
    assert.equal(read.content, "Line one\nLine two");
    assert.equal(read.fromLine, 2);
    assert.equal(read.lineCount, 2);
  } finally { await plugin.service.stop(); }
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

test("fragment acknowledgements wait for merge and only final memory records success", async () => {
  const client = clientFake({ enabled: true, semantics: { enabled: true, interval_seconds: 3600 } });
  const original = client.request; let fragment = 0;
  client.request = async (op, args) => {
    if (op === "claim_job") return { job: { job_id: `j${fragment}`, lease_token: "lease", payload: {} } };
    if (op === "complete_job") { fragment++; return { ok: true, status: fragment < 3 ? "partial_completed" : "completed" }; }
    return original(op, args);
  };
  const plugin = createMemoryPlugin(apiFake(), { client }); await plugin.service.start({});
  try {
    for (let index = 0; index < 2; index++) {
      await plugin.pumpOnce(); const status = await plugin.diagnostics();
      assert.equal(status.semantic.state, "waiting_merge"); assert.equal(status.semantic.completedJobs, 0);
      assert.equal(status.semantic.lastSuccessUtcIso, null); assert.equal(status.semantic.failedJobs, 0);
    }
    await plugin.pumpOnce(); const status = await plugin.diagnostics();
    assert.equal(status.semantic.completedJobs, 1); assert.equal(status.semantic.lastSuccessJobId, "j2");
    assert.ok(status.semantic.lastSuccessUtcIso); assert.equal(client.calls.some(call => call.op === "fail_job"), false);
  } finally { await plugin.service.stop(); }
});

test("settled blocked memory acknowledgements report backlog without retrying a completed lease", async () => {
  for (const blocked of ["merge_capacity_exceeded", "memory_rejected"]) {
    const client = clientFake({ enabled: true, semantics: { enabled: true, interval_seconds: 3600 } });
    const original = client.request;
    client.request = async (op, args) => {
      if (op === "claim_job") return { job: { job_id: "blocked", lease_token: "lease", payload: {} } };
      if (op === "complete_job") return { ok: true, status: blocked };
      return original(op, args);
    };
    const plugin = createMemoryPlugin(apiFake(), { client }); await plugin.service.start({});
    try {
      await plugin.pumpOnce(); const status = await plugin.diagnostics();
      assert.equal(status.semantic.state, "retry_pending"); assert.equal(status.semantic.failedJobs, 1);
      assert.match(status.semantic.lastError, new RegExp(blocked)); assert.equal(status.semantic.completedJobs, 0);
      assert.equal(status.semantic.lastSuccessUtcIso, null); assert.equal(client.calls.some(call => call.op === "fail_job"), false);
    } finally { await plugin.service.stop(); }
  }
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
    "from pathlib import Path; import sys; from mirally.core.memory import MemoryStore; s=MemoryStore(Path(sys.argv[1])); s.put_memory({'id':'memory_sample','title':'OpenClaw MCP design','content':'Observed project work on MCP integration; source observation obs-1.','kind':'episodic','scope':'user/local','provenance':'observed','status':'active','source_observation_ids':['obs-1'],'observed_start_utc_ms':1791702575297,'observed_end_utc_ms':1791702651320}); s.close()", database],
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


test("semantic settings reload updates an existing worker without restart or configuration parameters", async () => {
  const client = clientFake(); const original=client.request;
  client.request=async(op,args)=>op==='reload_settings'?{settings:{enabled:true,semantics:{enabled:false,max_calls_per_hour:24}},health:{status:'ready',semantic_maintenance:{pending_windows:2}}}:op==='health'?{status:'ready',semantic_maintenance:{pending_windows:2}}:original(op,args);
  const plugin=createMemoryPlugin(apiFake(),{client});
  await assert.rejects(plugin.reloadSettings(),/not ready/);
  assert.equal(client.started,0);
  await plugin.service.start({});
  const result=await plugin.reloadSettings();
  assert.equal(result.applied,true);
  assert.equal(result.settings.semantics.max_calls_per_hour,24);
  assert.equal(client.started,1);assert.equal(client.stopped,0);
  assert.deepEqual((await plugin.diagnostics()).worker.semanticMaintenance,{pending_windows:2});
  await plugin.service.stop();
});

test("semantic reload disabling prevents new calls while an in-flight completion can commit", async () => {
  let complete, claimed=0;const begun=new Promise(resolve=>complete=resolve);let finish;
  const model=new Promise(resolve=>finish=resolve);
  const client=clientFake({enabled:true,semantics:{enabled:true}});const original=client.request;
  client.request=async(op,args)=>op==='claim_job'?{job:++claimed===1?{job_id:'fixture',lease_token:'lease',payload:{}}:null}:op==='reload_settings'?{settings:{enabled:true,semantics:{enabled:false}},health:{status:'ready'}}:original(op,args);
  const api=apiFake();let calls=0;api.runtime.llm.complete=async()=>{calls++;complete();return model;};
  const plugin=createMemoryPlugin(api,{client});await plugin.service.start({});
  const pumping=plugin.pumpOnce();await begun;
  await plugin.reloadSettings();
  finish({text:'{}',usage:{totalTokens:2}});await pumping;
  assert.equal(client.calls.some(c=>c.op==='complete_job'),true);
  await plugin.pumpOnce();assert.equal(calls,1);
  assert.equal((await plugin.diagnostics()).semantic.state,'disabled');
  await plugin.service.stop();
});

test("a claimed lease waits for concurrent disable reload before model dispatch", async () => {
  let releaseClaim, releaseReload, claimStarted;
  const begun = new Promise(resolve => claimStarted = resolve);
  const claim = new Promise(resolve => releaseClaim = resolve);
  const reload = new Promise(resolve => releaseReload = resolve);
  const client = clientFake({enabled:true,semantics:{enabled:true}}); const original = client.request;
  client.request = async(op,args) => {
    if(op === 'claim_job') { claimStarted(); return claim; }
    if(op === 'reload_settings') return reload;
    return original(op,args);
  };
  const api=apiFake(); let calls=0;
  api.runtime.llm.complete=async()=>{ calls++; return {text:'{}'}; };
  const plugin=createMemoryPlugin(api,{client}); await plugin.service.start({});
  try {
    const pumping=plugin.pumpOnce(); await begun;
    const reloading=plugin.reloadSettings();
    releaseClaim({job:{job_id:'claimed',lease_token:'lease',payload:{}}});
    await new Promise(resolve=>setImmediate(resolve)); assert.equal(calls,0);
    releaseReload({settings:{enabled:true,semantics:{enabled:false}},health:{status:'ready'}});
    await reloading; await pumping;
    assert.equal(calls,0); assert.equal(client.calls.some(call=>call.op==='fail_job'),true);
  } finally { await plugin.service.stop(); }
});

test("invalid reload acknowledgement does not silently enable semantic completion", async () => {
  const client=clientFake();const original=client.request;
  client.request=async(op,args)=>op==='reload_settings'?{settings:{semantics:{enabled:true}}}:original(op,args);
  const api=apiFake();let calls=0;api.runtime.llm.complete=async()=>{calls++;return {text:'{}'};};
  const plugin=createMemoryPlugin(api,{client});await plugin.service.start({});
  await assert.rejects(plugin.reloadSettings(),/invalid settings/);await plugin.pumpOnce();assert.equal(calls,0);
  await plugin.service.stop();
});

test("file settings watcher enables disabled semantics, adopts routing and budgets, and stops polling", async t => {
  t.mock.timers.enable({apis:['setInterval','setTimeout']});
  let live={enabled:true,semantics:{enabled:false,interval_seconds:3600,model:null,agent_id:'main',max_output_tokens:1500}};
  const client=clientFake(structuredClone(live)); const original=client.request; let polls=0, claims=0;
  client.request=async(op,args)=>{
    if(op==='health'){polls++;return {status:'ready',settings:structuredClone(live),config_reload:{state:'ready',revision:polls}};}
    if(op==='claim_job'){claims++;return {settings:structuredClone(live),job:{job_id:'auto',lease_token:'lease',payload:{model:'obsolete/model',agent_id:'main',max_tokens:live.semantics.max_output_tokens}}};}
    return original(op,args);
  };
  const api=apiFake();const requests=[];api.runtime.llm.complete=async params=>{requests.push(params);return {text:'{}'};};
  const plugin=createMemoryPlugin(api,{client});await plugin.service.start({});
  try{
    live={enabled:true,semantics:{enabled:true,interval_seconds:1,model:'fixture/new',agent_id:'ufo',max_output_tokens:8000,max_calls_per_hour:42,max_tokens_per_day:12345}};
    t.mock.timers.tick(2000);await new Promise(resolve=>setImmediate(resolve));
    assert.equal(requests.length,1);assert.equal(requests[0].model,'fixture/new');assert.equal(requests[0].agentId,'ufo');assert.equal(requests[0].maxTokens,8000);
    const status=await plugin.diagnostics();assert.equal(status.settings.semantics.interval_seconds,1);
    assert.equal(status.settings.semantics.max_calls_per_hour,42);assert.equal(status.settings.semantics.max_tokens_per_day,12345);
    assert.equal(client.calls.some(call=>call.op==='reload_settings'),false);
    t.mock.timers.tick(1000);await new Promise(resolve=>setImmediate(resolve));assert.equal(requests.length,2);
    live={...live,semantics:{...live.semantics,enabled:false}};
    t.mock.timers.tick(2000);await new Promise(resolve=>setImmediate(resolve));
    await plugin.pumpOnce();assert.equal(requests.length,2);assert.equal(claims,2);
    await plugin.service.stop();const stoppedPolls=polls;
    t.mock.timers.tick(10000);await new Promise(resolve=>setImmediate(resolve));assert.equal(polls,stoppedPolls);
  }finally{await plugin.service.stop();t.mock.timers.reset();}
});

test("fresh worker health and claim settings prevent stale routing and dispatch after file disable",async()=>{
  let live={enabled:true,semantics:{enabled:true,model:'fixture/initial',agent_id:'main',max_output_tokens:1500}};
  const client=clientFake(structuredClone(live));const original=client.request;let claims=0;const requests=[];
  client.request=async(op,args)=>{
    if(op==='health')return {status:'ready',settings:structuredClone(live)};
    if(op==='claim_job'){claims++;return {settings:{enabled:true,semantics:{enabled:true,model:null,agent_id:'ufo',max_output_tokens:6000}},job:{job_id:'fresh',lease_token:'lease',payload:{model:'stale/model'}}};}
    return original(op,args);
  };
  const api=apiFake();api.runtime.llm.complete=async params=>{requests.push(params);return {text:'{}'};};
  const plugin=createMemoryPlugin(api,{client});await plugin.service.start({});
  try{
    await plugin.pumpOnce();assert.equal(requests[0].model,undefined);assert.equal(requests[0].agentId,'ufo');assert.equal(requests[0].maxTokens,6000);
    live={...live,semantics:{...live.semantics,enabled:false}};
    await plugin.pumpOnce();assert.equal(claims,1);assert.equal(requests.length,1);
  }finally{await plugin.service.stop();}
});

test("invalid file settings preserve last valid state and disable permits in-flight commit",async()=>{
  let live={enabled:true,semantics:{enabled:true,model:'fixture/valid',interval_seconds:3600}};
  let reloadState='ready',release,begun;const entered=new Promise(resolve=>begun=resolve);
  const result=new Promise(resolve=>release=resolve);
  const client=clientFake(structuredClone(live));const original=client.request;
  client.request=async(op,args)=>{
    if(op==='health')return {status:'ready',settings:structuredClone(live),config_reload:{state:reloadState,error:reloadState==='invalid'?'invalid YAML':null}};
    if(op==='claim_job')return {job:{job_id:'inflight',lease_token:'lease',payload:{}}};
    return original(op,args);
  };
  const api=apiFake();api.runtime.llm.complete=async()=>{begun();return result;};
  const plugin=createMemoryPlugin(api,{client});await plugin.service.start({});
  try{
    reloadState='invalid';let status=await plugin.diagnostics();
    assert.equal(status.worker.configReload.state,'invalid');assert.equal(status.settings.semantics.model,'fixture/valid');
    const pumping=plugin.pumpOnce();await entered;
    reloadState='ready';live={...live,semantics:{...live.semantics,enabled:false}};
    await plugin.diagnostics();release({text:'{}'});await pumping;
    assert.equal(client.calls.some(call=>call.op==='complete_job'),true);
    assert.equal((await plugin.diagnostics()).semantic.state,'disabled');
  }finally{await plugin.service.stop();}
});

test("a late watcher response cannot replace settings after service restart",async()=>{
  const disabled={enabled:true,semantics:{enabled:false}};
  const client=clientFake(disabled);const original=client.request;let release,first=true;
  const delayed=new Promise(resolve=>release=resolve);
  client.request=async(op,args)=>{
    if(op==='health' && first){first=false;return delayed;}
    if(op==='health')return {status:'ready',settings:disabled};
    return original(op,args);
  };
  const plugin=createMemoryPlugin(apiFake(),{client});await plugin.service.start({});
  const pending=plugin.diagnostics();await plugin.service.stop();await plugin.service.start({});
  release({status:'ready',settings:{enabled:true,semantics:{enabled:true,model:'stale/model'}}});await pending;
  try{assert.equal((await plugin.diagnostics()).settings.semantics.enabled,false);}
  finally{await plugin.service.stop();}
});

test("late health acknowledgement cannot roll back a newer explicit disable",async()=>{
  const enabled={enabled:true,semantics:{enabled:true,model:'old/model'}};
  const disabled={enabled:false,semantics:{enabled:true,model:'new/model'}};
  const client=clientFake(enabled);const original=client.request;let release;
  const pendingHealth=new Promise(resolve=>release=resolve);
  client.request=async(op,args)=>op==='health'?pendingHealth:op==='reload_settings'?{settings:disabled,health:{status:'disabled',settings:disabled}}:original(op,args);
  const plugin=createMemoryPlugin(apiFake(),{client});await plugin.service.start({});
  try{
    const reading=plugin.diagnostics();await plugin.reloadSettings();release({status:'ready',settings:enabled,config_reload:{revision:'old'}});
    const status=await reading;assert.equal(status.settings.enabled,false);assert.equal(status.settings.semantics.model,'new/model');
    assert.equal(status.semantic.state,'disabled');
  }finally{await plugin.service.stop();}
});

test("claim routing survives old health and concurrent reload respects reserved output",async()=>{
  const initial={enabled:true,semantics:{enabled:true,model:'old/model',max_output_tokens:2000}};
  const updated={enabled:true,semantics:{enabled:true,model:'new/model',agent_id:'ufo',max_output_tokens:12000}};
  const client=clientFake(initial);const original=client.request;let releaseClaim,claimEntered,healthCalls=0;
  const begun=new Promise(resolve=>claimEntered=resolve);const claim=new Promise(resolve=>releaseClaim=resolve);
  client.request=async(op,args)=>{
    if(op==='health')return ++healthCalls===1?{status:'ready',settings:initial}:{status:'ready'};
    if(op==='claim_job'){claimEntered();return claim;}
    if(op==='reload_settings')return {settings:updated,health:{status:'ready',settings:updated}};
    return original(op,args);
  };
  const api=apiFake();const requests=[];api.runtime.llm.complete=async params=>{requests.push(params);return {text:'{}'};};
  const plugin=createMemoryPlugin(api,{client});await plugin.service.start({});
  try{
    const pumping=plugin.pumpOnce();await begun;await plugin.reloadSettings();
    releaseClaim({settings:initial,job:{job_id:'reserved',lease_token:'lease',payload:{model:'old/model',max_tokens:2000}}});await pumping;
    assert.equal(requests[0].model,'new/model');assert.equal(requests[0].agentId,'ufo');assert.equal(requests[0].maxTokens,2000);
    assert.equal((await plugin.diagnostics()).settings.semantics.model,'new/model');
  }finally{await plugin.service.stop();}
});

test("claim-time file disable preserves disabled diagnostics for global and semantic switches",async()=>{
  for(const next of [{enabled:false,semantics:{enabled:true}},{enabled:true,semantics:{enabled:false}}]){
    const client=clientFake({enabled:true,semantics:{enabled:true}});const original=client.request;
    client.request=async(op,args)=>op==='claim_job'?{status:'disabled',settings:next,job:null}:original(op,args);
    const api=apiFake();let calls=0;api.runtime.llm.complete=async()=>{calls++;return {text:'{}'};};
    const plugin=createMemoryPlugin(api,{client});await plugin.service.start({});
    try{await plugin.pumpOnce();assert.equal(calls,0);assert.equal((await plugin.diagnostics()).semantic.state,'disabled');}
    finally{await plugin.service.stop();}
  }
});

test("health polling timeout reports failure without restarting a busy worker",async t=>{
  t.mock.timers.enable({apis:['setInterval','setTimeout']});
  const client=clientFake();const original=client.request;let healthCalls=0;
  client.request=async(op,args)=>{
    if(op==='health'){healthCalls++;throw new Error('Memory request health timed out');}
    return original(op,args);
  };
  const failures=[];const plugin=createMemoryPlugin(apiFake(),{client});
  await plugin.service.start({serviceHealth:{reportFailure:error=>failures.push(error.message)}});
  try{
    for(let index=0;index<3;index++){
      t.mock.timers.tick(2000);await new Promise(resolve=>setImmediate(resolve));
    }
    assert.equal(healthCalls,3);assert.equal(failures.length,3);assert.match(failures[0],/timed out/);
    assert.equal(client.started,1);assert.equal(client.stopped,0);assert.equal(plugin.ready,true);
    assert.equal((await plugin.diagnostics()).running,true);
  }finally{await plugin.service.stop();t.mock.timers.reset();}
});

test("official loopback WebSocket opens a fresh operation after timer inventory retirement", {timeout:45000}, async()=>{
  const dist=path.join(memoryPackageRoot,'integrations/openclaw/node_modules/openclaw/dist');
  const cacheBundle=readdirSync(dist).find(name=>name.startsWith('plugin-cache-')&&name.endsWith('.mjs'));
  const {a:createCache,C:retireCache,D:withCache,o:getCache,x:retainCache}=await import(pathToFileURL(path.join(dist,cacheBundle)).href);
  const {callGatewayFromCli}=await import('openclaw/plugin-sdk/gateway-runtime');
  const retired=createCache();await retireCache(retired);
  const temporary=mkdtempSync(path.join(os.tmpdir(),'mirally-memory-loopback-'));
  const configPath=path.join(temporary,'openclaw.json');
  writeFileSync(configPath,JSON.stringify({gateway:{mode:'local',auth:{mode:'token',token:'fixture-only'}}}));
  const oldConfig=process.env.OPENCLAW_CONFIG_PATH,oldState=process.env.OPENCLAW_STATE_DIR;
  process.env.OPENCLAW_CONFIG_PATH=configPath;process.env.OPENCLAW_STATE_DIR=temporary;
  const server=new WebSocketServer({host:'127.0.0.1',port:0});
  await new Promise(resolve=>server.once('listening',resolve));
  let calls=0,connectScopes;
  server.on('connection',socket=>{
    socket.send(JSON.stringify({type:'event',event:'connect.challenge',payload:{nonce:'fixture',ts:Date.now()}}));
    socket.on('message',buffer=>{
      const request=JSON.parse(String(buffer));
      if(request.method==='connect'){
        connectScopes=request.params.scopes;
        socket.send(JSON.stringify({type:'res',id:request.id,ok:true,payload:{type:'hello-ok',protocol:3,server:{version:'fixture',connId:'fixture'},features:{methods:['mirally.memory.consolidate'],events:[]},snapshot:{},policy:{tickIntervalMs:30000}}}));
      }else{
        assert.equal(request.method,'mirally.memory.consolidate');assert.deepEqual(request.params,{});
        assert.notEqual(getCache(),retired);const release=retainCache(getCache());release();calls++;
        socket.send(JSON.stringify({type:'res',id:request.id,ok:true,payload:{processed:true}}));
      }
    });
  });
  try{
    const api=apiFake();api.config.gateway={port:server.address().port};
    // This direct access is the exact native refusal seen in the old timer.
    withCache(retired,()=>assert.throws(()=>retainCache(getCache()),/inventory has retired/));
    const result=await withCache(retired,()=>requestMemoryConsolidation(api,{},(method,opts,params,extra)=>
      callGatewayFromCli(method,{...opts,token:'fixture-only'},params,{...extra,deviceIdentity:null})));
    assert.deepEqual(result,{processed:true});assert.equal(calls,1);assert.ok(connectScopes.includes('operator.admin'));
  }finally{
    for(const socket of server.clients)socket.terminate();await new Promise(resolve=>server.close(resolve));
    if(oldConfig===undefined)delete process.env.OPENCLAW_CONFIG_PATH;else process.env.OPENCLAW_CONFIG_PATH=oldConfig;
    if(oldState===undefined)delete process.env.OPENCLAW_STATE_DIR;else process.env.OPENCLAW_STATE_DIR=oldState;
    rmSync(temporary,{recursive:true,force:true});
  }
});

test("registry replacement completes through its current API while borrowing the Gateway worker",async()=>{
  const temporary=mkdtempSync(path.join(os.tmpdir(),'mirally-memory-registry-'));
  const client=clientFake({enabled:true,semantics:{enabled:true}});const original=client.request;
  client.request=async(op,args)=>op==='claim_job'?{job:{job_id:'current',lease_token:'lease',payload:{}}}:original(op,args);
  const pluginConfig={root:temporary,configPath:path.join(temporary,'memory.yaml')};
  const oldApi=apiFake({pluginConfig}),currentApi=apiFake({pluginConfig});let oldCalls=0,currentCalls=0;
  oldApi.runtime.llm.complete=async()=>{oldCalls++;throw new Error('Plugin inventory has retired; begin a new plugin operation.');};
  currentApi.runtime.llm.complete=async()=>{currentCalls++;return {text:'{}'};};
  const owner=createSharedMemoryPlugin(oldApi,{client});
  const replacement=createSharedMemoryPlugin(currentApi,{client:clientFake()});
  await owner.service.start({});await replacement.service.start({});
  try{
    await replacement.pumpOnce();assert.equal(oldCalls,0);assert.equal(currentCalls,1);
    assert.equal((await replacement.diagnostics()).semantic.completedJobs,1);
    assert.equal(client.calls.some(call=>call.op==='complete_job'),true);
  }finally{await replacement.service.stop();await owner.service.stop();rmSync(temporary,{recursive:true,force:true});}
});

test("loopback consolidation accepts only the fixed operation and derives native default port",async()=>{
  const api=apiFake();let captured;
  const result=await requestMemoryConsolidation(api,{},async(...args)=>{captured=args;return {processed:true};});
  const {resolveGatewayPort}=await import('openclaw/plugin-sdk/core');
  assert.equal(result.processed,true);assert.equal(captured[0],'mirally.memory.consolidate');
  assert.equal(captured[1].port,String(resolveGatewayPort(api.config)));assert.equal(Object.hasOwn(captured[1],'url'),false);
  assert.deepEqual(captured[2],{});assert.deepEqual(captured[3].scopes,['operator.admin']);
});

test("consolidation gateway handler rejects client-authored prompts, models, agents and leases",async()=>{
  const handlers=new Map();
  const api=apiFake({registerService(){},registerMemoryCorpusSupplement(){},on(){},
    registerGatewayMethod:(name,handler,options)=>handlers.set(name,{handler,options})});
  pluginEntry.register(api);
  const entry=handlers.get('mirally.memory.consolidate');assert.equal(entry.options.scope,'operator.admin');
  for(const params of [{prompt:'external'},{model:'external/model'},{agent_id:'ufo'},{job_id:'forged',lease_token:'forged'}]){
    let response;await entry.handler({params,respond:(...args)=>response=args});
    assert.equal(response[0],false);assert.equal(response[2].code,'INVALID_REQUEST');
  }
});

test("failed operation admission never consumes a model job or worker reservation",async t=>{
  t.mock.timers.enable({apis:['setTimeout','setInterval']});
  const client=clientFake({enabled:true,semantics:{enabled:true,interval_seconds:1}});
  const failures=[];let attempts=0;
  const plugin=createMemoryPluginNative(apiFake(),{client,dispatchOperation:async()=>{attempts++;throw new Error('Gateway authorization refused');}});
  await plugin.service.start({serviceHealth:{reportFailure:error=>failures.push(error.message)}});
  try{
    t.mock.timers.tick(1000);await new Promise(resolve=>setImmediate(resolve));
    assert.equal(attempts,1);assert.equal(client.calls.some(call=>call.op==='claim_job'||call.op==='fail_job'),false);
    assert.equal((await plugin.diagnostics()).semantic.failedJobs,0);assert.match(failures[0],/authorization refused/);
  }finally{await plugin.service.stop();t.mock.timers.reset();}
});

test("native Qwen transport needs authored reasoning compatibility to emit the per-call off flag",async()=>{
  const {createLlmRuntime,createApiRegistry}=await import('@openclaw/ai');
  const {registerBuiltInApiProviders}=await import('@openclaw/ai/providers');
  const registry=createApiRegistry();registerBuiltInApiProviders(registry);const runtime=createLlmRuntime(registry);
  const base={id:'qwen/qwen3.8-flash',name:'Fixture',api:'openai-completions',provider:'fixture-relay',baseUrl:'https://fixture.invalid/v1',
    input:['text'],contextWindow:32000,maxTokens:4000,cost:{input:0,output:0,cacheRead:0,cacheWrite:0}};
  const context={messages:[{role:'user',content:[{type:'text',text:'Return JSON.'}],timestamp:Date.now()}]};
  for(const [reasoning,compat,expected] of [[false,{},undefined],[true,{thinkingFormat:'qwen'},false],[true,{thinkingFormat:'qwen-chat-template'},'template']]){
    let payload;
    await runtime.completeSimple({...base,reasoning,compat},context,{apiKey:'fixture',reasoning:'off',responseFormat:{type:'json_object'},
      onPayload:value=>{payload=value;throw new Error('wire fixture captured before any HTTP request');}});
    assert.ok(payload);assert.deepEqual(payload.response_format,{type:'json_object'});
    if(expected==='template')assert.equal(payload.chat_template_kwargs.enable_thinking,false);
    else assert.equal(payload.enable_thinking,expected);
  }
});
