# Tailscale 备用访问

在 App Settings → General 的现有远程设置区域配置备用访问。HTTP 使用
Tailscale IP + TCP Serve，默认访问端口 8768；HTTPS 使用检测到的 Serve
域名 + 原生证书。IP/域名自动检测，无可编辑绑定 IP 或任意代理目标。
入口只属于 tailnet，不调用 Funnel。Pan 主监听保持原样。

## 配置与调用链

```json
{"tailscale":{"startupEnabled":false,"protocol":"http","port":8768}}
```

旧配置缺失此节时默认关闭。仅接受这三个字段；布尔值必须为布尔，协议只接受
http/https，端口为 1–65535 的整数。保存只写配置，不改变当前映射。
开启/应用使用已保存配置，修改运行入口按关闭旧入口 → 创建新入口执行；
失败不自动重开旧入口。关闭始终可用，包括有未保存表单时。

`AppSettingsModal → TailscaleSettings → /api/remote/tailscale`
（GET 状态、PUT 保存、POST /start 或 /stop）→
`Controller.operation → control/status → tailscale CLI`。
server lifespan 在 terminal runtime 内启动控制器，try/finally 负责退出清理。
目标端口优先取当前 Uvicorn server.config.port；嵌入式无此对象时使用既有
Pan 端口解析。目标固定为当前实例的 127.0.0.1，创建控制器后不随磁盘端口改写漂移。

实际创建命令为以下形状，不包含 --yes、登录、授权、reset 或全局配置写入：

```text
tailscale serve --bg --tcp=<入口端口> tcp://127.0.0.1:<运行端口>
tailscale serve --bg --https=<入口端口> http://127.0.0.1:<运行端口>
tailscale serve --tcp=<拥有的入口端口> off
tailscale serve --https=<拥有的入口端口> off
```

## 所有权、失败与时序

- 同一 profile 的进程级非阻塞文件锁保持到 shutdown，防止第二 Pan 实例
  清理第一个实例的记录。线程 RLock 串行化状态、保存、启动、关闭和退出。
- 创建前读取 node status 和 Serve JSON。目标端口已有 TCP、Web、Funnel
  或 Foreground 入口即拒绝，包括目标相同的用户映射。其它端口与 Services
  不改写。HTTP/HTTPS 不尝试共用同一端口。
- `data/tailscale/ownership.json` 先原子写入 creating 意图（含设备 Self.ID、
  协议、端口、完整期望 TCP/Web/Funnel/Foreground 快照），flush/fsync 后执行
  CLI，再读取确认精确相等，最后原子提交 active。不同设备身份不恢复/清理。
- 关闭先撤销动态 terminal Origin，再读取实际配置。只有仍精确相等且无新增
  路径/入口的记录才调用该端口 off；读回确认端口已空后删除记录。
  外部修改、CLI 失败、超时、离线或磁盘失败保留记录并就地报错，不 reset。
- CLI stdin 为 DEVNULL，隐藏 Windows 命令窗口，15 秒预算。非零/超时也可能
  已写入 daemon，因此创建失败后尝试同样的严格清理；清理无法确认则保留日志。
- 请求取消不取消工作线程。取消标志由持锁的开启流程在提交后检查，在同一锁内
  关闭该入口，再向调用方传播取消；不会释放锁后再去关闭后来请求的入口。
  已完成的操作是提交边界，随后到达的取消不回滚已完成的操作。
- 退出先标记 closing，再串行关闭；拒绝后续开启/保存。正常关闭失败留记录并记日志。
  启动总是先协调遗留记录，确认清理成功后才按 startupEnabled 决定是否重新创建。

**异常退出边界：** 原生 `--bg` 配置由 tailscaled 持续持有，Pan 被强杀后映射
可能仍存在，直到 Pan 下次恢复或用户人工关闭。默认关闭保证下次成功恢复后
不保留上次临时入口；daemon 不可访问、磁盘记录损坏或外部修改时无法保证清理，
必须报告待协调状态。这不是“强杀即自动撤销”的承诺。

**外部并发边界：** CLI 没有向此控制器暴露跨 read/check/mutate 的 CAS。
控制器的锁保护 Pan 请求与同 profile 实例，不约束用户同时执行外部 Serve CLI。
每次删除前精确比对，可拒绝已观察到的修改；外部工具恰好在检查和命令之间修改，
或替换成完全相同的映射，无法通过 Serve JSON 区分来源。操作期间应避免外部修改
同端口；此边界仍需要 MA 审阅，不声称能解决 daemon 层并发来源鉴别。

## WS 与 terminal

应用 `/ws` 与 `/ws/agent` 本身没有 Origin allowlist；现有语义保持。
terminal REST/WS 原有严格 Origin + Host + Fetch Metadata gate 保留，并仅加入
当前确认生效的一个备用 Origin。关闭或观测到入口不匹配时撤销；配置预览地址
不被加入。显式非法 terminal allowlist 仍 fail closed。
HTTPS Serve 终止 TLS 后回环是 HTTP；浏览器无 Origin 的 GET 可用精确允许
authority 反推当前入口协议，不信任任意 X-Forwarded-* 来扩大许可。

状态刷新是用户触发，无后台轮询。`running` 表示已确认的 Serve 配置与在线节点，
不是跨设备可达性证明。现存连接在原生 Serve off 后的排空/断开行为未跨设备实测。

## 验证记录（2026-10-07）

授权范围内未新增/运行测试 suite。直接实验脚本与替身 CLI 位于系统临时目录
`pan-tailscale-direct-20261007`，每次运行使用新的临时 profile 与动态端口。
真实 tailscale 仅只读运行 help、status --json、serve status --json：CLI 1.102.3，
节点在线，Serve `{}`。IP/DNS 为检测快照，源码不硬编码本机值。

- Python py_compile：五个变动 Python 文件通过。
- `npx tsc -b`、两处变动 TSX scoped ESLint：通过。
- `npm run build`：通过；verify-precompressed-assets 确认 58 个资源。
- package.json/pnpm-lock 与 practical 比较一致，仅本 worktree junction 复用依赖；无 install。
- 替身 CLI 的独立 subprocess + 落盘状态直接实验：保存不启停、改端口、已有相同
  映射拒绝、外部改目标后关闭拒绝、写 daemon 后命令失败补偿、模拟 crash 后默认
  关闭清理/默认开启恢复、HTTPS 前置拒绝及配置形状、落盘前失败、取消、多实例互斥通过。
  另一个直接故障实验确认清理权限失败跨恢复保留记录、并发 start/stop 与
  start/shutdown 串行化、退出后禁止开启、缺 CLI/离线/权限错误不创建映射。
- 实际 Pan HTTP/WS routes + 动态端口 TCP relay：terminal GET 200、外部 Origin 403、
  应用 WS ping/pong、terminal WS 合法来源到达 ID 校验 400/外部来源 403、关闭后
  terminal GET 403 通过。WS 故意拒绝实验同时输出现有 ASGI handshake diagnostic。
- Chromium 真实 build：App Settings 可见，保存默认保持关闭，热开启/关闭，离线就地
  错误通过；桌面截图已人工查看。该实验临时目标/入口 55496/55499。
- 原生 Windows PTY + 实际 terminal REST/WS + TCP relay：创建 200、WS hello、
  claim 控制权、input accepted、实际输出 `PAN-TS-REMOTE-OK`、临时文件内容确认、
  close 200 通过。最后一次临时 profile `pan-tailscale-profile-2q7yv8x_`，目标/入口
  59709/59713，terminal `term_398bcc972f0f4005`。shutdown 完成且无 unconfirmed/
  retained_for_retry，secrets_retained=false。首次原生实验在 sidecar 缺依赖环境中
  engine-startup-failed，保留 `pan-tailscale-profile-hxqjkz5j` 的 cleanup-failed 记录；
  launcher 日志显示 engine-cleanup=converged，未发现该实验存活进程。核对 sidecar
  package/lock 一致后仅 worktree junction 复用 practical sidecar 依赖，重试通过；
  没有新增安装、修改终端实现或把首次失败隐去。直接 HTTP 客户端设置 trust_env=false。
- HTTPS 后端跳转仅直接 ASGI 实验：精确 Host + 浏览器 GET 元数据 200，伪造
  Host/forwarded header 403。带测试域名 Host 的 socket 请求两次返回 502，Pan
  无请求日志；归于尚未确定的环境路径，不等同于 HTTPS Serve 失败或成功。

未操作 8767/8768，未改变真实 tailnet Serve、主监听、代理/DNS/防火墙、QQ/MCP/
Cloudflare 服务，未重启真实服务。跨设备、真实 HTTPS 首次账户配置/证书、tailnet
策略、移动端布局与存量远程连接关闭行为仍待验证。TA 提交不是 MA 验收、整合或 push。

## 官方依据

- [Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve)：后台持久化、TCP/HTTPS 与定向 off。
- [1.102.3 Serve schema](https://github.com/tailscale/tailscale/blob/v1.102.3/ipn/serve.go)：TCP/Web/Foreground/Funnel 与互斥类型。
- [1.102.3 CLI 实现](https://github.com/tailscale/tailscale/blob/v1.102.3/cmd/tailscale/cli/serve_v2.go)：TCP 目标形状及 HTTPS handler。
