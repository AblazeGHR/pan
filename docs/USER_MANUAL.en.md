# Pan User Manual

> A beginner-oriented guide to installing Pan, creating the first Session, using an SMA/Meta-Agent, dispatching work asynchronously, receiving reports, and delivering changes. Claims here follow the current React Dashboard, `packages/mcp/server.py`, `packages/web/server.py`, `packages/core/worker.py`, and `manifest.json`.

**[中文](./USER_MANUAL.md) · English**

## Contents

1. [Pan in one minute](#1-pan-in-one-minute)
2. [Install, start, and ports](#2-install-start-and-ports)
3. [Your first task](#3-your-first-task)
4. [Two ways to create a Meta-Agent](#4-two-ways-to-create-a-meta-agent)
5. [Dashboard guide](#5-dashboard-guide)
6. [Manage and parent-child relationships](#6-manage-and-parent-child-relationships)
7. [Subscribe and completion reports](#7-subscribe-and-completion-reports)
8. [pan_access](#8-pan_access)
9. [Choosing dispatch tools](#9-choosing-dispatch-tools)
10. [Worker lifecycle and watchdog](#10-worker-lifecycle-and-watchdog)
11. [Worktrees and delivery](#11-worktrees-and-delivery)
12. [MCP and troubleshooting](#12-mcp-and-troubleshooting)
13. [Security and cleanup](#13-security-and-cleanup)

## 1. Pan in one minute

Pan organizes CLI Agents as a supervisor and executors. A Meta-Agent (SMA, Super Meta-Agent) can decompose your goal, create or reuse child Sessions, dispatch tasks asynchronously, subscribe to reports, verify results, and summarize delivery.

| Term | Meaning |
|---|---|
| Agent | The logical orchestration object, addressed through a Session ID |
| Session | The persistent container holding history, model, adapter, workdir, relationships, and queues (`ses_...`) |
| Worker | The temporary CLI process executing for one Session |
| Adapter | The CLI integration, currently including `cbc`, `kimi`, `opencode`, `claude`, and `codex` |
| Meta-Agent/SMA | A Session with Pan MCP and orchestration permissions; it supervises rather than doing every task itself |

Killing or watchdog-reclaiming a Worker does not delete its Session. Use a regular Session for a simple question; use SMA for parallel work, consolidated delivery, or long-running collaboration.

## 2. Install, start, and ports

Install at least one supported CLI and verify it in the same environment that starts Pan:

```powershell
cbc --version
kimi --version
opencode --version
claude --version
codex --version
```

On Windows:

```powershell
pip install -r minimal-requirements.txt
Copy-Item config.example.json config.json
Set-Location packages/web; pnpm install; pnpm build; Set-Location ../..
python main.py
```

Open <http://127.0.0.1:8768>. `main` defaults to 8768; the test branch convention is 8767. `PAN_PORT` overrides the server port and `PAN_API_URL` controls where the MCP server connects. For `report_subscribe`, the MCP target, `PAN_API_URL`, and the `PAN_AGENT_SESSION_ID` Session must belong to the same Pan instance and port.

## 3. Your first task

1. Click **New** in the Sidebar.
2. Enter a name such as `first-task`.
3. Choose an available **Adapter**, optionally a **Session Template**, and optionally a server-side **Workdir**.
4. Select the Session and click **Start**, or send from the input box (a missing Worker is auto-spawned).
5. Send a self-contained prompt:

```text
Inspect src/utils.py, fix the null-pointer issue, run the relevant tests, and report changed files and test results.
```

Press Enter to send and Shift+Enter for a newline. Continue in Chat, inspect files in **Editor**, or use the session context menu when finished.

## 4. Two ways to create a Meta-Agent

### 4.1 Session Template (recommended for beginners)

The root `manifest.json` currently provides `SMA(NoAdapter)` and `SMA(cbc)`. Both mount `pan` and `pan-qq`, include an SMA system prompt, and default to:

```json
{"restrict_to_managed": false, "can_claim_unmanaged": true, "auto_claim_created": true}
```

Choose **New → Session Template → SMA(NoAdapter)**, then ask it to explain Pan or give it a real goal. `SMA(NoAdapter)` leaves the adapter selectable; `SMA(cbc)` pins `cbc`. Template values are a baseline: explicit creation fields override template values, which override system defaults.

### 4.2 Existing Agent + MCP + skill

Enable the `pan` MCP server for the existing Agent. When it is a Pan Session, the adapter generates a session-scoped MCP config under `data/mcp-configs/` and injects `PAN_AGENT_SESSION_ID`. You can also run the stdio server directly:

```powershell
$env:PAN_API_URL = "http://127.0.0.1:8768"
python -m packages.mcp.server --transport stdio
```

Install `docs/skills/pan/SKILL.md` into the Agent CLI’s skill location. Verify with `pan_handbook()`, then run a small `session_list(summary=true)` and a `report_subscribe` + `agent_assign` test. A standalone MCP process has no `PAN_AGENT_SESSION_ID`, so identity-dependent tools such as claim and report subscription are unavailable. Full Meta-Agent behavior needs MCP, the skill, and a Pan-managed Session.

## 5. Dashboard guide

The Sidebar lists Sessions; Chat talks to the selected Session; Editor accesses its workdir. The top bar exposes **Start**, **Restart**, **Interrupt**, **Takeover**, and **Kill**. A session context menu contains actions including **Manage** and **Postbox**.

Session cards support **drag & drop**: dragging within the same level changes the display order (persisted server-side, equivalent to the custom sort `POST /api/sessions/order`); dropping a card onto the center of another card quickly establishes/releases the managed relationship (equivalent to Manage/Managed in the Manage panel, via claim/unclaim).

**Manage Sessions** has four areas: **Managed by** (parent manager), **Manages** (Manage/Managed relationship buttons and independent Subscribe/Subscribed report buttons), **Pan Access**, and **MCP Server** selection. **Postbox** is for QQ inbox subscriptions, not Worker completion reports.

## 6. Manage and parent-child relationships

If `ses_parent` manages `ses_child`:

```text
ses_parent.managed  = ["ses_child"]
ses_child.managedBy = "ses_parent"
```

`managed` means “children I manage”; `managedBy` means “my parent manager”. A Session has one manager at a time, so a child already managed by another manager cannot be claimed. In UI, click **Manage** or **Managed**. MCP uses `session_claim` and `session_unclaim`. Unclaim removes the relationship and also unsubscribes reports, but does not delete the Session. `report_unsubscribe` stops reports while retaining the relationship. UI management is a direct high-privilege path; MCP `restrictToManaged` does not limit the Dashboard itself.

If a managed child should remain in the management tree but temporarily reject tasks, messages, and notices from other Sessions, the current manager can call `session_readonly(session_id="ses_child", enabled=true)`. Pass `enabled=false` to clear it. The operation never claims a Session and returns `readonly_session` to rejected senders. This is an orchestration state, not HTTP/API authentication or a filesystem read-only permission.

## 7. Subscribe and completion reports

The normal Meta-Agent flow is:

```text
report_subscribe → agent_assign → Worker done/error → manager queue_pending → session_get
```

Reports are persisted in the manager’s `queue_pending`; the wake-up signal is not the report source. A report contains fields such as `status`, `result`, `sessionId`, `taskId`, and `workerId`. The Dashboard exposes this in **Manage → Manages**: **Subscribe** becomes **Subscribed**. Claiming a Session also auto-subscribes; unclaiming auto-unsubscribes.

`agent_notify(target_session_id, text)` is a real MCP tool for persistent, asynchronous notifications. Use it when a background command or long-running job (for example `nohup`, a long test run, a compiler, or an external script) finishes outside the current Agent/Worker lifetime. The job can report afterward:

```text
agent_notify(target_session_id="ses_parent", text="Background tests finished: 128 passed; log is in artifacts/test.log.")
```

The notice is persisted in the target Session's `queue_pending`; if the target has no live Worker, Pan wakes/spawns it. It only delivers a reliable report: it does not grant extra permissions, bypass approvals, or bypass managed isolation. Do not confuse it with `/api/qq/notify`, an internal QQ-plugin route. Use `report_subscribe` for child-task completion/error reports, `agent_assign` for ordinary new tasks, `agent_send` for queued follow-up context, and `agent_send_force` for an urgent restart-and-send.

If report subscription returns 404, the running server may not contain the route. Check version and port alignment; temporarily inspect `session_get.lastResult.status` (`queued → running → done/error`).

## 8. pan_access

`restrictToManaged` limits MCP operations to the caller and its managed Sessions. `canClaimUnmanaged` allows claiming Sessions with no manager, but never overrides another manager. `autoClaimCreated` automatically claims Sessions created by the caller. Ordinary Sessions default all three to false; both current SMA templates default to unrestricted access, unmanaged claiming, and auto-claiming. Unauthorized MCP calls return structured errors such as `permission_denied` or `missing_identity`. These are MCP boundaries, not HTTP authentication.

## 9. Choosing dispatch tools

| Tool | Behavior | Use |
|---|---|---|
| `agent_assign(session_id, text, task_id?)` | New asynchronous task; returns `queued`; auto-spawns | Default for new work and parallel fan-out; reuse `task_id` on retry |
| `agent_send(session_id, text)` | Queued multi-turn message; does not interrupt | Additional context or a follow-up |
| `agent_send_force(session_id, text)` | Restart + send for a live Worker; queues if none | Urgent constraint, direction change, or stuck Worker |
| `agent_notify(target_session_id, text)` | Persistent asynchronous notice for detached background work; auto-wakes/spawns the target when needed | Use for a later result/status report, not ordinary task dispatch |

`worker_*` names are compatibility aliases. Dispatch is asynchronous: `queued` means accepted, not complete. Wait for the report, then inspect and verify; do not repeatedly resend because the result is not immediate.

## 10. Worker lifecycle and watchdog

`session_create` creates no Worker. Start/spawn creates one temporary CLI process per Session. Watchdogs reclaim queued silence, overlong stream tasks, and idle Workers; the global watchdog also recovers non-empty queues with no live Worker. `workerStatus: null` means no live process. Start again, `agent_spawn`, or `agent_assign` can recover it without deleting Session history. Use Kill for a stuck process or deliberate cleanup, not as a substitute for unclaiming.

## 11. Worktrees and delivery

Use one child Session and one Git worktree/branch per parallel code task. Give each Session an absolute `workdir`, keep merge ownership in one directory, and ask Workers to test, run `git diff --check`, commit, and not push:

```text
Modify only the specified worktree. Run relevant tests and git diff --check, create a clear commit, and do not push. Report changed files, verification, and commit hash.
```

Deleting a Session stops its Worker and removes Session metadata/config, but does not remove the ordinary workdir directory. Preserve or commit artifacts first.

## 12. MCP and troubleshooting

Confirm `pan` is enabled, restart the Worker after MCP changes, and ask the Agent to call `pan_handbook()`. Prefer `session_list(summary=true)` and targeted `session_get(limit=15)` instead of transferring full histories. For failures, check CLI availability, `workerStatus`, `data/logs/pan.log`, `PAN_API_URL`, the 8768/8767 instance, and whether `PAN_AGENT_SESSION_ID` belongs to that instance. MCP requests use snake_case; HTTP bodies use camelCase (`sessionId`, `panAccess`).

## 13. Security and cleanup

The default API has no authentication and binds to loopback intentionally. Do not expose it publicly without an explicit security design. Do not delete Sessions you do not own or understand; unclaim is the reversible relationship operation. Do not mass-delete `ses_*`. Use `session_batch_delete` only for confirmed disposable child Sessions after delivery.

For the complete reference, read [`SKILL.md`](skills/pan/SKILL.md), [`http-api.md`](skills/pan/references/http-api.md), [`ws-protocol.md`](skills/pan/references/ws-protocol.md), and the Chinese manual’s [`pan-user-manual-images.txt`](pan-user-manual-images.txt).
