# QQ Bridge 插件与 SnowLuma 使用说明

**[English](./QQ_PLUGIN_SNOWLUMA.en.md) · 中文**

本文说明 Pan 如何选择和管理 OneBot 网关。SnowLuma 自身的安装、QQ 客户端兼容范围和隐私/许可要求以 SnowLuma 当前发行版的文档为准；Pan 只检查插件文件、进程所有权和 WebSocket 端口状态。

## 当前默认状态

- `config.example.json` 中 `qq.enabled` 默认是 `true`，表示 Pan 会启动 QQ bridge 子进程；它不代表 SnowLuma 已安装或会自动启动。
- 示例配置没有预选 SnowLuma。Plugin 页面按 `qq.plugin_id`、旧的 `qq.channel` 或默认 `napcat` 显示当前选择；legacy `.env` / `qq.channels` 配置也可能影响实际连接方式。
- 仓库提供的 `packages/qq/gateway_plugins_manifest.json` 包含 NapCat、LLOneBot 和 SnowLuma。清单中的 `autoStart` 均为 `false`；SnowLuma 是可选网关，默认不会由 Pan 启动。
- 随仓库提供的注册项包含开发机专用绝对路径。首次打开 **App Settings → Plugin** 时，Pan 会把模板复制到运行数据目录 `data/qq_plugins/manifest.json`。请先改成当前主机上的真实绝对路径。

不使用 QQ 时，在 `config.json` 中设 `qq.enabled` 为 `false`。使用 QQ 时，需在 `qq.python` 或 `PAN_QQ_PYTHON` 指定的 Python 环境中安装 `packages/qq/requirements.txt`。

## 配置 SnowLuma 网关

SnowLuma adapter 使用 OneBot v11 WebSocket；随仓库提供的示例地址是 `ws://127.0.0.1:3003`。在运行 Pan 的主机上安装并配置 SnowLuma，使它提供 Pan 可访问的 OneBot forward WebSocket。若 SnowLuma 要求 token，准备好它的 OneBot token。

**Windows 前置条件：** 使用 Windows 原生 Hook 时，SnowLuma 与桌面 QQ 必须以同一个 Windows 用户、相同权限级别运行。若一方以管理员身份提升、另一方以普通权限运行，或两者属于不同用户，Hook 注入会失败。部署步骤见 [SnowLuma 官方 Windows 部署说明](https://snowluma.github.io/en/docs/guide/deploy/windows)。

打开运行中的 `data/qq_plugins/manifest.json`，确认 `snowluma` 注册项的地址和启动信息符合你的安装：

```json
{
  "id": "snowluma",
  "name": "SnowLuma",
  "channel": "snowluma",
  "wsUrl": "ws://127.0.0.1:3003",
  "cwd": "<SnowLuma 安装目录的绝对路径>",
  "command": ["<node.exe 的绝对路径>", "<index.mjs 的绝对路径>"],
  "env": {"SNOWLUMA_HOOK_AUTOLOAD": "1"},
  "autoStart": false
}
```

`cwd` 与启动命令首项必须是当前主机存在的绝对路径；`command` 是参数数组，Pan 会直接启动该程序，不经 shell。示例项目前把 `SNOWLUMA_HOOK_AUTOLOAD` 设为 `1`；请按所安装的 SnowLuma 版本确认是否保留该环境变量。若你的 SnowLuma 监听地址或端口不同，相应修改 `wsUrl`。切换到 SnowLuma 前，确认该 WebSocket 端口未被其他网关占用。

## 在 Pan 中启用

1. 在 **App Settings → Plugin** 检查 SnowLuma 卡片。若显示 **Not installed**，先修正 `data/qq_plugins/manifest.json` 的 `cwd` 和 `command`，再重新打开或刷新 Plugin 页面。
2. 点击 SnowLuma 卡片上的 **Select**。Pan 会保存选择，并提示重启主服务以让 QQ bridge 改连该网关。若此前使用 legacy 多通道配置，选择插件会切换到单个选中网关；原 `qq.channels` 配置会保留。
3. 用 App Settings → General 中的主服务 **Restart** 完成切换。Pan 重启后回到 Plugin 页面，点击 **Start** 启动当前选中的 SnowLuma；如果 SnowLuma 已由其他方式启动、端口可达，状态会显示 **Endpoint in use**，Pan 不会接管或停止外部进程。
4. SnowLuma 卡片若显示 token 未配置且网关启用了认证，在卡片输入 OneBot token 并点击 **Save token**。保存后按页面提示重启 Pan，让 QQ bridge 使用新 token。
5. 确认 SnowLuma 显示 **Running (Pan owned)** 或 **Endpoint in use**，并在 SnowLuma 自身界面确认 QQ 客户端已按其要求连接。最后由用户在 QQ 客户端发起一条消息，检查 Pan 是否收到并能回复。

**Start with Pan** 只作用于当前选中的插件，并仅影响以后 Pan 启动时的自动启动行为。仓库清单默认关闭此选项。Pan 只能停止它自己启动并仍持有进程身份的插件；不会强行停止外部网关。

### 状态含义

| Plugin 页面状态 | 含义 |
|---|---|
| **Not installed** | 注册的工作目录或启动文件不存在 |
| **Stopped** | 文件存在、对应 WebSocket 端口当前没有监听 |
| **Running (Pan owned)** | 插件进程由 Pan 启动且仍可确认归 Pan 管理 |
| **Endpoint in use** | WebSocket 端口可连接，但 Pan 不拥有该进程 |

这些状态只说明本地注册项、进程或端口的情况，不证明 SnowLuma Hook 兼容当前 QQ 版本，也不证明真实 QQ 消息已双向送达。SnowLuma 的客户端支持和授权流程需按其官方文档确认。

## 多个 QQ 账号

SnowLuma 可为同一 Windows 用户下登录的多个 QQ 账号提供各自的 OneBot 实例。Pan 的 Plugin 页在选中 SnowLuma 后可维护多个账号：

1. 在 **App Settings → Plugin → SnowLuma** 的账号区点击 **Add account**，逐项填写 QQ 号和本机 WS 地址，再点 **Save accounts**。例如第一个 `ws://127.0.0.1:3003`，第二个 `ws://127.0.0.1:3004`。QQ 号与端口必须各不相同。空列表则继续使用原有单账号配置。
2. 在 SnowLuma WebUI 中给每个账号的 OneBot WS 设置对应端口。各账号使用 Plugin 页保存的同一个 OneBot token。
3. 确认 SnowLuma 能看到各账号及对应 WS 端口。保存 Pan 账号列表后重启 Pan，桥接会创建 `snowluma`、`snowluma2` 等通道，并按消息的 `bot_uin` 路由收发。旧 `qq.channels` 配置仍保留。
4. 在 Pan 的 QQ 通道列表中确认每个账号分别显示 `connected: true`；再由用户分别发送真实消息，检查 Pan 收到并回复。

修改账号列表或端口后，Pan QQ 桥接需要重启。如果还修改了 SnowLuma 的账号级 WS 端口，应在 SnowLuma WebUI 中应用配置或重启 SnowLuma。不能让两个账号共用一个 WS 监听端口。此前在 practical 环境验证了两个账号的 WS 连接及 OneBot 身份查询；真实入站与回复仍需分别验收。

## 切回其他网关或恢复旧配置

在 Plugin 页面选中 NapCat 或 LLOneBot，按页面提示重启 Pan；如需由 Pan 管理进程，先确认该网关注册路径有效，再点击 **Start**。切换框架前应按旧网关要求退出其进程及 QQ 客户端，避免两个框架争用同一个 QQ 客户端或 WebSocket 端口。

若要恢复 legacy 多通道方式，先停止由 Pan 管理的插件，再清除 `config.json` 中由 Plugin 页面写入的 `qq.plugin_id`；保留并检查原有 `qq.channels` / `.env` 配置后重启 Pan。不要为切换而删除 `data/qq_plugins/manifest.json`，该文件保存当前主机上的插件注册信息。

## 相关文档

- [Pan 用户手册](USER_MANUAL.md)：QQ 开关、Dashboard 与其他功能。
- [README](../README.md)：安装和快速开始。
