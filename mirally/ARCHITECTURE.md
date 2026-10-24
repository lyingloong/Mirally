# Mirally 运行结构

当前默认入口 `python -m mirally` → `framework_host.py` → 同仓 `kernel/dsh/packages/mirally/mirally-product/start.mjs`。新主链不依赖 OpenClaw；显式 `--openclaw` 保留旧维护路线。

- `framework/`：产品业务 owner 与 SQLite，持久设置、会话代次引用、命令和来源回执、偏好、提醒、主动候选及收件箱；不复制原生 transcript。
- `framework/context.py`：按需只读组合现有 Activity 与 Memory owner，保留来源版本和缺口。
- `framework_workers.py`：有开关和预算的采集、维护和语义任务生命周期；完成请求经同一原生模型桥。
- `frontend/control-ui/`：固定 2026.9.6 原前端源码，保留 Mirally 页面与皮肤；`ui/src/app/mirally-client.ts` 适配本机 HTTP/SSE。`framework_ui/legacy-native-bridge.js` 承接旧原生消息；页面没有独立模型循环。
- `kernel/dsh/packages/mirally/mirally-product/`：本机鉴权 Host、DSH Session/Agent loop/LLM 适配、取消与恢复。
- `kernel/dsh/packages/mirally/mirally-extensions/`：Skill、脚本、MCP 与原生可信插件的版本、授权和生命周期组合。
- 既有 `core/activity`、`core/memory`、`runtime/activity`、`runtime/memory` 与平台适配继续负责原有领域数据。`runtime/ufo` 与只读 UFO 内核保留为维护参考，新默认框架本阶段不接入。

接口、数据路径、迁移和实际验收见 [框架第一版](../docs/framework-v1.md)。下面保留旧模块的细节参考，其中 OpenClaw 专用宿主仅由维护入口使用。

# Mirally product layout

This describes the current Python/OpenClaw baseline. The refactor target and
stage gates live in [the framework plan](../docs/framework-refactor-plan.md).
The editable DSH source in `../kernel/dsh/` now powers the default launcher.
The following OpenClaw-specific details describe only the explicit maintenance route.

The product package is split by responsibility:

- `core/` owns conversation state, intent handoff, and task lifecycle. It does
  not import Tk or Windows APIs.
- `core/memory/` owns durable, versioned memories, source tombstones, indexed
  recall, leased synthesis jobs and persistent usage budgets. Its database is
  independent of rebuildable Activity projections.
- `core/activity/` owns the product-neutral observation protocol, privacy
  decisions, single-writer SQLite store, asset lifecycle, and consumer cursor
  contract. It does not import UFO or Windows APIs.
  Its `knowledge/` package projects rebuildable Items, Episodes, search context
  and memory candidates from the original observations.
  Projection v2 preserves complete captured source evidence and emits observed actions,
  objects, changes, references and uncertainty. Each observation is committed
  atomically; projection failures remain visible in a bounded retry queue.
  The deterministic `activity.event.v1` view joins observed actions, window
  states and text within existing Episodes, preserving partial fragments,
  evidence references and uncertainty without inferring completed user tasks.
- `runtime/` owns product configuration, speech providers, and the adapter that
  starts a UFO native `Session`.
- `runtime/activity/` owns the activity sensor lifecycle, CLI and
  agent-control lease bridge. Desktop capture defaults on and respects an
  explicit disabled profile.
  The organizer consumes the source database read-only; its context source and
  stdio MCP adapter serve shared OpenClaw activity tools without starting the
  collector. MCP discovery does not access Activity configuration or SQLite;
  the query bridge opens lazily on a tool call and closes with its server.
  Direct native UFO loops do not consume Activity knowledge.
- `runtime/memory/` owns continuously running memory maintenance, read-only
  recall, CLI and portable Memory MCP. The additive OpenClaw memory plugin
  supervises its worker, performs optional semantic completion through the
  host-managed model route, and recalls relevant committed memories before
  the default local agent's prompt. It preserves the native memory slot and
  never starts Activity collection. See [Activity memory](../docs/activity-memory.md).
- `platform/` owns Windows-only capture isolation, raw input, focus, and taskbar
  integration.
- `platform/activity/` owns read-only foreground identity/window titles,
  bounded browser address-bar extraction in an isolated UIA worker,
  input metadata aggregation, the isolated Raw Input helper, and bounded UIA structure
  inspection. WGC/content workers remain isolated platform adapters and do not
  belong in the core store.
- `assets/` owns the narrow presentation additions for the native Control UI,
  including its companion, Activity settings and model settings.

The only product frontend is `control_ui_shell.py`, hosting the official
OpenClaw Control UI. `openclaw_host.py` manages its native process connection.
The former Tk UFO workbench and experimental Tk OpenClaw client have been
removed together with their compatibility imports and launch modes.
New UFO product code belongs in the responsibility packages above.

The activity data layer is independent from the OpenClaw session database and
from UFO task callbacks. `ObservationStore` assigns durable `ingest_seq`
cursors, publishes asset files only after an atomic commit, and represents
privacy denials, gaps, stale evidence, and expired assets explicitly.
When enabled, the current implementation starts the foreground
poller, an isolated Raw Input metadata helper, and a UIA worker with a deadline. The
content layer adds explicit window/all-monitor WGC, a process-isolated
Windows.Media.Ocr worker, UIA Name/Value/Text, committed-text deltas and
sequence-checked clipboard reads. Capture defaults on and starts after the
desktop host connects; the settings switch persists an explicit opt-out.
Closing the desktop ends its capture without changing that preference. Config
reads and MCP discovery remain passive. WinEvent/WTS lifecycle events are not
yet wired.
The enabled sensor treats foreground metadata, input metadata, and UIA
structure as one baseline contract and records a degraded collector state when
one platform source is unavailable.
Persistent `collector_diagnostics` records effective channels, source health,
safe counters and pause intervals without captured content. Monitor shutdown
waits for its workers, closes the sensor and drains remaining observations
through a read-only source before closing derived storage. Failed worker joins
retain the stores and process lock so shutdown can be retried safely.
Authorized text and structures are stored without character, node or byte
cropping. UIA declares its privacy/visibility scope and reports read failures;
consumer summaries and queries apply their own budgets to derived views.

`runtime/activity/scheduler.py` coalesces foreground, periodic, typing-pause,
click, scroll and clipboard triggers in a bounded queue. One evidence thread
owns expensive sources; the foreground poller and Raw Input message pump never
wait for COM, graphics or clipboard delayed rendering. Pause generations and
pre/post identity checks discard stale results. Monitor frames use explicit
monitor IDs and do not claim the foreground application owns the whole image.
Image SHA-256 gates OCR; quota eviction uses store tombstones. See
[rich capture](../docs/activity-rich-capture.md) for coverage limits.

`UFO/` is a Git submodule pointing at the Mirally UFO fork. Product-owned
configuration is kept under `config/mirally/ufo/`; `runtime.config` configures
the upstream loader with that directory before UFO modules are imported.

For the complete architecture and source-level call chains, see:

- [UFO repository architecture](../docs/ufo-architecture.md): sessions, agents,
  processors, MCP tools, automation, AIP, Galaxy, and supporting subsystems.
- [Mirally integration](../docs/ufo-integration.md): startup, configuration,
  callbacks, runtime compatibility, tool routing, and current integration gaps.
- [Native runtime plan](../docs/native-runtime-plan.md): proposed execution
  contract, control boundaries, code organization, and native-path acceptance
  tests. Some planning sections describe earlier milestones.
- [UFO as an OpenClaw agent](../docs/ufo-openclaw-agent.md): the current Gateway
  harness, separate worker, selected model, configuration and verification limits.

The desktop task route is `Mirally main agent -> sessions_spawn(agentId="ufo")
-> OpenClaw Gateway -> mirally-ufo Agent Harness -> runtime/ufo/worker.py ->
UfoExecutor -> normal Session`. The harness only admits native subagent session
keys; ordinary UFO conversations are rejected before starting a worker. Its
internal execution definition is hidden from direct chat choices and remains
available to host configuration and task history. A desktop subtask skips
conversation intent classification and starts the executor.
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
1.5-second cancellation grace period if cleanup does not finish. Cancellation
cannot undo an OS action that has already been dispatched.
