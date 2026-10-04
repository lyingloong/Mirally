/** Display projection only. OpenClaw remains the owner of sessions and runs. */
import {
  createSessionProjection, reduceSessionProjection, reduceSessionProjectionRunEvent,
  readSessionMessageIdentity, projectLiveSessionMessage,
  reconcileSessionProjectionSnapshot,
} from "@openclaw/gateway-client";

export function textOf(message) {
  if (typeof message?.content === "string") return message.content;
  return (Array.isArray(message?.content) ? message.content : [])
    .filter(part => part?.type === "text").map(part => part.text ?? "").join("\n");
}

export class SessionView {
  constructor(key) {
    this.key = key;
    this.projection = createSessionProjection({ sessionKey: key });
    this.info = {};
    this.activeRunIds = new Set();
    this.activeUnknown = false;
    this.activity = null;
    this.error = "";
    this.errorType = "";
  }

  history(history) {
    this.info = history.sessionInfo ?? {};
    const scope = { sessionKey: this.key };
    for (const field of ["sessionId", "agentId", "lifecycleRevision", "activeLeafEntryId"]) {
      const value = this.info[field] ?? history[field];
      if (value !== undefined) scope[field] = value;
    }
    this.projection = reconcileSessionProjectionSnapshot(this.projection, history.messages ?? [], scope);
    if (this.errorType === "uncertain" && this.projection.entries.every(entry => !entry.pending)) {
      this.error = "";
      this.errorType = "";
    }
    this.activeRunIds = new Set(this.info.activeRunIds ?? []);
    if (history.inFlightRun?.runId) this.activeRunIds.add(history.inFlightRun.runId);
    this.activeUnknown = this.info.hasActiveRun === true && this.activeRunIds.size === 0;
    if (history.inFlightRun?.message) {
      this.chat({ ...history.inFlightRun, state: "delta", sessionKey: this.key });
    }
    if (!this.activeRunIds.size && !this.activeUnknown) this.activity = null;
  }

  pending(message, idempotencyKey) {
    this.projection = reduceSessionProjection(this.projection, {
      type: "sendPending", sessionKey: this.key, idempotencyKey,
      message: { role: "user", content: [{ type: "text", text: message }], timestamp: Date.now() },
    });
    this.error = "";
    this.errorType = "";
  }

  acknowledged(ack, idempotencyKey) {
    this.projection = reduceSessionProjection(this.projection, {
      type: "sendAcknowledged", sessionKey: this.key, runId: ack.runId, idempotencyKey,
    });
    // Steering/followup admissions can have a send id distinct from execution.
    // Only exact run facts from history/events define the active set.
  }

  failed(idempotencyKey, error) {
    this.projection = reduceSessionProjection(this.projection, {
      type: "sendFailed", sessionKey: this.key, runId: idempotencyKey,
    });
    this.error = error;
    this.errorType = "send";
  }

  persisted(payload) {
    this.projection = reduceSessionProjection(this.projection, {
      type: "messagePersisted", sessionKey: this.key,
      message: payload.message, envelope: payload,
    });
  }

  chat(payload) {
    if (!payload.runId) return;
    let event = payload;
    // Protocol v4 may omit snapshots for ordinary text appends. Build one
    // for the SDK reducer, retaining all non-text blocks and replacements.
    if (payload.state === "delta" && !payload.message && typeof payload.deltaText === "string") {
      const previous = this.projection.runs[payload.runId]?.message;
      const text = payload.replace ? payload.deltaText : textOf(previous) + payload.deltaText;
      const blocks = (Array.isArray(previous?.content) ? previous.content : []).filter(part => part.type !== "text");
      event = { ...payload, message: { ...previous, role: "assistant", content: [...blocks, { type: "text", text }] } };
    }
    const transition = reduceSessionProjectionRunEvent(this.projection, event, { sessionKey: this.key });
    if (transition) this.projection = transition.projection;
    const run = this.projection.runs[payload.runId];
    if (["status", "delta"].includes(payload.state) && (!run || run.status === "streaming")) this.activeRunIds.add(payload.runId);
    if (["final", "aborted", "error"].includes(payload.state)) {
      this.activeRunIds.delete(payload.runId);
      if (this.activity?.runId === payload.runId) this.activity = null;
      if (payload.state === "error") {
        this.error = payload.errorMessage || "本轮执行失败，请查看原生界面。";
        this.errorType = "run";
      }
    }
  }

  agent(payload) {
    if (payload.stream === "lifecycle" && payload.data?.phase === "start") this.activeRunIds.add(payload.runId);
    if (payload.stream === "tool" && this.activeRunIds.has(payload.runId)) {
      this.activity = { runId: payload.runId, ...payload.data };
    }
  }

  snapshot() {
    // The SDK keeps chat run output separately from durable transcript rows.
    // Project each current run once for display; the official identity rules
    // prefer its persisted row when the history/event receipt arrives.
    let display = this.projection;
    for (const run of Object.values(this.projection.runs)) {
      if (run.message) display = projectLiveSessionMessage(display, run.message, { runId: run.runId }, { sessionKey: this.key });
    }
    return {
      key: this.key, info: this.info,
      activeRunIds: [...this.activeRunIds], activeUnknown: this.activeUnknown,
      activity: this.activity, error: this.error, errorType: this.errorType,
      entries: display.entries.map(entry => ({
        message: entry.message, pending: entry.pending, live: entry.live,
        identity: entry.identity ?? readSessionMessageIdentity(entry.message),
      })),
    };
  }
}
