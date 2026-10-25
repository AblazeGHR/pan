# Locker 三项改动验收证据

- 分支：`feature/locker-threads-20261025`；基线：`f7555607fcffaa1d0a7398fbc3bb7ad5201fb4c5`。
- 范围：未读定位/分界线/滚到底、移除 answeredAt 投影、可嵌套附着提醒卡。
- 实现契约：`docs/design/locker-threads.md`。

## 已验证

| 层 | 命令 / 入口 | 结果 |
|---|---|---|
| 后端隔离测试 | `python scripts/run_tests_isolated.py -- python -m pytest tests/test_locker_threads.py tests/test_user_cards.py tests/test_locker_messages.py tests/test_locker_unread_summary.py tests/test_user_locker_lifecycle.py tests/test_locker_message_producer.py tests/test_locker_related_context.py -q --junitxml=audit/locker-threads/backend.xml` | 65 passed，0 failed，0 skipped，exit 0 |
| Vitest | 在 packages/web：`pnpm exec vitest run src/views/LockerView.test.tsx src/views/lockerModel.auth.test.ts` | 38 passed，exit 0 |
| TypeScript | 在 packages/web：`pnpm exec tsc -b` | exit 0；无诊断输出 |
| 生产构建 | 在 packages/web：`pnpm build` | exit 0；90 个压缩资产校验通过 |
| 真实 Chromium + 生产 dist + 模拟 API | 在 packages/web：`node e2e/locker-threads.mjs` | 六组断言通过，page errors 为 0；见 browser.json |

后端覆盖三类父项、字段查询/owner MCP 工具响应、旧 schema 升级、类型/owner/recipient 校验、自环与历史坏环拒绝、终态/已读互不联动、事务级联及失败回滚、后代 tombstone、提醒清理与 1500 层删除。

前端覆盖首条实际时间排序未读、附着不移动分界线、已读后的分界线更新、五页上限及可见提示、无时间线未读到底、滚动按钮平滑到底、单份原卡答案、同父排序、平铺父子链、嵌套即时插入、就地错误。
纯模型测试遍历 20000 层及损坏环，无递归或渲染深度上限；原有总量容量规则不变。

截图：`locker-1440.png`、`locker-390.png`（分界线/浮动按钮/附着链），`load-limit-1440.png`（五页上限提示）。

## 验证边界

- 浏览器所有请求经 Playwright route 模拟，未创建 HTTP listener；没有启动或停止 Pan 服务，未操作 8767/8768。
- 后端 HTTP 使用隔离 TestClient；没有宣称真实 HTTP 服务与真实 stdio→HTTP→GUI 全链路验收。
- 没有执行全量后端/全量前端测试、远端 CI、三平台验证、真实用户数据迁移、部署或人工验收。
- 构建保留既有 dynamic/static import 与大 chunk 提示；后端保留 pytest timeout、Starlette、pydantic 兼容性警告，未压制。
- 历史 answeredAt 投影纯前端移除，无历史重写；parent 为新增可空存储列。
- 未 push；候选仅提交本分支，可从基线快进集成。
