# Mirally product host

This private workspace is both the Mirally composition bundle and its product service. It runs the vendored native Agent, Session, loop, provider adapters and JSONL persistence, serves the product UI over authenticated loopback HTTP/SSE, and supervises the Python domain process. The Python service owns product state; native Session logs own conversation history.

The bundle mounts no general shell or unrestricted filesystem tools. Product queries, explicit reminders, preferences, Skills and configured MCP/script extensions use their capability owners. Script confirmation is bound to an authenticated product command rather than model arguments.

The root product launcher supplies repository-relative runtime locations and an isolated home. Normal mode requires user model configuration. Explicit `testMode` mounts a deterministic offline adapter and reports that mode in status and runtime metadata.

Model calls use the existing pi-ai adapter for OpenAI-compatible chat, Responses and Anthropic Messages. Auxiliary calls support text and inline images through the native attachment store. Provider keys remain in the Python private settings and per-configuration adapter references, never the browser response or native log.

Restart recovery reads native history and marks interrupted commands unknown instead of replaying pending effects. Cancel admission is durable in the product service before native cancellation is requested. Physical actions remain subject to the executor's cancellation and outcome guarantees.

## Known limitations

The default delivery channel is the durable local inbox while the host is running. Image payloads and requests have explicit size limits. Session history is derived on read; missing native generations report gaps. Browser cookie handoff lasts for the current host process. Real provider, desktop and device-sleep behavior require separate authorized acceptance runs.
