/** Live native Gateway checks. No Mirally controller or desktop automation. */
import { readFile, writeFile, mkdir, access } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { GatewayClient } from "@openclaw/gateway-client";
import { PROTOCOL_VERSION } from "@openclaw/gateway-protocol/version";

const root = fileURLToPath(new URL("../../", import.meta.url));
if (!process.argv.includes("--live")) {
  console.error("This check calls the configured model. Run with --live to proceed.");
  process.exit(2);
}
const config = JSON.parse(await readFile(resolve(root, "config/mirally/openclaw/openclaw.json"), "utf8"));
const secrets = [config.gateway.auth.token, ...Object.values(config.models.providers).map(p => p.apiKey)].filter(Boolean);
function redact(value) {
  let text = typeof value === "string" ? value : JSON.stringify(value);
  for (const secret of secrets) text = text.replaceAll(secret, "[REDACTED]");
  return text;
}
const output = resolve(root, "output/openclaw-native-verification/gateway");
await mkdir(output, { recursive: true });
const checks = {};
const evidence = [];
const activeTests = new Map();
let client;
let hello;

async function connect() {
  const ready = Promise.withResolvers();
  client = new GatewayClient({
    url: `ws://127.0.0.1:${config.gateway.port}`,
    token: config.gateway.auth.token,
    minProtocol: PROTOCOL_VERSION, maxProtocol: PROTOCOL_VERSION,
    role: "operator", scopes: ["operator.admin", "operator.read", "operator.write", "operator.approvals"],
    caps: ["tool-events"],
    onHelloOk: value => { hello = value; ready.resolve(value); },
    onConnectError: error => ready.reject(error),
    onEvent: event => evidence.push({ receivedAt: Date.now(), ...event }),
  });
  client.start();
  await ready.promise;
  checks.gateway_protocol = hello.protocol === PROTOCOL_VERSION;
}

async function request(method, params = {}) {
  const result = await client.request(method, params);
  evidence.push({ method, params, result, receivedAt: Date.now() });
  return result;
}

async function turn(sessionKey, message) {
  const ack = await request("chat.send", { sessionKey, message, idempotencyKey: randomUUID() });
  if (!ack.runId) throw new Error("Native send returned no runId.");
  activeTests.set(ack.runId, sessionKey);
  const result = await client.request("agent.wait", { runId: ack.runId, timeoutMs: 240000 }, { timeoutMs: 250000 });
  evidence.push({ method: "agent.wait", result, runId: ack.runId });
  if (result.status !== "ok") throw new Error(`Run did not finish successfully: ${redact(result)}`);
  activeTests.delete(ack.runId);
  return request("chat.history", { sessionKey, limit: 100 });
}

function textOf(message) {
  if (typeof message.content === "string") return message.content;
  return (message.content ?? []).filter(part => part.type === "text").map(part => part.text).join("\n");
}
function lastAssistant(history) {
  return textOf(history.messages.filter(message => message.role === "assistant").at(-1) ?? {});
}

async function waitFor(predicate, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error("Timed out waiting for native test evidence.");
}

async function verifyActiveRun() {
  const label = randomUUID().slice(0, 8);
  const directory = resolve(config.agents.defaults.workspace, "native-verification", label);
  await mkdir(directory, { recursive: true });
  const worker = resolve(directory, "worker.py");
  const ready = resolve(directory, "ready.txt");
  const release = resolve(directory, "release.txt");
  const resultFile = resolve(directory, "result.txt");
  const replacement = `STEER-${label}`;
  await writeFile(worker,
    "from pathlib import Path\nimport time\np = Path(__file__).parent\n" +
    "(p/'ready.txt').write_text('ready', encoding='utf-8')\n" +
    "deadline = time.monotonic() + 120\nwhile not (p/'release.txt').exists():\n" +
    "    if time.monotonic() > deadline: raise SystemExit('release timed out')\n" +
    "    time.sleep(0.2)\nprint('worker finished')\n", "utf8");
  const created = await request("sessions.create", { agentId: "main", displayName: "Mirally 补充与重连验收" });
  const sessionKey = created.key;
  await request("sessions.messages.subscribe", { key: sessionKey });
  const ack = await request("chat.send", { sessionKey, idempotencyKey: randomUUID(), message:
    `这轮是后台验收，不进行出生流程。用exec工具运行Python脚本 ${worker}，解释器是 ${resolve(root, ".venv/Scripts/python.exe")}，使用后台方式或短yield。脚本等待外部释放文件，请通过process工具等待它完成。不要自己创建release.txt。脚本成功退出之后，用write工具在 ${resultFile} 写入 ORIGINAL-${label}，然后结束。不要操作桌面。` });
  activeTests.set(ack.runId, sessionKey);
  await waitFor(async () => { try { await access(ready); return true; } catch { return false; } }, 90000);
  await client.stopAndWait();
  await connect();
  await request("sessions.messages.subscribe", { key: sessionKey });
  const restored = await request("chat.history", { sessionKey, limit: 100 });
  checks.active_run_after_reconnect = restored.inFlightRun?.runId === ack.runId || restored.sessionInfo?.activeRunIds?.includes(ack.runId) === true;
  const steer = await request("chat.send", { sessionKey, idempotencyKey: randomUUID(), queueMode: "steer", message:
    `补充要求：脚本成功后，result.txt内容改成 ${replacement}，不要写最初的ORIGINAL标记。此消息用于验证运行中补充。继续原任务即可。` });
  // An ACK only proves admission. The final file below proves model consumption.
  evidence.push({ kind: "steer-admission", ...steer });
  await writeFile(release, "release", "utf8");
  const outcome = await client.request("agent.wait", { runId: ack.runId, timeoutMs: 180000 }, { timeoutMs: 190000 });
  evidence.push({ method: "agent.wait", result: outcome, runId: ack.runId });
  if (outcome.status !== "ok") throw new Error(`Steering run did not finish: ${redact(outcome)}`);
  activeTests.delete(ack.runId);
  await waitFor(async () => { try { return (await readFile(resultFile, "utf8")).trim() === replacement; } catch { return false; } }, 45000);
  checks.steer_consumed = (await readFile(resultFile, "utf8")).trim() === replacement;
  const final = await request("chat.history", { sessionKey, limit: 100 });
  checks.steer_in_history = final.messages.some(message => message.role === "user" && textOf(message).includes(replacement));
  console.log("Active reconnect and consumed steering verified:", replacement);
}

async function verifyAbort() {
  const created = await request("sessions.create", { agentId: "main", displayName: "Mirally 停止回合验收" });
  const sessionKey = created.key;
  await request("sessions.messages.subscribe", { key: sessionKey });
  const ack = await request("chat.send", { sessionKey, idempotencyKey: randomUUID(), message:
    "这是一轮停止按钮验收。请写一篇较长的关于科技史的文章，不使用工具、不写文件，也不进行出生流程。" });
  activeTests.set(ack.runId, sessionKey);
  await request("chat.abort", { sessionKey, runId: ack.runId });
  await waitFor(() => evidence.some(event => event.event === "chat" && event.payload?.runId === ack.runId && event.payload?.state === "aborted"), 15000);
  activeTests.delete(ack.runId);
  const history = await request("chat.history", { sessionKey, limit: 10 });
  checks.agent_abort_event = true;
  checks.agent_idle_after_abort = history.sessionInfo?.hasActiveRun === false;
}

try {
  await connect();
  const roster = await request("sessions.subscribe", { limit: 30 });
  checks.native_session_subscription = roster.subscribed === true && Array.isArray(roster.list?.sessions);
  const manifest = JSON.parse(await readFile(resolve(root, "output/openclaw-native-verification/verification.json"), "utf8"));
  const baseline = await request("chat.history", { sessionKey: manifest.sessionKey, limit: 100 });
  checks.original_task_history = baseline.messages.some(message => textOf(message).includes(manifest.marker));
  const originalCalls = baseline.messages.flatMap(message => (Array.isArray(message.content) ? message.content : []))
    .filter(part => part.type === "toolCall").map(part => part.name);
  checks.file_tools_verified = ["read", "write", "exec"].every(name => originalCalls.includes(name));
  console.log("Original native tool calls:", [...new Set(originalCalls)].join(", ") || "See saved history projection");

  const createdContext = await request("sessions.create", { agentId: "main", displayName: "Mirally 原生上下文验收" });
  const sessionKey = createdContext.key;
  checks.native_session_creation = typeof sessionKey === "string" && sessionKey.length > 0;
  await request("sessions.messages.subscribe", { key: sessionKey });
  const marker = `MIRA-${randomUUID().slice(0, 8)}`;
  await turn(sessionKey, `这是一轮临时验收对话。请记住：项目代号是 ${marker}，负责人是林晓，约定下次讨论时间是14:30。只回复一句确认，不需要工具，不要写入文件或长期记忆，也不进行出生流程。`);
  await client.stopAndWait();
  await connect();
  await request("sessions.messages.subscribe", { key: sessionKey });
  const recovered = await request("chat.history", { sessionKey, limit: 100 });
  checks.reconnect_restored_history = recovered.messages.some(message => textOf(message).includes(marker));
  const followup = await turn(sessionKey, "刚才的项目代号、负责人和下次讨论时间分别是什么？不要读文件，不用任何工具，只根据当前会话回答。");
  const answer = lastAssistant(followup);
  checks.same_session_context = [marker, "林晓", "14:30"].every(term => answer.includes(term));
  console.log("Context after reconnect:", redact(answer));

  const createdWeb = await request("sessions.create", { agentId: "main", displayName: "Mirally 原生联网验收" });
  const webKey = createdWeb.key;
  await request("sessions.messages.subscribe", { key: webKey });
  const web = await turn(webKey,
    "请做一次原生联网验收：先用web_fetch读取https://example.com并报告标题；然后用browser工具在openclaw独立浏览器中打开同一网站并读取snapshot，报告页面标题和Learn more链接指向。不要调用web_search，不操作我的桌面鼠标，不打开普通桌面窗口，也不要进行出生流程。报告实际工具失败，不要猜测。" );
  const webAnswer = lastAssistant(web);
  const webToolEvents = evidence.filter(event => event.event === "agent" && event.payload?.sessionKey === webKey && event.payload?.stream === "tool");
  checks.web_fetch_invoked = webToolEvents.some(event => event.payload?.data?.name === "web_fetch");
  checks.browser_invoked = webToolEvents.some(event => event.payload?.data?.name === "browser");
  checks.web_answer = webAnswer.includes("Example Domain") && /iana\.org/.test(webAnswer);
  console.log("Web/browser result:", redact(webAnswer).slice(0, 1800));
  console.log("Live tool names:", [...new Set(webToolEvents.map(event => event.payload?.data?.name))].join(", "));
  await verifyActiveRun();
  await verifyAbort();
  for (const method of ["exec.approval.list", "plugin.approval.list", "openclaw.approval.list", "question.list"]) {
    await request(method);
  }
  checks.native_interaction_lists_readable = true;
} catch (error) {
  checks.error = redact(error.message);
  console.error(redact(error.message));
  process.exitCode = 1;
} finally {
  if (client) {
    for (const [runId, sessionKey] of activeTests) {
      try { await request("chat.abort", { sessionKey, runId }); } catch { /* Preserve diagnostics if disconnected. */ }
    }
    await client.stopAndWait();
  }
  await writeFile(resolve(output, "evidence.json"), redact(evidence), "utf8");
  await writeFile(resolve(output, "checks.json"), JSON.stringify(checks, null, 2), "utf8");
  console.log(JSON.stringify(checks, null, 2));
  if (Object.values(checks).some(value => value !== true)) process.exitCode = 1;
}
