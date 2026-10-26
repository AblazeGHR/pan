# CI 40206573331 Windows 修复候选

基线 `5d0c7f1f471e10d21c287b9820e99dd90276e5f9`；分支 `fix/locker-live-tests-20261025`。
原 CI run 的 HEAD 是 `e8e1763224bf88eb4556b701954534a42c094c01`，不是本次候选基线。
原 run 的 Windows 3.12/3.14 均失败，ubuntu/macOS fast 层成功。
脱敏统计与检查入口见 [validation.json](validation.json)。

## 逐项定位与修复

1. `test_locker_order_scroll_live`：旧脚本要求进入即显示最新 50 条并到底；113 条全部未读时，现规格要求展开到第一条未读并定位顶部。构建完成后本地原脚本复现 Expected 50 / Received 113。更新为精确验证全序、首条未读 ID/分界线/几何位置、手动首条标读后的下一条定位、Read all 后聚合及单源的最新 50 条/底部、分页锚点、新消息不拉走读者、显式标读跨 reload 保留及准确 PUT 次数。真实点击 scroll-to-bottom，并确认不会消费未读。
2. `test_locker_messages_cards_real_service_browser_cli_restart`：旧 `.locker-timeline > article` 选择器已失效，当前每项外有 `data-locker-entry` 容器；原脚本本地与 CI 都得到空数组。改为同时断言真实 article 数量与完整 entry ID 顺序；退出/401 清空检查也改为实际后代 article，避免空选择器造成假绿。补入卡片 ID、逐题作答/拒绝的精确 HTTP 持久化结果与冷重启显示。保留真实授权、stdio producer、附件下载、真实 Trigger Pause、fakeCLI 通知消费、晚到 GET、工作区与多尺寸检查。
3. `test_real_http_stdio_locker_roundtrip_and_restart`：旧脚本要求已答 reminding card 在创建/回答位置投影两次；本地与 CI 都是 Expected 2 / Received 1。更新为单行且保留正文、答案、只读控件、ID、显式 read/unread 与刷新后的状态。fixture 在两个根项之后创建附着卡，精确要求 DOM 为“第一根项、其附着卡、第二根项”，证明附着卡不按自身创建时间插入根时间线。根项标读后，分界线移到第二根项；附着卡独立未读。实际 stdio 作答与服务冷重启后验证 parent 持久化，通知数量严格随新增 fixture 从 4 调整为 5。
4. `test_dual_job_tabs_and_old_lifecycle_over_real_http`：CI 异常发生在 `json.loads(delete_marker.read_text())`，不是 HTTP 500/空响应。`write_text` 创建文件后尚未写完，`exists()` 就被当成 ready；这是独立 fixture 发布竞态。本地原 live 用例通过，受控线程/文件探针可稳定复现旧写法暴露空 JSON。改为同目录 `.tmp` 写完关闭后 `replace(marker)`；探针确认写入期间正式 marker 不存在，发布后 JSON 完整。真实 HTTP/浏览器的取消、删除、重试与 PID/birth 清理断言保留。

## 额外发现的产品缺陷

恢复滚动分页断言后，真实 Chromium 发现：切入全已读来源且 window size 不变时，onScroll 闭包可能一直捕获 `entering=true`，到顶后无法加载更早 50 条。新增 LockerView 单测在修复前稳定得到 Expected 100 / Received 50；产品修复只将回调门禁改为读取 `entryScope.current === scope`，随后单测与真实浏览器均通过。没有恢复旧的默认到底行为。

## 验证结果

| 层 | 结果 |
|---|---|
| Windows Python 3.14.5，四项真实集成 | 4 passed，0 failed，0 skipped，exit 0 |
| Windows Python 3.12.12，同四项 | 4 passed，0 failed，0 skipped，exit 0 |
| 后端 Locker/Card/问题/线程/未读契约 | 87 passed，0 failed，0 skipped，exit 0 |
| Vitest LockerView 与 lockerModel.auth | 42 passed，0 failed，0 skipped，exit 0 |
| `pnpm build` | exit 0，90 个预压缩产物校验通过 |
| 原脚本红证据 | product 与 user-cards 本地复现；scroll 首轮与未完成构建重叠，登录超时，完成构建后单独重跑复现 113/50 |
| 新产品回归红证据 | 新单测修复前 1 failed；39 个未匹配测试被 `-t` 排除；完整绿色测试无 skip |

命令（仓库根执行；前端命令在 `packages/web`）：

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_locker_order_scroll_live.py tests/test_locker_product_live.py tests/test_user_cards_live.py tests/test_legacy_job_transition_http.py -q --junitxml=artifacts/locker-ci-final-314.xml
python scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_locker_order_scroll_live.py tests/test_locker_product_live.py tests/test_user_cards_live.py tests/test_legacy_job_transition_http.py -q --junitxml=artifacts/locker-ci-final-312.xml
python scripts/run_tests_isolated.py -- python -m pytest tests/test_locker_threads.py tests/test_user_cards.py tests/test_interaction_questions.py tests/test_locker_messages.py tests/test_user_locker_lifecycle.py tests/test_locker_unread_summary.py -q --junitxml=artifacts/locker-ci-contracts.xml
pnpm exec vitest run src/views/LockerView.test.tsx src/views/lockerModel.auth.test.ts
pnpm build
```

HTTP harness 使用临时 data/profile、随机非 8767/8768 端口；真实生产前端与 Chromium，没有 API interception。product 的 CLI 是自有 fakeCLI，非真实模型账号。原始本地日志入口为 `artifacts/locker-ci-*.log`、`packages/web/locker-*.log` 与 `packages/web/build-local.log`；本提交只保留脱敏检查与统计，不提交 fixture key/nonce、临时 profile、截图或运行记录。

六份原始 JUnit XML 在统计提取后移入本地忽略目录 `_e2e_tmp/locker-ci-40206573331/`，红证据仍保留；旧的已跟踪审计产物恢复至基线，避免把本轮 fixture 与截图混入候选。

本地 3.14 环境缺少 pytest-timeout 插件，因此存在原有 timeout marker 警告；四项 live 的 subprocess timeout 仍生效。3.12 环境含该插件。另有现有 Pydantic 字段定义警告，无测试失败。

未验证：候选远端 CI、全量 Windows pytest、候选 ubuntu/macOS 重跑、真实 profile 迁移、部署、真实 provider 与人工验收。本次没有修改 CI，没有 push，没有推进 main/practical，没有启停 8767/8768。
