# Pan User Manual

> A complete hands-on guide for users who actually want to use Pan. For a quick conceptual overview, see the README; for the orchestration cold-start manual (from a Meta-Agent's perspective), see `docs/skills/pan/SKILL.md`; this manual is the full-featured operational reference on top of both.
>
> Applies to: `main` branch (after commit aa430a0). Ports default to 8768 on `main` (8767 on `test`).

**[中文](./USER_MANUAL.md) · English**

## Table of Contents

1. [What is Pan](#1-what-is-pan)
2. [Installation & Startup](#2-installation--startup)
3. [Quick Start (UI Walkthrough)](#3-quick-start-ui-walkthrough)
4. [UI Guide](#4-ui-guide)
5. [Configuration](#5-configuration)
6. [Best Practices](#6-best-practices)
7. [Core Operations](#7-core-operations)
8. [Orchestration: A Meta-Agent Guide](#8-orchestration-a-meta-agent-guide)
9. [Channels: Web / QQ / Remote](#9-channels-web--qq--remote)
10. [Troubleshooting](#10-troubleshooting)
11. [Security & Ops Notes](#11-security--ops-notes)
12. [Developer & API Reference](#12-developer--api-reference)
13. [Related Documents](#13-related-documents)

---

## 1. What is Pan

Pan is a **CLI Agent orchestration platform** (orchestrator): under a Supervisor/Worker architecture, a "Meta-Agent supervisor" (also called SMA, Super Meta Agent) directs multiple Workers in parallel via MCP (Model Context Protocol) tools and WebSocket (WS) event streams. A traditional AI coding assistant is one-to-one; Pan is **one-to-many** — you talk to a single supervisor, and it decomposes tasks and dispatches an entire team of CLI Agent workers.

### 1.1 The Usage Spectrum

From the shallowest to the fullest usage:

| Tier | What you use it for | Components involved |
|------|--------------------|---------------------|
| Minimal | One entry point to manage multiple CLI sessions: create sessions, dispatch tasks, read results | **Web Dashboard (recommended)** / HTTP API |
| Advanced | One Meta-Agent orchestrating multiple Workers in parallel (fan-out) | MCP tools + report_subscribe |
| Full | External Agent cluster collaboration + multi-channel control (Web/QQ/public network) + Memory/Character | all modules |

### 1.2 Core Concepts at a Glance

| Concept | English | Description |
|---------|---------|-------------|
| Agent / Session | Session | **Logical orchestration object**: persistent identity (`ses_<16hex>`) with an inbox (`queue_pending`), a level (agentLevel), and a management chain (managedBy). Delivery/orchestration semantics are bound to it, independent of the Worker lifecycle |
| Worker | Worker | **Physical executor**: a temporary CLI process instance (cbc/kimi/...), owned by an Agent, killable/rebuildable at any time. "The process is incidental" |
| Meta-Agent / SMA | Meta-Agent | Supervisor role: does not do the work itself — only decomposes, dispatches, collects reports, and accepts results. Any party that can send commands (MCP/HTTP) + receive intel (report subscription/WS) + has an identity (`PAN_AGENT_SESSION_ID`) can play it |
| Adapter | CLI Adapter | One protocol adapter per CLI Agent: `cbc` / `kimi` / `opencode` / `claude` / `codex` |
| Memory | Memory | Hybrid vector + full-text (SQLite FTS5) retrieval, auto-injecting relevant memories before work starts |
| Character | Character | A persona + its own memory store (`char_<16hex>`), keeping the same identity across Sessions |
| Watchdog | Watchdog | One per Worker: auto-cleans stuck/silent/idle timeouts; the global one can also auto-replenish Sessions whose queue is non-empty but have no live Worker |

Data-model essentials: **Workers have no memory; Sessions do.** Every spawn is a fresh process; the Session stores `history` and `cliSessionId`, and when a Worker is rebuilt the adapter uses `--resume` to restore full context from the CLI's native transcript.

---

## 2. Installation & Startup

### 2.1 Prerequisites

- Python 3.14 (development environment: 3.14.5)
- Node.js + pnpm (to build the React frontend)
- At least one supported CLI: `cbc` (CodeBuddy CLI), `kimi`, `opencode`, `claude`, `codex`

Pan does not install third-party Agent CLIs. In the **same terminal / user environment that will start Pan**, verify that at least one CLI is installed globally:

```bash
cbc --version
kimi --version
opencode --version
claude --version
codex --version
```

At least one command should print a version. At startup, Pan logs a `ready/unavailable` status for every CLI. A missing optional CLI does not prevent Pan from starting, but creating a Worker with that adapter shows installation, PATH, and restart guidance. If all CLIs are missing, Workers cannot run. A background service may have a different PATH from your interactive terminal, so restart Pan after installing a CLI or changing PATH. Inspect `GET http://127.0.0.1:8768/api/cli/status` for live diagnostics.

> Frontend note: **React Dashboard is the only maintained and recommended frontend.** The legacy Vanilla frontend is deprecated and served only at `/vanilla` as a fallback (see §12.3); new users are advised not to use it.

### 2.2 Installation Steps

```bash
# 1. Install the minimal dependencies (core only, no Memory ML chain)
pip install -r minimal-requirements.txt

# 2. Generate configuration (every field is optional; omitted fields use defaults)
cp config.example.json config.json        # Windows: copy config.example.json config.json

# 3. Build the React frontend (recommended; output → packages/web/dist/)
cd packages/web && pnpm install && pnpm build && cd ../..

# 4. Start
python main.py
# → http://127.0.0.1:8768
```

`scripts/` also has helper scripts that skip the manual steps: `setup.bat` / `setup.sh` (install deps, generate config.json, probe the QQ interpreter, etc.), `start_pan.bat` / `start.sh` (start), `stop_pan.bat` / `stop.sh` (stop; the Windows version kills the exact process tree via the PID file, without touching other python processes).

**macOS / Linux quick path** (the scripts automate steps 1-3 above):

```bash
bash scripts/setup.sh    # first time: create .venv + deps + config.json + frontend build
bash scripts/start.sh    # background start (PID in data/process.pid, log data/pan.out.log)
bash scripts/stop.sh     # stop
```

### 2.3 Ports and Environment Variables

| Port | Purpose |
|------|---------|
| 8768 | Pan main service (default on `main`; 8767 on `test`; the `port` field in `config.json`) |
| 8769 | Remote status service (`remote.status_port`) |
| 8080 | QQ plugin (NoneBot) HTTP API, not exposed |
| 3001 / 3002 | NapCat / LLOneBot gateways (forward WS) |
| 9740 | Default port for MCP server SSE/streamable-http mode |

| Env var | Default | Description |
|---------|---------|-------------|
| `PAN_PORT` | — | Overrides `port` |
| `PAN_HOST` | `127.0.0.1` | Listen address (non-loopback prints an unauthenticated warning) |
| `PAN_API_URL` | `http://127.0.0.1:8768` | Address the MCP server uses to reach Pan Core |
| `PAN_URL` | `http://127.0.0.1:{port}` | Address QQ Bridge uses to reach Pan Core |
| `PAN_QQ_API_URL` | `http://127.0.0.1:8080` | Address pan-qq MCP uses to reach the QQ plugin |
| `PAN_QQ_PYTHON` | platform default | QQ bot interpreter path |
| `PAN_QQ_MODE` | — | Overrides `qq.mode` |
| `ONEBOT_WS_URLS` / `ONEBOT_ACCESS_TOKEN` | — | QQ channel WS address / token (can be written in `packages/qq/.env`) |

### 2.4 Stopping

Ctrl+C exits gracefully (the QQ bot subprocess is terminated along with it); or use `scripts/stop_pan.bat` / `stop.sh`.

### 2.5 macOS / Linux Notes

- **Case-sensitive paths**: macOS/Linux filesystems are case-sensitive — `.venv`, `config.json`, `packages/qq/.env` etc. must match the docs exactly; a wrong case means "file not found";
- **QQ interpreter**: the path is resolved automatically by `main.py` (`PAN_QQ_PYTHON` env var > `qq.python` in config.json > platform default); on macOS/Linux it defaults to `python3`, so no manual config is needed — only set `qq.python` or `PAN_QQ_PYTHON` if you need a specific interpreter; if you don't use QQ, set `qq.enabled=false` in config and skip setup.sh's QQ dependency step;
- **Process-group reaping**: on Linux `setsid` is available, so `start.sh` makes main.py the leader of its own process group and `stop.sh` reaps the whole group; macOS has no `setsid` by default, degrading to a plain background process — `stop.sh` recursively TERMs the children (including the QQ bridge) then shuts down gracefully. **Only the recorded PID + process group is killed; other python processes are never touched.**

---

## 3. Quick Start (UI Walkthrough)

This chapter walks you through your first task using the **browser interface**: from starting the service to seeing the AI's output — no command line required.

### 3.1 Start the Service and Open the UI

> **Don't feel like reading the docs?** After starting the service, create an `SMA(NoAdapter)` session and ask it "how do I get the most out of Pan?" — it will pull up the orchestration handbook (`pan_handbook`) and teach you step by step.

```bash
# Windows
python main.py

# macOS / Linux (background start; run setup first time)
bash scripts/setup.sh    # first time only
bash scripts/start.sh
```

Open <http://127.0.0.1:8768> in a browser (the default port; see §5.2 to change it). You'll see Pan's **React Dashboard**: the session list (Sidebar) on the left, the chat main area on the right.

### 3.2 Create a Session

1. Click **New** at the top of the left sidebar to quickly create a default-named session; or click the ⚙ gear next to it to open the **new-session config dialog** and set:
   - **Adapter (CLI)**: which CLI Agent does the work (cbc / kimi / opencode etc., see §7.9);
   - **Session name**: a name that represents the task (e.g. `fix-login-bug`);
   - **Work directory (workdir)**: which files the AI may read/write (default `data/workdirs/<session name>`);
   - **Session template**: optional — e.g. the SMA template preloads Meta-Agent orchestration capabilities (see §8).
2. Once created, the session appears in the left list as a card with a status dot, adapter badge, model, message count, etc.

> More settings such as model, permission mode, and thinking level don't need to be decided at creation time — change them anytime in the session settings (see §4.6).

### 3.3 Start the Worker and Send a Task

1. Select the session you just created and click **Start** in the top bar to start the Worker (it launches the corresponding CLI Agent process).
2. Type a task in the input box at the bottom, e.g.: `Fix the null-pointer bug in src/utils.py and make the unit tests pass.`
3. Press **Enter** to send (Shift+Enter for a newline). While the Worker is busy, messages are automatically queued in the **send queue** and processed in order once it's idle.

### 3.4 Watch the Live Output

- The status dot in the top bar changes color with the Worker state: **green = idle, blue = running, yellow = taken over by you, red = error**;
- Replies appear in the chat stream one by one; tool calls are collapsed into a row — click one to see the raw output in the **DetailPanel** on the right; thinking blocks can be expanded inline;
- The React frontend currently retains the **TUI**-style view: user input has a green border and message blocks have left-side color bars. The **Bubble** view and top-bar toggle are currently hidden and marked deprecated so they can be re-enabled later if needed.

### 3.5 Reading Results and What's Next

- When the task finishes, the status dot returns to green (idle) and the reply stays in the chat stream;
- You can keep asking follow-ups in the input box at any time (multi-turn);
- After the AI edits files, switch to the **Editor** view to browse/edit files in the session's workdir (see §4.4);
- To clean up, right-click the session → Delete (see §4.5).

> The full feature map is in Chapter 4 "UI Guide"; "when to do what" advice is in Chapter 6 "Best Practices".

---

## 4. UI Guide

This chapter describes each area of the React Dashboard (`packages/web/src/`) and how to use it.

### 4.1 Overall Layout

After opening the home page, the UI splits into two parts:

- **Sidebar (left)**: the session list; navigation and the create entry are at the top;
- **Main area**: three views, switched via the sidebar or keyboard shortcuts:

| View | Route | Purpose |
|------|-------|---------|
| Chat | `/` | Talk to a single session: send tasks, watch output, adjust settings |
| Editor | `/editor` | Browse/edit files in the session's workdir |
| Manage | `/manage/:id` | Session management panel: claim/being-claimed, MCP permissions, etc. |

Shortcuts: **Ctrl+B** collapses/expands the sidebar (collapsed to a narrow icon bar); **Ctrl+1 / Ctrl+2** switch between Chat/Editor; **Ctrl/Cmd+K** opens the command palette (§4.7).

### 4.2 Session List and Creation

- **New** button: quickly creates a default-named session;
- **⚙ New-session dialog** (NewSessionModal): set Adapter, session name, workdir, session template at creation; Output Mode appears only for adapters that support multiple modes (e.g. cbc);
- Session cards (SessionItem) show: status dot, adapter badge, message count, model, workdir, credit, etc.;
- The list supports search filtering, sorting, and grouping by directory or manager (controls above the sidebar);
- The **top bar** (TopBar) appears for the selected session: Start (start the Worker), Restart, Interrupt (interrupt the current task), Takeover (take over the terminal locally), Kill, plus the status dot and worker ID.

### 4.3 Chat View

- **Sending messages**: the input row (InputRow) at the bottom — Enter sends, Shift+Enter inserts a newline; the gear left of the input opens session settings (§4.6).
- **Send queue** (SendQueuePanel): messages queue automatically while the Worker is busy; the queue panel lets you edit, reorder, delete, merge-send, or clear; Agent queues are grouped separately.
- **Message stream** (ChatMessages): shows history and live replies; tool calls collapse into clickable rows — clicking shows the raw output in the **DetailPanel** on the right; thinking blocks expand inline.
- **Display style**: TUI style is currently fixed; the Bubble implementation is retained but deprecated, and its toggle entry is temporarily hidden.

### 4.4 Editor View

- **File tree** (FileTree) on the left: browse the session's workdir; rename/delete files;
- **Multi-tab editor** (EditorPane) in the main area: open several files at once; Markdown files support **Edit / Preview / Split** modes.

### 4.5 Session Management Operations

Right-click a session (or use the selected session's menu, SessionMenu):

| Action | Description |
|--------|-------------|
| Rename | Rename (written back to the CLI's native storage) |
| Reimport | Re-import the underlying CLI session |
| Branch | Fork an independent copy of the current session (inherits settings, independent of each other; CLI sessions only) |
| Manage | Open the management panel (ManageModal) |
| Postbox | Open inbox subscription (PostboxModal) |
| Select | Enter multi-select mode for batch deletion |
| Delete | Delete the session (also kills the Worker; does not delete the workdir directory) |

- **Management panel** (ManageModal) has three sections: ① managed by whom (can unbind); ② who you manage (claim/unclaim + subscribe to completion reports); ③ MCP permissions and MCP server selection.
- **Import** (ImportModal): browse and import existing CLI history sessions from cbc / kimi / opencode etc., reusing history context.
- **Postbox subscription** (PostboxModal): subscribe a QQ conversation to the current session's inbox; new QQ messages are pushed into `queue_pending` as reminders (used with the QQ channel, see §9.2).

### 4.6 Settings

- **Session settings** (SettingsPopover, the gear left of the input): model, permission mode, Thinking/Effort level, Output Mode, plus operations on the Worker.
- **Global settings** (AppSettingsModal, the gear in the sidebar): default grouping of the session list, message visibility (meta-agent / task-agent / QQ message toggles), theme switching, and **config hot-reload** (adapter / worker / plugin / memory config takes effect without a restart).

### 4.7 Command Palette

**Ctrl/Cmd+K** opens the CommandPalette: type a keyword to quickly switch views, create sessions, jump to a session, switch themes, collapse the sidebar, and more.

---

## 5. Configuration

The config file is `config.json` at the repo root (**gitignored**); the template is `config.example.json`. Every field is optional — omitted fields use defaults. Below, common settings are organized by "what you want to achieve"; the full field cheat-sheet is in §5.7.

### 5.1 Generate Configuration from the Template

```bash
cp config.example.json config.json    # Windows: copy config.example.json config.json
```

After editing `config.json`, restart `python main.py` for it to take effect; some adapter / worker / plugin / memory settings support hot-reload (a button in the UI global settings, or `POST /api/config/reload`).

### 5.2 Change the Port

```json
{ "port": 8768 }
```

Defaults: 8768 on `main`, 8767 on `test`. The `PAN_PORT` env var overrides it (higher priority). The access URL changes accordingly.

### 5.3 Default Model and Available Models

```json
{
  "cbc":  { "model": "deepseek-v4-flash", "models": [] },
  "kimi": { "model": "moonshot-cn/kimi-k2.6", "models": [] }
}
```

- `model`: the **default model** used when creating sessions for that adapter;
- `models`: **left empty (`[]`) = auto-detect** all available models of that CLI; **filled in = restrict** which models appear in the UI (e.g. a team that wants to lock down to a single model).

### 5.4 Permission Mode (important, security-related)

```json
{ "cbc": { "permission_mode": "bypassPermissions" } }
```

Options: `""` (default) / `default` / `acceptEdits` / `bypassPermissions` / `plan` / `dontAsk` / `auto`.

- `bypassPermissions` (default): the CLI Agent executes commands / edits files **without per-step approval** — by design for automated orchestration; use it in a trusted environment;
- More conservative `default` / `acceptEdits`: the AI asks for confirmation before modifying files — safer but more interruptive.

> No auth + binding to 127.0.0.1 by default is the established design (§11). Permission mode governs "what the AI may do while working", not "who can access the service".

### 5.5 Frontend Mode

```json
{ "frontend": "coexist" }
```

| Value | Effect |
|-------|--------|
| `coexist` (default) | `/` 307-redirects to `/react/` (React Dashboard); the legacy frontend is at `/vanilla` |
| `react` | Only React takes over `/` (no legacy entry) |
| `legacy` | Legacy frontend only (**deprecated**, not recommended) |

### 5.6 Worker Timeouts (stuck-job reclamation)

```json
{
  "worker": {
    "timeout_sec": 300,
    "idle_sec": 300
  }
}
```

- `timeout_sec`: **silence timeout** — if a running task emits no output for this many seconds, it's treated as stuck and killed (default 300). Long thinking / large file reads won't be false-killed (output keeps it alive);
- `task_timeout_sec`: **stream task duration cap** (default 1800) — a single task that runs longer than this is reclaimed; a backstop for "outputting but looping";
- `idle_sec`: **idle reclamation** — a process idle for this many seconds after a task is reclaimed to free resources (default 300; sessions you took over or errored ones are skipped).

### 5.7 Field Cheat-Sheet

| Field | Default | Description |
|-------|---------|-------------|
| `port` | `8768` | Main service port |
| `frontend` | `"coexist"` | Frontend mode: `coexist` / `react` / `legacy` |
| `cbc.model` | `"deepseek-v4-flash"` | cbc default model |
| `cbc.permission_mode` | `"bypassPermissions"` | Permission mode (see §5.4) |
| `cbc.always_thinking_enabled` | `false` | Thinking toggle; `effort` is ignored when false |
| `cbc.effort` | `""` | Thinking level (`none/off/auto/low/medium/high/xhigh/max/ultracode`) |
| `cbc.models` | `[]` | Empty = auto-detect; filled = restrict available models |
| `kimi.model` | `"moonshot-cn/kimi-k2.6"` | kimi default model |
| `cbc_import.*` | see template | External session import filters (message count / time window / directory match, etc.) |
| `worker.timeout_sec` | `300` | Silence timeout (kill when no output) |
| `worker.task_timeout_sec` | `1800` | stream task duration cap |
| `worker.idle_sec` | `300` | Idle reclamation (`held`/`zombie` skipped) |
| `memory.enabled` | `true` | Memory injection toggle |
| `qq.enabled` | `true` | Whether to start the QQ bot |
| `qq.mode` | `"mirror"` | `mirror` auto-replies / `selective` only feeds the inbox for the orchestrator |
| `qq.channel` | `"napcat"` | `napcat` / `llonebot` gateway |
| `qq.python` | `""` | QQ bot's dedicated interpreter path |
| `remote.*` | see template | Cloudflare Tunnel: `enabled`/`quick_tunnel`/`config_path`/`status_port` etc. |
| `logging.*` | INFO / `data/logs/pan.log` | Log level, file, rotation |
| `plugin_manifests` | `["manifest.json"]` | External Character profiles / templates / MCP server manifests |

---

## 6. Best Practices

### 6.1 Core Paradigm: SMA Is Your Single Entry Point

Pan's everyday usage recommends the "**one entry point**" paradigm: **you talk to a single SMA (Super Meta Agent) session**, and everything else — decomposing tasks, creating sessions, dispatching, collecting reports, verifying and aggregating, cleaning up — is handled by SMA.

- **Recommended template: `SMA(NoAdapter)`** — it doesn't explicitly bind a CLI adapter; it's a **pure orchestrator**: it does no work itself, only dispatches other sessions via the pan MCP tools. The `SMA` template binds cbc explicitly, for when you want it to do some work directly.
- Both SMA templates come with full orchestration permissions (pan_access): `auto_claim_created` (newly created sessions are managed automatically), `can_claim_unmanaged` (can claim any unmanaged session), `restrict_to_managed=false` (not restricted by ownership isolation), and mount the `pan` + `pan-qq` MCP servers.
- **The point**: you don't need to manually create a pile of sessions in the Dashboard or manage each Worker's lifecycle and reports — **SMA manages Pan for you**. All you do is "give a goal → accept results".

> The direct path shown in Chapter 3 (creating sessions yourself, sending messages directly) suits simple/one-off tasks; for work that needs decomposition, parallelism, and aggregation, follow this paradigm.
>
> **Don't feel like reading the docs? Just ask SMA "how do I use Pan!"** — SMA will pull up the orchestration handbook via `pan_handbook` and walk you through creating sessions, dispatching tasks, and collecting reports.

### 6.2 Running a Full Task Cycle with SMA

1. Create a session, choose the **SMA(NoAdapter)** template (UI new-session dialog → Session Template, see §4.2);
2. Give it a goal, e.g.: "Research options A / B / C in parallel, conclude each, and merge them into a comparison report";
3. SMA runs the **three decision questions** (can it truly run in parallel? is splitting faster? does precision matter?) to decide whether to decompose;
4. Once it decides to decompose, SMA does the rest automatically via the pan MCP tools:
   - `session_create` creates sub-sessions (**managed automatically** — no manual claim needed);
   - `worker_assign` asynchronously dispatches a task to each sub-session;
   - `report_subscribe` subscribes to completion reports;
5. When each sub-Worker finishes, the report lands in SMA's inbox `queue_pending`;
6. SMA aggregates, verifies (trust-but-verify), and hands you one merged result;
7. Cleanup: SMA uses `session_batch_delete` to batch-delete sub-sessions (subscriptions are removed along with them) — not your job.

> SMA's behavioral details (three decision questions, parallel fan-out, serial dependencies, etc.) are in Chapter 8 "Orchestration: A Meta-Agent Guide"; this chapter only covers how you should use SMA.

### 6.3 When You Can Skip SMA (direct connection)

| Scenario | Advice |
|----------|--------|
| Simple instant tasks (a quick question, edit one line) | Create an ordinary session and send a message directly — no need to go through SMA |
| Want to watch a long task's output in real time | Connect to that session directly and follow along |
| Parallel tasks / aggregation delivery / long-term collaboration | Hand it to SMA — that's its job |

### 6.4 How to Write Task Descriptions

- **To SMA**: goal + boundaries + acceptance criteria. Decomposition, sub-session creation, and sub-task writing are SMA's job — you don't plan for it;
- **SMA to sub-sessions**: SMA handles it by the rule "new sessions need self-contained descriptions (background/goal/files involved/acceptance criteria); sessions with context get short instructions" — you never face sub-sessions directly;
- To tell whether a session has context: check whether the chat history already has content in the UI.

### 6.5 How to Verify

- What you verify is SMA's **deliverable** (the merged report), not each sub-session;
- Spot-check: in the UI session list, all sub-sessions show as managed by SMA — open them to see individual results;
- For important changes, have SMA include verification steps (run tests/commands), and inspect the changes in the Editor view (§4.4).

### 6.6 When to Take Over Manually

- SMA going in circles / decomposing poorly: correct it directly in conversation — it will re-decompose;
- A sub-Worker stuck: the watchdog reclaims it, and SMA can also interrupt with send_force; if you really need hands-on, Takeover that sub-session (§4.2);
- SMA itself needs servicing: Takeover its terminal, fix things, then restart to continue.

### 6.7 Character, Memory, and Long-Lived Sessions

- Give the SMA main session a **Character + Memory** (§1.2): it will remember your preferences and project background long-term, getting better with use;
- Long-lived specialist sessions (e.g. a persona'd conversation role): create them separately and let SMA claim and manage them; SMA invokes them on demand — you don't operate them directly.

### 6.8 Session Organization & Cleanup

- Sub-sessions are almost never your concern: SMA creates them, SMA subscribes, SMA deletes (`session_batch_delete`);
- You only maintain: the SMA main session (kept long-term) + the very few long-lived sessions;
- Reminder: deleting a session does not delete the workdir directory (§4.5) — persist anything you want to keep before deleting.

---

## 7. Core Operations

This chapter explains the meaning and use cases of each operation (entry points: UI in Chapter 4; HTTP/MCP in Chapter 12, Developer reference).

### 7.1 The Three Dispatch Modes

| Operation | Semantics | Use for |
|-----------|-----------|---------|
| assign | **Async new task**: returns queued immediately, auto-spawns if no live Worker; `taskId` is idempotent (re-sending the same id never runs it twice) | New tasks / parallel fan-out (default first choice) |
| send | Message an existing Agent: **queues without interrupting**, processed when idle; persistent queue if no live Worker | Follow-ups / extra hints |
| send_force | **Force push = restart + send**: interrupts a running task and delivers immediately | Direction changes / urgent commands / stuck-Worker backstop |

### 7.2 Lifecycle

- **spawn**: start the Worker (an Agent has exactly one Worker at a time; an existing one is killed first);
- **kill**: kill the Worker process, Session data preserved (the process is incidental — rebuild anytime);
- **restart**: kill the process and rebuild with resume;
- **interrupt**: interrupt the current task (running only).

### 7.3 Ownership: claim / unclaim

- **claim**: establishes a two-way "supervisor ↔ session" managed relationship; **claim auto-subscribes to reports**; rejected if the target is already managed by someone else;
- **unclaim**: removes the management and auto-unsubscribes;
- Each Session belongs to exactly one supervisor (star topology); the upstream management chain can be queried.

### 7.4 branch (fork)

Forks an independent copy of an existing session: inherits settings and MCP bindings, independent of the original. Good for "trying another path" — delete the branch if it fails, the mainline is unaffected.

### 7.5 takeover (manual control)

Restarts the Worker and opens the adapter's native interactive CLI (`--resume` restores context) in a **new terminal window**, setting the Worker to `held`. While held, task delivery is rejected and the watchdog skips it. To recover: restart to clear `held`.

### 7.6 handoff (body-double handover)

Use cases: context too large and needs trimming, or switching adapters mid-task (a normal session can't switch adapter). Creates a twin session B to replace A: B takes over all of A's relationship graph (managed, subscriptions, QQ bindings), A is archived as `(archive) <original name>`; only a compact summary is carried — **avoiding long-session context bloat**.

### 7.7 Batch Deletion

Multi-select batch delete also cleans up cross-session references. Note: deletion does not remove the workdir directory; the underlying CLI session still exists and can be re-imported anytime.

### 7.8 QQ Subscription

Subscribe a QQ conversation (friend/group) to a Pan session: when new QQ messages arrive, a `@@@@by qq` reminder is pushed into `queue_pending` and the Worker is woken (used with §9.2).

### 7.9 Multiple CLIs (Adapters)

One adapter per CLI Agent: `cbc` / `kimi` / `opencode` / `claude` / `codex`. Whichever you pick at session creation does the work; to switch CLIs mid-task use §7.6 handoff (a normal session can't switch adapters). Adapter details (execution mode, MCP injection) are in §12.3.

---

## 8. Orchestration: A Meta-Agent Guide

This chapter is for users who use Pan as a dispatch console, letting one supervisor direct multiple Workers in parallel.

### 8.0 Install the pan skill into Your Agent CLI (strongly recommended)

**pan skill** (`SKILL.md`) is a **cold-start manual** for agents that want to act as a Meta-Agent supervisor: it teaches the agent Pan's orchestration flow (`session_create → report_subscribe → agent_assign → queue_pending`), MCP tool conventions, and pitfalls all at once. Once installed, the agent automatically knows these when it starts — **no need to teach it from scratch in your prompt**; combined with MCP tool injection, the agent can start supervising Workers right away.

How to install:

- **Source of truth**: `docs/skills/pan/SKILL.md` (git-tracked, updated with the repo; treat it as authoritative);
- **CodeBuddy (cbc, the main adapter)**: the repo already ships a project-level copy at `.codebuddy/skills/pan/SKILL.md`; when using CodeBuddy inside this repo's workdir it is **loaded automatically — no action needed**. To use it in another project, copy the whole `pan/` directory into that project's `.codebuddy/skills/`;
- **Other CLIs that support Agent Skills** (e.g. Claude Code's `.claude/skills/`, Codex's `~/.codex/skills/`, etc.): place `pan/SKILL.md` in that CLI's skill directory. The `name` / `description` frontmatter is the skill's metadata (description affects when the skill triggers — keep the original name).

### 8.1 The Three Decision Questions

Ask yourself before dispatching: ① can it truly run in parallel? ② is splitting faster? ③ does precision matter? If any one fails → do it yourself; if all pass → dispatch in parallel.

### 8.2 Parallel Fan-out Main Flow

```
session_create → report_subscribe (subscribe) → agent_assign × N → queue_pending collects reports → session_get to aggregate → session_delete to finish
```

- `agent_assign` returns queued immediately — **no manual polling needed**;
- There is exactly one orchestration path for completion notifications: MCP `report_subscribe` → reports persist to the meta-agent's `queue_pending` (survives service restarts);
- External WS monitoring (`/ws/agent`, `packages/mcp/monitor_workers.py`) is only for testing/troubleshooting/external coordinators;
- Pass `task_id` (a uuid-like idempotency key) so retries never run twice;
- Zombie notification: when a managed Session's Worker dies abnormally mid-task, you get a `{"status":"error","type":"zombie",...}` report (idle reclamation after normal completion does not report).

### 8.3 trust-but-verify Acceptance

Before merging reports, verify each change and run tests. To read results: `session_get(session_id)` → `lastResult.status` (`queued`/`running`/`done`/`error`/`pending`) and the `result` field.

### 8.4 Parallel Worktrees

When multiple Workers modify one project, point all Sessions' `workdir` at the same project directory via **absolute paths** (or use separate git worktrees) to avoid commit conflicts. `workdir` defaults to `data/workdirs/<name>` (relative base = the data root of the actually-running Pan instance; trust the `workdir` field returned by `session_create`).

### 8.5 Serial Dependencies

The blocking handoff was removed (2026-08-26). Serial = dispatch then subscribe to the report; the report landing in `queue_pending` is the "next step" signal — "waiting" is the meta-agent's idle state, not a blocking call.

### 8.6 Cleanup

After finishing, `session_delete` / `session_batch_delete` to free processes and disk; the watchdog only reclaims processes, never Sessions. Clean up sessions you no longer need in a timely manner.

---

## 9. Channels: Web / QQ / Remote

### 9.1 Web

Main channel: Dashboard (Chapters 3/4) + `/ws` + HTTP API (§12.2).

### 9.2 QQ (QQ Bridge)

Control Pan from QQ: message the Bot in QQ, and the message becomes a Worker instruction.

- Dependencies: `packages/qq/requirements.txt` (nonebot2 + onebot adapter + httpx), running on a **dedicated interpreter** (NoneBot is not installed in the project .venv; `setup.bat` probes and writes `qq.python`).
- Gateway: NapCat (forward WS, port 3001) or LLOneBot (3002), chosen via `qq.channel`; the WS address goes in `ONEBOT_WS_URLS` in `packages/qq/.env` or `qq.<channel>.ws_urls` in config.
- Startup: `python main.py` auto-starts/stops the QQ bot according to `qq.enabled` (PID written to `data/qq_bot.pid`); degrades gracefully when NapCat is unreachable (reconnects every 3s).
- Modes: `mirror` (auto-creates a Session and replies on message) / `selective` (messages only go to the inbox, handled by the meta-agent via the pan-qq MCP).
- Orchestration hookup: `session_qq_subscribe` (§7.8) receives inbox reminders; `command_routes` in `manifest.json` can route QQ prefix commands straight to an external HTTP API (bypassing the LLM).

### 9.3 Remote (Cloudflare Tunnel)

Access the console from the public internet while away:

```bash
python -m packages.remote        # or scripts/start_cf.ps1
```

`remote.quick_tunnel=true` prints a temporary `*.trycloudflare.com` URL; `false` requires `remote.config_path` pointing to the named tunnel's config.yml (the public domain comes from its `ingress.hostname`). Status service: `curl http://127.0.0.1:8769/status`. The tunnel forwards Pan's main port — the public side is equally **unauthenticated** (§11).

---

## 10. Troubleshooting

| Symptom | Cause & fix |
|---------|-------------|
| Worker status dot disappears / becomes `null` | Reclaimed by the watchdog (idle/silence/task timeout). Just send another task or click Start — it auto-rebuilds and restores context (Session data intact) |
| No reply for a long time | Check the top-bar status: `idle` = done but unread (just read the chat stream); `running` and over time — may have been reclaimed. Reclamation only kills the process, never the Session |
| Timeout config not taking effect | After changing `worker.*`, restart or hot-reload (UI global settings or `POST /api/config/reload`, scope `worker`) |
| Queue not being consumed | When `queue_pending` is non-empty but no Worker is live, the global watchdog (30s tick) auto-starts one; if it still sits, check `data/logs/pan.log` for watchdog/branch logs |
| Port in use | Change `port` or `PAN_PORT`; make sure the old instance was tree-killed via `stop_pan.bat` |
| No output in chat | Check whether the message area is scrolled to the bottom / a panel is collapsed; tool rows need a click to expand (§4.3) |
| QQ won't connect | Check NapCat/LLOneBot is running and `ONEBOT_WS_URLS` / `qq.<channel>.ws_urls` point at the right ports (3001/3002); if the QQ bot crashes, look at the startup warning in `data/logs/pan.log` (Pan Core is unaffected) |
| First task on a character session stutters | embedding first load / network retry; wait for the 15s timeout to degrade, or set `memory.enabled: false` |
| Worker reports "Worker process dead" | The process crashed or was reclaimed; Start again / resend the task (context auto-restores) |
| workdir left behind after deleting a Session | By design (delete does not remove the directory); clean up manually if needed; the native CLI session can be re-imported |
| New users see a blank `/vanilla` | The legacy Vanilla frontend is deprecated and not built (needs `npx tsc` from the project root); use `/react/` instead |

---

## 11. Security & Ops Notes

- **The API has no auth**; binding to `127.0.0.1` by default is intentional. Changing `PAN_HOST` to non-loopback exposes every endpoint to the network (a warning is printed at startup). `pan_access` isolation only applies at the MCP layer; HTTP/frontend have the highest privileges.
- **config.json is gitignored**: the port, QQ token (`ONEBOT_ACCESS_TOKEN`), `remote.config_path`, etc. all live there — don't commit them; credentials never enter the codebase.
- **On-disk data locations** (all relative to the repo root): `data/sessions/` (Session metadata + `.history.jsonl`), `data/workdirs/` (default work directories), `data/mcp-configs/` (per-session MCP configs), `data/characters/`, `data/memory/` (SQLite memory store), `data/logs/pan.log`, `data/qq_bot.pid`. For backup/migration, copy the directories as a whole.
- **Memory dependency degradation**: `minimal-requirements.txt` doesn't include the ML chain; vector search needs `sentence-transformers` — if missing, lazy-loading degrades gracefully without affecting Core; missing `jieba` notably lowers Chinese retrieval quality.
- **Remote public exposure**: the Cloudflare Tunnel side has no auth — the public internet can reach all APIs; enable only if you understand the risk, and consider stacking external protection such as Cloudflare Access.
- **git worktree scenario**: a worktree has no `.venv` of its own; use the main repo's interpreter.

---

## 12. Developer & API Reference

> This chapter is for development/integration scenarios (scripts, external Agents, custom frontends). Regular users usually don't need it; for day-to-day operations use the UI from Chapters 3/4.

### 12.1 MCP Tool Layer

#### 12.1.1 Connection Methods

**Method A: Auto-injected inside a Session (recommended)** — specify `mcpServers: ["pan"]` when creating the Session (or use a template that ships MCP, such as SMA); the adapter auto-generates `data/mcp-configs/<session_id>.mcp.json` at spawn and injects it via `--mcp-config`, while also writing the `PAN_AGENT_SESSION_ID` / `PAN_AGENT_SESSION_TITLE` env vars (tools use these to identify the caller). Per-adapter injection: cbc/claude write `--mcp-config`; kimi uses a session-isolated home (`--kimi-home`); opencode uses a project-level `opencode.json`; codex injects inline via `-c mcp_servers.*`.

**Method B: Standalone process (any MCP client)**:

```bash
# stdio (local CLI client; declare the command in .mcp.json / --mcp-config)
PAN_API_URL=http://127.0.0.1:8768 python -m packages.mcp.server --transport stdio

# SSE / streamable-http (remote or multi-client, default port 9740, path /sse)
python -m packages.mcp.server --transport sse --port 9740
```

Backend address priority: `--pan-url` arg > `PAN_API_URL` env var > `http://127.0.0.1:8768`. A standalone process has no `PAN_AGENT_SESSION_ID`, so identity-dependent tools (claim / report_subscribe / manager_chain etc.) are unavailable.

> **Three-way alignment**: the MCP server's target port (`PAN_API_URL`) must be the same instance as the one hosting `PAN_AGENT_SESSION_ID`, otherwise `report_subscribe` / `qq_bind` fail.

#### 12.1.2 `pan` Server Tools (35)

Naming tiers: `agent_*` are first-class tools (addressed by session_id, tolerant of no live process); `worker_*` are compatibility aliases (DEPRECATED) — only `worker_id` process addressing is a legacy-only path; new code always uses `agent_*`.

**Session management (15)**

| Tool | Key parameters | Description |
|------|----------------|-------------|
| `session_create` | `name` (required, unique), `adapter?`/`model?`/`permission_mode?`/`workdir?`/`session_template?`/`character_id?`/`system_prompt?`/`pan_access?` | Create a session (no spawn); workdir defaults to `data/workdirs/<name>`, use absolute paths outside Pan |
| `session_import` | `action` (`list_projects`/`list_workspaces`/`list_sessions`/`import`), `adapter?`, `cwd?`/`project_dir?`, `session_id?` | Import external CLI history sessions (cbc/kimi/opencode...); creates the Session without spawning |
| `session_list` | `summary?` | List all sessions; `summary=true` returns only compact fields (preferred for patrols — avoids blowing up output with full history) |
| `session_managed` | — | Summary of sessions the caller manages (needs `PAN_AGENT_SESSION_ID`) |
| `manager_chain` | — | The caller's upstream manager chain |
| `session_get` | `session_id`, `limit?` | Details (history + lastResult) |
| `session_update` | `session_id`, `model?`/`permission_mode?`/`always_thinking_enabled?`/`effort?`/`max_thinking_tokens?`/`mcp_servers?`/`game_id?` | PATCH wrapper; changing mcp_servers returns `requireRestart: true` (idle worker respawns to apply) |
| `session_delete` | `session_id` | Delete and kill the worker (does not delete workdir) |
| `session_batch_delete` | `session_ids` | Batch delete (each passes the managed-isolation check) |
| `session_handoff` | `session_id`, `handoff_prompt` (required), `copy_settings?`(=true), `adapter?`/`model?`/`permission_mode?` | Body-double handover (§7.6) |
| `session_claim` / `session_claim_many` | `session_id` / `session_ids` | Claim (auto report_subscribe; rejected if already managed by someone else) |
| `session_unclaim` / `session_unclaim_many` | same as above | Remove managed (auto-unsubscribe) |
| `session_history` | `session_id`, `limit?=50`, `before?` | Paginated history |

**Agent orchestration (7, first-class)**

| Tool | Parameters | Description |
|------|------------|-------------|
| `agent_spawn` | `session_id`, `adapter?`, `model?` | Spawn a Worker; kills an existing one first; spawn claims immediately (auto claim) |
| `agent_task` | `session_id`, `text`, `source?` | Send a task; auto-spawns if no live Worker |
| `agent_assign` | `session_id`, `text`, `task_id?` | **Async dispatch** (default first choice for new tasks), taskId idempotent |
| `agent_send` | `session_id`, `text` | Queues without interrupting; persistent queue if no live Worker |
| `agent_send_force` | `session_id`, `text` | restart + send, takes effect immediately |
| `agent_kill` | `session_id` | Kill the Worker (data preserved; harmless no-op if no live Worker) |
| `agent_list` | `summary?` | Alias of `session_list` |

**Worker compatibility aliases (7, DEPRECATED)**: `worker_spawn` / `worker_task` / `worker_assign` / `worker_send` / `worker_send_force` / `worker_kill` / `worker_list` — delegate internally to the same `agent_*` implementations; `worker_id` process addressing is the legacy path.

**Subscribe / QQ / others (6)**

| Tool | Parameters | Description |
|------|------------|-------------|
| `report_subscribe` | `session_id` | Subscribe to completion reports (**subscribe = manage**, auto claim) |
| `report_unsubscribe` | `session_id` | Unsubscribe (only sessions you manage) |
| `session_qq_subscribe` / `session_qq_unsubscribe` | `target_type` (`"user"`/`"group"`), `target_id` | Subscribe/unsubscribe QQ inbox reminders (`@@@@by qq` into the inbox) |
| `model_list` | `adapter?` | List the adapter's available models |
| `pan_handbook` | — | Return the full `docs/skills/pan/SKILL.md` (call it first during cold-start) |

#### 12.1.3 `pan-qq` Server Tools (6, `packages/qq/mcp.py`)

`qq_send_message` / `qq_read_conversation` / `qq_read_inbox` / `qq_list_contacts` / `qq_bind` / `qq_unbind`. In `selective` mode the meta-agent uses these for selective QQ send/receive; after `qq_bind`, new messages are pushed into `queue_pending` as `@@@@by qq` reminders. The SMA template mounts it by default.

#### 12.1.4 Security Model (MCP layer)

No traditional auth; relies on "identity injection + managed isolation": a Session's `pan_access` has three capability bits — `restrict_to_managed` / `can_claim_unmanaged` / `auto_claim_created` (all False by default). Restricted callers operating on others' Sessions get `permission_denied`; spawn/task/assign/send carry "dispatch = manage". Note these limits are **enforced only at the MCP layer**; the HTTP API doesn't check them (see §11).

### 12.2 HTTP/WS API

Base `http://127.0.0.1:<port>`; all responses are JSON; failures are usually HTTP 200 + `{"error": "..."}`. The full 69-endpoint list is in the README's "API Overview"; this section covers the main endpoints with examples. **Request bodies use camelCase; MCP params use snake_case** (e.g. HTTP `sessionId` ↔ MCP `session_id`; the create response calls it `id`, but subsequent request bodies always use `sessionId`).

Minimal flow (the UI equivalent is Chapter 3):

```bash
BASE=http://127.0.0.1:8768

# 1. Create a Session (session only, no Worker)
curl -X POST $BASE/api/sessions -H "Content-Type: application/json" \
  -d '{"name":"fix-h1","adapter":"cbc","model":"hy3"}'
# → full session object; note the returned "id" (ses_...) and "workdir"

# 2. Dispatch a task asynchronously (auto-spawns if no live Worker, returns immediately)
curl -X POST $BASE/api/assign -H "Content-Type: application/json" \
  -d '{"sessionId":"ses_xxxx","text":"Fix the null pointer in utils.py and pass the tests"}'
# → {"status":"queued","workerId":"worker-1","sessionId":"ses_xxxx"}

# 3. Read the result (poll lastResult.status: queued → running → done/error)
curl $BASE/api/sessions/ses_xxxx

# 4. Cleanup: delete the Session (also kills the Worker; note: does not delete the workdir)
curl -X DELETE $BASE/api/sessions/ses_xxxx
```

> **Windows curl note**: inline Chinese bodies error with `{"detail":"There was an error parsing the body"}` due to terminal encoding (GBK). Always use `--data-binary @body.json` (UTF-8) or python requests for Chinese text.

#### 12.2.1 Session Management

| Method + path | Purpose |
|---------------|---------|
| `GET /api/sessions` (`?summary=1`) | List all (summary compacts; full history truncated to the last 50) |
| `POST /api/sessions` | Create (body: `name`/`adapter`/`model`/`permissionMode`/`workdir`/`sessionTemplate`/`systemPrompt`/`alwaysThinkingEnabled`/`effort`/`maxThinkingTokens`/`outputMode`/`panAccess`/`characterId` etc., all optional) |
| `GET /api/sessions/{id}` | Details (`lastResult`/`workerStatus`/`managedBy`/`reportSubscriptions` etc.) |
| `GET /api/sessions/{id}/history?limit=50&before=<cursor>` | Paginated history |
| `PATCH /api/sessions/{id}` | Update settings (model/effort/MCP etc.; idle Workers respawn automatically, running ones get `pending_restart`) |
| `POST /api/sessions/{id}/rename` | Rename (body `{"name"}`; written back to the adapter's native storage) |
| `POST /api/sessions/{id}/branch` | Fork branch (§7.4) |
| `POST /api/sessions/{id}/handoff` | Body-double handover (§7.6) |
| `DELETE /api/sessions/{id}` / `POST /api/sessions/batch-delete` | Delete / batch delete |
| `GET /api/sessions/{id}/managers` | Manager chain |

#### 12.2.2 Workers and Task Delivery

```bash
# spawn (sessionId required; kills an existing Worker first)
curl -X POST $BASE/api/spawn -d '{"sessionId":"ses_xxxx"}'
# send a task (addressed by workerId or sessionId; auto-spawns if no live Worker)
curl -X POST $BASE/api/task -d '{"sessionId":"ses_xxxx","text":"..."}'
# list running Workers
curl $BASE/api/list
# kill a Worker
curl -X POST $BASE/api/kill/worker-1
```

Others: `POST /api/worker/{id}/restart|settings|rename|branch|interrupt|takeover`, `GET /api/worker/{id}/takeover-command`.

#### 12.2.3 Orchestration Endpoints

`POST /api/assign`, `POST /api/send` (`force:true` = force), `POST /api/claim` / `POST /api/unclaim` (body `{"managerId","sessionId"}`), `POST /api/report-subscribe` / `POST /api/report-unsubscribe` — semantics in §7/§8.

#### 12.2.4 Import / Settings / Manifest / Memory / Files

| Category | Endpoints |
|----------|-----------|
| Generic import | `GET /api/adapters/{adapter}/sessions`, `POST /api/adapters/{adapter}/sessions/import` (claude/codex use this) |
| cbc/kimi/opencode import | `GET /api/cbc/projects`, `GET /api/cbc/sessions`, `GET /api/cbc/browse`, `POST /api/cbc/sessions/import`; `GET /api/kimi/workspaces`, `GET /api/kimi/sessions`, `POST /api/kimi/sessions/import`; `GET /api/opencode/sessions`, `POST /api/opencode/sessions/import` |
| Models/Adapter | `GET /api/models?adapter=cbc`, `GET /api/adapter/config?adapter=cbc`, `GET /api/adapters`, `GET /api/cli/status` (Agent CLI availability diagnostics) |
| Settings | `GET`/`PUT /api/settings/ui` (App Settings display settings); `POST /api/config/reload` (hot-reload config.json, `{"scope":"adapters"\|"worker"\|"all"}`); `POST /api/manifest/reload` (hot-reload manifest) |
| Templates/MCP | `GET /api/session-templates` (`GET /api/characters/profiles` is its deprecated alias), `GET /api/mcp/servers`, `GET /api/manifest/command-routes` |
| Character | `GET`/`POST /api/characters`, `GET`/`DELETE /api/characters/{id}` |
| Memory | `POST /api/memory/index`, `GET /api/memory/search?q=`, `GET /api/memory/stats`, `POST /api/memory/inject` |
| File system | `GET /api/fs/list`, `GET /api/fs/read`, `POST /api/fs/write`, `POST /api/fs/rename`, `POST /api/fs/delete` (limited to the session workdir; rejects `..` escapes; 5 MiB per file) |
| QQ | `POST /api/qq/subscribe`, `POST /api/qq/unsubscribe`, `POST /api/qq/notify`, `GET /api/qq/contacts` |

#### 12.2.5 WebSocket

| Endpoint | Purpose |
|----------|---------|
| `ws://127.0.0.1:{port}/ws` | Dashboard channel: receives all broadcast events; the only client message is `{"type":"user_inject","sessionId":"...","text":"..."}` (send a task, auto-spawns if no Worker) |
| `ws://127.0.0.1:{port}/ws/agent` | Meta-Agent channel: pushes only `worker.result` by default; supports subscribe filtering + reconnect backfill, and can send task/spawn/assign/send/kill directly |

`/ws/agent` client message examples:

```json
{"type": "subscribe", "eventTypes": ["worker.result", "worker.zombie"], "sessionIds": ["ses_xxxx"]}
{"type": "reconnect", "sessionIds": ["ses_xxxx"]}
```

Omitted/empty `eventTypes` = default `["worker.result"]`; `["*"]` subscribes to everything. Full broadcast event set: `worker.stream` / `worker.result` / `worker.status` / `worker.spawned` / `worker.crashed` / `worker.zombie` / `worker.destroyed` / `worker.restarted` / `worker.reconfigured`, `session.created` / `session.updated` / `session.renamed` / `session.deleted` / `sessions.deleted`, `error`. `worker.result` looks like:

```json
{"type": "worker.result", "workerId": "worker-1", "sessionId": "ses_xxxx",
 "status": "done", "result": "...", "taskId": "...", "taskSeq": 3}
```

### 12.3 Multi-CLI Adapters

Adapter protocol (`packages/core/adapters/base.py`) + registry (`registry.py`). Five built-in adapters:

| Adapter | CLI | Execution mode | Resume/Fork | MCP injection | Notes |
|---------|-----|----------------|-------------|---------------|-------|
| `cbc` | CodeBuddy CLI | stream + oneshot (the only dual-mode) | ✔ / ✔ (`--fork-session`) | `--mcp-config` | Main adapter; native JSON stream protocol |
| `kimi` | Kimi CLI | stream (long-running wrapper) | ✔ / ✔ | session-isolated home (`--kimi-home`) | thinking mode controlled by its own config.toml |
| `opencode` | OpenCode CLI | stream (wrapper) | ✔ / ✔ | project-level `opencode.json` | |
| `claude` | Claude Code CLI | one-shot | ✔ | `--mcp-config` | per-task `claude -p --output-format stream-json` |
| `codex` | OpenAI Codex CLI | stream (wrapper) | ✔ | `-c mcp_servers.*` inline (zero file pollution) | |

Execution modes: `stream` is a long-running process (messages written to stdin, can mount MCP); `oneshot` starts a one-off process per task (enabled with `outputMode: "oneshot"`, only where the adapter declares support). Special behaviors: `docs/references/cli-adapter-special-behaviors.md`.

Frontend note (dual-frontend maintenance convention): **React Dashboard is the only maintained and recommended frontend** (source `packages/web/src/` → `pnpm build`); the legacy Vanilla frontend is deprecated (source `packages/web/ts/app.ts` → compiled from the project root via `npx tsc`; artifacts are gitignored and must not be edited directly); the `/vanilla` route remains accessible as a fallback, but new users are advised not to use it.

---

## 13. Related Documents

- `README.md` — project overview, selling points, 69-endpoint API index
- `docs/skills/pan/SKILL.md` — single source of truth for orchestration knowledge (Meta-Agent cold-start manual; `pan_handbook` returns its full text over MCP)
- `docs/skills/pan/references/http-api.md` / `ws-protocol.md` — HTTP/WS technical details and polling backstop strategy
- `docs/references/cli-adapter-special-behaviors.md` — per-CLI special behaviors
- `importantInfo.md` — quick reference for ports and startup order
