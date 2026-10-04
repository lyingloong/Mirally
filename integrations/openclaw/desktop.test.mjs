import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { verify, createPublicKey } from "node:crypto";
import { createDeviceHost } from "./device-store.mjs";
import { SessionView, textOf } from "./session-view.mjs";

test("device identity persists and signs the challenge payload", () => {
  const directory = mkdtempSync(join(tmpdir(), "mirally-device-test-"));
  try {
    const first = createDeviceHost(directory, "ws://127.0.0.1:18789");
    const second = createDeviceHost(directory, "ws://127.0.0.1:18789");
    assert.deepEqual(first.identity, second.identity);
    const signature = first.hostDeps.signDevicePayload(first.identity.privateKeyPem, "challenge-payload");
    assert.equal(verify(null, Buffer.from("challenge-payload"), createPublicKey(first.identity.publicKeyPem), Buffer.from(signature, "base64url")), true);
    const params = { deviceId: first.identity.deviceId, role: "operator" };
    first.hostDeps.storeDeviceAuthToken({ ...params, token: "token-one", scopes: ["operator.read"], expectedToken: null });
    second.hostDeps.storeDeviceAuthToken({ ...params, token: "stale-write", scopes: [], expectedToken: null });
    assert.equal(first.hostDeps.loadDeviceAuthToken(params).token, "token-one");
    second.hostDeps.clearDeviceAuthToken({ ...params, expectedToken: "stale-write" });
    assert.equal(first.hostDeps.loadDeviceAuthToken(params).token, "token-one");
    const other = createDeviceHost(directory, "ws://127.0.0.1:18790");
    assert.equal(other.hostDeps.loadDeviceAuthToken(params), null);
    const abort = new AbortController();
    abort.abort();
    assert.throws(() => first.hostDeps.storeDeviceAuthToken({ ...params, token: "no", scopes: [], signal: abort.signal }));
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test("chat snapshots include their delta and are never appended twice", () => {
  const view = new SessionView("agent:main:test");
  view.chat({ state: "delta", runId: "run", seq: 1, message: { role: "assistant", content: [{ type: "text", text: "Hello" }] }, deltaText: "Hello" });
  view.chat({ state: "delta", runId: "run", seq: 2, deltaText: " world" });
  assert.equal(textOf(view.projection.runs.run.message), "Hello world");
  view.chat({ state: "delta", runId: "run", seq: 3, deltaText: "", replace: true });
  assert.equal(textOf(view.projection.runs.run.message), "");
  view.chat({ state: "final", runId: "run", seq: 4, message: { role: "assistant", content: [{ type: "text", text: "Finished" }] } });
  assert.equal(view.activeRunIds.size, 0);
  assert.equal(view.snapshot().entries.filter(entry => textOf(entry.message) === "Finished").length, 1);
  view.history({ messages: [{ role: "assistant", content: [{ type: "text", text: "Finished" }],
    __openclaw: { id: "persisted-final", seq: 1, runId: "run", runTerminal: true } }], sessionInfo: { hasActiveRun: false, activeRunIds: [] } });
  assert.equal(view.snapshot().entries.filter(entry => textOf(entry.message) === "Finished").length, 1);
});

test("restored active run is stoppable by its exact identity; another run cannot retire it", () => {
  const view = new SessionView("agent:main:test");
  view.history({ messages: [], inFlightRun: { runId: "owned" }, sessionInfo: { hasActiveRun: true, activeRunIds: ["owned"] } });
  view.chat({ state: "final", runId: "unrelated", seq: 1 });
  assert.deepEqual([...view.activeRunIds], ["owned"]);
  view.agent({ stream: "tool", runId: "unrelated", data: { name: "exec" } });
  assert.equal(view.activity, null);
  view.agent({ stream: "tool", runId: "owned", data: { name: "read" } });
  assert.equal(view.activity.name, "read");
  view.chat({ state: "aborted", runId: "owned", seq: 2 });
  assert.equal(view.activeRunIds.size, 0);
  assert.equal(view.activity, null);
});

test("send admission is distinct from transcript persistence", () => {
  const view = new SessionView("agent:main:test");
  view.pending("补充信息", "send-one");
  view.acknowledged({ runId: "send-one", status: "steered" }, "send-one");
  assert.equal(view.projection.entries.some(entry => entry.pending), true);
  assert.equal(view.activeRunIds.size, 0);
  view.persisted({ messageId: "user-one", messageSeq: 1, idempotencyKey: "send-one", runId: "run-one",
    message: { role: "user", content: [{ type: "text", text: "补充信息" }], id: "user-one", idempotencyKey: "send-one" } });
  assert.equal(view.projection.entries.filter(entry => textOf(entry.message) === "补充信息").length, 1);
  assert.equal(view.projection.entries.every(entry => !entry.pending), true);
});

test("delayed delta cannot restart a completed run or duplicate its text", () => {
  const view = new SessionView("agent:main:test");
  view.chat({state:"delta",runId:"run",seq:1,deltaText:"old"});
  view.chat({state:"final",runId:"run",seq:3,message:{role:"assistant",content:[{type:"text",text:"final"}]}});
  view.chat({state:"delta",runId:"run",seq:2,deltaText:"stale"});
  assert.equal(view.activeRunIds.size,0);
  assert.equal(textOf(view.projection.runs.run.message),"final");
});

test("a native reset replaces the old session lifecycle and pending display", () => {
  const view = new SessionView("agent:main:test");
  view.history({messages:[{role:"user",content:"old"}],sessionInfo:{sessionId:"session",lifecycleRevision:1}});
  view.pending("old pending","old-send");
  view.history({messages:[],sessionInfo:{sessionId:"session",lifecycleRevision:2,hasActiveRun:false,activeRunIds:[]}});
  assert.equal(view.snapshot().entries.length,0);
});
