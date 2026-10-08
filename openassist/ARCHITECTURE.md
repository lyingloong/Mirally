# OpenAssist product layout

The product package is split by responsibility:

- `core/` owns conversation state, intent handoff, and task lifecycle. It does
  not import Tk or Windows APIs.
- `core/activity/` owns the product-neutral observation protocol, privacy
  decisions, single-writer SQLite store, asset lifecycle, and consumer cursor
  contract. It does not import UFO or Windows APIs.
- `runtime/` owns product configuration, speech providers, and the adapter that
  starts a UFO native `Session`.
- `runtime/activity/` owns the optional activity sensor lifecycle, CLI and
  agent-control lease bridge. It is disabled unless the user enables its own
  activity configuration.
- `platform/` owns Windows-only capture isolation, raw input, focus, and taskbar
  integration.
- `platform/activity/` owns read-only foreground identity/window titles,
  bounded browser address-bar extraction in an isolated UIA worker,
  input metadata aggregation, the isolated Raw Input helper, and bounded UIA structure
  inspection. WGC/content workers remain isolated platform adapters and do not
  belong in the core store.
- `ui/` owns Mirally's Tk workbench, edge companion, themes, and presentation.

The default frontend is feat's `control_ui_shell.py`, hosting the official
OpenClaw Control UI. `openclaw_host.py` manages its native process connection.
The Tk modules at the package root remain compatibility imports; native host
and experimental OpenClaw client modules retain their existing locations.
New UFO product code belongs in the responsibility packages above.

The activity data layer is independent from the OpenClaw session database and
from UFO task callbacks. `ObservationStore` assigns durable `ingest_seq`
cursors, publishes asset files only after an atomic commit, and represents
privacy denials, gaps, stale evidence, and expired assets explicitly.
When explicitly enabled, the current implementation starts the foreground
poller, an isolated Raw Input metadata helper, and a bounded UIA worker. The
content layer adds explicit window/all-monitor WGC, a process-isolated
Windows.Media.Ocr worker, UIA Name/Value/Text, committed-text deltas and
sequence-checked clipboard reads. The sensor remains disabled by default;
the enabled template and isolated monitor test use rich content defaults. WinEvent/WTS
lifecycle events and automatic host startup are not yet wired.
The enabled sensor treats foreground metadata, input metadata, and UIA
structure as one baseline contract and records a degraded collector state when
one platform source is unavailable.

`runtime/activity/scheduler.py` coalesces foreground, periodic, typing-pause,
click, scroll and clipboard triggers in a bounded queue. One evidence thread
owns expensive sources; the foreground poller and Raw Input message pump never
wait for COM, graphics or clipboard delayed rendering. Pause generations and
pre/post identity checks discard stale results. Monitor frames use explicit
monitor IDs and do not claim the foreground application owns the whole image.
Image SHA-256 gates OCR; quota eviction uses store tombstones. See
[rich capture](../docs/activity-rich-capture.md) for coverage limits.

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
