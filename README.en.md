# Pan

> One entry point for all your tasks — talk to a single Meta-Agent and it decomposes and orchestrates a whole team of CLI Agent workers running in parallel.

**English · [中文](./README.md)**

Pan is a **CLI Agent orchestration platform**. Built on a Supervisor/Worker architecture, one "Meta-Agent" supervisor directs multiple Workers (each an independently running CLI Agent session) through MCP tools and WebSocket event streams. Each Worker works in its own git worktree, and you can command the platform from a web dashboard, QQ, a public tunnel, or any Agent CLI — and you can always watch, interrupt, or take over any Worker's terminal.

- **Tech stack**: Python + FastAPI + WebSocket + SQLite (FTS5 full-text search) + optional embedding-based vector search; frontend is dual-track: React (primary development) + Vanilla JS (stable fallback).

---

## Table of Contents

- [Introduction](#introduction)
- [Features](#features)
- [Core Concepts](#core-concepts)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Multi-CLI Adapters](#multi-cli-adapters)
- [Meta-Agent Orchestration](#meta-agent-orchestration)
- [Configuration](#configuration)
- [API Overview](#api-overview)
- [Channels & Integrations](#channels--integrations)
- [Operational Notes](#operational-notes)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## Introduction

Traditional one-to-one AI coding assistants work as "you say one thing, it does one thing." Pan upgrades this to **one-to-many**: you talk to a single supervisor, who orchestrates multiple Workers in parallel and consolidates the results into one deliverable for you.

Typical use cases:

- **Parallel tasks** — drive multiple subtasks of the same project, multiple projects, or even life chores (schedules, reminders, automation) at the same time;
- **Multiple CLIs** — hand different tasks to different CLI Agents and switch between them without losing context;
- **AI with memory** — hybrid vector + full-text retrieval injects relevant memory at startup, and a persona persists across Sessions;
- **Command from anywhere** — Dashboard / QQ / public tunnel / MCP are all entrances to the same control plane.

## Features

- **Meta-Agent orchestration (SMA)** — one supervisor runs the full loop: decompose → parallel dispatch → subscribed reporting → trust-but-verify acceptance → consolidated delivery.
- **Protocol-based multi-CLI adapters** — `CliAdapter` protocol + registry with **cbc / kimi / opencode / claude / codex** built in; the orchestration layer is unaware of the underlying CLI.
- **Session handoff (session_handoff)** — when switching CLIs, a twin session takes over, carrying the relationship graph / subscriptions / reports while keeping only a compact summary to avoid context bloat.
- **Managed subscription inbox** — subscription-based reports are delivered to an on-disk inbox; the supervisor "dispatches work, then checks the inbox." Reports survive disconnects and reconnects.
- **Self-healing Worker lifecycle** — `stream` / `one-shot` execution modes; a three-tier Watchdog timeout cleanup plus an on-disk queue that rebuilds Workers after an abnormal process exit.
- **Memory + Character** — SQLite FTS5 + embedding hybrid retrieval; a Character (persona) and its memory store persist across Sessions as the same identity.
- **Per-session MCP** — each Session can mount its own MCP Server; two servers ship built in: `pan` (27 orchestration tools) and `pan-qq` (6 QQ tools).
- **Multi-channel access** — Web Dashboard (React + Legacy dual-track), QQ Bridge (pluggable NapCat / LLOneBot channels), Cloudflare Tunnel, and any Agent CLI (WS + MCP).
- **Session import** — historical sessions from cbc / kimi / opencode / claude / codex can be imported and reused, avoiding re-exploration and re-initialization.

## Core Concepts

| Concept | Description |
|---------|-------------|
| **Session** | A persistent conversation container (`ses_<16hex>`), independent of the Worker lifecycle |
| **Worker** | A temporary CLI Agent subprocess bound to a Session; two forms: `stream` (long-running) and `one-shot` (single task) |
| **Meta-Agent (SMA)** | The supervisor role: does no work itself, only decomposes, dispatches, listens, and accepts |
| **CLI Adapter** | One protocol-based adapter per CLI Agent (cbc / kimi / opencode / claude / codex) |
| **session_handoff** | Creates a twin Session to take over an old one; relationship graph / subscriptions / reports follow |
| **assign** | Asynchronously dispatches a task (taskId idempotent); you receive a report when it completes |
| **report-subscribe** | Subscription-based reporting: a finished Worker auto-delivers its report to the supervisor's on-disk inbox |
| **claim** | Establishes a bidirectional supervisor ↔ Worker management binding |
| **branch** | Forks an independent branch from an existing Session, inheriting model / memory / tools |
| **takeover** | Takes an AI Session back into a human terminal |
| **Watchdog** | One per Worker: cleans up hangs / timeouts; a global-level Watchdog restocks Workers |
| **Memory** | Hybrid vector + full-text (FTS5) retrieval, auto-injected before work starts |
| **Character** | Persona + dedicated memory store, keeping the same identity across Sessions |
| **QQ Bridge** | Bridges QQ messages ↔ Worker commands; NapCat / LLOneBot channels are switchable |
| **Remote** | Cloudflare Tunnel exposing the control plane to the public internet |

## Quick Start

### Prerequisites

- Python 3.14 (current development environment: 3.14.5)
- Node.js + npm (to compile the legacy frontend)

### Install & Start

```bash
# 1. Install the minimal dependencies (core only, no Memory ML chain)
pip install -r minimal-requirements.txt

# 2. Generate configuration
cp config.example.json config.json
# Windows: copy config.example.json config.json
# Every field is optional; models are auto-detected when left empty

# 3. Compile the legacy frontend (TS source → static/js/app.js)
#    Must run from the project root (root tsconfig, not packages/web's React tsconfig)
npx tsc

# 4. Start
python main.py
# → http://127.0.0.1:8768
#   main branch defaults to 8768; test branch to 8767; override with PAN_PORT

# 5. Run tests
python -m pytest tests/ -q
```

### React Frontend (in development)

```bash
cd packages/web
pnpm install   # first time
pnpm build     # output → packages/web/dist/
pnpm dev       # dev mode: Vite HMR + proxy to backend
```

The serving route is controlled by the `frontend` field in `config.json`:

| `frontend` | Behavior |
|------------|----------|
| `coexist` (default) | `/` legacy frontend + `/react/` React SPA |
| `react` | React takes over `/` |
| `legacy` | Legacy frontend only |

> The backend API/WS evolves for React first; if a backend change breaks the legacy frontend, patch `ts/app.ts` to follow — do not constrain backend changes.

## Architecture

```
         Meta-Agent                   Human                     Remote access
    (Agent CLI / MCP)           (Dashboard)            (Cloudflare Tunnel)
          │                          │                          │
   /ws/agent + MCP tools       /ws + HTTP                Public URL + WS
    (event stream + commands) (observe + inject + takeover) (Dashboard / QQ Bot external access)
          │                          │                          │
          └──────────┬───────────────┘                          │
                     │                                          │
            ┌────────▼────────┐                                 │
            │  Pan Core         │◄──────────────────────────────┘
            │  (FastAPI service) │        HTTP / WebSocket
            │                   │
            │  Session Manager │
            │  ├─ Worker-1     │── CliAdapter protocol (cbc / kimi / opencode / claude / codex)
            │  ├─ Worker-2     │── ... (unaware of each other, routed by adapter name)
            │  └─ Worker-N     │
            │                   │
            │  Character framework │── profile → character → memory
            │  Memory subsystem    │── SQLite + FTS5 + embedding retrieval
            │  Event Bus           │─── WS broadcast
            │  Session Store       │─── JSON persistence
            └──────────────────┘
```

### Module Layout

| Directory | Responsibility |
|-----------|----------------|
| `packages/core/` | Core module: process management + message routing + Memory + Adapters. All external modules talk to Core only over HTTP/WS APIs |
| `packages/web/` | Web channel: FastAPI routes + WebSocket + Dashboard (69 HTTP endpoints) |
| `packages/qq/` | QQ channel: NoneBot2 bridge + pluggable channels + pan-qq MCP |
| `packages/mcp/` | MCP Server: 27 tools, can be started standalone |
| `packages/remote/` | Cloudflare Tunnel remote channel |
| `scripts/` | Start / stop / tunnel / pre-commit scripts |
| `docs/` | Documentation (git-tracked; `docs/skills/pan/SKILL.md` is the single source of truth for orchestration knowledge) |
| `tests/` | Tests (26 files) |

## Multi-CLI Adapters

Workers are decoupled from any specific CLI: each CLI Agent has an adapter implementing the `CliAdapter` protocol (`packages/core/adapters/base.py`; methods grouped into metadata / process spawn / message encoding / event parsing / takeover), registered by name in the registry (`packages/core/adapters/registry.py`) at startup.

| Adapter | CLI | Execution mode | Description |
|---------|-----|----------------|-------------|
| `cbc` | CodeBuddy CLI | stream + one-shot | Native JSON stream protocol; the primary adapter |
| `kimi` | Kimi CLI | stream (long-running wrapper) | Calls `kimi -p` per message inside a wrapper |
| `opencode` | OpenCode CLI | stream (long-running wrapper) | Calls `opencode run --format json` per message inside a wrapper |
| `claude` | Claude Code CLI | one-shot | Calls `claude -p --output-format stream-json` per message; MCP injected via `--mcp-config` |
| `codex` | OpenAI Codex CLI | stream (long-running wrapper) | Calls `codex exec --json` per message inside a wrapper; MCP injected inline via `~/.codex/config.toml` |

The companion `SessionsProvider` protocol (`packages/core/adapters/base.py`) unifies each CLI's native session storage (history / usage / title / fork) behind a single read/write interface. The server resolves the provider by adapter name, so adding a new CLI needs no per-CLI import / branch / rename dispatch logic (generic endpoint: `/api/adapters/{adapter}/sessions[/import]`).

Model configuration follows a "configure as little as possible" principle: in `config.json`, leaving `models` **empty auto-detects** the CLI's available models (cbc parses `--help`, kimi parses config.toml); **setting it restricts** the available models.

## Meta-Agent Orchestration

The Meta-Agent is not a special program but a **role** — any party (your Agent CLI, a script, or even another Pan session) can play the "supervisor" as long as it meets three conditions:

1. **Can send commands** — through MCP tools (27, e.g. `worker_spawn` / `worker_assign` / `worker_send` / `session_handoff`) or the HTTP API;
2. **Can receive intel** — by subscribing to the WebSocket event stream (`worker.result` / `worker.status` / `worker.crashed`…), or via subscription reports delivered to its own on-disk inbox;
3. **Has an identity** — Pan records who is commanding and isolates Workers to prevent privilege escalation.

Pan ships a built-in **SMA (Super Meta Agent) orchestration template** (`session_templates.SMA` in `manifest.json`): create a "super orchestration agent" session in one click, mounted with the Pan core MCP and the QQ channel MCP, with full permissions + auto-claim + auto-subscribe — a ready-to-use AI project manager.

### Orchestration Methodology

SMA's dispatching follows a methodology (encoded in `docs/skills/pan/SKILL.md`):

1. **Three decision questions** — decide whether to decompose: ① Can it truly run in parallel? ② Is it faster decomposed? ③ Does precision matter? If any answer is no → do it yourself; if all yes → dispatch in parallel;
2. **Parallel dispatch** — `worker_assign` fans out asynchronously to multiple Workers (each in its own git worktree to avoid commit conflicts) and returns immediately without blocking;
3. **Subscription-based reporting** — `report_subscribe` auto-delivers completion reports to the supervisor's on-disk inbox; reports survive disconnects and reconnects;
4. **Trust-but-verify acceptance** — before merging reports, check each change and run tests;
5. **Consolidated delivery** — collect all results and merge them into one deliverable.

### The Orchestration Layer Is Unaware of the Underlying CLI

The SMA talks to Workers only through MCP tools / WS event streams and neither knows nor cares which CLI runs underneath. So "which task goes to which CLI" is **configurable**: by writing model rules (system prompt) for the SMA, tasks can be routed by type — e.g. "heavy work to cbc, lightweight research to kimi, writing to opencode" — with no changes to the cluster itself.

## Configuration

The config file is `config.json` at the repo root (gitignored); the template is `config.example.json`. All fields are optional; omitted fields fall back to the defaults built into `packages/core/config.py`.

| Setting | Default | Description |
|---------|---------|-------------|
| `port` | 8768 | Main service port (main branch); test branch: 8767 |
| `frontend` | `coexist` | `coexist` / `react` / `legacy` |
| `cbc.model` | `deepseek-v4-flash` | Default cbc model |
| `cbc.models` | `[]` | Empty = auto-detect (parsed from cbc `--help`); set = restrict available models |
| `cbc.permission_mode` | `bypassPermissions` | cbc permission mode |
| `kimi.model` | `moonshot-cn/kimi-k2.6` | Default kimi model |
| `kimi.models` | `[]` | Empty = auto-detect (parsed from config.toml); set = restrict available models |
| `worker.timeout_sec` | 300 | Quiet-timeout kill for queued tasks / no-output read timeout (seconds) |
| `worker.task_timeout_sec` | 1800 | Max runtime for a stream-running task (long thinking / large file reads are not killed) |
| `worker.idle_sec` | 300 | Idle reclamation (seconds; held / zombie skipped) |
| `qq.enabled` | true | Whether to start the QQ bot (main.py spawns / terminates it based on this) |
| `qq.mode` | `mirror` | `mirror` full mirror auto-reply / `selective` selective sending (messages only enter the inbox, decided by the meta-agent via pan-qq MCP) |
| `qq.channel` | `napcat` | QQ channel: `napcat` / `llonebot` (pluggable OneBot 11 gateways) |
| `remote.enabled` | false | Whether to enable Cloudflare Tunnel |
| `remote.quick_tunnel` | true | true uses a temporary URL; false uses a named tunnel (requires `remote.config_path`) |
| `remote.status_port` | 8769 | Remote status service port |
| `logging` | INFO / `data/logs/pan.log` | Log level, rotation, console output |
| `plugin_manifests` | `["manifest.json"]` | External Character profile manifests |

**Environment variables**:

| Variable | Default | Description |
|----------|---------|-------------|
| `PAN_PORT` | — | Overrides `port` |
| `PAN_HOST` | `127.0.0.1` | Listen address |
| `PAN_URL` | `http://127.0.0.1:{port}` | Base URL used by the QQ Bridge to reach Pan Core |
| `PAN_API_URL` | `http://127.0.0.1:8768` | URL used by the MCP server to reach Pan Core |
| `PAN_QQ_API_URL` | `http://127.0.0.1:8080` | URL used by pan-qq MCP to reach the QQ bot |
| `PAN_QQ_PYTHON` | miniforge | Interpreter used for the QQ bot |
| `PAN_QQ_MODE` | — | Overrides `qq.mode` |
| `ONEBOT_WS_URLS` / `ONEBOT_ACCESS_TOKEN` | — | Override the QQ channel connection URL / token |

## API Overview

### HTTP (`packages/web/server.py`, 69 endpoints)

**Session management**

```
GET    /api/sessions                    → list all Sessions
POST   /api/sessions                    → create a Session
GET    /api/sessions/{id}               → get Session details
GET    /api/sessions/{id}/history       → get message history (paginated)
PATCH  /api/sessions/{id}               → update a Session (incl. requireRestart semantics)
POST   /api/sessions/{id}/rename        → rename
POST   /api/sessions/{id}/branch        → branch a Session
POST   /api/sessions/{id}/handoff       → session handoff (create a twin Session to take over)
DELETE /api/sessions/{id}               → delete a Session
POST   /api/sessions/batch-delete       → batch delete
```

**Worker management**

```
POST   /api/spawn                       → start a new Worker
POST   /api/task                        → send a task to a Worker
POST   /api/kill/{worker_id}            → stop a Worker
GET    /api/list                         → list active Workers
POST   /api/worker/{id}/restart         → restart a Worker
POST   /api/worker/{id}/settings        → update Worker configuration
POST   /api/worker/{id}/rename          → rename a Worker
POST   /api/worker/{id}/branch          → branch a Worker
POST   /api/worker/{id}/interrupt       → interrupt a Worker (running only)
POST   /api/worker/{id}/takeover        → take over a Worker terminal (restart + held)
GET    /api/worker/{id}/takeover-command → generate a takeover command (not executed)
```

**Orchestration**

```
POST   /api/assign                      → asynchronously dispatch a task (taskId idempotent)
POST   /api/report-subscribe            → subscribe to Worker reports (also establishes the managed relation)
POST   /api/report-unsubscribe          → unsubscribe from reports
POST   /api/claim                       → bind a managed relation
POST   /api/unclaim                     → unbind a managed relation (also unsubscribes reports)
```

**QQ binding**

```
POST   /api/qq/subscribe                → a Pan session subscribes to inbox updates of a QQ conversation
POST   /api/qq/unsubscribe              → unsubscribe
POST   /api/qq/notify                   → QQ plugin reports an inbox update
GET    /api/qq/contacts                 → recent QQ contacts / groups
```

**Character / Memory**

```
GET    /api/characters/profiles         → list available Profiles (session templates)
GET    /api/manifest/command-routes     → list QQ command routes
GET    /api/characters                  → list Characters
POST   /api/characters                  → create a Character
GET    /api/characters/{id}             → get Character details
DELETE /api/characters/{id}             → delete a Character
POST   /api/memory/index                → index a memory directory (.md → SQLite)
GET    /api/memory/search               → hybrid memory retrieval
GET    /api/memory/stats                → memory store statistics
POST   /api/memory/inject               → manually inject memory
```

**Filesystem (within the session workdir, with path-escape validation)**

```
GET    /api/fs/list                     → list a directory
GET    /api/fs/read                     → read a file
POST   /api/fs/write                    → write a file
POST   /api/fs/rename                   → rename
POST   /api/fs/delete                   → delete
```

**Adapters / Import**

```
GET    /api/models?adapter=cbc          → get the model list
GET    /api/adapter/config?adapter=cbc  → Adapter configuration
GET    /api/adapters                    → list available Adapters
GET    /api/adapters/{adapter}/sessions[/import] → generic session import / browse
GET    /api/cbc/projects                → CBC project list
GET    /api/cbc/sessions                → CBC Session list
GET    /api/cbc/browse                  → browse CBC Session files
POST   /api/cbc/sessions/import         → import a CBC Session
GET    /api/kimi/workspaces             → Kimi Workspace list
GET    /api/kimi/sessions               → Kimi Session list
POST   /api/kimi/sessions/import        → import a Kimi Session
```

### WebSocket

```
WS   /ws           Dashboard: receives user_inject only; broadcasts all events
WS   /ws/agent     Meta-Agent: subscribe (filter by eventTypes / sessionIds + replay on reconnect),
                   reconnect, task, spawn, assign, send, kill, list
```

Broadcast events: `worker.stream` / `worker.result` / `worker.status` / `worker.spawned` / `worker.crashed` / `worker.zombie` / `worker.destroyed` / `worker.restarted` / `worker.reconfigured`, `session.created` / `session.updated` / `session.renamed` / `session.deleted` / `sessions.deleted`, `error`.

### MCP Server (`packages/mcp/server.py`, 27 tools)

```
session_create / session_import / session_list / session_managed / session_get /
session_delete / session_batch_delete / session_handoff / session_claim /
session_claim_many / session_unclaim / session_unclaim_many / session_update /
session_history / session_qq_subscribe / session_qq_unsubscribe /
report_subscribe / report_unsubscribe /
worker_spawn / worker_task / worker_kill / worker_list / worker_assign /
worker_send / worker_send_force / model_list / pan_handbook
```

There is also a standalone **pan-qq MCP server** (`packages/qq/mcp.py`, 6 tools): `qq_send_message` / `qq_read_conversation` / `qq_list_contacts` / `qq_read_inbox` / `qq_bind` / `qq_unbind`.

Launch: `python -m packages.mcp.server --transport stdio|sse|streamable-http [--port 9740]` (default stdio; API address from `PAN_API_URL`).

## Channels & Integrations

### Web / Dashboard

- `http://127.0.0.1:{port}` — legacy Dashboard; `/react/` — React Dashboard
- `ws://127.0.0.1:{port}/ws` — Dashboard WebSocket
- `ws://127.0.0.1:{port}/ws/agent` — Meta-Agent WebSocket

### Meta-Agent (MCP)

Pan ships a built-in `pan` MCP server (27 tools). Any Agent CLI can act as a Meta-Agent by connecting over the MCP protocol (stdio / SSE / streamable-http), or by connecting to the `/ws/agent` WebSocket directly to subscribe to the event stream and issue commands. See the launch instructions under "[MCP Server](#mcp-server-packagesmcpserverpy-27-tools)".

### QQ Bridge

Dependencies are in `packages/qq/requirements.txt` (nonebot2 + onebot-adapter-onebot + httpx). To start:

1. Start your chosen gateway: NapCat (forward WS server, port 3001) or LLOneBot (port 3002), selected by `qq.channel` in `config.json`;
2. `python main.py` (or `scripts/start_pan.bat`) — main.py spawns / terminates the QQ bot automatically based on `qq.enabled` in `config.json` (`packages/qq/bot.py`, PID written to `data/qq_bot.pid`); no manual start is needed.

> Note: the QQ bot runs under the miniforge interpreter (NoneBot is not installed in the project .venv); override with `PAN_QQ_PYTHON`.

QQ access is abstracted as a switchable **Channel**: the `QQChannel` interface (`packages/qq/channels/base.py`) defines lifecycle / message callbacks / send & receive / contact queries; NapCat and LLOneBot are both thin subclasses of the OneBot 11 gateway (`packages/qq/channels/`). Business logic depends only on the interface, so switching gateways requires zero business-code changes.

`qq.mode` controls the bridging behavior: `mirror` (full mirror auto-reply, default) / `selective` (messages only enter the inbox + history, with replies decided by the meta-agent via pan-qq MCP). `command_routes` in `manifest.json` can declare QQ prefix commands that are forwarded directly to an external HTTP API (bypassing the LLM).

### Remote (Cloudflare Tunnel)

```bash
python -m packages.remote
# or scripts/start_cf.ps1
```

- `quick_tunnel: true` → prints a temporary `*.trycloudflare.com` URL; `false` → requires a named-tunnel yml at `remote.config_path`
- Status service: `curl http://127.0.0.1:8769/status`
- The public domain comes from `ingress.hostname` in the yml pointed to by `config_path`; the tunnel exposes the Pan main port (`config.port`)

## Operational Notes

- **Security model**: the API has no authentication and intentionally binds to `127.0.0.1` (loopback) by default. Setting `PAN_HOST` to a non-loopback address exposes every endpoint on the network (main.py warns on startup). Security focuses on boundary validation: workdir path-escape checks, character_id format checks.
- **Port quick reference**: Pan main service 8768 (main) / 8767 (test); Remote status 8769; NoneBot2 8080 (not public); NapCat 3001 / LLOneBot 3002.
- **Worker timeout semantics**: a stream-running task is judged hung by its **task runtime** (`worker.task_timeout_sec`, default 1800s); queued tasks use a quiet timeout (`worker.timeout_sec`, default 300s) — long thinking / large file reads are not falsely killed.
- **Worker dual mode**: `stream` (long-running; can mount MCP); `one-shot` (single task; only when `output_mode=oneshot`). Dispatching goes through `worker_assign` / `worker_send` (the blocking `worker_handoff` was removed on 2026-08-26; serial dependencies use assign + report_subscribe too).
- **Memory dependencies & degradation**: `minimal-requirements.txt` excludes the ML chain. Enabling vector search requires `sentence-transformers` (default embedding provider for the web frontend). When optional libraries are missing, lazy loading + ImportError fallback degrade gracefully without affecting Core startup; missing `jieba` notably degrades Chinese retrieval quality.
- **QQ bot process management**: main.py spawns / terminates the QQ bot based on `qq.enabled` (PID in `data/qq_bot.pid`); `scripts/stop_pan.bat` kills the exact process tree, not all python.exe.
- **No separate .venv in worktrees**: when testing / running inside a git worktree, use the main repo's `.venv`.
- **Python version**: the repo declares no version file (no pyproject.toml / .python-version); the actual runtime is Python 3.14.5.

## Documentation

- [`docs/skills/pan/SKILL.md`](docs/skills/pan/SKILL.md) — single source of truth for Pan orchestration knowledge (cold-start manual, MCP tool conventions, pitfalls & conventions)
- [`docs/design/`](docs/design/) — design docs (adapter architecture, kimi / opencode adaptation, one-shot mode, etc.)
- [`docs/plans&overviews/`](docs/plans&overviews/) — project planning & implementation records
- [`docs/references/`](docs/references/) — reference notes
- [`importantInfo.md`](importantInfo.md) — quick reference for ports and startup order

## Contributing

- Development uses a **git worktree parallel-branch** model: each feature is developed in its own worktree / branch and must pass tests before merging to main.
- **Dual-frontend conventions** (see `CODEBUDDY.md`):
  - The legacy source lives in `packages/web/ts/app.ts`; `static/js/app.js` is a compiled artifact (gitignored). **Never edit the artifact directly**; after editing, run `npx tsc` from the project root;
  - The React source lives in `packages/web/src/`; the output `dist/` is gitignored. After editing, run `cd packages/web && pnpm build`;
  - The pre-commit hook (`git config core.hooksPath scripts`) checks both: legacy (`tsc --noEmit`) and React (`pnpm build`).
- Run tests: `python -m pytest tests/ -q`.
- If you change MCP tools / HTTP API / workdir conventions, update `docs/skills/pan/SKILL.md` (the single source of truth).

## License

This repository does not currently include an open-source license file (LICENSE). Please contact the author to confirm the licensing terms before using, distributing, or modifying it.
