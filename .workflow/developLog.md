# Pan 开发归档日志

## 2026-09-22（待真人验收，不构成归档）

- `cba9df0d143127b10500e237b0468a80f521b18d` · `T-FRONTEND-CONSOLIDATED-20260922` · `feature/frontend-consolidated-20260922` 完成选择性前端集成；TA 已完成，MA 已验收整合范围。Vitest 649/649、TypeScript/build、消息探针 9/9、reconcile 3/3、event-loop 13/13、120k 冷历史 E2E、Chromium CBC strict 8/8 通过；全库 pytest 仅有既有 Codex quota docstring 失败。当前 `main@591367a65f88e9d5270e8e99920ef5448d1d68c9` 尚未合入，尚未 push，尚未真人验收；真实 provider、生产 8768 未验证，Steer receipt 服务端协议仍是后续缺口。报告：`D:\project\pan-worktrees\frontend-consolidated-20260922\FRONTEND_INTEGRATION_REPORT.md`。

## 2026-09-19

- `ad94e9fa63b2862977d03e3439896f31dad14dde` · T-060 · 任务概括：以 Pan skill 验证任务概括与可执行验收写法；实验提交 `a6966e2840ed747cbea5460a4b1af89229b94b2c` 后，按“workflow 与 Pan 隔离”恢复 Pan skill 范围；最终边界提交位于 practical，不合入 main、未 push，任务关闭。
- `754110fde02943234fac523c02483637726d8cc8` · T-059 · 任务概括：将已完成的前端性能、Usage 缓存、草稿附件和测试基线任务按依赖顺序合入 main，并用共享前端/后端定向回归与隔离 Chromium 验证整合结果；批次整合完成，开发者行为验收仍由 overview 跟踪。
- `e4ca61cb3e93bbc4aa788ab929a768dcf42221dc` · T-054 · 任务概括：整理 workflow overview、任务索引和验收入口，清除过时执行状态并保留可定位的历史证据；文档维护已整合，后续不作为活跃任务。

## 2026-09-15

- `31e1ee310cbf1082b01a7f0282fc817f4a1f5a79` · T-004 · 完成 Windows 窄编码日志兼容与 CI Python 版本同步；已合入 main，开发者验收通过。
- `0e431e57d4bc53aabc15a1427cc59800ebc256a1` · T-007 · 完成 MCP `model_list` 的 adapter 显式发现与错误提示；已合入 main，开发者验收通过。
- `65f82b06ab795ce598cfa6a901b6b0fd333b4b66` · T-009 · 完成 Vanilla 前端移除、React-only 路由与 dist 缺失保护；已合入 main，开发者验收通过。

## 2026-09-13

- `2073aa267aa75870a1bf5e944b87b4a6f6a05561` · T-025 · 按 DEC-001 的 A + C 完成普通文件 paste/drop、目录第一阶段拒绝、session-scoped AttachmentRef/MessagePart 结构化协议、客户端/服务端附件统一、queue/WebSocket/history/重试恢复兼容和旧 Markdown/text 格式兼容；真实 Chromium 与隔离 API、前后端定向测试及构建门禁通过，开发者已验收。

## 2026-09-11

- `f497d35196e7bbddde3aee564619871acc277edc` · T-002 · 完成 Session Details、重命名、Usage/System prompt 折叠与相关 UI 行为；已合入 main，开发者验收通过；后续 T-020 为独立任务。

## 2026-09-09

- `51a159ccdab049be01253345c3919fb3b93a944c` · T-003 · 完成 ChatMessages 底部跟随与 Scroll to bottom 行为；已合入 main，开发者验收通过；exact-bottom 基线问题保留为独立证据。
- `bf73b5bba1dbcf8667ca042a41ffc0e521456a06` · T-001 · 完成 Session prompt 拆分与非递归交接准备；已合入 main，开发者验收通过；后续 Session prompt UI 由独立任务追踪。
