# Event–Trigger–Action v0.1 实施补充（2026-10-22）

用户已授权开始实现，采用隔离策略；不移动 main/practical，候选须 MA 验收。
按设计稿 1.2 建议默认值实施：默认关闭、多事件 OR、单 Action、同字段同次求值缓存；参数命中冻结、Action 开始读取一次最新定义；停用取消 queued，running 继续；失败不自动重试，中断 running uncertain；用户 token 控制启停/删除/已启用执行配置修改；Agent 仅未启用草稿。
Session 选择器 v0.1 支持 explicit/event，query 本版不做但明确列为后续必做，不删除扩展位置。逐目标串行、固定目标集及结果聚合采用原稿建议。
能力方向可覆盖 API/命令，不要求本版适配全部 Pan API；首例必须复用 Pause Everything 业务入口，保留 Group immutable release/pin 和现有业务检查。
实施阶段：核心契约及后端闭环 → 前端/外部插件接入 → 隔离端到端及真人验收。核心阶段交付后停止等待 MA；前端与外部接入待核心契约明确后再拆分。
