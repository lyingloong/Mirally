# Mirally product host

This private workspace is both the Mirally composition bundle and its product service. It runs the vendored native Agent, Session, loop, provider adapters and JSONL persistence, serves the product UI over authenticated loopback HTTP/SSE, and supervises the Python domain process. The Python service owns product state; native Session logs own conversation history.

The bundle mounts no general shell or unrestricted filesystem tools. Product queries, explicit reminders, preferences, Skills and configured MCP/script extensions use their capability owners. Script confirmation is bound to an authenticated product command rather than model arguments.

The root product launcher supplies repository-relative runtime locations and an isolated home. Normal mode requires user model configuration. Explicit `testMode` mounts a deterministic offline adapter and reports that mode in status and runtime metadata.

Model calls use the existing pi-ai adapter for OpenAI-compatible chat, Responses and Anthropic Messages. Auxiliary calls support text and inline images through the native attachment store. Provider keys remain in the Python private settings and per-configuration adapter references, never the browser response or native log.

The model settings page offers an explicitly selected DeepSeek preset, editable context capacity, image input and reasoning effort. An empty stored `reasoning_effort` preserves the previous boolean policy; an explicit `default` leaves effort to the service, while `off` requests disabled reasoning when supported by the protocol. No user-specific service, key or model is a product default.

Named connections are product settings: `model_connections` maps stable ids to `{name, model}`, and `model_routes` assigns `chat` and `background`. The implicit `default` connection reads the original `settings.model` directly, preserving old clients and credentials without a migration copy. `model.connections.list`, `model.connection.save`, `model.connection.remove` and `model.routes.set` validate changes through the Python owner. Each id retains only its own key; switching an existing connection's origin requires a new key or explicit clearing. Assigned connections cannot be deleted. Public settings redact every connection's key.

One PiAI adapter serves all configured connections, with an immutable credential reference per model generation. Main Agent requests use `chat`; semantic memory jobs and initiative use `background` through the existing auxiliary bridge. Updating routes affects subsequent calls while a prepared call retains its original profile and key. `resolveProductConnection(settings, {purpose, connectionId})` is the internal lookup for other capability owners; missing explicit ids fail rather than falling back to another service. New image submissions to a connection without declared image input are refused before admission or network dispatch, instead of silently projecting them to text.

Authenticated `POST /api/models/discover` reads only the saved endpoint's model listing, retaining advertised input modalities, context/output limits and effort metadata. It does not change settings or certify those capabilities. It refuses redirects, bounds the reply to 2 MiB and 1,000 rows, and times out after 20 seconds. `POST /api/models/test` sends one fixed text prompt through the same product LLM chain, with a 512-token output cap and 90-second timeout. It returns the text, elapsed time and test/normal mode without creating a conversation. A successful text probe does not certify vision, tools or every reasoning level. Both requests participate in Host shutdown and cancel when the caller disconnects.

Both probe requests accept an optional `connection_id`; an empty body continues to probe `default`. The UI edits one saved connection at a time and discards stale advisory results on save or refresh. The root `tools/verify_framework_model_connections.py` exercises real Host routing, isolated provider credentials, legacy probes and pre-network image refusal without external API calls.

Restart recovery reads native history and marks interrupted commands unknown instead of replaying pending effects. Cancel admission is durable in the product service before native cancellation is requested. Physical actions remain subject to the executor's cancellation and outcome guarantees.

## Known limitations

The default delivery channel is the durable local inbox while the host is running. Image payloads and requests have explicit size limits. Session history is derived on read; missing native generations report gaps. Browser cookie handoff lasts for the current host process. Real provider, desktop and device-sleep behavior require separate authorized acceptance runs.
