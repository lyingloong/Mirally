import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import {
  AgentHarnessPreflightError, assembleHarnessContextEngine, emitAgentEvent,
  setActiveEmbeddedRun, clearActiveEmbeddedRun, runAgentHarnessGatewayQuestion,
  claimPendingAgentQuestionAnswer, cancelPendingAgentQuestionForSession,
  resolveBootstrapContextForRun,
  isSubagentSessionKey,
} from "openclaw/plugin-sdk/agent-harness-runtime";
import { SessionManager } from "openclaw/plugin-sdk/agent-sessions";
import { appendSessionTranscriptMessageByIdentityStrict } from "openclaw/plugin-sdk/session-transcript-runtime";
import {createRunDiagnostics} from "./diagnostics.mjs";

export const HARNESS_ID = "mirally-ufo";
export const WORKER_MODES = Object.freeze({conversation: "conversation", desktopSubtask: "desktop_subtask"});
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const MAX_FRAME = 2_000_000;
const CONTEXT_CHARS = 32_000;

function refuse(message) {
  return new AgentHarnessPreflightError(message, {scope: "harness", userMessage: message});
}

function configuredCompatibilityOnly(ctx, config) {
  // The SDK's override flag also includes model compatibility metadata. It
  // does not mean every flagged route has headers, a proxy or a custom body.
  // Admit only a concrete config row whose entire override surface we handle.
  const providers = Object.entries(config?.models?.providers ?? {}).filter(([id]) =>
    id.trim().toLowerCase() === ctx.provider?.trim().toLowerCase());
  if (providers.length !== 1 || !ctx.modelId) return false;
  const provider = providers[0][1];
  const nonempty = value => value != null && (typeof value !== "object" || Object.keys(value).length > 0);
  if ([provider.localService, provider.headers, provider.request, provider.params,
    ctx.modelProvider?.request].some(nonempty) || provider.authHeader !== undefined ||
    provider.timeoutSeconds !== undefined) return false;
  const rows = (provider.models ?? []).filter(row => row.id === ctx.modelId);
  if (rows.length !== 1) return false;
  const row = rows[0];
  if ([row.headers, row.params].some(nonempty)) return false;
  const compat = row.compat;
  if (!compat || !Object.keys(compat).length) return false;
  const fields = new Set(["thinkingFormat", "supportsReasoningEffort", "supportedReasoningEfforts", "reasoningEffortMap"]);
  if (Object.keys(compat).some(key => !fields.has(key))) return false;
  if (compat.thinkingFormat !== undefined && compat.thinkingFormat !== "openai") return false;
  if (compat.supportsReasoningEffort !== true) return false;
  const efforts = compat.supportedReasoningEfforts;
  if (!Array.isArray(efforts) || !efforts.length || efforts.some(value =>
    !["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"].includes(value))) return false;
  const mapping = compat.reasoningEffortMap;
  if (mapping !== undefined && (!mapping || typeof mapping !== "object" || Array.isArray(mapping) ||
    Object.entries(mapping).some(([level, effort]) =>
      !["off", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"].includes(level) ||
      !efforts.includes(effort)))) return false;
  const ref = `${ctx.provider}/${ctx.modelId}`;
  // Support probes don't identify the agent. Reject configured body overrides
  // on this model rather than guessing which source set the aggregate flag.
  const defaults = config?.agents?.defaults;
  const agents = [...Object.values(config?.agents?.entries ?? {}), ...(config?.agents?.list ?? [])];
  if ([defaults?.params, defaults?.models?.[ref]?.params,
    ...agents.flatMap(agent => [agent.params, agent.models?.[ref]?.params])].some(nonempty)) return false;
  return true;
}

export function routeSupport(ctx, config) {
  const route = ctx.modelProvider;
  // Explicit opt-in plus an implemented transport contract, not model-name
  // inference or a claim of arbitrary provider/runtime compatibility.
  if (ctx.requestedRuntime !== HARNESS_ID) return {supported: false, reason: "explicit-only"};
  if (!route || route.api !== "openai-completions")
    return {supported: false, reason: "UFO currently requires OpenAI-compatible Chat Completions"};
  if (route.requestTransportOverrides !== "none" &&
      !(route.requestTransportOverrides === "present" && configuredCompatibilityOnly(ctx, config)))
    return {supported: false, reason: "UFO cannot reproduce this route's custom request settings or compatibility metadata"};
  // A custom baseUrl is an endpoint override that this transport reproduces
  // exactly. Azure API-version semantics require a different client.
  if (route.azureApiVersion)
    return {supported: false, reason: "UFO does not implement Azure API-version semantics"};
  if (route.preparedAuth?.mode && route.preparedAuth.mode !== "api-key")
    return {supported: false, reason: "UFO currently supports API-key authentication"};
  try {
    const url = new URL(route.baseUrl);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
      return {supported: false, reason: "Unsupported model endpoint"};
  } catch { return {supported: false, reason: "Missing prepared model endpoint"}; }
  return {supported: true, reason: "explicit UFO runtime with supported prepared transport"};
}

function textOf(message) {
  if (typeof message.content === "string") return message.content;
  return (message.content ?? []).filter(block => block.type === "text").map(block => block.text).join("\n");
}

export function taskContext(messages, instructions = "") {
  const previous = [];
  for (const message of messages) {
    if (!["user", "assistant"].includes(message.role)) continue;
    const text = textOf(message);
    if (!text) continue;
    if (message.role === "user") previous.push({request: text, inputs: [], result: "", status: "completed"});
    else if (previous.length) {
      previous.at(-1).result += (previous.at(-1).result ? "\n" : "") + text;
      if (message.stopReason === "error") previous.at(-1).status = "failed";
      if (message.stopReason === "aborted") previous.at(-1).status = "cancelled";
    }
  }
  const context = {previous_tasks: previous, engine_instructions: instructions};
  if (JSON.stringify(context).length > CONTEXT_CHARS)
    throw refuse("会话上下文超过 UFO 的执行预算，请缩小引用内容或新建会话；本次没有执行桌面操作。");
  return context;
}

function parametersFor(params, model) {
  const authored = params.streamParams ?? {};
  const allowed = new Set(["temperature", "maxTokens"]);
  if (Object.keys(authored).some(key => !allowed.has(key)))
    throw refuse("当前 UFO 适配器不支持这些模型请求参数，请使用受支持的模型配置。");
  const parameters = {};
  if (authored.temperature !== undefined) parameters.temperature = authored.temperature;
  if (authored.maxTokens !== undefined) parameters.max_tokens = authored.maxTokens;
  const compat = model.compat ?? {};
  if (compat.supportsReasoningEffort && compat.supportedReasoningEfforts) {
    const level = params.thinkLevel === "off" ? "none" : params.thinkLevel;
    const effort = compat.reasoningEffortMap?.[level] ?? level;
    if (!compat.supportedReasoningEfforts.includes(effort))
      throw refuse("选定的思考等级无法传给 UFO 模型，请选择支持的等级。");
    parameters.reasoning_effort = effort;
  } else if (params.thinkLevel && params.thinkLevel !== "off") {
    throw refuse("此 UFO 模型暂不支持设置思考等级，请选择关闭思考。");
  }
  return parameters;
}

export function preparedRoute(params) {
  const model = params.modelRegistry.find(params.provider, params.modelId);
  const prepared = params.runtimePlan?.auth?.modelRoute;
  const base = prepared?.baseUrl ?? model?.baseUrl;
  if (!model || (prepared?.api ?? model.api) !== "openai-completions" || !params.resolvedApiKey)
    throw refuse("UFO 需要已配置的 OpenAI-compatible 视觉模型和 API Key。");
  if (params.modelHasVision === false || !model.input?.includes("image"))
    throw refuse("UFO 桌面观察需要支持图片输入的模型。");
  return {api: "openai-completions", base_url: base, model: params.modelId,
    key: params.resolvedApiKey, reasoning: Boolean(model.reasoning),
    parameters: parametersFor(params, model)};
}

// Session classification belongs to OpenClaw. Do not infer delegation from
// the prompt, agent id, or spawnedBy alone; child continuations keep their key.
export function workerMode(params) {
  if (!isSubagentSessionKey(params.sessionKey))
    throw refuse("桌面执行器仅供 Mirally 主助手调用子任务，请返回主助手提出桌面操作要求。");
  return WORKER_MODES.desktopSubtask;
}

function workerProcess(root) {
  // The Windows venv python.exe is a console launcher. It can create a
  // second visible conhost even when Node passes windowsHide=true. pythonw
  // uses the same venv and keeps the private worker pipe-only.
  const pythonw = path.join(root, ".venv/Scripts/pythonw.exe");
  const python = existsSync(pythonw) ? pythonw : path.join(root, ".venv/Scripts/python.exe");
  if (!existsSync(python) || !existsSync(path.join(root, "UFO/ufo/module/sessions/session.py")))
    throw refuse("UFO 执行环境未就绪，请初始化 UFO submodule 并安装根目录 Python 依赖。");
  return spawn(python, ["-m", "mirally.runtime.ufo.worker", "--root", root], {
    cwd: root, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    env: {...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1"},
  });
}

function writeFrame(worker, value) {
  const frame = JSON.stringify(value) + "\n";
  if (Buffer.byteLength(frame) > MAX_FRAME) throw refuse("UFO 请求过大，没有继续执行。");
  if (worker.stdin.destroyed || worker.stdin.writableEnded) throw new Error("UFO control channel closed");
  worker.stdin.write(frame);
}

function forceTerminate(worker) {
  if (!worker || worker.exitCode !== null || worker.killed) return;
  if (process.platform === "win32") {
    // python.exe may be a launcher with a second Python child. Kill the
    // process tree so a blocked UIA/COM call cannot retain the Gateway run.
    const killer = spawn("taskkill", ["/PID", String(worker.pid), "/T", "/F"],
      {stdio: "ignore", windowsHide: true});
    killer.on("error", () => {});
  } else {
    try { worker.kill("SIGKILL"); } catch {}
  }
}

export async function* readFrames(stream) {
  let buffer = Buffer.alloc(0);
  for await (const chunk of stream) {
    buffer = Buffer.concat([buffer, Buffer.from(chunk)]);
    let end;
    while ((end = buffer.indexOf(10)) >= 0) {
      if (end > MAX_FRAME) throw new Error("Oversized UFO frame");
      const line = buffer.subarray(0, end).toString("utf8");
      buffer = buffer.subarray(end + 1);
      if (line.trim()) yield JSON.parse(line);
    }
    if (buffer.length > MAX_FRAME) throw new Error("Oversized UFO frame");
  }
  if (buffer.length) throw new Error("Incomplete UFO frame");
}

function assistantMessage(params, result, usage) {
  let text = result.message || "UFO 未取得执行结果。";
  if (result.status === "partial") text = "部分完成\n\n" + text;
  if (result.status === "incomplete") text = "尚未完成\n\n" + text;
  return {role: "assistant", content: [{type: "text", text}], api: "openai-completions",
    provider: params.provider, model: params.modelId, timestamp: Date.now(),
    stopReason: result.status === "cancelled" ? "aborted" : result.status === "failed" ? "error" : "stop",
    ...(result.status === "failed" ? {errorMessage: text} : {}),
    usage: {...usage, cacheRead: 0, cacheWrite: 0,
      cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0}},
  };
}

async function contextFor(params) {
  const receipt = params.userTurnTranscriptRecorder?.getAdmissionReceipt();
  const target = params.sessionTarget ?? {agentId: params.agentId, sessionId: params.sessionId, sessionKey: params.sessionKey};
  const manager = params.sessionManager ?? await SessionManager.openModelContextAsync(target, {
    cwd: params.workspaceDir, admission: receipt, signal: params.abortSignal,
    limits: {maxBytes: 128_000, maxEvents: 200},
  });
  const messages = manager.buildSessionContext().messages;
  const assembled = await assembleHarnessContextEngine({
    contextEngine: params.contextEngine, sessionId: params.sessionId, sessionKey: params.sessionKey,
    agentId: params.agentId, messages, tokenBudget: params.contextTokenBudget,
    modelId: params.modelId, providerId: params.provider, harnessId: HARNESS_ID,
    availableTools: new Set(), sandboxed: false, prompt: params.prompt, transcriptReadFence: receipt,
  });
  const bootstrap = await resolveBootstrapContextForRun({workspaceDir: params.workspaceDir,
    config: params.config, sessionId: params.sessionId, sessionKey: params.sessionKey,
    agentId: params.agentId, readOnlyState: true, warn() {}});
  const workspaceInstructions = bootstrap.contextFiles.map(file =>
    `Workspace reference (${file.path}):\n${file.content}`).join("\n\n");
  const history = [...(assembled?.messages ?? messages)];
  if (history.at(-1)?.role === "user" && textOf(history.at(-1)) === params.prompt) history.pop();
  return taskContext(history,
    [params.extraSystemPrompt, workspaceInstructions, assembled?.systemPromptAddition].filter(Boolean).join("\n"));
}

export function createUfoHarness(api, dependencies = {}) {
  // The native loader may compile modules into its cache. Use the declared
  // plugin root, never the compiled module's import.meta location, in a Gateway.
  const root = dependencies.root ?? (api.rootDir ? path.resolve(api.rootDir, "../../..") : ROOT);
  const launch = dependencies.spawnWorker ?? (() => workerProcess(root));
  const readContext = dependencies.contextFor ?? contextFor;
  const persistAssistant = dependencies.persistAssistant ?? appendSessionTranscriptMessageByIdentityStrict;
  const question = dependencies.question ?? runAgentHarnessGatewayQuestion;
  const registerRun = dependencies.registerRun ?? setActiveEmbeddedRun;
  const unregisterRun = dependencies.unregisterRun ?? clearActiveEmbeddedRun;
  const workers = new Map();
  return {
    id: HARNESS_ID, label: "桌面执行（内部子任务）", autoSelection: {providerIds: []},
    executionEnvironment: "host-only",
    supports: ctx => routeSupport(ctx, api.config),
    async runAttempt(params) {
      // Refuse top-level chats before context/auth preparation or worker launch.
      const mode = workerMode(params);
      const diagnostic = createRunDiagnostics(root, params.runId,
        message => api.logger?.warn?.(message));
      diagnostic("harness_entered");
      api.logger?.info?.(`[UFO] runId=${params.runId} progress=.artifacts/ufo-runs/${params.runId}.jsonl`);
      const host = params.hostCapabilities;
      host.assertActive();
      params.abortSignal?.throwIfAborted();
      if (params.disableTools || params.modelRun || params.pluginHarnessToolPolicyRestricted ||
          params.sandbox?.enabled || params.requireWorkspaceOnly)
        throw refuse("UFO 不能在禁用工具或受限沙箱的会话中操作本机桌面。");
      if (params.images?.length || params.clientTools?.length)
        throw refuse("当前 UFO 接入暂不支持图片附件和客户端工具，请先使用文字要求。");
      const route = preparedRoute(params);
      if (params.userTurnTranscriptRecorder) {
        const receipt = await params.userTurnTranscriptRecorder.persistApproved({expectedSessionId: params.sessionId});
        if (receipt?.rejectedReason || params.userTurnTranscriptRecorder.isBlocked())
          throw refuse("当前输入没有获得会话执行权，未启动 UFO。");
      }
      diagnostic("phase", {stage: "context_assembly"});
      const context = await readContext(params);
      diagnostic("phase", {stage: "context_ready"});
      host.assertActive();
      params.abortSignal?.throwIfAborted();
      params.onExecutionPhase?.({phase: "context_assembled", provider: params.provider,
        model: params.modelId, backend: HARNESS_ID});
      if (mode === WORKER_MODES.desktopSubtask)
        params.onExecutionPhase?.({phase: "desktop_subtask_dispatched", provider: params.provider,
          model: params.modelId, backend: HARNESS_ID});
      const worker = launch();
      diagnostic("worker_spawned", {pid: worker.pid});
      worker.stderr.resume(); // Never echo provider exceptions or private desktop data.
      let stopped = false, settled = false, generation = 0, ready = false, progressed = false;
      let startupStage = "worker_startup", startupTimeoutStage;
      let timedOut = false, startupTimedOut = false;
      let forceStopTimer;
      let result, failure, lastToolError;
      const usage = {input: 0, output: 0, totalTokens: 0};
      const toolMetas = [], receipts = new Map(), inputAuthorities = new Map(), requests = new Set();
      let hadEffects = false;
      const closed = new Promise(resolve => {
        worker.once("error", () => { failure = new Error("Cannot start UFO worker"); resolve(); });
        worker.once("close", code => {
          diagnostic("worker_closed", {exit_code: code});
          if (code !== 0 && !failure) failure = new Error("UFO worker exited unexpectedly");
          resolve();
        });
      });
      workers.set(worker, closed);
      worker.stdin.on("error", () => { failure ??= new Error("UFO control channel closed"); });
      const abort = () => {
        if (stopped || settled) return;
        stopped = true; generation++;
        diagnostic("cancel_requested");
        for (const controller of requests) controller.abort();
        try { writeFrame(worker, {type: "cancel"}); } catch {}
        if (!ready) worker.stdin.end();
        // A native Windows call can remain inside a non-interruptible COM
        // operation. Give cooperative cleanup a short window, then kill the
        // worker tree so the host session receives a terminal outcome.
        forceStopTimer ??= setTimeout(() => forceTerminate(worker), 1500);
      };
      const assertCurrent = () => {
        host.assertActive();
        params.abortSignal?.throwIfAborted();
        if (stopped || settled) throw new Error("UFO run is no longer active");
      };
      const inject = async (text, options, assertAuthority) => {
        assertCurrent(); assertAuthority();
        if (options?.images?.length || options?.media?.length)
          throw new Error("UFO steering currently accepts plain text only");
        const assertInput = () => {assertAuthority(); options?.abortSignal?.throwIfAborted();};
        assertInput();
        if (options?.userTurnTranscriptRecorder) {
          await options.userTurnTranscriptRecorder.persistApproved({expectedSessionId: params.sessionId});
          assertCurrent(); assertInput();
          if (options.userTurnTranscriptRecorder.isBlocked()) throw new Error("Steering input was not admitted");
        }
        const receipt = randomUUID();
        // Check after all preparation, immediately before queue dispatch.
        assertCurrent(); assertInput();
        generation++;
        for (const controller of requests) controller.abort();
        inputAuthorities.set(receipt, assertInput);
        let timer;
        const adopted = new Promise((resolve, reject) => {
          const settle = value => {clearTimeout(timer); resolve(value);};
          receipts.set(receipt, {resolve: settle, reject: error => {clearTimeout(timer);reject(error);}, options});
          timer = setTimeout(() => {
            receipts.delete(receipt);
            settle({transcriptCommit: "unconfirmed", errorMessage: "补充已交给 UFO，但尚未确认模型采用；请勿重复发送。"});
          }, Math.min(options?.deliveryTimeoutMs ?? 30_000, 300_000));
        });
        try {
          writeFrame(worker, {type: "supplement", text, receipt_id: receipt});
        } catch (error) {
          receipts.get(receipt)?.resolve({transcriptCommit: "unconfirmed", errorMessage: "UFO 控制连接已断开。"});
          receipts.delete(receipt);
          throw error;
        }
        return await adopted;
      };
      const handle = {
        runId: params.runId, startedAtMs: params.startedAtMs,
        toolAuthorityFingerprint: params.toolAuthorityFingerprint,
        isStreaming: () => !settled, isCompacting: () => false,
        isAbortable: () => !stopped && !settled, isAborted: () => stopped, isStopped: () => settled,
        ownsLiveness: () => !settled,
        queueMessage: (text, options) => inject(text, options, () => {}),
        messageInjectionV2: {
          version: 2, isAvailable: () => !stopped && !settled,
          queueMessage: (text, options, assertion, authorityKind) => {
            // Removing an already-adopted source item from UFO's native memory
            // has not been verified. Refuse this scope before any queue effect.
            if (authorityKind === "source-bound")
              throw refuse("UFO 暂不支持来源权限可能独立撤销的运行中补充，请停止后发起新任务。");
            return inject(text, options, assertion);
          },
          claimPendingUserInputAnswer: (text, options, assertAuthority, authorityKind) =>
            claimPendingAgentQuestionAnswer({sessionKey: params.sessionKey, text,
              authority: {kind: authorityKind, assertCurrent: assertAuthority}}),
          cancelPendingUserInput: (resolvedBy, assertAuthority, authorityKind) =>
            cancelPendingAgentQuestionForSession({sessionKey: params.sessionKey, resolvedBy,
              authority: {kind: authorityKind, assertCurrent: assertAuthority}}),
        },
        abort, cancel: abort,
      };
      registerRun(params.sessionId, handle, params.sessionKey, params.sessionFile, params.agentId);
      params.abortSignal?.addEventListener("abort", abort, {once: true});
      const timeout = setTimeout(() => { timedOut = true;
        params.onAttemptTimeout?.(new Error("UFO run budget expired")); abort(); }, params.timeoutMs ?? 600_000);
      const startup = setTimeout(() => {
        if (!progressed) {
          startupTimedOut = true;
          // Keep the stage observed at the deadline.  A cancelled worker may
          // emit a late cleanup event (for example `initialization`) after
          // this callback, but that is not where the timeout occurred.
          startupTimeoutStage = startupStage;
          diagnostic("startup_timeout", {stage: startupTimeoutStage, timed_out: true});
          failure = new Error(ready ? "UFO worker initialization made no progress" : "UFO worker initialization timed out");
          abort();
        }
      }, params.ufoStartupTimeoutMs ?? 60_000);
      const emit = (stream, data) => {
        emitAgentEvent({runId: params.runId, stream, data});
      };
      const request = async frame => {
        diagnostic("host_callback", {operation: frame.operation});
        const controller = new AbortController(), captured = generation;
        requests.add(controller);
        const check = () => { assertCurrent(); controller.signal.throwIfAborted();
          if (captured !== generation) throw new Error("UFO request superseded"); };
        let value;
        try {
          check();
          // Retain each source-bound assertion through prompt consumption and
          // every subsequent tool dispatch, including after adoption receipts.
          for (const assertion of inputAuthorities.values()) assertion();
          if (frame.operation === "authorize_model" || frame.operation === "authorize_observation") {
            value = true;
          } else if (frame.operation === "authorize") {
            const decision = await host.runBeforeToolCall({toolName: frame.name,
              params: frame.arguments, toolCallId: frame.call_id,
              nativeOperation: {cwd: params.workspaceDir}});
            check();
            if (decision?.blocked) throw new Error("Tool policy denied this action");
            if (decision?.params && JSON.stringify(decision.params) !== JSON.stringify(frame.arguments))
              throw new Error("UFO cannot reproduce rewritten tool arguments");
            value = true;
          } else if (frame.operation === "confirm") {
            const approval = await host.requestApproval({signal: controller.signal,
              title: "UFO 桌面操作需要确认", description: frame.text, detail: frame.action,
              severity: "warning", toolName: "ufo_desktop_action",
              allowedDecisions: ["allow-once", "deny"], timeoutMs: 300_000});
            check();
            const resolved = approval?.id && !approval.decision ? await host.waitForApproval({
              approvalId: approval.id, timeoutMs: 300_000, signal: controller.signal,
            }) : approval;
            check(); value = resolved?.decision === "allow-once";
          } else if (frame.operation === "ask") {
            const id = randomUUID();
            const answer = await question({questions: [{id, header: "UFO", question: frame.text}],
              sessionKey: params.sessionKey, agentId: params.agentId, runId: params.runId,
              timeoutMs: 300_000, signal: controller.signal, delivery: params});
            check();
            const values = answer.answers?.answers?.[id];
            if (answer.status !== "answered" || !values?.length) throw new Error("User question was not answered");
            value = {text: values.join("\n"), receipt_id: id};
          } else throw new Error("Unknown UFO callback");
          check();
          for (const assertion of inputAuthorities.values()) assertion();
          writeFrame(worker, {type: "response", id: frame.id, ok: true, value});
          diagnostic("host_callback_finished", {operation: frame.operation});
        } catch {
          diagnostic("host_callback_refused", {operation: frame.operation});
          try { writeFrame(worker, {type: "response", id: frame.id, ok: false,
            error: "操作授权已取消、被拒绝或当前要求已变化。"}); } catch {}
        } finally { requests.delete(controller); }
      };
      try {
        for await (const frame of readFrames(worker.stdout)) {
          if (frame.type === "ready" && !ready && frame.version === 1) {
            ready = true; assertCurrent();
            diagnostic("worker_ready");
            params.onExecutionPhase?.({phase: "process_spawned", provider: params.provider,
              model: params.modelId, backend: HARNESS_ID});
            writeFrame(worker, {type: "run", run_id: params.runId, text: params.prompt, context, route, mode});
            params.userTurnTranscriptRecorder?.markSentToProvider();
          } else if (!ready) throw new Error("UFO protocol handshake failed");
          else if (frame.type === "request") {
            // Questions must not block the reader: cancellation and context
            // changes continue to arrive while a person decides.
            void request(frame);
          } else if (frame.type === "usage") {
            usage.input += frame.input; usage.output += frame.output; usage.totalTokens += frame.total;
            host.reportOutputTokens?.(frame.output);
          } else if (frame.type === "accepted") {
            // Queue admission is not proof the model consumed this input.
            receipts.get(frame.receipt_id)?.options?.onQueueAccepted?.(true);
          } else if (frame.type === "event") {
            const event = frame.event;
            if (typeof event.stage === "string") startupStage = event.stage;
            // Import heartbeats prove liveness, not successful initialization.
            // Keep the deadline until the native Session actually starts (or
            // an older worker reaches a model/tool or explicit user wait).
            if (!progressed && (event.stage === "session_running" ||
                ["planning", "paused"].includes(event.phase) ||
                ["model_attempt", "tool_started"].includes(event.type))) {
              progressed = true;
              clearTimeout(startup);
            }
            diagnostic(event.type, event);
            if (event.type === "context_applied") {
              for (const id of event.receipt_ids ?? []) {
                const pending = receipts.get(id);
                if (pending) {
                  inputAuthorities.get(id)?.();
                  pending.options?.userTurnTranscriptRecorder?.markSentToProvider();
                  pending.resolve(); receipts.delete(id);
                }
              }
            } else if (event.type === "tool_started") {
              params.onExecutionPhase?.({phase: "tool_execution_started", provider: params.provider,
                model: params.modelId, backend: HARNESS_ID, tool: event.action,
                toolCallId: event.call_id});
              hadEffects ||= event.tool_type === "action";
              emit("tool", {phase: "start", name: event.action, toolCallId: event.call_id, args: event.arguments});
            } else if (event.type === "tool_finished") {
              const resolution = params.observeToolTerminal?.({toolName: event.action,
                toolCallId: event.call_id, arguments: event.arguments, executionStarted: true,
                outcome: event.ok ? "success" : "failure",
                nativeMutation: {mutatingAction: event.tool_type === "action", replaySafe: event.tool_type !== "action"}});
              if (resolution) lastToolError = resolution.lastToolError;
              toolMetas.push({toolName: event.action, toolCallId: event.call_id, isError: !event.ok,
                replaySafe: event.tool_type !== "action"});
              emit("tool", {phase: "result", name: event.action, toolCallId: event.call_id,
                isError: !event.ok, result: {status: event.ok ? "completed" : "failed"}});
            } else if (event.type === "phase" || event.type === "waiting") {
              if (event.type === "waiting" && event.stage === "tool" && event.timed_out && !timedOut) {
                timedOut = true;
                failure = new Error("UFO desktop tool timed out");
                abort();
              }
              if (event.phase === "planning")
                params.onExecutionPhase?.({phase: "model_call_started", provider: params.provider,
                  model: params.modelId, backend: HARNESS_ID});
              emit("ufo", {phase: event.phase ?? event.stage, summary: event.summary,
                stopping: event.stopping, inFlight: event.in_flight});
              params.onRunProgress?.({reason: event.phase ?? event.stage ?? "ufo", backend: HARNESS_ID});
            }
          } else if (frame.type === "result" && !result) {
            diagnostic("result", frame.result);
            result = frame.result; worker.stdin.end();
          } else throw new Error("Unexpected UFO protocol message");
        }
      } catch (error) {
        diagnostic("protocol_error", {error_type: error?.name});
        failure = error; abort();
        worker.stdin.end();
      } finally {
        // Prefer cooperative cleanup; abort() has a bounded force-kill
        // fallback for a native call that never returns.
        await closed;
        if (forceStopTimer) clearTimeout(forceStopTimer);
        settled = true; clearTimeout(timeout); clearTimeout(startup);
        params.abortSignal?.removeEventListener("abort", abort);
        unregisterRun(params.sessionId, handle, params.sessionKey, params.sessionFile);
        workers.delete(worker);
        for (const controller of requests) controller.abort();
        for (const pending of receipts.values()) pending.reject(new Error("UFO did not confirm adoption of this input"));
      }
      // A startup timeout asks abort() to stop the worker, so the worker may
      // report a late cooperative "cancelled" result. Preserve the actual
      // cause instead of exposing a generic cancelled/error turn to OpenClaw.
      if (startupTimedOut) result = {
        status: "failed",
        message: `UFO 执行器初始化超时（阶段：${startupTimeoutStage ?? startupStage}），未进入原生 Session；请查看 .artifacts/ufo-runs/${params.runId}.jsonl。`,
        reason: "startup_timeout",
      };
      else if (!result) result = timedOut ?
        {status: "incomplete", message: "UFO 桌面操作超时，已停止本次任务。"} :
        {status: stopped ? "cancelled" : "failed",
          message: stopped ? "执行已停止；已发出的操作已完成收尾。" : "UFO 执行进程未能返回结果，请检查依赖和配置。"};
      if (failure && result.status === "completed") result = {status: "incomplete",
        message: "执行进程异常结束，无法确认任务全部完成。"};
      const assistant = assistantMessage(params, result, usage);
      diagnostic("finished", {status: result.status, reason: result.reason});
      let owned = false;
      // Internal startup protection is not a user cancellation. Persist this
      // diagnostic reply so restart recovery does not end with a blank Error.
      if ((!stopped || startupTimedOut) && assistant.stopReason !== "aborted") {
        host.assertActive();
        const saved = await persistAssistant({...params.sessionTarget, agentId: params.agentId,
          sessionId: params.sessionId, sessionKey: params.sessionKey, config: params.config,
          runId: params.runId, message: assistant, eventId: "ufo:" + params.runId, idempotencyLookup: "scan",
          assertCurrent: host.assertActive});
        if (saved.kind === "rejected" || saved.ok === false) throw new Error("UFO reply could not be persisted");
        owned = true;
      }
      const terminal = timedOut ? {kind: "timeout", phase: "prompt", source: "run_budget", aborted: true} :
        startupTimedOut ? {kind: "failed", phase: "prompt", source: "startup", error: new Error(assistant.errorMessage)} :
        result.status === "cancelled" || stopped ? {kind: "aborted", source: "external"} :
        result.status === "failed" ? {kind: "failed", source: "prompt", error: new Error(assistant.errorMessage)} : {kind: "ok"};
      const replay = {hadPotentialSideEffects: hadEffects, replaySafe: !hadEffects};
      return {terminal, sessionIdUsed: params.sessionId, sessionFileUsed: params.sessionFile,
        agentHarnessId: HARNESS_ID, assistantTranscriptOwned: owned,
        messagesSnapshot: [assistant], assistantTexts: [textOf(assistant)],
        lastAssistant: assistant, currentAttemptAssistant: assistant, toolMetas, lastToolError,
        didSendViaMessagingTool: false, messagingToolSentTexts: [], messagingToolSentMediaUrls: [],
        messagingToolSentTargets: [], cloudCodeAssistFormatError: false,
        replayMetadata: replay, currentAttemptReplayMetadata: replay,
        itemLifecycle: {startedCount: toolMetas.length, completedCount: toolMetas.length, activeCount: 0},
        attemptUsage: usage, hasSuccessfulModelResponse: usage.output > 0};
    },
    async dispose() {
      for (const worker of workers.keys()) {
        try { writeFrame(worker, {type: "cancel"}); worker.stdin.end(); } catch {}
      }
      await Promise.allSettled([...workers.values()]);
    },
  };
}
