# Event–Trigger–Action v0.1 实施补充（2026-10-22）

用户已授权开始实现，采用隔离策略；不移动 main/practical，候选须 MA 验收。
按设计稿 1.2 建议默认值实施：默认关闭、多事件 OR、单 Action、同字段同次求值缓存；参数命中冻结、Action 开始读取一次最新定义；停用取消 queued，running 继续；失败不自动重试，中断 running uncertain；用户 token 控制启停/删除/已启用执行配置修改；Agent 仅未启用草稿。
原核心阶段 Session 选择器支持 explicit/event；query 当时列为必做后续并保留扩展位置。逐目标串行、固定目标集及结果聚合采用原稿建议。
能力方向可覆盖 API/命令，不要求本版适配全部 Pan API；首例必须复用 Pause Everything 业务入口，保留 Group immutable release/pin 和现有业务检查。
实施阶段：核心契约及后端闭环 → 前端/外部插件接入 → 隔离端到端及真人验收。核心阶段交付后停止等待 MA；前端与外部接入待核心契约明确后再拆分。


## Session query 必做后续授权与实现补充

MA 已接受核心候选 f8bcf34c；用户授权在同隔离树继续 query 后端。
采用结构化白名单谓词 and/or/not，七字段 id/name/adapter/managedBy/
readonlySession/pinned/workspaceIds 均可可靠取得；逐字段限制 eq/ne/in/contains。
name.contains 为区分大小写的字面子串；workspaceIds.contains 为精确元素包含，
workspaceIds 使用现有托管根 effective_workspace_ids，pinned 使用共享 Pin 状态。
拒绝未知字段/类型/空布尔组，不提供脚本/SQL/正则或任意属性遍历。
深度8、节点64、操作数数组128、文字512、候选2048、结果256；超限整次失败，
不截断或回退选择全体。执行开始一次查询，按 ID 升序固定去重结果并在副作用前
落盘；冻结后不随改名/移动/新增变更，恢复 uncertain 不重新查询或重放。
零结果成功并保存选择诊断，逐目标业务检查、取消及部分失败语义沿用核心阶段。
新增后端模板/语法发现支持，Agent 草稿仍关闭、Trigger 用户授权和旧 Group/provider
安全门禁不变。完整字段/参数/边界以 core-contract 的 Session query 小节为准。
本任务不改 React、外部插件适配或其他 TA 工作树，不移动 main/practical。
