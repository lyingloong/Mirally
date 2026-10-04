/** Read-only live check of the actual desktop device and bridge. No model call. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { DesktopBridge } from "./desktop-bridge.mjs";

if (!process.argv.includes("--live")) {
  console.error("Use --live to check the configured local Gateway.");
  process.exit(2);
}
const root = fileURLToPath(new URL("../../", import.meta.url));
const config = JSON.parse(readFileSync(resolve(root, "config/openassist/openclaw/openclaw.json"), "utf8"));
const events = [];
const bridge = new DesktopBridge(config, event => events.push(event));
async function waitFor(predicate) {
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise(accept => setTimeout(accept, 100));
  }
  throw new Error("Desktop bridge check timed out. Check local Gateway/device pairing.");
}
try {
  bridge.start();
  await waitFor(() => events.some(event => event.type === "sessions"));
  const hello = events.find(event => event.type === "connection" && event.connected);
  if (!hello.scopes.includes("operator.read") || !hello.scopes.includes("operator.write")) throw new Error("Native desktop scopes not granted.");
  const key = bridge.sessions[0]?.key;
  if (!key) throw new Error("No native session available for the read-only check.");
  await bridge.command({ op: "select", params: { key } });
  await waitFor(() => events.some(event => event.type === "session" && event.key === key));
  const snapshot = events.findLast(event => event.type === "session" && event.key === key);
  console.log(JSON.stringify({ deviceConnected: true, scopes: hello.scopes, nativeSessionRows: bridge.sessions.length,
    historyLoaded: Array.isArray(snapshot.entries), historyEntries: snapshot.entries.length,
    interactionMethodsUnavailable: events.findLast(event => event.type === "interactions")?.unavailable ?? [] }));
} finally { await bridge.stop(); }
