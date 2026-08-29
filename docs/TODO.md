# Pan 统一待办清单

> 创建：2026-08-27。集中收录散落在各文档中的待办 / 未完成 / 待决策项，每条注明来源文档。
> 各原文档中的对应条目保留原位（或已划线标注），本文件是**唯一汇总视图**；完成某项后请在本文件划线并回写来源文档（若该文档仍现行）。

---

## 一、待决策（开放项）

来源均为 `docs/阶段计划与进度.md`「质量审查整改待决策项」除非另注。

| # | 项 | 说明 | 优先级 |
|---|----|------|--------|
| D1 | **workdir 放行策略** | `_ALLOWED_WORKDIR_ROOTS=None` 对绝对路径直接放行，结合 `/api/fs/*` 可读写删任意目录——鉴权推迟后唯一实际安全边界（A 白名单根目录 / B 绝对路径只读 / C 维持现状） | **优先决策** |
| D2 | **manifest 信任模型** | `_parse_mcp_server` 不校验 command/args/env/cwd，manifest 可声明任意可执行文件被 CLI 子进程执行（A 视为受信记录在案 / B 校验 + command 白名单） | 中 |
| D3 | **server.py 拆分** | God object，70+ HTTP 路由横跨多领域（A 按领域拆 Router / B 抽公共 helper / C service 层） | 中 |
| D4 | **legacy app.ts 去留** | God file；现挂 `/vanilla` 稳定备份入口（倾向 A 冻结，只修 bug） | 低 |
| D5 | **enrich 协议 sync→async** | ⚠️ 2026-08-27 复核：`time.sleep(0.2)` 已移至 `cbc/adapter.py:448`（`enrich_after_result` 内），worker 侧出现 `asyncio.to_thread` 迹象——是否仍阻塞事件循环**待复核**后再决策 | 低 |
| ~~D6~~ | ~~kimi fork 判定~~ | **已解决（2026-08-27 核对）**：`kimi/adapter.py` `fork_args` 已实现目录复制 fork（调 `kimi_sessions.fork_kimi_session`），不再无条件返回 `[]` | ✅ |
| D7 | **Session 原子写 + 缓存锁** | `write_text` 非原子 + `save_async` 并发写 + `_cache` 无锁（临时文件 + `os.replace` + `threading.Lock`） | 中 |
| D8 | **广播并发发送** | `broadcast` 对同一 WS 无锁并发 `send_json`（慢客户端超时已修，连接级锁未做） | 低 |
| D10 | **load_config 缓存 vs 热重载** | 直接缓存会破坏「运行时编辑 config.json 立即生效」（A 缓存 / B 缓存+mtime / C 现状） | 低 |
| D11 | **ANN 升级** | 触发条件型：单 character chunk >~1 万才立项 | 触发式 |
| D12 | **API key 落库改 hash** | `embedder.py` 用 `api_key[-8:]` 作 `provider_key`（改 sha256，旧缓存一次性失效） | 低 |

---

## 二、稳定性 / 测试欠账

| 项 | 说明 | 来源 |
|----|------|------|
| L4 watchdog 分支不确定点 | `pending_signal` 载荷收窄留待真源迁移后与 consumer 侧同步；worker 级 watchdog 是否整体替换为全局级待评估 | 阶段计划与进度.md |
| L5 MCP 隔离细节 | 隔离的权限边界、claim 释放时机、与报告订阅的交互 | 阶段计划与进度.md |
| R1 idle 覆盖边缘窗口 | stream `_read_stdout` 在队列仍有任务时置 idle，`idle_sec` 短时可能提前回收——低优确认 | 阶段计划与进度.md |
| R2 补 3 个集成测试 | stream 端到端序号配对、reconnect 中途断线补发、「超时→kill→重试」幂等组合（修复均已落地，测试未补） | 阶段计划与进度.md |
| 测试夹具用户名 | `tests/test_kimi_adapter.py:19` `KIMI_TEST_WORKDIR` 含本机用户名，其他机器跑测试失败（2026-08-27 核对仍存在） | 跨设备移植报告 / 阶段计划与进度.md |
| opencode handoff 复验 | 原 worker_handoff 已移除；新 `agent_assign`（别名 worker_assign）/`session_handoff` 链路下 opencode stream 完成信号是否仍超时未复验 | design/opencode-adaptation.md |
| opencode fork event 溯源 | fork 经 DB 复制，假定从 session/message/part 恢复；若还需 event 溯源行需补 | design/opencode-adaptation.md |
| HTTP 全链路 sanity（kimi MCP） | 独立端口起 server 走 `/api/sessions`+`/api/spawn`+`/api/task` 链路未跑（worker 级集成已覆盖，低风险） | design/kimi-mcp-solution.md |

---

## 三、功能 / 演进方向

| 项 | 说明 | 触发条件 | 来源 |
|----|------|---------|------|
| P2 SDK 模块 | Worker 快速包装为专用 Agent；形态未定（代码生成 vs 配置模板），需先立项 | 有第二个外部项目（非 RuleWhisper）接入 | 阶段计划与进度.md / 目标与范围.md §7 |
| 事件协议版本化 + stable API 清单 | 外部接入前置 | 第二个外部项目接入 | 目标与范围.md §7 |
| 外部项目接入规范 | 同上 | 同上 | 目标与范围.md §7 |
| Adapter 与 Core 耦合评估 | 是否进一步解耦 | 待议 | 目标与范围.md §7 |
| P2 transport 层 / 两层抽象 | adapter 拥有单轮执行 `run_turn`，支持 ACP/serve 类协议；worker 退化为调度层 | 确定要接 ACP/serve 时立项 | design/adapter-architecture.md |
| kimi ACP 接入 | kimi 官方 stdio 长驻 JSON-RPC；需独立调研，产出 `kimi-acp-adaptation.md` | 另起一轮 | design/kimi-adaptation.md §6 |
| gemini adapter | 调研表已列，未接 | 有需求时 | design/adapter-architecture.md §6 |
| aider 接入 | 收益最低（无 resume/结构化输出） | 低优先级 | design/adapter-architecture.md §7 |
| P1 oneshot Legacy 前端跟随 | `ts/app.ts` 补 executionModes / output mode UI | 低优先可选 | design/adapter-p1-oneshot.md |
| `_extract_cbc_error` 收编 adapter | 仍在 `worker.py:1119`（2026-08-27 核对）；可搬进 adapter 作可选收尾 | 低优先可选 | design/adapter-p1-oneshot.md |
| kimi MCP 方案 A 兜底 | 合并用户级 mcp.json；仅设计保留，未落地 | 方案 C 失效时 | design/kimi-mcp-solution.md |
| 服务端消息队列 | `Session.send_queue` + CRUD API，客户端退化为镜像 | 远期方向 | archive/design-message-queue.md |
| session_import 增强 | import 端点 `_check_session_name` 校验、`action="browse"` 文件树浏览 | 可选，量大时 | archive/design-import-session-mcp.md |
| QQ 富媒体 / 全事件上行 / 反向控制 | QQ 通道后续方向 | 有需求时 | archive/qq-llm-management-survey.md |
| LICENSE | 仓库未附开源许可证 | 发布前 | README.md |

---

## 四、前端细化（活跃计划）

来源：`docs/frontend-polish-plan.md`（全部未动工，其中 P1-1 已核实无对应组件目录）。

- [ ] P0-1 消息交互打磨
- [ ] P0-2 会话列表打磨
- [ ] P1-1 记忆 / 搜索面板（memory/search 前端零使用）
- [ ] P1-2 设置 / Postbox 打磨
- [ ] P2 批量视觉/交互打磨

---

## 五、跨设备移植

来源：`docs/跨设备移植报告-2026-08-19.md`（2026-08-27 复核更新）。

| 优先级 | 项 | 说明 |
|--------|----|------|
| 高 | config.json 去机器化 | `remote.config_path` 可空 + `PAN_CF_CONFIG` / `%USERPROFILE%` 兜底；`plugin_manifests` 移除仓库外引用或改环境变量占位（本机配置，整体拷贝迁移必改） |
| 高 | .mcp.json / data/mcp-configs 相对化 | 用仓库根推导 command/cwd（或 `${PAN_ROOT}` 占位符） |
| 中 | server.py 残余 CWD 依赖 | 约 L691/1838/1953/2095/2264 共 5 处 `Path.cwd()` 兜底（2026-08-27 复核仍在），是否改仓库根推导待决策（与 D1 可合并考虑） |
| 中 | 历史会话迁移 | `~/.codebuddy/projects`、`~/.kimi-code`、`~/.claude/projects`、`~/.codex` 整体拷贝 + 保持项目路径一致 |
| 中 | CDN vendor 本地化 | `packages/web/index.html` 等的 jsdelivr（katex/marked/highlight）+ Google Fonts，离线机器前端渲染失效 |
| 低 | tunnel 凭据 | 整体拷贝带上 `.cloudflared`；否则切 `quick_tunnel: true` |

---

## 六、外部联调（依赖外部环境）

来源：`docs/plans&overviews/RuleWhisper联动方案.md` §6.3/§6.4。需在有 RuleWhisper 插件 + QQ 群环境的机器上验证：

- [ ] §6.3 冒烟测试清单 4 项（QQ 前缀命令路由 + game_id 绑定链路）
- [ ] §6.4 联调验收 3 项

## 七、有意不做（记录在案，非欠账）

| 项 | 理由 | 来源 |
|----|------|------|
| skill 包装 /pan-monitor | SKILL.md §4 已提供直接用法，暂不包装 | plans&overviews/Worker监督与事件驱动模式.md |
| worker_handoff 恢复 | 已于 2026-08-26 彻底移除，串行依赖统一走 assign + report_subscribe / session_handoff | SKILL.md（既定决策） |
| API 鉴权 | 既定姿态：无鉴权 + 绑 loopback，安全重点转向 workdir/manifest 边界（见 D1/D2） | 阶段计划与进度.md D0 |
| Dynamic Tool 注册与执行映射 | **有意舍弃（2026-08-29）**：MCP 已覆盖外部工具接入；Pan 不额外暴露运行时工具回调，Codex 的 `item/tool/call` 返回明确的不支持错误。只有未来出现 Pan 内部运行时工具需求时才重新立项 | Codex 对接审计 |
