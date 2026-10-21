# Event–Trigger–Action v0.1 实施补充（2026-10-22）

用户已授权开始实现，采用隔离策略；不移动 main/practical，候选须 MA 验收。
按设计稿 1.2 建议默认值实施：默认关闭、多事件 OR、单 Action、同字段同次求值缓存；参数命中冻结、Action 开始读取一次最新定义；停用取消 queued，running 继续；失败不自动重试，中断 running uncertain；用户 token 控制启停/删除/已启用执行配置修改；Agent 仅未启用草稿。
Session 选择器 v0.1 支持 explicit/event，query 本版不做但明确列为后续必做，不删除扩展位置。逐目标串行、固定目标集及结果聚合采用原稿建议。
能力方向可覆盖 API/命令，不要求本版适配全部 Pan API；首例必须复用 Pause Everything 业务入口，保留 Group immutable release/pin 和现有业务检查。
实施阶段：核心契约及后端闭环 → 前端/外部插件接入 → 隔离端到端及真人验收。核心阶段交付后停止等待 MA；前端与外部接入待核心契约明确后再拆分。

## 外部插件适配补充

外部适配复用 Catalog 的 `events` 声明及 schema、provider command_ref 解析、Runtime 和实际 Session plugin bindings 启用权威。选择 stdio_jsonrpc 的可协商 `pan-events/v1` 扩展；不新增公开任意发布路由。协议、边界、限额和示例见 [EVENTS.md](../plugins/EVENTS.md)。事件进程是通用插件 Runtime 持有的每 plugin 单实例；工具连接仍按 Session/Panel 隔离，三个 MCP 模式不重复生成事件 owner，也不以第一次调用工具作为启动条件。

连接/generation 绑定身份，禁止 payload 指定 source；最后一个启用成员撤销或断连即撤销 handle、订阅并标 definitions unavailable。重启/重启用实际注册 ready 后由原 Service.plugin_ready 唤醒 queued，不改 Engine/Store/Bus。仅实时扇出、不补投；每订阅者独立有界队列，128 KiB 帧、64 KiB payload、5 秒应答/写入超时；慢消费者失败可见并独立断连。Action Group release/pin 和原三模式业务权限保持原有契约。

本候选已按 MA 指令纳入核心并发修复 `f8bcf34c`（本分支 cherry-pick 为 `d0f93baa`），插件实现不自行修改 engine.py/store.py/events.py。验证分为真实 Node stdio→Service→TriggerRun→Action、真实三模式 MCP/IPC/Node 工具共存、以及自有 HTTP/fakeCLI 服务启动/停用/重启；真人浏览器/实际收费 provider 留待独立验收。
