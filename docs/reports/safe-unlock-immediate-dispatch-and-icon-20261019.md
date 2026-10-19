# Safe 解锁即时调度与 Illegal 锁图标

实现提交：`935730fae96975f1c4813201ad30006dfeb16043`。分支 `review/legal-offline-illegal-lock-20261018`，原独立工作树；基于 `216148a0e8c469e5289bd194b0405a61ae3b0ed4`。

## 改动

此前 safe 动作原子提交并广播解锁后，没有调用队列唤醒。无活 Worker 时必须等 30 秒全局 watchdog 扫描，再加 CLI 启动时间；这是漏接即时调度，不是产品等待策略。

现在 `clear_session_illegal_for_startup` 在成功原子提交后释放 queue/spawn lock，发布快照，再对有可调度积压的 Session 调用既有 `_wake_worker(auto_spawn=False)`。无活 Worker 时该入口调度现有去重 recovery task，watchdog 只作失败兜底；不等待 create_worker 来延迟 API 响应。释放 spawn lock 后才唤醒，避免 create_worker 重入同锁。失败落盘不唤醒；重复无变化 safe、无可调度工作、Pause 不额外触发。已有 Manual、报告锁、lease、owner、native gate 继续由原交接路径复检。

Illegal 来源的队列锁按钮改为红色斜体小写 i + 右下角小锁。保留原圆角矩形外框、尺寸、aria-label、禁用状态、点击 handler 和解锁 API；Manual 及其它锁保持原图标。使用服务端已有 `meta.lockSource`，补齐 TypeScript 可选类型，无新后端状态。

英文 title：

> This message is locked because the Worker entered Illegal status. You can unlock it manually, or mark the Session as safely offline to unlock it automatically.

## 验证

| 范围 | 结果 | 退出码 | 证据 |
| --- | --- | --- | --- |
| 完整 legal_offline + startup 核心回归 | 80 passed，0 skipped | 0 | `audit/safe-unlock-regression.xml` |
| 故障/锁/即时调度定向回归 | 13 passed（10 个与上行重复，3 个真实 HTTP） | 0 | `audit/safe-unlock-backend.xml` |
| SendQueuePanel 组件 | 5 passed，0 skipped | 0 | `audit/illegal-lock-icon-frontend.log` |
| 新图标真实 Chromium、title、点击手动解锁 | 1 passed，0 skipped | 0 | `audit/illegal-lock-icon-browser.xml` |
| React build + tsc + 压缩资产 | 通过，70 assets | 0 | `audit/illegal-lock-icon-build.log` |
| 环境 Check / commit React hook / diff check | 通过 | 0 | 环境日志与命令输出 |

去重合计 89 个测试通过，0 failures/errors/skips。构建保留既有 import/chunk 警告。原有测试函数名称未因业务迁移随意删改。

即时调度实测使用真实隔离服务与本地 fake CLI：持久 Illegal-only 积压、停止自有实例，再以 test-only 配置把 watchdog 周期延到 3600 秒后重启。取消 Pause 后全锁仍不启动；safe API 成功后在 8 秒预算内观察到 CLI 接收消息，证明不依赖 watchdog。单元回调同时核验 disk 已安全/解锁且 spawn lock 已释放。落盘失败、Pause、Manual、独立运行责任、owner 活/未知和多事故幂等由相关回归覆盖。

Chromium 使用生产构建、真实 HTTP 临时数据根，打开服务端队列，检查红 i 及右下小锁、英文 title，点击后变为普通解锁状态。已查看实际截图并保存 `audit/illegal-lock-icon.png`。初次浏览器探针的队列入口 selector 写错，终止自有测试、改用真实按钮 ID 后复验通过，没有修改产品断言来掩盖失败。

## 边界与清理

所有测试使用隔离入口、自有临时数据根和动态 loopback 非保护端口，无远端模型消耗。未跑全仓库/远端 CI，也未宣称用户实体键盘/生产 supervisor 验收。canonical node_modules 只读复用，未安装依赖；dist 由 build 生成。最终本工作树自有 isolated_http_server/fake_stream_cli 残留为 0。

未移动/合并 main/practical、未 push/release、未重启既有 Pan、未对 8767/8768 发测试请求。现有用户服务需自行重启加载改动。
