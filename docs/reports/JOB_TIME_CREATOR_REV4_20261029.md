# Scheduler local once/anchor 输入与迁移 fixture 返修

本轮基线/保留候选：`e17a7c0e8da071723c1cce9d7bdb8b0710f1e4d2`。
最终源码：`475747872a5a89f3d395df95d6bf7b4d347f16ff`，分支
`r6/job-time-creator-line-20261029`。源码逻辑提交：

- `fd30edabb`：只增加三处自有临时 Session fixture。
- `b9864ea68`：服务端只读时间解析、普通 once/anchor 字段与定向验证。
- `475747872`：保留 schema 已接受的小写 t/z 显式时间表示。

本报告取代 [revision 3 报告](JOB_TIME_CREATOR_REV3_20261029.md) 中“普通 once 必须手填
offset”的实现说明；原候选及全部 `evidence/time-creator-rev2/` 红证据保持原样。
未吸入其他候选，未合并/push，未移动 main/practical；无既有服务、8767/8768、真实
data/profile/账号操作。由 MA 独立审阅/组合。

## 普通时间输入

`TimeProvider.resolve_input` 复用平台当前 host zone、既有 `_wall_instant` 的 gap/fold
判定与 `_decimal_add/timestamp`。只解析 whole wall second，再加原 Decimal fraction；
不会通过 float、浏览器 Date 或 datetime 微秒精度截断小数。没有第二计算器、规则引擎、
确认 token 或持久确认记录。

薄入口为 `FinalService.resolve_time_input`、`CanonicalService` protocol 与
`POST /api/event-creators/time-input/resolve`。沿既有 `eventCreator.read` / 用户鉴权，
输入为 `{value: string, timezone?: string}`；value 最长 256，override 最长 64。
响应是只读 snapshot：

| 输入/结果 | 表单行为 | 持久化 |
|---|---|---|
| 普通本地 `2027-01-02 09:30:12.123456789`，无 override | 当前 Pan 服务端主机 zone 求值；显示实际 zone 与 UTC instant；自动解析，不要求 offset 或额外确认 | 解析成功才把唯一 offset-aware UTC `at`/`anchor` 交给原保存 API |
| 本地时间 + 明确 timezone override | 用所填 IANA zone/固定 offset；不使用浏览器 timezone | 同上 |
| gap | `nonexistent/dst_gap`；明确提示本地时间不存在，阻止保存 | 无规则/游标/确认写入 |
| fold | `ambiguous/dst_fold`，返回两个带实际 offset 的 localTime 与对应 UTC instant | 用户必须明确选择一个，再交原保存；不静默取 first |
| 已存在的 explicit at/anchor | 原字符串保留，包括任意小数与 schema 允许的 t/z 大小写；不自动按 host 重解释 | 不改旧数据、cursor、identity 或 pending publication |
| invalid/unavailable | 结构化无效或不可用；不 fallback 到浏览器或另一个 zone | 阻止保存 |

`SchedulerInstantInput` 由 once/interval anchor 两处共用，因此 Event Register 和现有时间
Template 表单使用同一行为。普通输入用可保留任意小数的日期时间字段，不调用浏览器
Date 转换；编辑输入或 override 立即使旧解析失效，AbortController + generation 拒绝
迟到回调。pending、gap、未选 fold 均保持 HTML constraint invalid。

once 在保存时已经成为一个确定 instant：**之后 Pan 主机时区变化不会移动该时刻**。
interval anchor 同样是确定 instant；elapsed Decimal/grid/afterPublication 规则没有改变。
daily/weekly/cron 仍遵循上一候选已确认的 host-following 行为。本轮不改变业务 payload，
不向持久规则添加输入草稿/确认状态，不放松 canonical raw once/anchor 的 offset-aware 校验。

解析入口本身无 SQLite 写入：专项测试对 HTTP 调用前后完整数据库 iterdump 作一致性断言。
普通成功、gap、fold、host-zone 变化、override、不存在 zone、显式 offset、分数秒和 once
保存后固定 instant 都有定向测试。

## 原 14 个红项的诊断与修复

| 原失败集合 | 缺失事实 | 本轮修复 |
|---|---|---|
| pending adoption 的 3 个 phase | `job_def` 用固定 `owned-session`，但未创建 Session | 在该测试创建 `Session(id="owned-session")` |
| source migration 的 10 个 case | successor graph 同样引用未落盘的 `owned-session` | 在 graph fixture 构造时创建该 Session |
| `test_ft01_system_grant_and_high_risk_remain_separate` | 替换成 product Action 后 literal `owned-test-session` 不存在；在 enabled fixture 的 create 阶段先得 400，未到授权分支 | 该测试创建 `Session(id="owned-test-session")` |

Session 文件仅落到仓库 autouse isolation 设定的 `tmp_path/sessions`。没有 monkeypatch
产品目标查找、授权或 schema，没有删断言、改期望、加 skip/xfail。三处 diff 共增加
6 行。原 source bytes、same identity、Run/效果次数、cold restart、uncertain、重复发布、
授权拒绝和 providerCalls=0 的断言全保留，相关 **42 项全部通过**。

high-risk 红项判定为 fixture 缺失，不需要任何共享产品授权修改；该测试现在实际运行
原 agent system-grant 拒绝与 user arbitrary-high-risk 拒绝分支并通过。

MA 提到的 tzlocal 缺失属于环境依赖差异。本轮没有修改其独立 `ma-time-deps` 环境或
为此改产品行为；原声明 `tzlocal>=5.3,<6` 保留。

## 本轮验证

| 层 | 结果 |
|---|---|
| 扩大后端组合 | **498 passed / 0 failed / 0 error / 0 skipped**；exit 0 |
| migration/pending + high-risk 定向 | **42 passed**；exit 0，包含于扩大组合 |
| resolver 首轮定向 | **9 passed**；exit 0，包含于扩大组合 |
| 前端 focused | **5 files / 81 passed**；exit 0 |
| TypeScript / production build | `pnpm --dir packages/web build` exit 0，88 个预压缩资产验证 |
| 新组件 ESLint | 0 errors / 0 warnings；初次 cleanup ref warning 已修正 |
| 真实认证生产 Chromium | **2 passed**；exit 0，pageErrors=0 |
| 常规提交 hook | React frontend OK；未绕过 hook |
| 静态/生成工件 | `git diff --check`、`python -m packages.jobs.final_contracts --check` 通过；无 schema 语义变更 |

浏览器测试服务使用自有临时 profile、随机非保护 loopback 端口，finally terminate+wait；
没有连接既有服务。新用例注入 **Pan host=America/New_York**，浏览器 context 为
**Asia/Tokyo**，普通 09:30 输入落盘为 14:30Z，证明未用浏览器 zone。另覆盖：
Asia/Singapore override；gap 阻止保存；fold 显示 -04:00/-05:00 两个实际时刻并选择第二个；
29-digit 后端/9-digit 浏览器 fraction；普通 interval anchor；原 explicit once 原样保留；
Template publish/默认 disabled 实例化。fake zone 只在测试 fixture 中注入，未改 OS zone/clock。

完整命令、统计、文件哈希见 [summary.json](../../evidence/time-creator-rev4/summary.json)；
JUnit、生产 bundle 日志、浏览器 JSON 与截图同目录。矩阵有重叠，计数不相加。

未验证：全量后端、Linux/macOS 实机与远端 CI、真实 OS timezone/clock 改动、真实 provider
效果、真实数据迁移、部署与人工验收。本轮绿不改变这些边界，也不把旧红证据改写成绿。
