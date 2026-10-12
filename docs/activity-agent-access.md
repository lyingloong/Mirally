# Activity Agent Access

The activity knowledge layer is a shared OpenClaw capability. It is not a
UFO-specific memory path and it is not injected into every prompt.

## Runtime path

```text
OpenClaw agent using its managed MCP registry
    -> local stdio `mirally-activity` MCP server
    -> bounded ActivityAgentBridge
    -> ActivityContextSource
    -> ActivityOrganizer / rebuildable knowledge SQLite
    -> ObservationStore (read-only source)
```

The MCP server is registered in `mcp.servers`. OpenClaw owns its discovery,
connections and process lifetime; catalog discovery can start the stdio child
before a tool is called. The child loads Activity configuration and opens its
query bridge only on the first Activity call. Tool discovery does not open
SQLite, read history or start the collector. A disabled profile or missing
source returns a structured `disabled` or `not_ready` result.

## Available tools

| Tool | Use |
| --- | --- |
| `activity_health` | Check collection and index availability |
| `activity_recent` | Retrieve a bounded recent timeline |
| `activity_search` | Search indexed titles, URLs, UI text and input metadata |
| `activity_around` | Inspect an explicit UTC time window |
| `activity_summary` | Summarize recent or explicitly dated applications and episodes |
| `activity_query` | Run a precise bounded query |
| `activity_events` | Return deterministic observed action/window/text fragments |
| `activity_episode` | Retrieve one episode by id |
| `activity_evidence` | Retrieve selected evidence references; assets are opt-in |

All tools are read-only with respect to raw observations and assets; querying
can update the rebuildable knowledge index. Results include status, source
Observation ids, evidence ids and bounded context fields. Context queries
default to `outline`; `activity_events` defaults to `evidence` to explain
observed actions. Historical UIA, OCR, screenshots and input metadata are evidence of
what was observed, not proof of the current desktop state. A desktop action
must take a fresh observation through the normal execution toolchain.

## Why there is no fixed prompt injection

Activity history is useful for questions such as "what was I doing earlier"
or "find the page I opened this morning", but it adds noise and cost to an
ordinary question. OpenClaw's compact tool directory hides MCP names and
descriptions until `tool_search` discovers them. The product-owned native
`mirally-activity` skill advertises the capability and matching questions,
including "你能看到我之前做了什么吗" and "能看到我使用电脑的activity吗".
It directs the model to discover Activity, check health, and retrieve the
requested period. Its description contains no historical user data.
The model calls the narrowest tool only when the current request needs
historical desktop context. This keeps the same policy for the default OpenClaw agent and future
MCP-capable harnesses. The native UFO loop is not an Activity consumer.

## Configuration

`tools/configure_openclaw.py` registers the product-owned stdio MCP server when
creating a native configuration. For an existing install, apply the official
targeted patch:

```powershell
.\.venv\Scripts\python.exe tools\configure_activity_agent.py
.\.venv\Scripts\python.exe tools\configure_activity_agent.py --apply
```

The first command validates the patch. The second registers the MCP server
and adds `integrations/openclaw/skills` to `skills.load.extraDirs`,
while preserving existing models, plugins and tool policy.
Activity's `mirally-activity__*` scope is added to the existing `tools.allow`
or `tools.alsoAllow` list without removing other grants. Explicit denials stay
effective; a policy that denies all Activity MCP tools is rejected.
The optional
`--legacy-plugin` flag keeps the older native plugin path for an OpenClaw
version without managed MCP; an existing Mirally native plugin entry is
disabled during migration so the two surfaces are not registered twice.
Activity collection stays separate in
`config/openassist/activity.yaml`, and the template is disabled by default.
To query an existing historical monitor dataset without starting a collector,
pass a dedicated YAML with explicit storage paths using `--activity-config`.

Verify the native guidance with
`.\run-openclaw.ps1 skills list --agent main --eligible --json`.
Existing extra skill directories, authored workspace files and skill policy
are preserved. With the default skill watcher, existing sessions refresh on
their next turn. An explicit `skills.load.watch: false`, disabled skill, or
per-agent skill allowlist can require the corresponding native refresh or
policy adjustment; the configuration helper does not override those choices.

The MCP child does not read model credentials. It receives only the product
root and optional activity config path. The bridge enforces limits on
time ranges, result count, text length, evidence ids and token budgets before
calling the organizer. The shared `ActivityContextPolicy` constrains
consumer purpose, modes, result counts and content channels. Explicit two-sided
time windows have a policy ceiling; a query without a time range can search
the whole indexed history within its result and token budgets. In structured
and evidence views, `include_uia` and `include_ocr` control channel details;
outline summaries can still mention those observed channels.

Queries catch up the index by default, bounded to ten 200-observation batches.
Remaining lag is reported as `index_lagging`. The source database is opened
read-only, and no collector/writer thread is created. Episode summaries are
deterministic observation counts, not semantic claims about user intent.
Context budgeting includes serialized episode references and evidence. Window
switches, URL navigation and idle periods seal episodes; late evidence can
revise the matching historical interval without extending a closed visit.

`activity_health.result.history` reports retained Observation counts and real
oldest/newest timestamps independently of index catch-up, with UTC and
host-local ISO labels for display. The millisecond values remain query bounds;
the model should not mentally convert them to calendar dates. The `collector`
section reports the last recorded state and diagnostic freshness; it does not
confirm current process liveness (`current_state: unverified`). `profile_enabled` means query access is
enabled, and `bridge_starts_collector` is always false. A disabled access
profile leaves historical availability unchecked rather than asserting that
no historical data exists. An empty recent 300-second query can coexist with
older readable history. The model should state the actual coverage date and
query the last recorded interval when the user asks for older activity.
Capability questions need only health metadata. A brief recap uses
`activity_summary` with explicit `start_utc_ms`/`end_utc_ms`, bounded application
statistics and snippets with source references, rather than enumerating every
item or episode. Detailed event queries and full pagination remain available
when the user requests them.

The default OpenClaw agent can retrieve relevant historical activity through
the MCP tools. There is no fixed history injection and no parallel
agent-specific activity database. Historical Activity remains evidence of what
was observed; an execution-capable agent must obtain a fresh desktop
observation before taking an action.

## Verify an existing dataset

Run the verifier against an explicit historical-data profile. It checks source
references, episode membership, complete cursor pagination and source database
hashes. With `--mcp`, it launches the saved OpenClaw stdio definition through
an MCP client, discovers and invokes all nine tools, and compares their metadata
with the current local bridge. This verifies the configured MCP transport;
it does not exercise a model turn in the live Gateway. The optional `--gateway`
flag is only for the legacy native plugin: the pinned OpenClaw `/tools/invoke`
endpoint does not expose managed MCP tools. No model call, desktop action or collector is started. The index
can catch up, and `--export` saves full Item/Episode content in the private local
artifact directory; stdout and the verification report contain aggregate data.
Knowledge CLI commands also consume the source read-only and emit UTF-8 JSON
when redirected, so Chinese text and OCR/UIA Unicode survive local exports.

```powershell
.\.venv\Scripts\python.exe tools\verify_activity_agent.py `
  --activity-config .artifacts/activity-agent-access/activity-history.yaml `
  --mcp `
  --export .artifacts/activity-agent-access/activity-items-and-episodes.json

.\run-openclaw.ps1 mcp doctor mirally-activity --probe --json
```

The default report is `.artifacts/activity-agent-access/verification.json`.
The native `mcp doctor --probe` command additionally proves that OpenClaw can
connect to the saved server and discover its tools. After editing MCP code,
restart existing Gateway MCP sessions or the Gateway before using those sessions.
A saved config, CLI probe or passing local test does not establish that an
already-connected Gateway session has loaded the same code. End-to-end model
tool selection remains a separate acceptance check.
The configuration helper includes a revision of product Activity Python code
in the child environment. Reapplying after code changes lets native MCP config
reload retire the corresponding old child without restarting the Gateway;
captured data and credentials are excluded from this revision.

An opt-in real-model acceptance command exercises the user's two original
questions, a historical recap and an unrelated question:

```powershell
.\.venv\Scripts\python.exe tools\verify_activity_agent_chat.py --run-model
```

It creates and archives an independent read-only Gateway test session using
the configured model, restricts tools to read/search and Activity MCP, and
records actual tool results and replies. It does not alter the main chat,
start collection or operate the desktop. This command incurs provider charges
and sends retrieved historical evidence to the configured model provider.
Omit `--run-model` to check only the test session's tool policy.

`activity_summary` defaults to the past 300 seconds. Use an explicit time window
with `activity_query` for older evidence. `empty` means no indexed matches in
the requested range; `index_lagging` also applies with `sync: false` and means
the index has not caught up. `budget_exceeded` means matching data or required
metadata could not fit; increase `token_budget` or choose `outline`, rather
than treating that response as no activity. Usable partial pages remain `ok`
with `truncated` and `next_cursor`. Search combines FTS and literal substring
matches with the same ordering and filters on every page, including Chinese
phrases, URLs and application names. Unknown continuation cursors and reversed
time ranges return errors rather than silently restarting a history query.

Budgets use a conservative three UTF-8 bytes per token estimate, not an exact
model tokenizer. Episode counts can change when segmentation rules improve;
Item and Observation counts, source references and the projection version are
the appropriate basis for comparing exports. Capture completeness (`complete`,
`partial`, `unavailable`) does not measure semantic accuracy. Episode summaries
and spans remain deterministic statistics, and the report includes capture gaps
instead of treating missing evidence as proof of user inactivity.

## MCP boundary

The Python `ActivityContextSource` and JSONL bridge are product-owned. MCP is a
thin stdio protocol adapter over that interface, so the organizer, query model,
budgets and evidence lifecycle do not depend on OpenClaw internals. The legacy
native plugin remains available only as a compatibility path for older OpenClaw
versions. It is disabled when applying the MCP migration. The UFO configuration
script does not register Activity; the native UFO loop remains a compatibility
desktop executor and does not directly consume Activity knowledge.

## Verification recorded on 2026-10-12

On the pinned official OpenClaw `2026.9.6` installation, native MCP probing
discovered all nine tools. An isolated Node process also used OpenClaw's actual
harness materialization and final tool-policy pipeline to expose and execute
all nine canonical `mirally-activity__*` tools. Their results matched the local
bridge, and the runtime was disposed after validation.

The historical profile contained 94 Observations, 94 Items and 22 Episodes. Source references,
complete pagination, episode membership and source database hashes passed.
Real configured-model turns also passed the two original capability questions,
a short historical recap and an unrelated question. Capability answers used
health metadata, displayed the correct recorded date and kept present collector
liveness unverified. The recap invoked one bounded historical summary with
source references; the unrelated question invoked no Activity tool. The
read-only test session was archived, and the main conversation was unchanged.

Offline and native-loader tests cover history bounds before index catch-up,
ISO timestamp display, query budgets, skill refresh and policy exclusions.
The root Python suite passed 547 of 549 tests with two expected skips; the
Node suite passed all 16 tests. Live desktop execution and starting the
collector are outside this acceptance check. Reports and full local acceptance
traces are kept in the ignored `.artifacts/activity-mcp-guidance/` directory;
the earlier transport export remains in `.artifacts/activity-mcp-resume/`.
