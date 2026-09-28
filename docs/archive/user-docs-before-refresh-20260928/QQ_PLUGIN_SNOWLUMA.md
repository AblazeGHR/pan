# QQ 插件与 SnowLuma 使用手册

## 已安装内容

- SnowLuma Windows 完整版 v1.14.20：`D:\project\SnowLuma`，入口为 `node.exe index.mjs`。
- Pan 的 **App Settings → Plugin** 已登记 NapCat、LLOneBot 和 SnowLuma。登记文件在运行 checkout 的 `data/qq_plugins/manifest.json`；首次打开 Plugin 页时从仓库的 `packages/qq/gateway_plugins_manifest.json` 复制。这个文件与根目录用于 Session/MCP 的 `manifest.json` 用途不同。
- `D:\project\Pan` 的 practical 配置已预置 SnowLuma 连接地址与密钥；SnowLuma 的 `config/onebot.json` 已设置只监听本机的 WS `3003`，HTTP 服务关闭，WebUI 只监听本机 `5099`。当前 QQ 通道选择与旧 `qq.channels` 保持原样。三个插件的 `autoStart` 都为 `false`，不会因合并代码自动登录 QQ 或启动网关。

## 首次切换到 SnowLuma

先重启运行 `D:\project\Pan` practical checkout 的 Pan 服务，让新增的后端接口和 Plugin 页面生效。重启会影响当前正在运行的 Pan 服务与 Worker，请按你现有的服务维护流程安排。

1. 在桌面版 QQ 中登录要共用的账号。SnowLuma 必须与 QQ 在**同一个 Windows 用户、相同权限级别**下运行。人仍在原 QQ 图形窗口里聊天；SnowLuma 向同一客户端注入 Hook，再向 Pan 提供 OneBot v11 WebSocket。
2. 如现有 NapCat 也注入同一个 QQ，先按其原有方式停掉 NapCat。不要让两个框架同时注入同一个 QQ 进程。
3. 在 **App Settings → Plugin** 选择 **SnowLuma**，点击 **Start**。第一次运行需由你阅读并确认 SnowLuma 的 EULA/隐私页面；打开 `http://127.0.0.1:5099/`，初始 `admin` 密码可在 `D:\project\Pan\data\qq_plugins\snowluma.log` 中找到。WS 端口和 token 已预配置，无需复制密钥。
4. 选择后重启 Pan，QQ 桥接会从旧网关改连 SnowLuma。若勾选 **Start with Pan**，以后只有当前选中的插件会自动启动。
5. 在 Plugin 页确认 SnowLuma 显示 `Endpoint in use` 或 `Running (Pan owned)`；再到 SnowLuma WebUI 确认 QQ 已注入、WS 已连接。由你在 QQ GUI 发一条测试消息，再验证 Pan 收到并回复，以检验同一账号双向收发。

如果希望以后随 Pan 启动，在 SnowLuma 卡片勾选 **Start with Pan**。只有**当前选中的插件**会自动启动，避免不同网关抢占端口。Pan 正常退出时会停止由 Pan 启动的网关；对外部已运行的网关，Pan 不接管也不强行停止。

NapCat 的旧 Windows 启动器可能要求管理员权限；若 Pan 进程没有该权限，Plugin 页会显示启动失败，请继续使用原有启动方式。NapCat 注入的 QQ 进程不能仅靠停止 Pan 启动器保证卸载 Hook；切换到 SnowLuma 前应按旧框架流程完整退出 NapCat/QQ，再重新打开官方 QQ。

## 切回原框架

在 Plugin 页停止 Pan 所启动的 SnowLuma；若它由其他方式启动，使用原启动方式对应的关闭方法。选择 NapCat 或 LLOneBot，按实际情况启动，随后重启 Pan。旧的 `qq.channels` 多通道配置仍保存在 `config.json`；清除 `qq.plugin_id` 后可恢复按多通道数组启动桥接。仅打开 Plugin 页不会改动原 QQ 桥接配置。

## 登记其他 OneBot 网关

编辑 `data/qq_plugins/manifest.json` 的 `plugins` 数组，新增一个对象，随后重新打开 Plugin 页：

```json
{
  "id": "my-gateway",
  "name": "My gateway",
  "channel": "onebot",
  "wsUrl": "ws://127.0.0.1:3010",
  "cwd": "D:/project/my-gateway",
  "command": ["D:/project/my-gateway/gateway.exe", "--config", "config.json"],
  "env": {},
  "autoStart": false
}
```

`command` 是参数数组，首项必须为绝对路径，不通过 shell 执行。`wsUrl` 是 Pan 注入/连接地址；`channel` 可填 `onebot`、`napcat`、`llonebot` 或 `snowluma`。如果网关需要认证，在 Plugin 页选择后保存它的 OneBot token。修改启动命令或地址前先停止该插件。执行文件只应来自可信来源；Pan 会按该命令直接启动它。

## 当前验证边界

代码和前端类型检查已执行，SnowLuma 自带配置解析器已确认预置配置有效。尚未替你登录 QQ、接受协议或让它注入正在使用的 QQ，因此同一 GUI 的真实收发需要按上面的第 5 步验收。SnowLuma 官方说明它直接附着桌面 QQ 进程；QQ 客户端更新后 Hook 的版本匹配仍需留意。
