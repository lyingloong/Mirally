import {appendFileSync, mkdirSync} from "node:fs";
import path from "node:path";

// This separate stream is safe to inspect while native logs contain desktop
// data. Never copy prompts, summaries, arguments, exceptions or model routes.
const TOKEN = /^[A-Za-z0-9_.:-]{1,100}$/;
const tokens = ["phase", "stage", "agent", "action", "error_type", "reason", "status", "operation"];
const numbers = ["attempt", "step", "elapsed_seconds", "remaining_seconds", "exit_code", "pid"];
const flags = ["timed_out", "stopping", "in_flight", "will_retry"];

export function diagnosticMetadata(event = {}) {
  const result = {};
  for (const key of tokens)
    if (typeof event[key] === "string" && TOKEN.test(event[key])) result[key] = event[key];
  for (const key of numbers)
    if (Number.isFinite(event[key])) result[key] = event[key];
  for (const key of flags)
    if (typeof event[key] === "boolean") result[key] = event[key];
  if (Array.isArray(event.stack)) {
    result.stack = event.stack.slice(-12).flatMap(frame => {
      if (frame && TOKEN.test(frame.file) &&
          (TOKEN.test(frame.function) || ["<module>", "<lambda>"].includes(frame.function)) && Number.isInteger(frame.line))
        return [{file: frame.file, function: frame.function, line: frame.line}];
      return [];
    });
  }
  return result;
}

export function createRunDiagnostics(root, runId, warn = () => {}) {
  if (typeof runId !== "string" || !/^[A-Za-z0-9_-]{1,100}$/.test(runId))
    throw new Error("Invalid UFO run identifier");
  const directory = path.join(root, ".artifacts", "ufo-runs");
  const file = path.join(directory, runId + ".jsonl");
  const started = Date.now();
  let bytes = 0, disabled = false;
  return (kind, event = {}) => {
    if (disabled || bytes >= 512_000) return;
    const record = {timestamp: new Date().toISOString(), run_id: runId,
      elapsed_seconds: Math.round((Date.now() - started) / 100) / 10,
      type: TOKEN.test(kind) ? kind : "event", ...diagnosticMetadata(event)};
    // elapsed_seconds from native wait events refers to that stage; retain the
    // full run clock separately so callers can distinguish the two.
    record.run_elapsed_seconds = Math.round((Date.now() - started) / 100) / 10;
    try {
      mkdirSync(directory, {recursive: true});
      const line = JSON.stringify(record) + "\n";
      appendFileSync(file, line, {encoding: "utf8"});
      bytes += Buffer.byteLength(line);
    } catch {
      disabled = true;
      warn("UFO diagnostic log unavailable; execution continues without it.");
    }
  };
}
