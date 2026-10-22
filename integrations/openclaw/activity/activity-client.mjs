import { spawn as defaultSpawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

const PACKAGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_LINE_BYTES = 8 * 1024 * 1024;

function asPositiveInteger(value, fallback, maximum) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return fallback;
  return Math.min(maximum, Math.floor(number));
}

function defaultPython(root) {
  const candidates = process.platform === "win32"
    ? [path.join(root, ".venv", "Scripts", "pythonw.exe"), path.join(root, ".venv", "Scripts", "python.exe")]
    : [path.join(root, ".venv", "bin", "python")];
  const python = candidates.find((candidate) => existsSync(candidate));
  if (!python) throw lineError("Mirally activity requires the repository virtual environment", "activity_python_missing");
  return python;
}

function lineError(message, code = "activity_bridge_error") {
  const error = new Error(message);
  error.code = code;
  return error;
}

/**
 * A lazy JSONL client for the product-owned activity knowledge bridge.
 *
 * The child is deliberately created by the first tool call rather than when
 * the Gateway loads the plugin. This keeps ordinary prompts free of activity
 * I/O and avoids creating a database when collection is disabled.
 */
export function createActivityClient(options = {}) {
  const root = path.resolve(options.root ?? PACKAGE_ROOT);
  const spawnImpl = options.spawnImpl ?? defaultSpawn;
  const logger = options.logger ?? (() => {});
  const timeoutMs = asPositiveInteger(options.timeoutMs, DEFAULT_TIMEOUT_MS, 120_000);
  const python = options.pythonPath ?? process.env.MIRALLY_PYTHON;
  const configPath = options.configPath ?? process.env.MIRALLY_ACTIVITY_CONFIG;
  const pending = new Map();
  let child = null;
  let receiveBuffer = "";
  let stopping = false;

  function settleAll(error) {
    for (const [id, entry] of pending) {
      clearTimeout(entry.timer);
      entry.cleanup?.();
      entry.reject(error);
      pending.delete(id);
    }
  }

  function stop(reason = "activity bridge stopped") {
    stopping = true;
    const error = lineError(reason, "activity_bridge_stopped");
    settleAll(error);
    if (child && !child.killed) {
      try { child.kill(); } catch { /* process may already have exited */ }
    }
    child = null;
    receiveBuffer = "";
    stopping = false;
  }

  function handleLine(line) {
    if (!line.trim()) return;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      logger("warn", "Activity bridge returned a malformed response line.");
      return;
    }
    const id = typeof message?.id === "string" ? message.id : "";
    const entry = pending.get(id);
    if (!entry) return;
    pending.delete(id);
    clearTimeout(entry.timer);
    entry.cleanup?.();
    if (message.ok === false) {
      const details = message.error;
      const text = typeof details === "string" ? details : details?.message ?? "Activity bridge request failed";
      entry.reject(lineError(text, details?.code ?? "activity_request_failed"));
      return;
    }
    if (typeof message.status === "string") {
      const { id: _id, ok: _ok, ...envelope } = message;
      entry.resolve(envelope);
    } else {
      entry.resolve(message.result ?? message.data ?? null);
    }
  }

  function attachProcess(process) {
    child = process;
    process.stdout?.setEncoding?.("utf8");
    process.stderr?.setEncoding?.("utf8");
    process.stdout?.on("data", (chunk) => {
      receiveBuffer += chunk;
      if (Buffer.byteLength(receiveBuffer, "utf8") > MAX_LINE_BYTES) {
        stop("Activity bridge response exceeded the size limit");
        return;
      }
      let newline;
      while ((newline = receiveBuffer.indexOf("\n")) >= 0) {
        const line = receiveBuffer.slice(0, newline).replace(/\r$/, "");
        receiveBuffer = receiveBuffer.slice(newline + 1);
        handleLine(line);
      }
    });
    process.stderr?.on("data", (chunk) => {
      const text = String(chunk).trim();
      if (text) logger("debug", `Activity bridge: ${text.slice(0, 1000)}`);
    });
    process.once("error", (error) => {
      const message = error?.message || "Unable to start activity bridge";
      settleAll(lineError(message, "activity_bridge_spawn"));
      child = null;
    });
    process.once("close", (code, signal) => {
      if (child !== process) return;
      child = null;
      if (!stopping && pending.size) {
        settleAll(lineError(`Activity bridge exited (${code ?? "unknown"}${signal ? `, ${signal}` : ""})`, "activity_bridge_exit"));
      }
    });
  }

  function ensureProcess() {
    if (child && !child.killed && child.stdin?.writable) return child;
    const args = ["-m", "mirally.runtime.activity.agent_bridge", "--root", root];
    if (configPath) args.push("--config", path.resolve(root, configPath));
    const executable = python ?? defaultPython(root);
    let process;
    try {
      process = spawnImpl(executable, args, {
        cwd: root,
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...globalThis.process?.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
      });
    } catch (error) {
      throw lineError(error?.message ?? "Unable to start activity bridge", "activity_bridge_spawn");
    }
    attachProcess(process);
    return process;
  }

  function request(op, args = {}, options = {}) {
    const signal = options.signal;
    if (signal?.aborted) return Promise.reject(lineError("Activity request was cancelled", "activity_request_cancelled"));
    const requestId = randomUUID();
    let process;
    try { process = ensureProcess(); } catch (error) { return Promise.reject(error); }
    const timeout = asPositiveInteger(options.timeoutMs, timeoutMs, 120_000);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const entry = pending.get(requestId);
        pending.delete(requestId);
        entry?.cleanup?.();
        stop("Activity bridge request timed out");
        reject(lineError("Activity bridge request timed out", "activity_request_timeout"));
      }, timeout);
      const cancel = () => {
        if (!pending.has(requestId)) return;
        pending.delete(requestId);
        clearTimeout(timer);
        cleanup();
        stop("Activity bridge request was cancelled");
        reject(lineError("Activity request was cancelled", "activity_request_cancelled"));
      };
      const cleanup = () => signal?.removeEventListener("abort", cancel);
      const entry = { resolve, reject, timer, cleanup };
      pending.set(requestId, entry);
      signal?.addEventListener("abort", cancel, { once: true });
      try {
        process.stdin.write(`${JSON.stringify({ id: requestId, op, args })}\n`, "utf8", (error) => {
          if (!error) return;
          if (!pending.has(requestId)) return;
          pending.delete(requestId);
          clearTimeout(timer);
          cleanup();
          reject(lineError(error.message, "activity_bridge_write"));
        });
      } catch (error) {
        pending.delete(requestId);
        clearTimeout(timer);
        cleanup();
        reject(lineError(error?.message ?? "Unable to send activity request", "activity_bridge_write"));
      }
    });
  }

  return Object.freeze({
    request,
    health: (args, options) => request("health", args, options),
    recent: (args, options) => request("recent", args, options),
    search: (args, options) => request("search", args, options),
    around: (args, options) => request("around", args, options),
    summary: (args, options) => request("summary", args, options),
    episode: (args, options) => request("episode", args, options),
    evidence: (args, options) => request("evidence", args, options),
    query: (args, options) => request("query", args, options),
    events: (args, options) => request("events", args, options),
    close: () => stop(),
    get process() { return child; },
  });
}

export const activityPackageRoot = PACKAGE_ROOT;

