# 面向 LLM 的 QQ 管理机器人框架/插件调研 —— 与 Pan 配合评估

> 调研日期：2026-08-22 ｜ 状态：调研文档，非实现计划
> 范围：QQ 协议层（NapCat/LLOneBot/go-cqhttp/Lagrange/Mirai）+ LLM 机器人框架（NoneBot2/Koishi/AstrBot/LangBot 等）+ 与 Pan 的配合方案
> 信息源：各项目 GitHub 仓库 / Releases / 官方文档，见文末附录

---

## 0. 结论先行

- **协议端**：维持现状 **NapCat**（NTQQ 系，活跃、功能最全）。go-cqhttp / Mirai 基于旧 OICQ 协议且已停更，不可作为新选型；LLOneBot 已更名重构为 LuckyLilliaBot；Lagrange.OneBot v1 进入 sunset。**全部方案都需真人 QQ 号登录**，这是共同硬约束。
- **框架端**：NoneBot2 / Koishi / AstrBot / LangBot 都能实现"选择性回复 + 富媒体"，但只有 **NoneBot2 天然就是"接收消息但不自动回复"的事件驱动模型**，且 Pan 正在使用、同 Python 生态。
- **推荐方案：方案 A —— 继续 NoneBot2，升级 `packages/qq/plugin.py`**（选择性发送 + 富媒体发送 API + 监听模式）。改动最小、满足全部需求；方案 C（直接对接 NapCat HTTP/WS）是 A 的长期演化方向，但现阶段属于重写，不建议立刻做；方案 B（换框架）收益不抵跨语言/架构迁移成本。

---

## 1. 调研背景与目标

Pan 目前通过 `packages/qq/plugin.py`（NoneBot2 插件）桥接 QQ：

```
QQ 用户 → NapCat (OneBot v11 WS) → NoneBot2 → plugin.py → Pan Core HTTP/WS → Worker
```

现状特征（与本次调研目标的差距）：

| 现状 | 目标 |
|---|---|
| **全量镜像**：每条 QQ 消息 → `/api/task` → worker → 自动回复 | **选择性发送**：QQ 会话不作为 session 同等历史；由编排者（meta-agent/worker）挑选何时把消息喂给哪个 session、何时回复 |
| **仅文本**：`bot.send(event, text)` + 1500 字符分块 | **富媒体**：表情/图片/合并转发/@/卡片 |
| 事件源仅 `on_message`，且只处理 @ 提及与私聊 | 更细的事件订阅（撤回/戳一戳/群成员变动/表情回应） |

本次调研回答两个问题：① 有哪些面向 LLM、可被外部 HTTP/WS 驱动的 QQ 框架/工具？② 用哪种方案让 Pan 实现"选择性发送 + 富媒体 + 精细控制"且改动最小。

---

## 2. 候选清单与简介

### 2.1 协议层（QQ 接入底层，OneBot 实现）

| 项目 | 简介 | 维护状态 |
|---|---|---|
| **NapCat**（NapNeko/NapCatQQ） | 基于 **NTQQ**（QQNT 客户端 Node 模块）的"无头"QQ 客户端 + OneBot v11 协议转译器。HTTP / 正向 WS / 反向 WS 全支持，另有 WebUI 管理、Docker 部署 | ✅ **活跃**，V4.18.19（2026-08），适配 QQ 9.9.32；OneBot 生态事实标准。⚠️ 2025-09 曾发生 WebUI 暴露导致 OneBot API 被恶意调用、账号被封事件 |
| **LLOneBot** → **LuckyLilliaBot** | 2024 年初首个成熟 NTQQ Hook 方案；后与 NapCat 决裂，**已更名重构**为 LuckyLilliaBot，转向 Milky/LagrangeV2 协议路线 | 🟡 以 LuckyLilliaBot 名义活跃（v8.1.9，2026-08），但品牌/技术栈变动大，选型优先级低于 NapCat |
| **go-cqhttp**（Mrs4s/go-cqhttp） | 基于 **MiraiGo（OICQ 旧私有协议）** 的经典 OneBot v11 实现；依赖外部签名服务器 | ⛔ **已停止维护**（最后提交 2024-05），README 明示无力应对官方封堵。旧协议被持续围堵，登录大概率已失效 |
| **Lagrange.OneBot**（LagrangeDev/Lagrange.Core） | C#/.NET 纯协议实现（不需跑 QQ 客户端壳），OneBot v11；依赖 SignServer | 🟡 内核活跃（2026-08），但 **OneBot v1 已进入 sunset**，新方向是 Milky/V2 |
| **Mirai + mirai-api-http**（mamoe/mirai） | Kotlin 框架，**OICQ 旧协议**；mirai-api-http 是**自有 API（非 OneBot 标准）** | ⛔ **准停更**（2024-09 后无实质提交；mirai-api-http 冻结在 2.10.0/2023-11）。旧协议被封堵风险高 |

### 2.2 机器人框架 / 编排层

| 框架 | 简介 | 定位 | 维护状态 |
|---|---|---|---|
| **NoneBot2**（nonebot/nonebot2 + nonebot-adapter-onebot） | Python 异步事件驱动 bot 框架，插件化，适配器负责协议层 | 通用 bot 框架，**非 LLM 专属**（Pan 正在用） | ✅ 活跃：nonebot2 2.5.0（2026-04），adapter-onebot 同时支持 v11/v12（2026-08 有提交） |
| **Koishi**（koishijs/koishi） | TypeScript/Node 通用 bot 框架，生态庞大（官方 19 平台适配器、社区 3900+ 插件） | 通用 bot 框架，LLM 靠社区插件 ChatLuna | ✅ 活跃；官方 chatgpt-bot 已停更，LLM 编排实际由 **ChatLuna**（2026-08-21 仍有提交，含被动唤醒/冷却等触发运行时）承担 |
| **AstrBot**（AstrBotDevs/AstrBot） | **LLM 专属**聊天机器人框架（Python），附 WebUI、MCP、Agent 能力；QQ 走 OneBot（aiocqhttp） | LLM 聊天框架 | ✅ 极活跃：4.27.4（2026-08-19），后端已迁 FastAPI + OpenAPI |
| **LangBot**（langbot-app/LangBot，原 RockChinQ/LangBot） | **生产级 LLM 机器人平台**（Python），支持 Agent/知识库/插件/MCP/n8n/Coze 等；QQ 个人号 + 官方接口 | 生产级 LLM 平台 | ✅ 活跃（2026-07 提交）；`respond-rules` 支持 at/前缀/正则/概率响应，pipeline 事件可 `prevent_default()` 完全接管回复 |
| **LangChain / LlamaIndex 的 QQ 通道** | — | LLM 编排库 | ❌ **无 QQ bot 集成**（仅微信聊天记录 loader，且非运行时通道）——只宜作为 Pan 内部编排层 |
| **Gensokyo-llm**（Hoshinonyaruko/Gensokyo-llm） | Go 写的 OneBot v11 → 多 LLM 轻量网关（/conversation + SSE） | OneBot↔LLM 桥 | ⛔ **疑似停更**（最后提交 2024-09），版本混乱，不建议新项目采用 |

---

## 3. 关键能力对比

### 3.1 协议层对比（对"富媒体 + 事件粒度 + 外部驱动"的决定性影响）

| 对象 | 维护状态 | 底层协议 | 富媒体 | 事件粒度 | 外部 HTTP/WS 驱动 | 登录/风险 |
|---|---|---|---|---|---|---|
| **NapCat** | ✅ 活跃 | NTQQ；OneBot v11（差异化实现） | **强**：`face`(表情) / `mface`(大表情) / `image`(本地/URL/base64) / `record`(语音) / `video` / `at`(含@全体) / `forward`+`node`(**可伪造内容**) / `reply` / `json`(卡片) / `lightapp`(小程序) / `file` / `poke`；markdown 需套双层合并转发 | **强**：消息(私聊/群/临时会话)、撤回(群+私聊)、成员增减/管理员/名片/禁言、戳一戳/点赞/设精、申请(好友/加群/邀请)；⚠️ **表情回应(emoji like)仅收到自己的**，他人回应需扩展接口 | ★★★★★ 协议网关，HTTP/WS/反向 WS 直驱，文档全、Docker/WebUI 齐 | 真人 QQ 号，扫码/自动登录；随 QQNT 升级需跟进；有 WebUI 暴露封号案例 |
| **LuckyLilliaBot**（原 LLOneBot） | 🟡 活跃但变动大 | NTQQ（Milky/LagrangeV2）；OneBot 11 + Satori + Milky | 与 OneBot 11 标准相当（未逐项核实） | 同 OneBot 11 标准 | ★★★★ 可直驱，但文档/社区体量小 | 真人 QQ 号扫码 |
| **go-cqhttp** | ⛔ 停更 | OICQ 旧协议（MiraiGo）；OneBot v11 | 强（v11 CQ 码全：face/image/record/at/forward/xml/json/红包/tts） | v11 标准全量；**无表情回应** | ★★★★ 可直驱但登录大概率失效 | 密码/扫码+签名服务器；**官方封堵，风险最高** |
| **Lagrange.OneBot** | 🟡 v1 sunset | NTQQ（C# 自实现）；OneBot v11 only | v11 标准（未逐项核实） | v11 标准 | ★★★★ 4 种接入直驱、部署轻；需自备 SignServer | 真人 QQ 号扫码 |
| **Mirai + mirai-api-http** | ⛔ 准停更 | OICQ 旧协议；**自有 API（非 OneBot）** | 自有消息体系（表情/图/音/转发齐全） | 齐全但自有规范；表情回应弱 | ★★★ 可直驱但非 OneBot 标准，需适配层 | 旧协议封堵，风险高 |

### 3.2 框架层对比（"选择性发送"是核心差异点）

| 框架 | 定位 | 维护状态 | **选择性回复** | 富媒体易用性 | 事件粒度 | 外部 HTTP/WS 驱动 |
|---|---|---|---|---|---|---|
| **NoneBot2** | 通用 bot 框架（Python） | ✅ 活跃 | **最强（天然）**：事件驱动，**默认不回复**，只有显式 `send()`/`finish()` 才回；「决定是否回复」可完全抽到外部 | 高：`MessageSegment.at/face/image/forward/reply` 一行构造 | **最全**：OneBot v11 全事件（消息/群变/撤回/poke）；表情回应非标准 | 内置 ASGI 可自挂端点；也可直接调 OneBot API；需写少量代码 |
| **Koishi (+ChatLuna)** | 通用 bot 框架（TS/Node） | ✅ 活跃 | 中：中间件模型 + ChatLuna 触发运行时（被动唤醒/冷却/黑名单），但**无开箱即用外部决策** | 高：Satori 元素 | 消息为主 | Web 控制台（WS）；无内置通用 HTTP API，需插件 | 
| **AstrBot** | LLM 专属聊天框架（Python） | ✅ 极活跃 | 中偏强：会话级「自定义规则」（关 LLM/拉黑/指定模型），插件可拦截阻断回复；**非消息级外部决策** | 高：QQ(OneBot) 文字/图片/语音 | 消息为主，群管事件弱 | WebUI OpenAPI + API key；无面向集成方的稳定 API |
| **LangBot** | 生产级 LLM 平台（Python） | ✅ 活跃 | **最强（配置+插件）**：`respond-rules`（at/前缀/正则/概率）+ pipeline 事件 `prevent_default()` 完全接管回复 + webhook 插件 | 高：MessageChain 抽象 | 消息对话为主，群管事件弱于 NoneBot2 | 插件 API + webhook + 事件监听器 |

### 3.3 对 Pan 三个关键能力的判读

1. **选择性发送**：NoneBot2 事件驱动模型天然满足（默认不回复）；LangBot 靠配置规则 + pipeline 事件也满足（但需整体迁入其平台）；AstrBot 是会话级粒度，做"消息级外部决策"要写插件；Koishi 无开箱即用外部决策。
2. **富媒体**：协议层决定能力上限（NapCat 最强），框架层决定易用性。NoneBot2 的 MessageSegment 构造富媒体是"一行代码"级别；发 **大表情 mface / 合并转发 forward / json 卡片**都走标准 segment。
3. **外部 HTTP/WS 驱动**：NoneBot2 既可自挂 ASGI 端点，也可由 Pan 直接调 OneBot HTTP API（`bot.call_api` 在插件内等价）；NapCat 本身对外就是 HTTP/WS 网关——这为方案 A → 方案 C 的平滑演化留了路。

---

## 4. 与 Pan 配合的三个候选方案

前提：协议端维持 NapCat 不变（无论哪个方案）。Pan 侧现有可复用机制：`/api/assign`（异步分派 + `worker.result` 事件）、`/api/handoff`（同步等待）、`/ws/agent`（订阅 worker.result，可按 session 过滤）、`managed`/`report_subscriptions`/`queue_pending`（meta-agent 订阅被管 session 完成报告）、`/api/claim`（建立托管关系）。

### 方案 A：继续 NoneBot2，升级 `plugin.py`（推荐）

**做法**：协议端（NapCat）、框架（NoneBot2）不变，只改 `packages/qq/plugin.py`：

1. **拆掉"每条消息 → /api/task → 自动回复"的硬绑定**，改为消息事件化：QQ 消息 → 结构化事件（sender/group/提及/富媒体段）→ 推给 Pan 的编排端点，**不进 session 历史**。
2. Pan 侧新增一个 **QQ 编排者（manager）session**，消费该 inbox，用其 worker 决定：忽略 / 路由到哪个 session（`/api/assign`）/ 何时回复。
3. 回复经 plugin.py 新增的**富媒体发送 API** 发出；完成结果经 `worker.result` / report 订阅回传给编排者，形成闭环。

**利**：
- 改动最小：只动一个文件，协议端/框架/部署不动；
- Python 同语言，可完全复用 Pan Core 现有的 manager/assign/report 机制（这正是"meta-agent 选择性发送"的现成底座）；
- NoneBot2 天然监听模式，无需对抗框架默认行为；
- 可渐进演化：plugin.py 已大量直接调 OneBot API，将来要彻底脱框架（方案 C）时是平滑迁移。

**弊**：
- 选择性决策逻辑要自己写（NoneBot2 不提供编排语义，框架只保证"不自动回复"）；
- 需要新增 plugin.py ↔ Pan 之间的"消息上行 / 指令下行"两个接口。

### 方案 B：换/加框架（Koishi / AstrBot / LangBot / 纯 onebot 客户端库）

**做法**：引入 LangBot / AstrBot / Koishi（或某个 onebot Python 客户端库）替代 NoneBot2 承担 QQ 接入。

**利**：
- LangBot 的选择性回复是**配置化**（respond-rules），少写代码；
- AstrBot / LangBot 自带 WebUI 管理、人格/模型管理，开箱即用度高。

**弊**：
- **架构重叠**：这些框架是"自带 LLM 编排"的平台，与 Pan Core 的 worker/manager 编排会形成两套 LLM 层，职责冲突；强行只用其"QQ 前端"部分反而比 NoneBot2 更绕（要去 override 它们的默认全量回复）；
- 跨语言成本（Koishi 为 TS/Node）；
- 需要重写现有一个可用且已在生产的桥接（现有 plugin.py 含 command-routes、game_id 绑定、WS 推送等成熟逻辑），**改动量远大于方案 A**；
- 事件粒度（群管事件）普遍弱于 NoneBot2 的 OneBot v11 全事件。

### 方案 C：直接对接 NapCat / LLOneBot 的 HTTP/WS API（Pan 自研轻量桥）

**做法**：放弃 bot 框架，由 Pan 侧（或在 plugin.py 内）直接作为 OneBot 客户端连 NapCat 的正向/反向 WS，自行解析事件、构造 segment 发消息。

**利**：
- 最灵活、依赖最少、无框架开销；
- 事件与发送完全掌控（NapCat 扩展接口直接可用）；
- 无 NoneBot2 版本升级/适配器约束。

**弊**：
- **本质是重写 plugin.py 已有的一半功能**（事件解析、重连、鉴权、分段发送、command-routes……），当前收益不抵成本；
- 需要自己维护 OneBot 协议细节（NapCat 差异化实现、CQ 码转义等）；
- 丢失 NoneBot2 的生态与测试过的稳定路径。

### 对比与推荐

| 维度 | A：升级 NoneBot2 | B：换框架 | C：自研轻量桥 |
|---|---|---|---|
| 改动量 | **最小**（单文件） | 大（整体替换） | 大（重写桥接） |
| 选择性发送 | ✅ 天然 + 复用 Pan manager 机制 | ✅ LangBot 配置化 / ⚠️ 架构重叠 | ✅ 完全掌控 |
| 富媒体 | ✅ MessageSegment | ✅ | ✅ 自构造 segment |
| 事件粒度 | ✅ OneBot v11 全事件 | ⚠️ 群管事件弱 | ✅ NapCat 全接口 |
| 复用现有 plugin.py | ✅ | ❌ | ⚠️ 半复用 |
| 长期演化空间 | ✅ 可平滑迁向 C | ❌ 绑定框架 | — |

**推荐：方案 A。** 理由：改动最小（只动 plugin.py）且完整满足"选择性 + 富媒体 + 精细控制"；NoneBot2 的事件驱动模型与"由外部编排者决定回复"的需求天然契合；Pan Core 现有的 manager / assign / report 订阅机制可直接作为选择性发送的编排底座。方案 C 作为 A 的长期演化方向（plugin.py 内部逐步直接调 OneBot API），现阶段不必做。方案 B 因架构重叠与迁移成本不推荐。

---

## 5. 方案 A 落地：`plugin.py` 新增能力点清单

> 以下为升级 `packages/qq/plugin.py`（含必要的 Pan Core 侧小改动）所需能力点，供实现阶段细化。

### 5.1 监听模式 / 选择性发送（核心）

1. **消息事件化**：将 `handle_message` 从"文本 → task → 回复"改为"消息 → 结构化 QQ 事件（sender、group、是否 @bot、富媒体段列表、原文）→ 上行推送"。
2. **上行通道（QQ → Pan）**：新增事件推送接口（建议 `POST /api/qq/inbox`，Core 侧把事件写入「QQ 编排者」manager session 的落盘队列——可复用 `queue_pending` 模式，断线不丢；或新增轻量 inbox 表）。**不进任何 worker session 的历史**。
3. **QQ 编排者 session**：Core 侧定义一个 manager session（如名字前缀 `qq-ctrl`），其 worker 消费 inbox 事件，自主决策：忽略 / 路由到哪个目标 session（`/api/assign`）/ 等待结果后决定是否回复。可复用 `managed` + `report_subscriptions`（订阅目标 session 完成报告）。
4. **下行决策（Pan → QQ）**：编排者决定回复后，经 plugin.py 的发送 API 下发；或允许编排者直接调用 `send_msg`（需在 Pan 侧暴露一个 QQ 发送端点，把 intent 转成 OneBot segment）。
5. **保留/退路**：现有"全量镜像"模式保留为可切换配置（`qq_mode = "mirror" | "selective"`），避免一刀切风险；command-routes（绕过 LLM 的指令直连）继续保留。

### 5.2 富媒体发送 API

6. **segment 数组支持**：发送端点接受 OneBot `MessageSegment` 数组而非纯文本，至少支持：
   - `at`（含 `qq="all"` @全体）、`reply`（引用回复）
   - `face`（QQ 小表情）、`mface`（大表情，NapCat 需以 image/mface 双格式处理）
   - `image`（本地路径 / URL / base64）
   - `record`（语音）、`video`
   - `forward` + `node`（合并转发，`content` 模式可伪造发言内容——适合"以群成员口吻转发摘要"）
   - `json`（卡片）
7. **长文本策略**：文本继续 1500 字符分块；富媒体段不分块，混合消息按段拼接。
8. **发送失败重试**：富媒体段（尤其图片 base64、语音）对协议端状态敏感，需失败重试与降级（回退纯文本）。

### 5.3 事件订阅增强

9. **全事件上行**：除消息外，将 OneBot v11 事件（`group_recall` 撤回、`notify/poke` 戳一戳、`group_increase/decrease` 成员变动、`group_admin/ban/card` 管理变动、`request` 申请）也以结构化事件推给 QQ 编排者，供其感知群动态。
10. **表情回应**：OneBot v11 生态仅支持"自己收到"的 emoji like（NapCat `notice.group_msg_emoji_like`）；他人回应需走 NapCat 扩展接口——作为可选增强，标注实现复杂度。

### 5.4 反向控制（可选增强）

11. **主动下发**：支持 Pan 侧主动发起——定时/编排触发的主动消息、撤回自己的消息、改群名片等，均经同一发送 API 路由。
12. **鉴权与安全**：当前 Pan API 无鉴权 + 绑 loopback（既定姿态）。inbox/发送 API 应延续 loopback 绑定；若 NapCat 暴露公网，需注意 2025-09 WebUI 暴露封号的前车之鉴（至少不开启远程 WebUI、WebUI 设强口令）。

---

## 6. 附录：主要信息来源

- NapCat：github.com/NapNeko/NapCatQQ（repo/releases）、napneko.github.io（develop/msg、develop/event）
- LLOneBot / LuckyLilliaBot：github.com/LLOneBot/LuckyLilliaBot、wesley-young.github.io（NapCat 传记，含 LLOneBot 关系与 2026-02 官方群解散事件）
- go-cqhttp：github.com/Mrs4s/go-cqhttp（README + issue #2471）
- Lagrange：github.com/LagrangeDev/Lagrange.Core、lagrangedev.github.io/Lagrange.Doc
- Mirai：github.com/mamoe/mirai、github.com/project-mirai/mirai-api-http
- NoneBot2：pypi.org/project/nonebot2、github.com/nonebot/adapter-onebot、onebot.adapters.nonebot.dev
- Koishi：koishi.chat、github.com/ChatLunaLab/chatluna、chatluna.chat、developer.cloud.tencent.com/article/2728030
- AstrBot：github.com/AstrBotDevs/AstrBot、docs.astrbot.app、github.com/Huanghun542/astrbot_plugin_silent_interceptor
- LangBot：github.com/langbot-app/LangBot、v3.docs.langbot.app（config/function/pipeline、plugin/dev/apis/pipeline-events）
- Gensokyo-llm：github.com/Hoshinonyaruko/Gensokyo-llm
- LangChain 微信 loader：langchain.com.cn/docs/integrations/chat_loaders/wechat
