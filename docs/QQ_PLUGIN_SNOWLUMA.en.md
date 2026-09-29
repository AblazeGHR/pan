# QQ Bridge Plugins and SnowLuma

**[中文](./QQ_PLUGIN_SNOWLUMA.md) · English**

This guide explains how Pan selects and manages OneBot gateways. For SnowLuma installation, QQ client compatibility, and privacy or license requirements, follow the documentation for the installed SnowLuma release. Pan checks plugin files, process ownership, and WebSocket reachability only.

## Current defaults

- In `config.example.json`, `qq.enabled` defaults to `true`, which starts the QQ bridge child process. It does not mean SnowLuma is installed or will start automatically.
- SnowLuma is not preselected in the example config. The Plugin page displays `qq.plugin_id`, legacy `qq.channel`, or `napcat` as the fallback selection. Legacy `.env` / `qq.channels` settings can also affect the active connection mode.
- The repository ships NapCat, LLOneBot, and SnowLuma entries in `packages/qq/gateway_plugins_manifest.json`. Each entry has `autoStart: false`; SnowLuma is optional and Pan does not start it by default.
- The shipped registry contains development-machine absolute paths. The first visit to **App Settings → Plugin** copies the template to `data/qq_plugins/manifest.json` under the running checkout's data directory. Replace those paths with real absolute paths on your host.

If you do not use QQ, set `qq.enabled` to `false` in `config.json`. To use QQ, install `packages/qq/requirements.txt` in the Python environment selected by `qq.python` or `PAN_QQ_PYTHON`.

## Configure the SnowLuma gateway

The SnowLuma adapter uses a OneBot v11 WebSocket. Its shipped example URL is `ws://127.0.0.1:3003`. Install and configure SnowLuma on a host reachable from Pan, with a OneBot forward WebSocket endpoint. If SnowLuma requires a token, have its OneBot token ready.

**Windows prerequisite:** For the native Windows hook, run SnowLuma and the desktop QQ client as the same Windows user and at the same privilege level. If they run under different users or with mismatched privileges—for example, one elevated as administrator and the other at normal privilege—the hook injection will fail. See [SnowLuma's official Windows deployment guide](https://snowluma.github.io/en/docs/guide/deploy/windows).

Open `data/qq_plugins/manifest.json` for the running checkout and verify that the `snowluma` entry matches your installation:

```json
{
  "id": "snowluma",
  "name": "SnowLuma",
  "channel": "snowluma",
  "wsUrl": "ws://127.0.0.1:3003",
  "cwd": "<absolute path to the SnowLuma install directory>",
  "command": ["<absolute path to node.exe>", "<absolute path to index.mjs>"],
  "env": {"SNOWLUMA_HOOK_AUTOLOAD": "1"},
  "autoStart": false
}
```

`cwd` and the first command item must be absolute paths that exist on the current host. `command` is an argument array; Pan starts the program directly without a shell. The shipped entry currently sets `SNOWLUMA_HOOK_AUTOLOAD` to `1`; confirm whether your SnowLuma version requires that variable. Change `wsUrl` if SnowLuma listens elsewhere. Before switching, make sure another gateway is not using the WebSocket port.

## Enable SnowLuma in Pan

1. Open **App Settings → Plugin** and check the SnowLuma card. If it shows **Not installed**, fix `cwd` and `command` in `data/qq_plugins/manifest.json`, then reopen or refresh the Plugin page.
2. Click **Select** on the SnowLuma card. Pan saves the selection and says the main service must restart before the QQ bridge connects to the new gateway. If legacy multi-channel mode was active, selecting a plugin switches to that single gateway; the old `qq.channels` configuration remains in place.
3. Use the main-service **Restart** in App Settings → General to apply the selection. After Pan restarts, return to Plugin and click **Start** for the selected SnowLuma. If SnowLuma was started another way and its port is reachable, the card shows **Endpoint in use**; Pan will not take ownership of or stop that external process.
4. If the selected SnowLuma card reports a missing token and the gateway uses authentication, enter the OneBot token and click **Save token**. Restart Pan as prompted so the QQ bridge uses the new token.
5. Confirm that SnowLuma shows **Running (Pan owned)** or **Endpoint in use**. In SnowLuma's own interface, confirm that the QQ client is connected as required. Then send a message from the QQ client and verify that Pan receives and can reply to it.

**Start with Pan** applies only to the selected plugin and controls auto-start on future Pan launches. It is off in the shipped registry. Pan can stop only a plugin process it started and still identifies as its own; it will not force-stop an external gateway.

### Status meanings

| Plugin status | Meaning |
|---|---|
| **Not installed** | The registered working directory or launch file is missing |
| **Stopped** | Files exist, but nothing is listening on the configured WebSocket port |
| **Running (Pan owned)** | Pan started the plugin and can still verify ownership of its process |
| **Endpoint in use** | The WebSocket port accepts connections, but Pan does not own the process |

These statuses describe the local registry, process, or port. They do not confirm that the SnowLuma Hook supports the current QQ version or that real QQ messages are delivered in both directions. Confirm client support and required authorization with SnowLuma's official documentation.

## Switch gateways or restore legacy configuration

Select NapCat or LLOneBot in Plugin and restart Pan as prompted. To let Pan manage its process, verify its registered paths and then click **Start**. Before switching frameworks, follow the old gateway's shutdown steps for its process and the QQ client so frameworks do not compete for the same QQ client or WebSocket port.

To restore legacy multi-channel mode, stop any plugin process started by Pan, remove the `qq.plugin_id` value written by the Plugin page from `config.json`, check the original `qq.channels` / `.env` configuration, and restart Pan. Do not delete `data/qq_plugins/manifest.json` as part of switching; it stores this host's plugin registrations.

## Related documents

- [Pan User Manual](USER_MANUAL.en.md): QQ settings, Dashboard, and other features.
- [README](../README.en.md): installation and quick start.
