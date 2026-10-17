# CC Switch Bridge：用户真机验收清单

插件是 `cc-switch-bridge`，不是 cc-switch 本体。实现只通过已安装 CLI 桥接，未改动、重编译、导入或打包原始 cc-switch。接口核对使用本机 `D:\openTools\cc-switch-cli-src` 的源码（核查时 HEAD `a82dac16`）；Pan UI 为自行实现，未复制其 GUI 代码。

本轮切换测试全部使用假 CLI。没有执行任何真实账号/配置切换，也未启动或操作原始 GUI。用户允许查询，但不把查询授权扩展为切换。下面所有真实切换均由用户本人执行；本轮没有合并 main/practical、构建 practical 或重启生产服务。

## 准备与基线

1. 在运行该工作树的新 Pan 实例前，选择自己的测试端口，并按既有方式启动；不要把旧运行进程误当作加载了新代码。前端需要对应构建。若使用默认 manifest，导航自动出现 CC Switch；生产路由是 `/react/plugins/cc-switch-bridge/accounts`。
2. 确认本机 CLI 有 `provider list --json`、`auth status --json`、`provider quota <id> --json`。可配置 `data/plugins/cc-switch-bridge/settings.json` 的 executable 为实际 `.exe`；不需要复制 cc-switch 数据库。原 cc-switch 程序应继续按它自己的方式定位共用数据库。
3. 用原 CLI 或 GUI 记录 Codex/Claude/Gemini 的当前 provider ID，以及 ChatGPT 默认账号 ID；按原 cc-switch 的备份方式保留必要备份。选两个已存在、自己确认可用的配置/账号，避免以正在执行重要任务的环境作切换对象。
4. 打开 Pan 面板。预期列表和原 cc-switch 的名称、ID、当前标记一致；只读取列表时没有远程额度请求，也没有改变当前 provider/default account。账号 API key、settingsConfig、token 和 URL query 不出现在 Pan 响应或页面。

## 查询与展示（不切换）

| 操作 | 预期 |
| --- | --- |
| 在 Codex / Claude / Gemini 之间切换页签 | 展示各自配置；不调用 provider switch，不改变 cc-switch 当前配置 |
| 输入名称、账号或地址搜索 | 仅过滤本页面；当前标记、账号和其他 Session 设置不变 |
| 点击某配置的“额度” | 仅查询该配置；展示已用比例、重置时间或余额，以及查询时间；不把不支持/错误当成 0% |
| 刷新列表或离开后返回 | 保留 bridge 中已有额度缓存及时间；不自动查询所有远程额度 |
| 在原 cc-switch 外部改变配置后刷新 Pan | 显示原产品的最新当前标记；bridge 不维护第二份账户数据库 |
| CLI 未安装、版本不兼容、断网、凭据过期 | 显示失败/不可用，不泄漏 token，不自动切换其他账号 |

额度查询可能触发原 cc-switch 自己的凭据刷新、网络请求或查询缓存；这是原 CLI 的查询语义，bridge 不保证其数据库字节绝对不变。Pan 只保存自己的展示缓存。

## 配置切换（仅用户执行）

1. 选一个非当前配置点“启用配置”，核对确认区的名称和“全局启用配置”提示。
   - 预期：在点“确认切换”前没有切换；取消后原 CLI/GUI 当前 ID 不变。
2. 点击“确认切换”。
   - 预期：一次操作、按钮暂时禁用；成功后 Pan 当前标记和原 CLI/GUI 的 provider ID 都变为所选 ID。原来的其他应用选择和 ChatGPT 默认账号不应被误当作切换对象；以 cc-switch 自身该应用切换语义为准。
3. 连续点击或浏览器偶发重复提交同一请求。
   - 预期：同 requestId 不重复执行；UI 不产生并发第二次切换。
4. 在 Pan 打开确认区后，从原 cc-switch 改变当前配置，再回 Pan 确认旧操作。
   - 预期：提示当前配置已变化，旧选择不会盲目覆盖；先刷新再决定。
5. 若请求中断/超时，先用原 CLI/GUI 核对真实 ID，再刷新 Pan。
   - 预期：界面不宣称已回滚、不自动重发；刷新成功后才允许新操作。不要通过重复点击测试网络未知结果。

## ChatGPT 默认账号（仅用户执行）

1. 记录默认账号 ID，然后在账号区选“设为默认账号”并确认。
   - 预期：对应 `auth default <account-id>`，Pan 和原 CLI 的默认账号标记一致。
2. 检查一个绑定了特定账号的 provider。
   - 预期：默认账号变化不会被 Pan 描述成“所有 provider 都切到该账号”；特定绑定仍由原 cc-switch 决定。额度缓存清除，需要手动查询后展示。
3. 若实际账号 ID 为纯数字，或账号已删除。
   - 预期：bridge 拒绝操作，提示刷新/无法安全按 ID 切换，不按旧序号误选其他账号。

## MCP 接入（切换仍由用户自行决定）

1. 在测试 Session 启用 cc-switch-bridge，分别选择独立、Pan 原生、gateway。
   - 预期：前两种仅有 quota 和 switch 两个业务工具；原生名为 `pan_cc_switch_bridge_quota`、`pan_cc_switch_bridge_switch`；gateway 发现仅这两个能力，list/default_account 不能绕过发现直接调用。
2. 调用 quota，参数 `{"app":"codex"}`。
   - 预期：返回可用配置/账号 ID、当前选择和缓存；没有切换，也没有批量远程额度查询。指定 id 后才查该配置额度。
3. 只有用户决定验证 MCP 切换时，才提供 `app/id/expectedCurrentId`；默认账号另带 `kind:"default_account"`。
   - 预期：执行既有 CLI 操作，前后核对状态；未知结果不得让 Agent 自动重试。
4. 关闭 Session 的 cc-switch-bridge 工具。
   - 预期：该 Session 不再有工具，Panel 仍可打开；不会关闭 cc-switch 或影响其他 Session。

## 恢复与记录

按记录的原 ID，使用面板或原 CLI/GUI 由用户恢复 provider/default account，再刷新确认。记录每项的前后 ID、时间、成功/失败、额度查询状态；不要记录 token。保留失败请求的 requestId 和 bridge 操作台账供排查，不手动删除 pending 后重放未知切换。

本清单是待执行的真机步骤和预期，不是“真实切换测试已通过”的声明。
