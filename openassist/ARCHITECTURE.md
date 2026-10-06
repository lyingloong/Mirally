# OpenAssist product layout

The product package is split by responsibility:

- `core/` owns conversation state, intent handoff, and task lifecycle. It does
  not import Tk or Windows APIs.
- `runtime/` owns product configuration, speech providers, and the adapter that
  starts a UFO native `Session`.
- `platform/` owns Windows-only capture isolation, raw input, focus, and taskbar
  integration.
- `ui/` owns Mirally's Tk workbench, edge companion, themes, and presentation.

The default frontend is feat's `control_ui_shell.py`, hosting the official
OpenClaw Control UI. `openclaw_host.py` manages its native process connection.
The Tk modules at the package root remain compatibility imports; native host
and experimental OpenClaw client modules retain their existing locations.
New UFO product code belongs in the responsibility packages above.

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
  tests. Some planning sections describe earlier milestones.
- [UFO as an OpenClaw agent](../docs/ufo-openclaw-agent.md): the current Gateway
  harness, separate worker, selected model, configuration and verification limits.

The selectable UFO route is `Control UI -> OpenClaw Gateway -> mirally-ufo
Agent Harness -> runtime/ufo/worker.py -> UfoExecutor -> normal Session`.
The default OpenClaw agent can use the same route as a desktop subagent via
`sessions_spawn(agentId="ufo")`. Both selecting UFO directly and spawning it as
a desktop subagent skip conversation intent classification and start the executor.
OpenClaw owns the durable session and model selection; the worker uses a private
temporary YAML snapshot and the host-prepared model route. Windows ownership
is serialized across workers by a native named mutex. The existing default
OpenClaw agent remains unchanged.

The worker's Windows stdio control channel uses `platform/stdio_pipe.py` to
check available pipe bytes before reading. An idle synchronous stdin read can
block NumPy/native DLL initialization on Windows, so startup verification keeps
the input pipe open and the control reader alive throughout the real imports.
Tests cover both `python.exe` and `pythonw.exe`, then deliver cancellation over
that same channel without calling a model or accessing the desktop.

The independent `--ufo` desktop route is `TaskController -> Executor.run ->
UfoExecutor -> SingleRequestSession(mode="normal")`. Conversation routing first
uses the injected completion service (the UFO `APP_AGENT` provider) without
starting desktop execution. Each desktop request creates a new native session.

`runtime/ufo/compat.py` applies scoped in-memory fixes for Galaxy imports,
Windows MCP discovery, and UIA cache failures. `runtime/ufo/controls.py` adds
the action admission gate, model/tool budgets, context injection and structured
events without editing the submodule. `runtime/ufo/observation.py` filters
Mirally's worker and registered frontend processes from screenshots, windows and controls. All scopes restore
their patched functions and window state after cleanup.

The adapter exposes actual capability boundaries: pause and supplement are
available at safe boundaries, cancellation is cooperative, and an in-flight
third-party call is allowed to settle before resources are restored. The
Gateway harness additionally terminates its own worker process tree after a
1.5-second cancellation grace period if cleanup does not finish. The independent
`--ufo` route retains cooperative cancellation; neither route can undo an OS
action that has already been dispatched.
