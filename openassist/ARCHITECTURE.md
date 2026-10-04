# OpenAssist product layout

The product package is split by responsibility:

- `core/` owns conversation state, intent handoff, and task lifecycle. It does
  not import Tk or Windows APIs.
- `runtime/` owns product configuration, speech providers, and the adapter that
  starts a UFO native `Session`.
- `platform/` owns Windows-only capture isolation, raw input, focus, and taskbar
  integration.
- `ui/` owns Mirally's Tk workbench, edge companion, themes, and presentation.

The modules that remain directly under `openassist/` are compatibility imports
for callers that still use the original flat paths. New product code should
import from one of the four responsibility packages above.

`UFO/` is a Git submodule pointing at the OpenAssist UFO fork. Product-owned
configuration is kept under `config/openassist/ufo/`; `runtime.config` configures
the upstream loader with that directory before UFO modules are imported.

For the complete architecture and source-level call chains, see:

- [UFO repository architecture](../docs/ufo-architecture.md): sessions, agents,
  processors, MCP tools, automation, AIP, Galaxy, and supporting subsystems.
- [OpenAssist integration](../docs/ufo-integration.md): startup, configuration,
  callbacks, runtime compatibility, tool routing, and current integration gaps.
- [Native runtime plan](../docs/native-runtime-plan.md): proposed execution
  contract, control boundaries, code organization, and native-path acceptance
  tests. This is a plan, not the current layout; OpenClaw integration is deferred.

The default desktop route is `TaskController -> Executor.run ->
UfoExecutor -> SingleRequestSession(mode="normal")`. Conversation routing first
uses the injected completion service (the UFO `APP_AGENT` provider) without
starting desktop execution. Each desktop request creates a new native session.

`runtime/ufo/compat.py` applies scoped in-memory fixes for Galaxy imports,
Windows MCP discovery, and UIA cache failures. `runtime/ufo/controls.py` adds
the action admission gate, model/tool budgets, context injection and structured
events without editing the submodule. `runtime/ufo/observation.py` filters
Mirally's process from screenshots, windows and controls. All scopes restore
their patched functions and window state after cleanup.

The adapter exposes actual capability boundaries: pause and supplement are
available at safe boundaries, cancellation is cooperative, and an in-flight
third-party call is allowed to settle before resources are restored. The
interface can therefore show “正在停止” without claiming that arbitrary native
threads were force-killed.
