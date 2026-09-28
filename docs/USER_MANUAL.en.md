# Pan User Manual

> For Pan users: installation, the Dashboard, Session and Workspace management, MA orchestration, Jobs, and lifecycle recovery. UI labels follow the current React Dashboard.

**[中文](./USER_MANUAL.md) · English**

**Contents:** [Pan basics](#overview) · [Install and start](#install) · [Create a Session](#first-session) · [Sessions and Workspaces](#workspaces) · [Relationships and access](#relationships) · [MA and reports](#orchestration) · [Jobs](#jobs) · [Workers and recovery](#lifecycle) · [QQ / SnowLuma](#qq) · [Integrations, troubleshooting, and security](#integrations)

<a id="overview"></a>
## 1. Pan basics

Pan manages long-lived CLI Agent identities separately from temporary processes. A **Session** keeps history, adapter, model, workdir, relationships, and queues. A **Worker** is the process that runs the CLI; it can stop and restart while its Session remains.

| Term | Meaning |
|---|---|
| Session | A persistent conversation identity that can carry an MA or TA role |
| Worker | A temporary CLI process running a Session; stopping a Worker does not delete the Session |
| Adapter | Integration for a CLI such as cbc, kimi, opencode, claude, or codex; availability depends on local installation and PATH |
| MA / TA | The MA (meta-agent) decomposes, assigns, and accepts work; a TA (task-agent) carries out a task. Both are Session roles |
| Workspace / workdir | A Workspace organizes Sessions and shared directories; `workdir` is the Agent's actual working directory |

Use a regular Session for a single task. Use an SMA template when one Session should assign work to several Sessions and collect their reports.

<a id="install"></a>
## 2. Install, start, and ports

### Prerequisites

- Python 3.10 or newer.
- Node.js 20 or newer and pnpm 9.7 to build the React Dashboard.
- At least one supported CLI on the PATH visible to Pan: `cbc`, `kimi`, `opencode`, `claude`, or `codex`. Missing CLIs do not prevent the Pan service from starting, but their Adapters cannot start Workers.

### Install and start

Run these commands from the repository root. Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r minimal-requirements.txt
Copy-Item config.example.json config.json
Set-Location packages/web
pnpm install
pnpm build
Set-Location ../..
python main.py
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r minimal-requirements.txt
cp config.example.json config.json
cd packages/web && pnpm install && pnpm build && cd ../..
python main.py
```

Open <http://127.0.0.1:8768>. You can also use the startup helpers: Windows `scripts\start_pan.bat`; macOS/Linux `bash scripts/start.sh`. The corresponding normal exit helpers are `scripts\stop.bat` and `bash scripts/stop.sh`. For a foreground process, run `python main.py` and press Ctrl+C to exit.

`minimal-requirements.txt` installs Pan Core/API/MCP dependencies, not the QQ bridge or Memory machine-learning dependencies. In the example config, `qq.enabled` defaults to `true`; set it to `false` in `config.json` to skip the QQ child process when you do not use QQ. To use QQ, install `packages/qq/requirements.txt` into the interpreter configured by `qq.python` or `PAN_QQ_PYTHON`, then configure a gateway. See the [QQ and SnowLuma guide](QQ_PLUGIN_SNOWLUMA.en.md). Optional Memory dependencies are in `memory-requirements.txt`.

| Setting | Current default / purpose |
|---|---|
| Pan port | `config.json` defaults `port` to `8768`; `PAN_PORT` overrides it |
| Listen address | `127.0.0.1` by default; `PAN_HOST` overrides it |
| `PAN_API_URL` | Address used by a standalone MCP server to reach Pan; keep it aligned if you use a non-default port |
| Remote | `remote.enabled` is `false` in `config.example.json` |

Identity-dependent MCP tools require `PAN_AGENT_SESSION_ID` to belong to that Pan instance. For `report_subscribe`, the MCP URL, `PAN_API_URL`, and the Session's Pan port must point to the same instance.

<a id="first-session"></a>
## 3. Create and use your first Session

1. Open the Dashboard. **New** in the sidebar creates a Session with defaults; the adjacent settings button opens the full creation form. **Import** browses and imports supported CLI histories.
2. In the full form, set a name and Adapter, plus any Session Template, model, permission mode, or workdir you need. The selected Adapter can start only when its CLI is available.
3. Select the Session and click **Start** in the top bar. You can also send a message directly; Pan will try to start a Worker if none is live.
4. Enter a task in Chat. Press Enter to send and Shift+Enter for a newline. Messages added while a Worker is busy enter its send queue and are handled when the current work yields.
5. Use **Editor** to inspect files in the Session workdir. The top bar also has Worker controls such as **Restart, Interrupt, Takeover, and Kill**. These affect the Worker, not the Pan main service.

The default workdir is on the Pan server. For parallel changes to one repository, give each Session a different Git worktree; Pan does not create worktrees automatically.

<a id="workspaces"></a>
## 4. Session lists and Workspaces

### Work with the sidebar

- Search filters Sessions. Sorting can use recent activity, name, or custom order; grouping can use workdir or manager relationships.
- Right-click a Session for actions such as **Pin / Unpin, Rename, Branch, Manage, msgBridge, Details, Select, and Delete**. **Reimport** is available for Sessions linked to imported CLI history.
- Pinned Sessions appear ahead of unpinned Sessions in the current group. When dragging is enabled, use the handle to change custom order or reorder pinned items within a group; the Dashboard shows feedback for the current drop target.
- **Select** mode supports moving multiple Sessions to a Workspace and batch deletion. If a selected Session has managed children, read the confirmation and choose whether to cascade.

### Organize with Workspaces

The Workspace rail on the left lists **All** and named Workspaces. You can create, rename, reorder, switch, or delete a Workspace. **All** is only a list scope; it does not assign Sessions to a Workspace. Use **Manage → Workspaces** to move a management-tree root Session into a Workspace. Managed children inherit the Workspace along their manager chain.

A Workspace is not a Git branch or a directory move. A Session's `workdir` determines where its Agent works; a Workspace stores grouping and directory references. In **Editor → Directories**, add a directory shared by the current Workspace or add a temporary directory. Adding or removing a reference does not create, move, or delete the directory on disk.

<a id="relationships"></a>
## 5. Relationships and access

The **Manage** panel edits a Session's management tree, Workspace, Pan Access, and MCP servers. If `ses_parent` manages `ses_child`, the parent's managed list contains the child and the child's `managedBy` points to the parent. A Session can have only one manager at a time.

- **Manage** establishes the relationship and also subscribes to completion reports. **Managed** removes the relationship and report subscription; it does not delete the child Session.
- **Subscribe / Subscribed** controls completion reports independently. Unsubscribing leaves the relationship in place.
- `session_readonly` lets the current manager temporarily block tasks, messages, and notices from other Sessions. It is not filesystem read-only or HTTP authentication.
- **Pan Access** settings `restrictToManaged`, `canClaimUnmanaged`, and `autoClaimCreated` limit Sessions available through MCP calls. They do not restrict direct Dashboard management.

<a id="orchestration"></a>
## 6. Create an MA and collect task reports

In the full creation form, new users can choose:

- `SMA(NoAdapter)`: loads the SMA orchestration template and lets you select the CLI Adapter.
- `SMA(cbc)`: uses the same SMA template with cbc selected.

Both current templates configure the `pan`, `pan-qq`, and `pan-wechat` MCP servers. The template provides MA guidance; each tool still depends on its CLI and optional channel dependencies.

A common flow subscribes to a child Session before assigning its task:

```text
report_subscribe → agent_assign → Worker completes or errors → manager queue_pending → acceptance
```

Use `agent_assign` for new work. Its `queued` response means accepted, not completed. `agent_send` queues follow-up context without interrupting the current task. `agent_send_force` is for an urgent message that needs a restart to be delivered. `agent_notify` persistently reports a result from a background command or external task. Completion reports are stored in the manager's `queue_pending` inbox and can be read after a disconnect.

From the Dashboard, click **Subscribe** for the target under the manager's **Manage → Relationship → Manages**. This is different from a QQ inbox subscription under **msgBridge**.

An existing Agent CLI can also enable the `pan` MCP server and select servers for its Pan Session under **Manage → MCP and Plugins**. Restart the Worker after changing its MCP selection. A standalone MCP server without a Pan-injected Session identity cannot call identity-dependent management or reporting tools.

<a id="jobs"></a>
## 7. Jobs and scheduled tasks

Open **Jobs** in the sidebar to filter by status or type, inspect details and run records, and create, pause/resume, or delete Jobs. The current user form offers:

| Type | Use |
|---|---|
| **Scheduled task** | Assign work to a Session, send a Session message, or run a configured command on a schedule |
| **Session message** | Send text to one Session at a chosen time or on a repeat schedule |
| **Session broadcast** | Send the same text to several Sessions on a schedule |
| **Background process** | Immediately start an argv command on the host running Pan; it is not a Session Worker and is not parsed through a shell |

Schedules can use one-time triggers, repeat intervals, calendar/cron options, and similar form choices. The form previews upcoming fire times. You can run a scheduled task now, pause or resume it, and inspect run history or errors in its details. A task may become undeliverable if its target Session is removed or cannot be reached; inspect its details to resolve the target or Job.

The Jobs **Settings** tab controls retention periods for completed, failed, timed-out, and cancelled Jobs and their log files. New configurations do not automatically clean these records; cleanup requires an enabled rule with a number of days.

Command Jobs run on the Pan host with paths and permissions available to the service process. Check the executable and working directory before submitting a command. Background processes use argv, not a shell string.

<a id="lifecycle"></a>
## 8. Worker lifecycle, Exit, and startup recovery

**Start / Restart / Interrupt / Takeover / Kill** control the selected Session's Worker. If a Worker stops or the watchdog reclaims it, the Session and its history remain; you can start it again. Deleting a Session is a separate operation.

The main-service **Restart** and **Exit** controls are under App Settings → **General**. In supported launch modes, the settings page provides the main-service restart action; if it is unavailable, restart Pan through the same entry point used to start it. These controls are different from the Worker **Restart** in the top bar. The current default values for **Restart and Exit Session policy** and **Startup preference** are **Ask every time**. Restart or Exit stops live Workers; when a Session still has a persisted legal state of running, the configured policy can ask whether to mark it offline or preserve that state.

If Pan starts and finds Sessions still recorded as running but without a live Worker, the Dashboard shows **Continue previous Sessions?**, lists the candidates, and asks you to choose:

1. **Restart these Sessions**: send “继续” (continue) to each candidate through the normal Session message path.
2. **Keep their legal state as running**: do not start Workers; preserve the persisted state.
3. **Update legal state to current Worker state**: do not restart; read the actual Worker state and update the persisted state.

Change later startup behavior under App Settings → General. If you choose to sync actual state, the Dashboard still requires an explicit startup recovery choice. An unexpected power loss or process crash is not the same as a confirmed Pan Exit; check the candidate Sessions before choosing.

<a id="qq"></a>
## 9. QQ Bridge and SnowLuma

NapCat, LLOneBot, and SnowLuma can be selected as OneBot gateways under **App Settings → Plugin**. The plugin list shipped with the repository includes SnowLuma as an optional entry. All three plugins in the shipped list have `autoStart: false`, and SnowLuma is not selected by default in the example config. The `qq.enabled` bridge switch and gateway auto-start are separate settings.

**Select** on the Plugin page saves the gateway choice; Pan indicates that the main service must restart before the QQ bridge connects to it. **Start / Stop** only manage plugin processes started by Pan. **Start with Pan** applies only to the selected plugin. **Running (Pan owned)** means Pan owns the process; **Endpoint in use** means the port responds but Pan does not manage the process; **Not installed** means the registered install paths do not contain the required files.

See the [QQ and SnowLuma guide](QQ_PLUGIN_SNOWLUMA.en.md) for registry fields, WebSocket URL, token, prerequisites, and switching steps. A reachable gateway status does not confirm that desktop QQ is signed in or that real messages can be sent and received; follow the guide's end-to-end manual check.

<a id="integrations"></a>
## 10. Optional integrations, troubleshooting, and security

### Optional integrations

- **Memory** is not part of the minimal runtime dependencies. Install `memory-requirements.txt` dependencies before enabling it.
- **Remote** is disabled by default (`remote.enabled: false`). Enabling Cloudflare Remote creates an additional remote access path and expands the service's exposure.
- **MCP and orchestration references**: see the [Pan skill](skills/pan/SKILL.md), [HTTP API reference](skills/pan/references/http-api.md), and [WebSocket protocol](skills/pan/references/ws-protocol.md). These are advanced integration references, not required for first startup.

### Troubleshooting

| Symptom | What to check |
|---|---|
| A Session will not start | Check CLI status in the Dashboard, confirm the CLI is on Pan's PATH, and inspect the selected Adapter and permission mode |
| A Worker does not reply | Check Worker status and the send queue. If the message is queued, do not resend the same task repeatedly; inspect `data/logs/pan.log` if needed |
| An MA report is missing | Confirm the manager subscribed to the target and that the MCP server, `PAN_API_URL`, and `PAN_AGENT_SESSION_ID` belong to the same Pan instance |
| A Job does not fire | Check whether it is paused, its next fire time and timezone, and errors or delivery status in its details |
| SnowLuma shows Not installed | Check absolute `cwd`, launch command, and WebSocket URL in `data/qq_plugins/manifest.json` |

### Security and cleanup

The API has no authentication by default and binds to loopback. Do not expose it to an untrusted network by changing `PAN_HOST` or opening a remote tunnel alone.

Manual Session deletion stops its Worker and removes Session records and history, but preserves an ordinary workdir. A separate data-retention policy is disabled by default. If Session auto-retention is enabled, Pan may delete a default workdir only after verifying that it belongs to that Session and is not referenced by another Session or Workspace. Commit or back up important files before cleanup.

## Related documents

- [README](../README.en.md): project overview and quick start.
- [中文用户手册](./USER_MANUAL.md).
- [QQ and SnowLuma guide](./QQ_PLUGIN_SNOWLUMA.en.md).
