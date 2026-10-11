# Activity Agent Access

The activity knowledge layer is a shared OpenClaw capability. It is not a
UFO-specific memory path and it is not injected into every prompt.

## Runtime path

```text
OpenClaw agent using the host tool registry
    -> mirally-activity plugin tools
    -> lazy JSONL bridge process
    -> ActivityContextSource
    -> ActivityOrganizer / rebuildable knowledge SQLite
    -> ObservationStore (read-only source)
```

The plugin is loaded at Gateway startup, but it does not start the collector,
open SQLite, or read historical activity. The bridge process is spawned on the
first activity tool call and exits with the plugin runtime. A disabled or
missing collector returns a structured `disabled` or `not_ready` result.

## Available tools

| Tool | Use |
| --- | --- |
| `activity_health` | Check collection and index availability |
| `activity_recent` | Retrieve a bounded recent timeline |
| `activity_search` | Search indexed titles, URLs, UI text and input metadata |
| `activity_around` | Inspect an explicit UTC time window |
| `activity_summary` | Summarize recent applications and episodes |
| `activity_query` | Run a precise bounded query |
| `activity_episode` | Retrieve one episode by id |
| `activity_evidence` | Retrieve selected evidence references; assets are opt-in |

All tools are read-only. Results include status and bounded context fields. The
default mode is `outline`; `structured` and `evidence` require an explicit
request. Historical UIA, OCR, screenshots and input metadata are evidence of
what was observed, not proof of the current desktop state. A desktop action
must take a fresh observation through the normal execution toolchain.

## Why there is no fixed prompt injection

Activity history is useful for questions such as "what was I doing earlier"
or "find the page I opened this morning", but it adds noise and cost to an
ordinary question. Tool descriptions provide the routing hint; the model calls
the narrowest tool only when the current request needs historical desktop
context. This keeps the same policy for the default OpenClaw agent, UFO desktop
subtasks, and future harnesses.

## Configuration

`tools/configure_openclaw.py` adds the shared plugin when creating a native
configuration. `tools/configure_ufo_agent.py` adds it alongside the UFO
harness. For an existing install, apply the official targeted patch:

```powershell
.\.venv\Scripts\python.exe tools\configure_activity_agent.py
.\.venv\Scripts\python.exe tools\configure_activity_agent.py --apply
```

The first command validates the patch. The second enables the shared plugin
and tools while preserving existing models, plugins and allowlists. An absent
plugin allowlist stays absent so unrelated plugins keep their existing policy.
An explicitly denied plugin is not enabled. Activity collection stays separate
in `config/openassist/activity.yaml`, and the template is disabled by default.
To query an existing historical monitor dataset without starting a collector,
pass a dedicated YAML with explicit storage paths using `--activity-config`.

The plugin does not read model credentials. Its child process receives only the
product root and optional activity config path. The bridge enforces limits on
time ranges, result count, text length, evidence ids and token budgets before
calling the organizer.

Queries catch up the index by default, bounded to ten 200-observation batches.
Remaining lag is reported as `index_lagging`. The source database is opened
read-only, and no collector/writer thread is created. Episode summaries are
deterministic observation counts, not semantic claims about user intent.
Context budgeting includes serialized episode references and evidence. Window
switches, URL navigation and idle periods seal episodes; late evidence can
revise the matching historical interval without extending a closed visit.

The default OpenClaw agent can retrieve relevant activity before delegating a
desktop task to UFO. Direct UFO selection currently does not expose the shared
OpenClaw tool registry inside its native HostAgent/AppAgent loop; registration
alone is not evidence of native UFO tool support. Future harnesses must consume
the host tool registry to use this same capability. There is no parallel
UFO-specific activity database or fixed history injection.

## Verify an existing dataset

Run the verifier against an explicit historical-data profile. It checks source
references, episode membership, complete cursor pagination and source database
hashes. With `--gateway`, it also invokes all eight tools through the official
loopback `/tools/invoke` endpoint and compares their metadata with the current
local bridge. No model call, desktop action or collector is started. The index
can catch up, and `--export` saves full Item/Episode content in the private local
artifact directory; stdout and the verification report contain aggregate data.
Knowledge CLI commands also consume the source read-only and emit UTF-8 JSON
when redirected, so Chinese text and OCR/UIA Unicode survive local exports.

```powershell
.\.venv\Scripts\python.exe tools\verify_activity_agent.py `
  --activity-config .artifacts/activity-agent-access/activity-history.yaml `
  --gateway `
  --export .artifacts/activity-agent-access/activity-items-and-episodes.json
```

The default report is `.artifacts/activity-agent-access/verification.json`.
After editing plugin code, use `.\run-openclaw.ps1 plugins reload mirally-activity`
to refresh the running plugin before verifying it. A saved config or passing
local test does not establish that the live Gateway has loaded the same code.

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

## MCP compatibility

The Python context source is the product-owned interface. OpenClaw currently
uses native plugin tools because they are available to every configured agent
without a second MCP daemon. The same operations can later be exposed through
an MCP server or loopback endpoint without changing the organizer or the
agent-facing query model.
