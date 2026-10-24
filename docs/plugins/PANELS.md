# 本地自由插件 Panel（`web-app/v1`）

`pan-plugin/v1` 的 `panels` 在 `account-switcher/v1` 之外新增版本化 renderer `web-app/v1`。插件提供自己的 HTML/CSS/JS 构建产物，宿主只负责构建、启停和资源加载；插件可用 React、Vue、原生脚本或任意静态站点生成器构建。远程网址 Panel、插件市场、沙箱和自动迁移不在本协议内。

## 声明契约

```json
{
  "protocol": "pan-plugin/v1",
  "id": "local-console",
  "display_name": "Local Console",
  "source": {"kind": "panel"},
  "panels": [{
    "id": "console",
    "title": "Local Console",
    "renderer": "web-app/v1",
    "entry": "index.html",
    "build": {
      "command": ["node", "build.mjs"],
      "cwd": ".",
      "output": "dist",
      "timeout_seconds": 120
    }
  }]
}
```

| 字段 | 约束 |
| --- | --- |
| `entry` | 相对 `build.output` 的入口文件，不能是绝对路径、`..` 或盘符。页面根和未知无扩展名路径回退到它。 |
| `build.command` | argv 数组，不经过 shell；由 manifest 提供，页面请求不能传入命令。 |
| `build.cwd` | 相对 manifest 目录，解析后必须仍在插件目录内。 |
| `build.output` | 构建产物目录，相对 manifest 目录，解析后必须仍在插件目录内；`entry` 必须位于其中。 |
| `build.timeout_seconds` | 1–3600，缺省 300。超时会终止构建。 |

`source.kind` 可为 `mcp`、`pan_api` 或 `panel`。`panel` 表示只有界面、没有 Agent 工具或 Session 绑定；带 MCP/API 的插件也可以同时声明 `web-app/v1` 面板。`panels[].actions` 仍可选，语义与 v1 相同（只能引用本插件能力）；自由界面通常直接调用已有 Pan API，不需要 actions。

资源入口固定为 `/panel-assets/<plugin-id>/<panel-id>/<build-id>/`。`GET /api/plugins/panels` 只在面板已构建并启用时返回它，并附带带 build ID 的 `assetPath`。build ID 不可变，旧页面继续读取旧构建，新页面读取新的 `activeBuild`。

## 构建与状态

宿主把每次成功构建的产物复制到 Pan 数据目录下的自有构建库，再从那里提供静态资源。构建目录不直接暴露插件源码或磁盘其他位置。每个构建副本带一份 `pan-panel-build/v1` 清单（`pluginId`、`panelId`、`entry`、`createdAt`、实际 argv），宿主以它确定该副本的入口；更换 manifest 的入口需要重新构建才会生效。

成功构建先持久化新的 `activeBuild`，再清理旧副本。保留最近 3 个成功构建，并额外保留切换前的成功构建（供已打开的页面懒加载旧 chunk）；`Unbuild` 才会删除全部宿主副本。构建失败保留当前产物与指针。若保存状态失败，新副本不会发布，旧构建和旧指针保持不变。

状态机：

- `unbuilt`：没有可用构建，不能启用。
- `building`：正在运行声明的命令；重复点击返回同一个进行中的构建，不并发启动第二个。
- `built`：有可用产物；可启用/停用。
- `failed`：最近一次构建失败。若之前有成功构建，`available` 仍为真、`activeBuild` 不变，继续服务上一版产物；失败日志保留。失败后仍可重新构建。

`POST /api/plugin-panels/{plugin}/{panel}/build` 启动构建；`enable`、`disable`、`unbuild` 管理入口。`unbuild` 删除宿主构建副本并回到 `unbuilt`、自动停用，但保留插件源码、manifest 配置和插件业务数据。构建/启停只影响面板入口和静态资源，不修改 Session 的 `plugin_bindings`、MCP 工具目录或事件订阅。

宿主重启时仍处于 `building` 的记录会标记为失败（“Build was interrupted by a Pan restart.”），不会永久显示进行中。

构建进程由宿主自有守护管理：Windows 使用 `CREATE_SUSPENDED` 创建、assign 到 `KILL_ON_JOB_CLOSE` Job Object、证明成员后再 resume；超时、取消、Pan 关闭和 root 先退出但子进程仍持有 stdout 时，都终止整个 Job 并核对无残留。进程创建本身是独立事务：取消/关闭与 spawn 竞争时，迟到创建仍归宿主所有并由 reaper 清理，关闭后的 store 拒绝再构建。阻塞输出由 Windows `CancelSynchronousIo` 或 POSIX 唤醒管道中断，reader 完成前不关闭 `BufferedReader`。POSIX spawn 时保存 session leader 的 PGID，root 退出后仍按保存值终止/校验整组；权限拒绝或未知查询返回失败，不当作已清空。查询或清理无法证明时状态直接失败并显示原因，不按 PID 猜测或只杀 root，也不会停在 `building`。清理失败不会发布新构建。

## 资源加载

- `/panel-assets/.../<build-id>/` 与 `.../index.html` 返回该构建入口 HTML；`assets/app-abc.js` 等相对路径按文件返回，旧 build ID 在其保留期内仍可读取。
- 带扩展名的缺失文件返回 404；无扩展名的未知路径回退入口，支持前端路由。
- 常见类型使用固定 MIME（`.js`/`.mjs` 为 `text/javascript`，`.css`、`.html`、`.json`、`.svg`、`.wasm`、字体等），未知类型按文件名推断。
- 所有响应使用 `Cache-Control: no-cache`，重建后刷新即可看到新入口和新资源。
- 路径在宿主构建副本内解析并做越界检查；`..`、绝对路径、盘符和 URL 编码的越界路径被拒绝。这是路径正确性约束，不是对插件内容的权限收紧。
- 面板在 iframe 中运行。宿主不注入用户 Bearer，也不拦截插件自行发起的网络请求或嵌入的远程内容；插件按自己的需要处理。

## 构建工具约定

产物可以是任何静态文件集合。常见工具需要把资源基址设为相对路径，例如 Vite：

```ts
export default { base: './', build: { outDir: 'dist' } };
```

React/Vue 的 `dist` 直接作为 `output`，把 `entry` 指向 `dist/index.html` 即可。构建命令需要在本机可执行，且不要依赖页面传入参数。示例：[React + Vite](../../examples/plugin-react-panel/README.md)（真实 React 构建、相对 base、懒加载 chunk）、[无依赖静态](../../examples/plugin-webapp/README.md)。

## 注册与操作

1. 把 manifest 加入 `config.json` 的 `plugin_manifests`（保留已有项），或通过设置中的 manifest reload 加载。
2. 打开 Settings → Plugins → **Local plugin panels**。
3. 点 **Build**，等待状态变为 **Built**（失败时显示日志，旧构建仍可用）。
4. 点 **Enable**，面板作为 View Entry 出现在导航中；打开后即加载插件界面。
5. **Disable** 只移除入口和资源访问，保留构建；**Unbuild** 删除构建副本并停用，源码与业务数据保留。

初次未构建时 Enable 按钮不可用。构建失败不会自动清空上一版，也不会改变 MCP/工具/事件运行语义。
