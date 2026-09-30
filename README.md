# openassist

Windows computer-use product workspace. UFO is the current Windows agent kernel. The product will add its own frontend and voice processing around a stable product API.

| Directory | Purpose | Imported from |
| --- | --- | --- |
| `UFO/` | Windows agent kernel and current product integration | `microsoft/UFO` |
| `apps/` | Product frontend (planned) | |
| `services/` | Product API and voice processing (planned) | |
| `packages/contracts/` | Shared task and event contracts (planned) | |

## Start the desktop app

Run from PowerShell in this directory:

```powershell
.\run.ps1
```

The launcher uses UFO's Python environment and opens an always-on-top chat window. Send one request at a time; each request starts a fresh UFO single-agent task. The window shows progress and completion, and handles agent questions and sensitive-action approvals. While idle or waiting for user input, the whole window is interactive, draggable, and resizable. While the agent is operating the desktop, the window becomes translucent and click-through so it does not intercept mouse actions. Runtime logs and screenshots are saved under `UFO/logs/`.

This product currently targets Windows. `Windows-Use/` and `openclaw/` may remain in a local checkout for evaluation, but are ignored by Git and are not part of the product repository. The original Git metadata for UFO and the evaluation projects is backed up outside this directory at `../openassist-git-history-20260930/`.

For the current agent, see [UFO's single-agent usage](UFO/README_ZH.md) and [the Windows CUA runbook](OPENCLAW_CUA_WINDOWS_RUNBOOK.md).
