/** Private stdio transport for Mirally. Uses the unmodified official client. */
import { readFileSync } from "node:fs";
import { resolve, delimiter, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import {
  GatewayClient, getGatewaySessionMessageSubscriptionCoordinator,
  resetGatewaySessionMessageSubscriptionCoordinator, isGatewayProtocolResponseError,
} from "@openclaw/gateway-client";
import { PROTOCOL_VERSION } from "@openclaw/gateway-protocol/version";
import { createDeviceHost } from "./device-store.mjs";
import { SessionView } from "./session-view.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const state = resolve(root, "config/openassist/openclaw");
const configPath = resolve(state, "openclaw.json");
const secrets = new Set();
function redact(text) {
  for (const secret of secrets) if (secret) text = text.replaceAll(secret, "[已隐藏凭据]");
  return text;
}
function emit(value) { process.stdout.write(redact(JSON.stringify(value)) + "\n"); }
function errorOf(error) { return redact(String(error?.message ?? error)); }

export class DesktopBridge {
  constructor(config, publish = emit) {
    this.publish = publish;
    this.views = new Map();
    this.selected = null;
    this.sessions = [];
    this.interactions = [];
    this.connected = false;
    this.epoch = 0;
    this.selectionRevision = 0;
    this.refreshTimer = null;
    this.rosterTimer = null;
    this.stopping = false;
    this.url = `ws://127.0.0.1:${config.gateway.port ?? 18789}`;
    const device = createDeviceHost(resolve(state, "mirally-client"), this.url, secret => secrets.add(secret));
    secrets.add(config.gateway.auth?.token);
    for (const provider of Object.values(config.models?.providers ?? {})) secrets.add(provider.apiKey);
    this.client = new GatewayClient({
      url: this.url, token: config.gateway.auth?.token,
      deviceIdentity: device.identity, hostDeps: { ...device.hostDeps, logError: () => {} },
      clientName: "gateway-client", clientDisplayName: "Mirally Desktop",
      clientVersion: "0.1.0", mode: "ui", platform: process.platform,
      role: "operator", scopes: ["operator.read", "operator.write", "operator.approvals", "operator.questions"],
      caps: ["tool-events"], minProtocol: PROTOCOL_VERSION, maxProtocol: PROTOCOL_VERSION,
      onHelloOk: hello => {
        this.connected = true;
        this.epoch++;
        resetGatewaySessionMessageSubscriptionCoordinator(this.client);
        this.publish({ type: "connection", connected: true, scopes: hello.auth?.scopes ?? [] });
        void this.restore().catch(error => this.report(error));
      },
      onClose: () => {
        this.connected = false;
        this.epoch++;
        for (const view of this.views.values()) view.projection = { ...view.projection, hasTransportGap: true };
        if (!this.stopping) this.publish({ type: "connection", connected: false, message: "连接已断开，正在重连。后台任务由 OpenClaw 继续管理。" });
      },
      onConnectError: error => this.publish({ type: "connection", connected: false, message: errorOf(error) }),
      onReconnectPaused: info => this.publish({ type: "connection", connected: false, message: `连接需要处理：${info.detailCode ?? info.reason}。请通过原生界面或 CLI 检查设备配对。` }),
      onGap: () => { void this.restore().catch(error => this.report(error)); },
      onEvent: event => this.event(event),
    });
  }

  report(error) { this.publish({ type: "error", message: errorOf(error) }); }
  start() { this.client.start(); }
  view(key) {
    if (!this.views.has(key)) this.views.set(key, new SessionView(key));
    return this.views.get(key);
  }
  scheduleSnapshot() {
    if (this.refreshTimer) return;
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      if (this.selected) this.publish({ type: "session", ...this.view(this.selected).snapshot() });
    }, 45);
  }

  async roster() {
    const epoch = this.epoch;
    const list = await this.client.request("sessions.list", { limit: 100, includeDerivedTitles: true, includeLastMessage: true });
    if (epoch !== this.epoch || !this.connected) return;
    this.sessions = list.sessions ?? [];
    this.publish({ type: "sessions", sessions: this.sessions });
  }

  async restore() {
    if (!this.connected) return;
    const epoch = this.epoch;
    await this.client.request("sessions.subscribe", { limit: 100 });
    if (epoch !== this.epoch) return;
    await this.roster();
    if (this.selected) await this.select(this.selected);
    await this.loadInteractions();
  }

  async select(key) {
    if (!this.connected) throw new Error("尚未连接 OpenClaw。草稿仍然保留。");
    const revision = ++this.selectionRevision;
    const epoch = this.epoch;
    const previous = this.subscription;
    this.subscription = null;
    this.selected = key;
    this.publish({ type: "selecting", key });
    if (previous) await getGatewaySessionMessageSubscriptionCoordinator(this.client).release(previous);
    const subscription = await getGatewaySessionMessageSubscriptionCoordinator(this.client).acquire(key, { includeApprovals: true });
    if (epoch !== this.epoch || revision !== this.selectionRevision) {
      await getGatewaySessionMessageSubscriptionCoordinator(this.client).release(subscription);
      return;
    }
    this.subscription = subscription;
    await this.history(key);
  }

  async history(key) {
    const epoch = this.epoch;
    const view = this.view(key);
    const revision = (view.historyRevision ?? 0) + 1;
    view.historyRevision = revision;
    const history = await this.client.request("chat.history", { sessionKey: key, limit: 200 });
    if (epoch !== this.epoch || view.historyRevision !== revision) return;
    view.history(history);
    if (this.selected === key) this.scheduleSnapshot();
  }

  async loadInteractions() {
    const epoch = this.epoch;
    const methods = ["exec.approval.list", "plugin.approval.list", "openclaw.approval.list", "question.list"];
    const results = await Promise.allSettled(methods.map(method => this.client.request(method, {})));
    if (epoch !== this.epoch) return;
    this.interactions = results.flatMap((result, index) => {
      if (result.status !== "fulfilled") return [];
      const list = result.value.approvals ?? result.value.questions ?? result.value.pending ?? [];
      return Array.isArray(list) ? list.map(item => ({ kind: methods[index], ...item })) : [];
    }).filter(item => !item.status || ["pending", "requested"].includes(item.status));
    const unavailable = results.flatMap((result, index) => result.status === "rejected" ? [methods[index]] : []);
    this.publish({ type: "interactions", items: this.interactions, unavailable });
  }

  event({ event, payload = {} }) {
    const key = payload.sessionKey ?? payload.key;
    const view = key ? this.views.get(key) : null;
    if (event === "chat" && view) {
      view.chat(payload);
      if (["final", "aborted", "error"].includes(payload.state)) void this.history(key).catch(error => this.report(error));
    } else if (event === "session.message" && view && payload.message) {
      view.persisted(payload);
    } else if (event === "agent" && view) {
      view.agent(payload);
      if (payload.stream === "lifecycle" && ["end", "error"].includes(payload.data?.phase)) {
        void this.history(key).catch(error => this.report(error));
      }
    } else if (event === "sessions.changed") {
      // An authoritative roster read also covers membership, deletions and
      // ancestor updates. Retain a trailing read for changes during a read.
      clearTimeout(this.rosterTimer);
      this.rosterTimer = setTimeout(() => {
        this.rosterTimer = null;
        void this.roster().catch(error => this.report(error));
      }, 200);
      if (key && this.views.has(key) && payload.session) {
        const current = this.view(key);
        const row = payload.session;
        if (Array.isArray(row.activeRunIds)) current.activeRunIds = new Set(row.activeRunIds);
        else if (row.activeRunIds === null) current.activeRunIds.clear();
        current.activeUnknown = row.hasActiveRun === true && !current.activeRunIds.size;
        current.info = row;
      }
      if (key && key === this.selected && ["reset", "delete", "compact", "rewind"].includes(payload.reason)) {
        void this.history(key).catch(error => this.report(error));
      }
    }
    if (view && key === this.selected) this.scheduleSnapshot();
    if (/approval\.(requested|resolved)$/.test(event) || /^question\./.test(event) || event === "session.approval") {
      void this.loadInteractions().catch(error => this.report(error));
    }
  }

  async command({ op, params = {} }) {
    if (op === "dashboard") return nativeDashboard();
    if (op === "shutdown") { await this.stop(); return {}; }
    if (!this.connected) throw new Error("尚未连接 OpenClaw。请检查 Gateway 是否运行，或打开原生界面。");
    if (op === "select") { await this.select(params.key); return { key: params.key }; }
    if (op === "new") {
      const created = await this.client.request("sessions.create", { agentId: "main", displayName: "新的会话" });
      await this.select(created.key);
      await this.roster();
      return { key: created.key };
    }
    if (op === "refresh") { await this.restore(); return {}; }
    if (op === "send") {
      const key = params.key;
      if (!key || typeof params.message !== "string" || !params.message.trim()) throw new Error("请选择会话并输入消息。");
      const id = params.idempotencyKey || randomUUID();
      const view = this.view(key);
      view.pending(params.message, id);
      this.scheduleSnapshot();
      try {
        const ack = await this.client.request("chat.send", {
          sessionKey: key, message: params.message, idempotencyKey: id, queueMode: "steer",
        });
        view.acknowledged(ack, id);
        this.scheduleSnapshot();
        // ACK is admission, not proof that the model consumed a supplement.
        void this.history(key).catch(error => this.report(error));
        return { ...ack, key, idempotencyKey: id };
      } catch (error) {
        if (isGatewayProtocolResponseError(error)) {
          view.failed(id, errorOf(error));
        } else {
          // A broken connection or timeout is not authoritative rejection.
          // Retain this pending projection and reuse the native send key on
          // an explicit retry; reconnect history can still commit the turn.
          view.error = "发送结果尚未确认。重连后将核对原生历史；重试相同消息会复用发送标识。";
          view.errorType = "uncertain";
          error.uncertain = true;
        }
        this.scheduleSnapshot();
        throw error;
      }
    }
    if (op === "abort") {
      const view = this.view(params.key);
      const runId = params.runId;
      if (!runId || !view.activeRunIds.has(runId)) throw new Error("没有可确认的执行回合，请刷新或通过原生界面停止。");
      return this.client.request("chat.abort", { sessionKey: params.key, runId });
    }
    throw new Error(`Unsupported desktop operation: ${op}`);
  }

  async stop() {
    this.stopping = true;
    clearTimeout(this.refreshTimer);
    clearTimeout(this.rosterTimer);
    await this.client.stopAndWait();
  }
}

export async function nativeDashboard() {
  const runtime = JSON.parse(readFileSync(resolve(root, ".artifacts/openclaw-runtime.json"), "utf8"));
  const node = resolve(root, runtime.node);
  const env = { ...process.env, OPENCLAW_STATE_DIR: state, OPENCLAW_CONFIG_PATH: configPath };
  env.PATH = dirname(node) + delimiter + (env.PATH ?? "");
  return new Promise((accept, reject) => {
    const child = spawn(node, [resolve(root, runtime.entry), "dashboard", "--json"], { cwd: root, env, windowsHide: true });
    let output = "";
    child.stdout.on("data", data => { output += data.toString(); });
    // Native stderr can contain local configuration diagnostics; never forward
    // it through the GUI channel or include it in public error messages.
    child.stderr.resume();
    const timeout = setTimeout(() => { child.kill(); reject(new Error("原生界面启动超时，请使用 run-openclaw.ps1 dashboard。")); }, 60000);
    child.once("error", error => { clearTimeout(timeout); reject(error); });
    child.once("exit", code => {
      clearTimeout(timeout);
      try {
        const value = JSON.parse(output);
        if (code !== 0 || !value.ok || !value.browserUrl) throw new Error("原生界面暂不可用，请检查 Gateway。" );
        accept({ url: value.browserUrl });
      } catch { reject(new Error("原生界面暂不可用，请检查 Gateway。")); }
    });
  });
}

async function main() {
  let config;
  try { config = JSON.parse(readFileSync(configPath, "utf8")); }
  catch { throw new Error("无法读取原生配置，请先完成 OpenClaw 安装与配置校验。"); }
  const bridge = new DesktopBridge(config);
  let stopping = false;
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  lines.on("line", line => {
    let input;
    try { input = JSON.parse(line); } catch { emit({ type: "error", message: "Invalid bridge command." }); return; }
    if (stopping) return;
    if (input.op === "shutdown") stopping = true;
    void bridge.command(input).then(result => {
      emit({ type: "result", id: input.id, op: input.op, result });
      if (stopping) lines.close();
    }).catch(error => emit({ type: "result", id: input.id, op: input.op, error: errorOf(error), uncertain: error.uncertain === true }));
  });
  lines.on("close", () => { stopping = true; void bridge.stop(); });
  bridge.start();
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { emit({ type: "error", message: errorOf(error) }); process.exitCode = 1; });
}
