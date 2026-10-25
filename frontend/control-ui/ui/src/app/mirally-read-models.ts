/** Read-only projections backed by Mirally product owners, never placeholder inventories. */
type Data = Record<string, any>;
export type MirallyReadModelApi = { rpc(method: string, params?: Data): Promise<Data> };
export type MirallyReadModelResult = { handled: false } | { handled: true; value: unknown };
export const MIRALLY_READ_METHODS = ["cron.status", "cron.list", "cron.get"] as const;

/** Presentation resolves the same product route as generation; default stays legacy-compatible. */
export function selectedProductModel(settings: Data, purpose = 'chat'): Data {
  const id = settings.model_routes?.[purpose] ?? 'default';
  if (id === 'default') return settings.model ?? {};
  const connection = settings.model_connections?.[id];
  if (!connection) throw new Error('已选择的模型连接不存在，请刷新模型设置。');
  return connection.model;
}

const unsupported = (method: string, detail = "当前 Mirally 内核尚未提供此读取能力"): never => {
  throw Object.assign(new Error(`${detail}：${method}`), {
    code: "METHOD_NOT_FOUND", gatewayCode: "METHOD_NOT_FOUND",
  });
};
const invalid = (message: string): never => {
  throw Object.assign(new Error(message), { code: "INVALID_PARAMS", gatewayCode: "INVALID_PARAMS" });
};
const key = (id: string) => `agent:main:${id}`;
const timestamp = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 8.64e15;

async function reminderJobs(api: MirallyReadModelApi, method: string) {
  const [source, history] = await Promise.all([
    api.rpc("commitment.list"), api.rpc("delivery.list"),
  ]);
  if (!Array.isArray(source.commitments) || !Array.isArray(history.deliveries)) {
    throw new Error("Mirally 提醒数据格式无效");
  }
  // These owners currently cap reads at 200 without a total or continuation cursor.
  // At that boundary, claiming a complete inventory would silently hide older facts.
  if (source.commitments.length >= 200 || history.deliveries.length >= 200) {
    unsupported(method, "提醒记录已达到当前读取上限，后端尚未提供完整分页");
  }
  const deliveries = new Map<string, Data>(history.deliveries
    .filter((item: Data) => item.kind === "commitment")
    .map((item: Data) => [item.reference_id, item]));
  return source.commitments.map((item: Data) => {
    if (typeof item.id !== "string" || !item.id || typeof item.title !== "string" ||
        typeof item.body !== "string" || !timestamp(item.due_at) || !timestamp(item.created_at) ||
        !["pending", "queued", "fulfilled", "cancelled", "expired"].includes(item.status)) {
      throw new Error("Mirally 提醒记录缺少有效的身份、时间或状态");
    }
    const receipt = deliveries.get(item.id);
    if (receipt && (!timestamp(receipt.created_at) || !timestamp(receipt.updated_at))) {
      throw new Error("Mirally 提醒投递记录缺少有效时间");
    }
    const enabled = item.status === "pending";
    const state: Data = {};
    if (enabled) state.nextRunAtMs = item.due_at;
    if (receipt) {
      state.lastRunAtMs = receipt.created_at;
      if (receipt.status === "delivered") state.lastRunStatus = "ok";
      if (receipt.status === "failed") state.lastRunStatus = "error";
      if (receipt.status === "cancelled") state.lastRunStatus = "skipped";
      state.lastDeliveryStatus = receipt.status === "delivered" ? "delivered"
        : receipt.status === "failed" || receipt.status === "cancelled" ? "not-delivered" : "unknown";
      if (["delivered", "failed", "cancelled"].includes(receipt.status)) {
        state.lastDelivered = receipt.status === "delivered";
      }
    }
    const sessionKey = item.conversation_id ? key(item.conversation_id) : undefined;
    return { id: item.id, name: item.title, description: item.body, agentId: "main",
      ...(sessionKey ? { sessionKey } : {}), enabled, createdAtMs: item.created_at,
      updatedAtMs: receipt?.updated_at ?? item.created_at,
      schedule: { kind: "at", at: new Date(item.due_at).toISOString() },
      sessionTarget: sessionKey ? `session:${sessionKey}` : "main", wakeMode: "now",
      payload: { kind: "systemEvent", text: item.body },
      delivery: { mode: "announce", channel: item.channel }, state };
  });
}

function pageInteger(value: unknown, fallback: number, low: number, high: number) {
  if (value === undefined) return fallback;
  return typeof value === "number" && Number.isSafeInteger(value) && value >= low && value <= high
    ? value : invalid("提醒列表分页参数无效");
}
function choice(value: unknown, fallback: string, choices: string[]) {
  if (value === undefined) return fallback;
  return typeof value === "string" && choices.includes(value) ? value : invalid("提醒列表筛选参数无效");
}

/** Only rpc is required; unsupported host-specific read domains remain explicit errors. */
export async function readMirallyModel(
  method: string, p: Data, api: MirallyReadModelApi,
): Promise<MirallyReadModelResult> {
  if (["sessions.observer.visibility", "sessions.branches.list", "sessions.companion.state",
    "question.list", "question.get", "tasks.list", "tasks.get", "tasks.history", "cron.runs",
    "cron.scratch.get"].includes(method)) {
    // Desktop actions have unknown/waiting states and no Gateway task ledger; the
    // companion here is the old host's independent Q&A thread, not Mirally's pet.
    return unsupported(method);
  }
  if (!(MIRALLY_READ_METHODS as readonly string[]).includes(method)) return { handled: false };
  const jobs: Data[] = await reminderJobs(api, method);
  if (method === "cron.status") {
    const status = await api.rpc("system.status");
    const due = jobs.filter(job => job.enabled).map(job => job.state.nextRunAtMs as number);
    return { handled: true, value: { enabled: status.ready === true && status.scheduler === "host_tick",
      triggersEnabled: false, jobs: jobs.length, nextWakeAtMs: due.length ? Math.min(...due) : null } };
  }
  if (method === "cron.get") {
    const id = p.id ?? p.jobId;
    if (typeof id !== "string" || !id) invalid("请选择需要读取的提醒");
    const job = jobs.find(item => item.id === id);
    if (!job) throw Object.assign(new Error("提醒不存在或已超出当前读取范围"), {
      code: "NOT_FOUND", gatewayCode: "NOT_FOUND",
    });
    return { handled: true, value: job };
  }
  const limit = pageInteger(p.limit, 50, 1, 200), offset = pageInteger(p.offset, 0, 0, 200);
  const enabled = choice(p.enabled, p.includeDisabled ? "all" : "enabled", ["all", "enabled", "disabled"]);
  const schedule = choice(p.scheduleKind, "all", ["all", "at", "every", "cron", "on-exit", "stream"]);
  const outcome = choice(p.lastRunStatus, "all", ["all", "ok", "error", "skipped", "unknown"]);
  const trigger = choice(p.trigger, "all", ["all", "conditional", "unconditional"]);
  const sort = choice(p.sortBy, "nextRunAtMs", ["nextRunAtMs", "updatedAtMs", "name"]);
  const direction = choice(p.sortDir, "asc", ["asc", "desc"]) === "asc" ? 1 : -1;
  if (p.query !== undefined && typeof p.query !== "string") invalid("提醒搜索文本无效");
  const query = (p.query || "").trim().toLocaleLowerCase();
  const filtered = jobs.filter(job =>
    (!p.agentId || p.agentId === "main") && (!p.sessionAgentId || p.sessionAgentId === "main") &&
    (!p.sessionKey || job.sessionKey === (p.sessionKey === "main" ? key("main") : p.sessionKey)) &&
    (enabled === "all" || job.enabled === (enabled === "enabled")) &&
    (schedule === "all" || schedule === "at") && trigger !== "conditional" &&
    (outcome === "all" || (job.state.lastRunStatus ?? "unknown") === outcome) &&
    (!query || `${job.name}\n${job.description}`.toLocaleLowerCase().includes(query)));
  filtered.sort((left, right) => {
    const a = sort === "name" ? left.name : sort === "updatedAtMs" ? left.updatedAtMs : left.state.nextRunAtMs;
    const b = sort === "name" ? right.name : sort === "updatedAtMs" ? right.updatedAtMs : right.state.nextRunAtMs;
    if (a === undefined || b === undefined) return a === b ? left.id.localeCompare(right.id) : a === undefined ? 1 : -1;
    return (typeof a === "string" ? a.localeCompare(b) : a - b) * direction || left.id.localeCompare(right.id);
  });
  if (offset > filtered.length) invalid("提醒列表偏移已失效，请重新加载");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(filtered)));
  const snapshotRevision = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  const rows = filtered.slice(offset, offset + limit);
  const values = p.compact ? rows.map(job => ({ id: job.id, name: job.name, agentId: job.agentId,
    enabled: job.enabled, updatedAtMs: job.updatedAtMs, nextRunAtMs: job.state.nextRunAtMs ?? null,
    lastRunAtMs: job.state.lastRunAtMs ?? null, lastRunStatus: job.state.lastRunStatus ?? null })) : rows;
  const next = offset + rows.length;
  return { handled: true, value: { jobs: values, snapshotRevision, total: filtered.length,
    limit, offset, nextOffset: next < filtered.length ? next : null, hasMore: next < filtered.length } };
}
