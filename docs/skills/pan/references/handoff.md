# 替身交接：先确认来源与接班者，再决定字段层次

调用前明确用户意图：**A 是要归档的来源；B 是接班者**。先问清要新建 B 还是使用已有 B，再确认两个精确 ID、简报、是否显式更新模型/权限，以及 original 元数据选择。已有 B 不是“带名字的新 Session”，不能清空上下文，也不能套用新建模式默认值。若用户意思不清楚，问具体缺口再调用，不猜源目标、不猜覆盖意图。

```python
# 新建 B：保持旧流程，省略 target_session_id
session_handoff(session_id="ses_A", handoff_prompt="现状、职责与未完成工作")

# 已有 B：设置默认保留；不要带 copy_settings=true
session_handoff(session_id="ses_A", target_session_id="ses_B",
                handoff_prompt="交给 B 的普通输入简报")

# 已有 B：只覆盖 original 元数据，不改变任何有效系统提示词
session_handoff(session_id="ses_A", target_session_id="ses_B",
                handoff_prompt="交接简报", override_original_prompt=True,
                original_prompt="本次明确选择的原始指令元数据")
```

## 字段决策表

HTTP 使用 camelCase，MCP 使用 snake_case。省略与 false 的含义取决于模式，不能把参数当同一层的设置复制。

| 层次 / 字段（MCP / HTTP） | 新建 B：默认与显式值 | 已有 B：默认与显式值 |
|---|---|---|
| 寻址 `session_id` / URL `{id}` | 来源 A，归档但不删除 | 来源 A，归档但不删除；不是目标 |
| 寻址 `target_session_id` / `targetSessionId` | 省略才新建 | 精确已有 B ID；空串/null/不存在报错，不退回新建 |
| 设置策略 `copy_settings` / `copySettings` | 省略=true，从 A 复制下列配置；false 使用创建默认 | 省略/false 保留 B；true 明确拒绝，不能静默覆盖已有上下文 |
| `adapter` | 显式优先于 A；copy=false 时必填 | 保留 B；传参拒绝，即使同值，不能借交接切换已有 CLI |
| `model` | 显式值 > A（copy=true）> 创建默认 | 省略保留 B；显式值经 B adapter 能力校验，仅无活/native Worker 可改 |
| `permission_mode` / `permissionMode` | 显式值 > A（copy=true）> 创建默认 | 同上；不以交接重启 Worker，不触及 constraint 或账号策略 |
| `adapter_config`、effort/thinking、MCP/plugin bindings、上下文限额等 | copy=true 从 A 深复制，排除 CLI ID 与 Steer 回执；copy=false 创建默认 | 保留 B 全部值及 CLI ID、Steer 回执；交接无独立覆盖参数 |
| `pan_access`、`character_id`、`session_template`、`game_id`、通知设置 | copy=true 从 A；copy=false 创建默认 | 保留 B，无交接覆盖参数 |
| `workdir` | 从 A | 保留 B，不切 cwd |
| original 选择开关 `override_original_prompt` / `overrideOriginalPrompt` | 默认 false，取 A.original_prompt；true 要求本次文本 | 同一选择规则，但结果仅覆盖 B.original_prompt 元数据 |
| original 文本 `original_prompt` / `originalPrompt` | 开关 true 使用本次字符串；空串明确清空；false 忽略残留文本 | 同左，**覆盖** B 原 original 元数据，不另存代替；永不注入 CLI |
| 实际有效 `system_prompt` / `systemPrompt`（只读） | 合成本次简报 + 所选 original，旧简报不叠加 | **逐字保留 B 交接前的有效值**（可与 B.original_prompt 不同，含空/None）；独立冻结持久化，当前及以后 restart/resume/newspawn 都不从覆盖后的 original/handoff 元数据重新合成 |
| 简报 `handoff_prompt` / `handoffPrompt` | 必填；保存本次简报，由新建合成器作为初始提示 | 必填；覆盖简报元数据，另作为**唯一一条普通输入**持久入队；不等同 system prompt，也不等同已被 CLI 消费 |
| history、CLI session ID、usage、task/owner/generation | B 全新，不复制 A 的 CLI 身份/history | 保留 B；A 的 worker、队列、Pause/Illegal 与最终报告也保留，不 kill/unlock |
| `managed` / `managed_by`、报告订阅 | 从 A 接替；B 管理归档 A | 合并去重 B 原子 Session/订阅与 A 关系，B 管理 A；第三方对 A 的报告订阅改指 B |
| QQ、WeChat 会话订阅 | 从 A 转移 | 与 B 原订阅合并去重；发现第三方占用 A 绑定时整体拒绝，不抢占 |
| Workspace | 沿用 A 有效根成员关系 | 保留 B 的根成员关系；当前仅同有效 Workspace，祖先/父子/不同 manager 拒绝，避免静默移动已有子树；跨组归属尚未定义 |
| 名称 | A 加 archive 前缀，B 取 A 原名（处理重名） | A 加 archive 前缀，B 保留原名 |
| Jobs | 提交后 retarget 到 B，逐项报告失败 | 同左；Job retarget 与多 Session 提交分层，失败不谎称 Session 回滚 |

## 回执、安全边界与重试

已有模式返回 `briefDelivery.queueItemId`，A 的 `handoffReceipt` 也持久保存该原 ID 与目标 B。B 的原队列、队列顺序、Pause、Illegal 自动锁、编辑 lease、native ownership/generation 均由普通队列消费路径处理；不会 Force、自动解锁或杀运行 Worker。A 可从自己的运行回合交接，终态报告按已提交的新管理关系交给 B。已有模式明确拒绝 readonly 来源/目标；设置覆盖要求 B 无活/native Worker。

`ok=true` 与 queued 证明交接/入队落盘，**不证明 CLI 已读**。按原 ID 检查 B queue / delivery ledger / history。A 的重复相同请求只返回原回执，不创建第二条消息；参数改变或尝试改回新建被拒绝。provider 写入结果未知时保留原 queue ID，禁止自动重投、换 ID、重复创建 B 或 Force 降级。

关系环路、损坏 backlinks、祖先/父子、不同 manager、不同 Workspace、第三方 QQ/WeChat 绑定冲突与不支持参数全部在修改前拒绝。已有 B 原管理树、订阅、bindings 不丢失。HTTP 获取 A/B spawn、插件与队列锁后提交，核心使用 store 锁；持久化失败恢复内存与已写 Session，若回滚落盘本身失败会明确返回失败详情，需要人工对账，不能自动重试。此实现不提供跨文件崩溃事务保证，也不把提交后的 Jobs retarget 当作同一事务。

MCP 对 A/B 都做受管理访问检查，已有模式不在预检时 claim，因此拒绝不会留下预先认领关系。HTTP 沿用 Pan 内部 API 的 MCP 权限分工，不因 body 自报身份授予权限。
