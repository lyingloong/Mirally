import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";

// A synthetic provider fixture keeps this compatibility regression independent
// of the public template's provider/model placeholders. No API is called.
const model = {
  id: "qwen/qwen3.8-flash", name: "Synthetic Qwen compatibility fixture",
  input: ["text", "image"], contextWindow: 200000, maxTokens: 32768,
  provider: "mirally", api: "openai-completions", baseUrl: "https://synthetic-provider.example/v1",
  reasoning: true,
  compat: {
    thinkingFormat: "openai", supportsReasoningEffort: true,
    supportedReasoningEfforts: ["none", "low", "medium", "xhigh"],
    reasoningEffortMap: { minimal: "low", high: "xhigh" },
  },
};
const context = { messages: [{ role: "user", content: "Hello", timestamp: 0 }] };
const dist = new URL("node_modules/@openclaw/ai/dist/", import.meta.url);
function nativeChunk(prefix) {
  const matches = readdirSync(dist).filter(name => name.startsWith(prefix) && name.endsWith(".mjs"));
  assert.equal(matches.length, 1, `Expected one pinned native ${prefix} chunk`);
  return new URL(matches[0], dist);
}
// Internal export aliases belong to the pinned OpenClaw 2026.9.6 build.
const { i: buildRequest } = await import(nativeChunk("openai-completions-stream-"));
const { s: getCompat } = await import(nativeChunk("openai-transport-params-"));

for (const mode of ["direct", "managed"]) {
  test(`Qwen ${mode} payload respects off, low and high without reducing output budget`, () => {
    for (const [level, expected] of [["off", "none"], ["low", "low"], ["high", "xhigh"]]) {
      const options = { maxTokens: 16384, [mode === "direct" ? "reasoningEffort" : "reasoning"]: level };
      const policy = mode === "direct" ? { mode, compat: getCompat(model) } : { mode };
      const payload = buildRequest(model, context, options, policy);
      assert.equal(payload.reasoning_effort, expected, `${mode} ${level}`);
      assert.equal(payload.max_completion_tokens ?? payload.max_tokens, 16384);
      assert.equal(payload.model, model.id);
    }
  });
}

test("Qwen fixture keeps an image in the native provider request", () => {
  assert.ok(model.input.includes("image"));
  const imageContext = { messages: [{ role: "user", timestamp: 0, content: [
    { type: "text", text: "Describe this image." },
    { type: "image", mimeType: "image/png", data: "c3ludGhldGlj" },
  ] }] };
  for (const mode of ["direct", "managed"]) {
    const policy = mode === "direct" ? { mode, compat: getCompat(model) } : { mode };
    const payload = buildRequest(model, imageContext, {}, policy);
    assert.ok(payload.messages.some(message => Array.isArray(message.content) &&
      message.content.some(part => part.type === "image_url" &&
        part.image_url.url === "data:image/png;base64,c3ludGhldGlj")));
  }
});
