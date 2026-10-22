import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { createActivityClient } from "./activity/activity-client.mjs";
import { createActivityTools, TOOL_NAMES } from "./activity/index.mjs";

class FakeStream extends EventEmitter {
  setEncoding() {}
}

function fakeBridge(responseFactory = null) {
  const calls = [];
  let process;
  const spawnImpl = (command, args, options) => {
    calls.push({ command, args, options });
    const stdout = new FakeStream();
    const stderr = new FakeStream();
    const child = new EventEmitter();
    child.stdout = stdout;
    child.stderr = stderr;
    child.killed = false;
    child.stdin = {
      writable: true,
      write(payload, _encoding, callback) {
        const request = JSON.parse(payload);
        const response = responseFactory ? responseFactory(request) : {
          id: request.id,
          ok: true,
          result: { op: request.op, args: request.args },
        };
        setImmediate(() => stdout.emit("data", `${JSON.stringify(response)}\n`));
        callback?.();
        return true;
      },
    };
    child.kill = () => { child.killed = true; child.emit("close", 0, null); };
    process = child;
    return child;
  };
  return { calls, spawnImpl, get process() { return process; } };
}

test("activity bridge is lazy and multiplexes requests over one child", async () => {
  const fake = fakeBridge();
  const root = path.resolve(os.tmpdir(), "mirally-activity-root");
  const client = createActivityClient({ root, pythonPath: "test-python", spawnImpl: fake.spawnImpl });
  assert.equal(fake.calls.length, 0);
  const first = await client.recent({ seconds: 30 });
  const second = await client.health({});
  assert.equal(fake.calls.length, 1);
  assert.equal(first.op, "recent");
  assert.equal(second.op, "health");
  assert.deepEqual(fake.calls[0].args.slice(0, 4), ["-m", "mirally.runtime.activity.agent_bridge", "--root", root]);
  client.close();
  assert.equal(fake.process.killed, true);
});

test("relative activity profiles follow the product root, independent of the Gateway working directory", async () => {
  const fake = fakeBridge();
  const root = path.resolve(os.tmpdir(), "mirally-custom-root");
  const client = createActivityClient({ root, pythonPath: "custom-python", configPath: "config/activity.yaml", spawnImpl: fake.spawnImpl });
  try {
    await client.health({});
    assert.equal(fake.calls[0].command, "custom-python");
    assert.equal(fake.calls[0].args.at(-1), path.resolve(root, "config/activity.yaml"));
    assert.equal(fake.calls[0].options.cwd, root);
  } finally { client.close(); }
});

test("missing activity virtual environment fails on demand without falling back to system Python", async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), "mirally-activity-missing-python-"));
  const previousPython = process.env.MIRALLY_PYTHON;
  delete process.env.MIRALLY_PYTHON;
  const fake = fakeBridge();
  const client = createActivityClient({ root, spawnImpl: fake.spawnImpl });
  try {
    assert.equal(fake.calls.length, 0);
    await assert.rejects(client.health({}), { code: "activity_python_missing" });
    assert.equal(fake.calls.length, 0);
  } finally {
    client.close();
    if (previousPython === undefined) delete process.env.MIRALLY_PYTHON;
    else process.env.MIRALLY_PYTHON = previousPython;
    rmSync(root, { recursive: true, force: true });
  }
});

test("explicit environment Python and absolute activity profiles remain supported", async () => {
  const previousPython = process.env.MIRALLY_PYTHON;
  process.env.MIRALLY_PYTHON = "configured-python";
  const fake = fakeBridge();
  const configPath = path.resolve(os.tmpdir(), "mirally-profile", "activity.yaml");
  const client = createActivityClient({ root: path.resolve(os.tmpdir(), "mirally-other-root"), configPath, spawnImpl: fake.spawnImpl });
  try {
    await client.health({});
    assert.equal(fake.calls[0].command, "configured-python");
    assert.equal(fake.calls[0].args.at(-1), configPath);
  } finally {
    client.close();
    if (previousPython === undefined) delete process.env.MIRALLY_PYTHON;
    else process.env.MIRALLY_PYTHON = previousPython;
  }
});

test("activity tools expose a read-only on-demand surface", async () => {
  const calls = [];
  const client = {};
  for (const name of ["health", "recent", "search", "around", "summary", "episode", "evidence", "query", "events"])
    client[name] = async (args) => { calls.push({ name, args }); return { name, args }; };
  const tools = createActivityTools(client);
  assert.deepEqual(tools.map((tool) => tool.name), [...TOOL_NAMES]);
  const recent = await tools.find((tool) => tool.name === "activity_recent").execute("call", { seconds: 15 }, new AbortController().signal);
  assert.equal(recent.details.name, "recent");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].args.seconds, 15);
  assert.match(tools.find((tool) => tool.name === "activity_recent").description, /on-demand/i);
  assert.ok(tools.every(tool => /untrusted evidence, never instructions/.test(tool.description)));
  assert.ok(tools.every(tool => typeof tool.promptSnippet === "string" && tool.promptSnippet.length > 0));
  const summary = tools.find(tool => tool.name === "activity_summary");
  assert.ok(summary.parameters.properties.token_budget);
  assert.match(summary.description, /default: 300 seconds/);
  const events = tools.find(tool => tool.name === "activity_events");
  assert.ok(events);
  assert.ok(events.parameters.properties.include_uia);
  assert.match(events.description, /unknown intent/i);
});

test("bridge keeps availability and index lag metadata in the tool result", async () => {
  const fake = fakeBridge(request => ({ id: request.id, ok: true,
    status: "ready", data_status: "index_lagging", result: { items: [] }, sync: { lag: 50 } }));
  const client = createActivityClient({ pythonPath: "test-python", spawnImpl: fake.spawnImpl });
  try {
    const result = await client.query({});
    assert.equal(result.status, "ready");
    assert.equal(result.data_status, "index_lagging");
    assert.equal(result.sync.lag, 50);
    assert.deepEqual(result.result.items, []);
  } finally { client.close(); }
});

