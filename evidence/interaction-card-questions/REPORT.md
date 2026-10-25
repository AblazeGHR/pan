# 多问题交互卡候选验收

基线：`0bb3971ef2a1c334b83d8eb172f3518607a4cfab`。候选分支：`feature/interaction-card-questions-20261025`。
候选提交包含本报告；完整 SHA 由该分支 `git rev-parse HEAD` 获取。仅在当前候选分支提交，main/practical 未推进。

## 实现

- v2 卡使用 questions[]；题目稳定 id、三种题型、默认 required、按题 answers map、容量校验。
- 一次事务保存整份 answers、终态与交付意图，不存草稿、不允许提交后修改。
- `{rejected:true,reason?}` 与正常答案并列。required 已答或已拒绝即可提交；可选题可省略。
- 整卡 reject 与全部题 reject 都为 rejected，只有这两种情况创建卡级拒绝通知；混合答案为 responded。
- trustedChoices 按 questionId/choiceId 分层，整卡用户认证与所有选中执行项的复检先于保存。
- 每个选中 Trigger 单独持久 Event 身份；unknown 只查询原 ID，重启与删除后继续核对。
- v1 原卡不迁移，保留原 choices/body/answer 行为；旧单值 text 入口归一化为 answer。
- MCP create/get/receipt 支持完整 v2 定义和答案，MCP 不提供用户回答授权。
- Locker 纵向逐题控件、逐题拒绝与可选原因、单一提交、逐题必填提示、全部答案只读展示。
- 未读按卡统计。既有 no_answer_needed、删除与 tombstone 边界保持。

## 最终验证

| 层 | 结果 | 证据 |
| --- | --- | --- |
| 隔离后端定向回归 | 148 passed / 0 skipped / exit 0 | [backend.xml](backend.xml) |
| 前端 Vitest | 35 passed / 0 skipped / exit 0 | [frontend.xml](frontend.xml) |
| TypeScript | pnpm exec tsc -b，exit 0 | [verification.json](verification.json) |
| 生产构建 | pnpm build，exit 0，90 个压缩资产核验 | [build.log.gz](build.log.gz) |
| Chromium 生产 bundle | required 就地提示、混合作答/拒绝、唯一提交、只读结果、1440/390 可滚动且无横向溢出 | [browser.json](browser.json) |

后端覆盖：混合 single/multi/text/reject、required、未知 ID/错误类型/重复选项/超长答案、20 题完整 Unicode、全 reject、整卡 reject、旧卡兼容、trustedChoices 作用域与变更拒绝、并发唯一胜者、重复内容冲突、unknown 原身份核对、删除后交付、来源通知与回执、卡级未读统计。
前端覆盖：每题错误、选中与拒绝原因、未授权选项禁用、撤销预提交拒绝、只读全部答案、超长提示、unknown 冻结原 payload 重试、特殊稳定 ID，以及既有 Locker 回归。

截图：[1440 编辑态](questions-1440.png) · [390 编辑态](questions-390.png) · [1440 只读态](readonly-1440.png) · [390 只读态](readonly-390.png)。
已查看桌面只读与移动编辑截图。浏览器全部请求由 Playwright 拦截并返回测试数据，未创建 HTTP listener；此层不证明真实 HTTP/MCP 传输。后端路由使用 TestClient/ASGITransport，MCP 方法使用受控调用适配；没有真实 stdio 进程测试。

## 容量与兼容

20 题完整 Unicode 答案可超过 64 KiB，因此 v2 交互 payload 使用 2000000 字节有界上限；仅受信 core 交互事件显式扩容。v1 卡、其他事件和插件默认上限保持 64 KiB，EventStore 总容量保持原限制。
全卡授权复检后，各 Trigger 的接纳仍可能独立失败；delivery 汇总实际状态。accepted 仅表示接纳，不能当成 Action 执行完成。

## 保留的红证据

[red/](red/) 保留原始早期失败（含无损 gzip 日志）：新增 MCP 回执测试漏读 receipt 包络；新增容量关键字影响旧卡丢回执测试调用；前端测试误用自身 disabled 属性判断 fieldset 继承禁用；HTTP 测试域名缺少 secure-context crypto.randomUUID。
分别修正测试读取、恢复 v1 调用形式、验证有效 :disabled 状态、改用全请求拦截的 HTTPS 域名后，最终测试均通过。未降低产品断言或增加 skip。
构建仍有包体 chunk 大小提示；pytest 有环境已有 timeout 配置、Starlette/Pydantic 弃用类警告，无失败或 skip。

## 边界

没有 push、没有实用服务启停、没有 8767/8768 操作、没有真实 profile/data 迁移。临时 SQLite 和进程内 Automation 对象只用于隔离测试。
未执行远端 CI、其他平台实跑、真实 HTTP 服务集成、部署与人工验收。没有推进 main/practical。
精确命令、退出码、源码和 bundle SHA-256 见 [verification.json](verification.json)。
