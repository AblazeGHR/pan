# R5-P8 MCP send 原子改名验收

- 分支：`r5/p8-mcp-send-rename-20261025`；基线/提交父节点：`862fc8e8e060f0c8517135a63ee528d789181eb2`。
- 此报告与实现处于同一候选提交；候选 SHA 用 `git rev-parse HEAD` 获取。只提交本分支，供 MA 快进集成；未推进 main/practical，未 push。
- 活动发送工具仅为 `agent_send`（默认更新当前任务）和 `agent_send_to_queue`（仅用于明确留到当前任务结束后的独立事项）。没有增加 legacy 工具或保留退役拼写 alias。
- `agent_send` 保留原能力路由与 `force=true`、稳定事务 ID、暂停/锁/Illegal/租约门禁和 unknown 对账语义；延迟工具保持普通持久队列路径。worker 兼容工具仍委托相应实现；现有群发工具范围不变。

## 变更范围

- MCP 定义/装饰器、worker 委托与说明，核心 MCP manifest 和根 SMA 模板提示词。
- Pan skill、HTTP/MA 参考、双语用户手册、活动设计/计划与文档引用。
- 内部探针、测试、工具清单 fixture；后端/前端的相关注释与 E2E 说明。React 执行逻辑没有变化。
- 完整路径清单：[changed-files.txt](changed-files.txt)。

## 验证

Windows / Python 3.14.5；通过隔离入口清除继承 PAN 设置，使用临时存储和自有动态端口。没有操作实用服务或 8767/8768；真实 transport 测试只创建并清理自身测试监听器/进程，使用 fakeCLI 或合成协议。

| 验证 | 结果 | 证据 |
|---|---|---|
| MCP/transport/manifest/skill/行为及兼容回归（24 文件） | 509 collected，507 passed，2 skipped，0 failed/error；exit 0 | [final.xml](final.xml)、[final.txt](final.txt)、[文件清单](test-files.txt) |
| 最终名称专项、寻址与发送元数据复验 | 24 passed，0 skipped/failed/error；exit 0 | [rename-final.xml](rename-final.xml)、[日志](rename-final.txt) |
| 当前/延迟、task 归属、暂停及 Illegal 锁等行为 | 23 passed；exit 0，已包含于扩展回归 | [behavior.xml](behavior.xml) |
| React messageFilter | 15 passed；exit 0 | [web-tests.txt](web-tests.txt) |
| pnpm install --frozen-lockfile / pnpm build | exit 0；88 个压缩产物核验通过 | [build.txt](build.txt) |
| git diff --check | exit 0 | 提交前检查 |

两项 skip 都来自 `test_addressing_compat.py`，原因均为已有 oneshot 模式退役，没有新增跳过。
已有依赖警告包括 Pydantic forward reference、Starlette/httpx 和旧 streamable HTTP client 的 deprecation；构建仍有大 chunk 提示，不影响退出码。

执行入口：
```powershell
$testFiles = Get-Content artifacts/p8-send-rename/test-files.txt
python scripts/run_tests_isolated.py -- python -u -m pytest $testFiles -q -ra -o addopts= --junitxml=artifacts/p8-send-rename/final.xml
python scripts/run_tests_isolated.py -- python -u -m pytest tests/test_mcp_send_rename.py tests/test_agent_naming.py tests/test_mcp_send_latency.py -q -ra -o addopts= --junitxml=artifacts/p8-send-rename/rename-final.xml
# packages/web 目录：
pnpm exec vitest run src/components/chat/messageFilter.test.ts
pnpm build
```

真实 stdio tools/list 与注册表对照，以及自有 streamable HTTP tools/list 与本地 registry 对照，均验证发送工具集合、无重复名称、描述和 schema。真实 stdio tools/call 还验证退役拼写返回 isError，没有遗留 handler/alias 路由。
MCP→HTTP handler→worker 的行为用合成 stdin receipt 验证：默认更新执行原生 admission，延迟工具持久入队；两者继承 original-task 的 taskId、active 来源和 sender 归属。暂停/Illegal 锁阻止即时交接。原有终态报告归属与幂等测试保持通过。

## grep 证据及历史排除

退役拼写在活动源码、fixtures、测试、提示词与文档中为 0 命中。`git ls-files` 逐文件扫描并统计，同时 `rg --hidden`（遵循 ignore，包含新增测试）复核，退出码 1 表示无匹配。完整路径及行计数见 [grep.json](grep.json)。

保留的不可改写历史证据目录：`audit/**`、`artifacts/**`、`evidence/**`、`docs/archive/**`；同时允许排除 `CHANGELOG*`，本次该处无退役拼写命中。原有历史命中行数依次为 audit 36、artifacts 134、evidence 10、docs/archive 6；单行 XML 可能包含多次拼写，统计单位为命中行。此候选新增的 grep.json 本身记录被检索的退役拼写，归入该证据排除范围。

首轮红证据完整保留：[focused.xml](focused.xml)、[focused-red.txt](focused-red.txt)：109 passed / 7 failed。6 项为新增夹具遗漏 sender Session，并使用了不存在的 Session 级锁字段，已修正为真实 sender 和 Illegal 锁；另 1 项为 catalog 仍要求 P7 已退役 Job 写入口，已改为检查新 send schemas，并增加完整 retired 工具集合缺席断言。没有降低投递、unknown、门禁或报告归属断言。

## 未测边界

未运行全仓后端套件、Ubuntu/macOS、远端 CI、真实 provider/账号、浏览器真实交互、真实用户数据迁移、部署或人工验收。已有服务没有启停，未 push。
