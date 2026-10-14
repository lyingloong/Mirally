import { spawn as defaultSpawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

export const memoryPackageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const MAX_LINE_BYTES = 4 * 1024 * 1024;
export function boundedInteger(value, fallback, maximum, minimum = 1) {
  return Number.isFinite(Number(value)) && Number(value) >= minimum
    ? Math.min(maximum, Math.floor(Number(value))) : fallback;
}
function failure(message, code) { return Object.assign(new Error(message), { code }); }
function pythonFor(root) {
  const candidates = process.platform === "win32"
    ? [path.join(root, ".venv", "Scripts", "pythonw.exe"), path.join(root, ".venv", "Scripts", "python.exe")]
    : [path.join(root, ".venv", "bin", "python")];
  const python = candidates.find(candidate => existsSync(candidate));
  if (!python) throw failure("Mirally memory requires the repository virtual environment", "memory_python_missing");
  return python;
}

/** One service-owned JSONL process. Reads never spawn or restart the worker. */
export function createMemoryClient(options = {}) {
  const root = path.resolve(options.root ?? memoryPackageRoot);
  const spawnImpl = options.spawnImpl ?? defaultSpawn;
  const timeoutMs = boundedInteger(options.queryTimeoutMs, 2500, 30000, 100);
  const logger = options.logger ?? (() => {});
  const pending = new Map();
  let child = null;
  let buffer = "";
  let stopping = false;
  let startPromise = null;
  let stopPromise = null;

  function rejectAll(error) {
    for (const entry of pending.values()) { clearTimeout(entry.timer); entry.cleanup(); entry.reject(error); }
    pending.clear();
  }
  function line(text) {
    if (!text.trim()) return;
    let value;
    try { value = JSON.parse(text); } catch { throw failure("Memory worker returned malformed JSON", "memory_protocol_error"); }
    const entry = pending.get(value.id);
    if (!entry) return; // A timed-out request may finish later without affecting the next request.
    pending.delete(value.id);
    clearTimeout(entry.timer);
    entry.cleanup();
    if (value.ok === false) entry.reject(failure(typeof value.error === "string" ? value.error : value.error?.message ?? "Memory request failed", value.error?.code ?? "memory_request_error"));
    else entry.resolve(value.result ?? null);
  }
  function killTree(target) {
    if (!target || target.exitCode != null || target.killed) return;
    if (process.platform === "win32" && target.pid) {
      const killer = spawnImpl("taskkill.exe", ["/PID", String(target.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
      killer.on?.("error", () => { try { target.kill(); } catch {} });
    } else { try { target.kill(); } catch {} }
  }
  function request(op, args = {}, requestOptions = {}) {
    const target = child;
    if (!target || !target.stdin?.writable || stopping && op !== "stop") return Promise.reject(failure("Memory service is unavailable", "memory_unavailable"));
    const signal = requestOptions.signal;
    if (signal?.aborted) return Promise.reject(failure("Memory request cancelled", "memory_cancelled"));
    const id = randomUUID();
    const limit = boundedInteger(requestOptions.timeoutMs, timeoutMs, 120000, 10);
    return new Promise((resolve, reject) => {
      const finishError = error => {
        if (!pending.has(id)) return;
        pending.delete(id); clearTimeout(timer); cleanup(); reject(error);
      };
      const cancel = () => finishError(failure("Memory request cancelled", "memory_cancelled"));
      const cleanup = () => signal?.removeEventListener("abort", cancel);
      const timer = setTimeout(() => finishError(failure("Memory request timed out", "memory_timeout")), limit);
      pending.set(id, { resolve, reject, timer, cleanup });
      signal?.addEventListener("abort", cancel, { once: true });
      try {
        target.stdin.write(`${JSON.stringify({ id, op, args })}\n`, "utf8", error => { if (error) finishError(failure(error.message, "memory_write_error")); });
      } catch (error) { finishError(failure(error?.message ?? String(error), "memory_write_error")); }
    });
  }
  async function stop() {
    if (stopPromise) return stopPromise;
    stopPromise = (async () => {
      stopping = true;
      const target = child;
      if (!target) return;
      try { await request("stop", {}, { timeoutMs: 1000 }); } catch {}
      target.stdin?.end?.();
      await new Promise(resolve => {
        if (child !== target) return resolve();
        const timer = setTimeout(() => { killTree(target); resolve(); }, 1500);
        target.once("close", () => { clearTimeout(timer); resolve(); });
      });
      if (child === target) child = null;
      buffer = "";
      rejectAll(failure("Memory service stopped", "memory_stopped"));
    })().finally(() => { stopping = false; stopPromise = null; });
    return stopPromise;
  }
  function start() {
    if (startPromise) return startPromise;
    startPromise = (async () => {
      if (stopping) await stopPromise;
      if (child) return request("health");
      if (!options.configPath) throw failure("Memory configPath is required", "memory_config_missing");
      const target = spawnImpl(options.pythonPath ?? pythonFor(root), ["-m", "openassist.runtime.memory.worker", "--root", root, "--config", path.resolve(root, options.configPath)], {
        cwd: root, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
      });
      child = target; buffer = "";
      target.stdout?.setEncoding?.("utf8"); target.stderr?.setEncoding?.("utf8");
      target.stdout?.on("data", chunk => {
        if (child !== target) return;
        try {
          buffer += chunk;
          if (Buffer.byteLength(buffer, "utf8") > MAX_LINE_BYTES) throw failure("Memory response exceeded size limit", "memory_protocol_error");
          let newline;
          while ((newline = buffer.indexOf("\n")) >= 0) {
            const text = buffer.slice(0, newline); buffer = buffer.slice(newline + 1); line(text);
          }
        } catch (error) { rejectAll(error); killTree(target); }
      });
      target.stderr?.on("data", chunk => logger("debug", `Memory worker: ${String(chunk).trim().slice(0, 1000)}`));
      target.once("error", error => { if (child === target) { child = null; rejectAll(failure(error.message, "memory_spawn_error")); } });
      target.once("close", code => {
        if (child !== target) return;
        child = null; buffer = ""; rejectAll(failure(`Memory worker exited (${code ?? "unknown"})`, "memory_worker_exit"));
        if (!stopping) options.onExit?.(code);
      });
      try { return await request("health", {}, { timeoutMs: 30000 }); }
      catch (error) { await stop(); throw error; }
    })().finally(() => { startPromise = null; });
    return startPromise;
  }
  return Object.freeze({ start, stop, request, get process() { return child; }, get pendingCount() { return pending.size; } });
}
