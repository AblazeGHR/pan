# Pan

Pan is a local web app for managing CLI Agent sessions and coordinating multi-agent work. Use the Dashboard to manage Sessions, workspaces, and Workers; add a meta-agent (MA) when you want one Session to assign work to others.

**[中文](./README.md) · English**

## What Pan does

- **Manage Sessions**: create, import, rename, branch, pin, and delete Sessions in one Dashboard. Built-in adapters cover cbc, kimi, opencode, claude, and codex; each requires its CLI to be installed locally.
- **Organize workspaces**: group Sessions, filter the list by workspace, and add shared workspace directories to the current Session's Editor. A Workspace is organizational metadata; it does not move or copy directories on disk. The Session `workdir` remains the Agent's working directory.
- **Coordinate Agents**: SMA templates provide an MA (supervisor) Session. An MA can create or claim child Sessions, dispatch work asynchronously, and collect completion or error reports from a durable inbox.
- **Track Jobs**: the Dashboard Jobs page lists scheduled tasks, scheduled Session messages or broadcasts, and immediate background processes.
- **Manage runtime state**: control Session Workers separately from the Pan service. After startup, choose how to handle Sessions still recorded as running when their Worker is absent.
- **Add integrations as needed**: configure MCP servers per Session and manage QQ OneBot gateways in App Settings → Plugin. Cloudflare Remote and Memory are optional; Remote is disabled by default.

Pan does not create a Git worktree for every child task. For isolated parallel code changes, assign each Session a different workdir or Git worktree.

## Quick start

### Prerequisites

- Python 3.10 or newer.
- Node.js 20 or newer and pnpm 9.7 to build the Dashboard.
- At least one supported CLI Agent available on the PATH seen by Pan: `cbc`, `kimi`, `opencode`, `claude`, or `codex`. Pan does not install these CLIs.

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

On macOS/Linux, activate a virtual environment and run the equivalent steps:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r minimal-requirements.txt
cp config.example.json config.json
cd packages/web && pnpm install && pnpm build && cd ../..
python main.py
```

The default Dashboard URL is <http://127.0.0.1:8768>. `config.json` sets `port` to `8768` by default; `PAN_PORT` overrides it. The service listens on `127.0.0.1` by default, and the API has no authentication by default.

In `config.example.json`, `qq.enabled` defaults to `true`, but this does not install or sign in to a QQ gateway. Set `qq.enabled` to `false` before startup if you do not use QQ. To enable QQ, install `packages/qq/requirements.txt` into the interpreter configured by `qq.python` / `PAN_QQ_PYTHON`, then select and configure a gateway using the [QQ and SnowLuma guide](docs/QQ_PLUGIN_SNOWLUMA.en.md).

For an already prepared checkout, use the helper scripts: Windows `scripts\start_pan.bat` / `scripts\stop.bat`, macOS/Linux `bash scripts/start.sh` / `bash scripts/stop.sh`. Run `python main.py` for a foreground process and press Ctrl+C to exit.

## Create your first Session

Open the Dashboard and click **New** in the sidebar for a quick Session. Use the adjacent settings button to choose an Adapter, Session Template, model, and workdir. Select the Session and click **Start**, or send the first message directly. Messages sent while the Worker is busy enter its send queue. Click **Import** to browse and import supported CLI histories.

Use a regular Session for a single Agent. To coordinate multiple Sessions, choose `SMA(NoAdapter)` (select an adapter after creation) or `SMA(cbc)` (pins cbc) in the full creation form. See the [English user manual](docs/USER_MANUAL.en.md) for details.

## Documentation

- [English user manual](docs/USER_MANUAL.en.md): install, Dashboard, workspaces, reports, Jobs, lifecycle, and troubleshooting.
- [中文用户手册](docs/USER_MANUAL.md)
- [QQ and SnowLuma guide](docs/QQ_PLUGIN_SNOWLUMA.en.md)
- [Pan orchestration and MCP handbook](docs/skills/pan/SKILL.md) for setting up an MA or external Agent.

## Security

Do not expose the unauthenticated service by changing its listen address to a public or LAN address. Cloudflare Remote provides a remote access path and must be enabled explicitly; review the access scope and gateway security before enabling it.
