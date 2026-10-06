# Terminal 合入前核验（2026-10-06）

## 对象与操作边界

- 候选工作树：`D:/project/pan-worktrees/terminal-final-integration-ma-20261004`，分支 `integrate/terminal-ma-final-20261004`。
- 本轮 main / practical 快照均为 `362d47ff0cd926275dab96b1b54aff7b256ef109`。
- `git ls-remote origin refs/heads/main` 核对远端为 `9d474b02`，与 origin/main 相同，且是本地 main 的祖先；没有未纳入的远端 main 更新。
- 候选以 `596a4915` 为起点，`git merge --no-edit main` 无冲突，生成 `d34dd978`。
- 对照树 `terminal-premerge-baseline-ma-20261006` 为 detached `362d47ff`，只用于归因；其 node_modules junction 为本轮自建，目标是候选树依赖目录。
- 本轮没有推进 main / practical，没有 push、重启实用服务或修改实用数据；8767 / 8768 未被测试接管。

## 旧数据与回退

新增 `tests/test_premerge_legacy_data.py`，8 项通过，数据均来自临时合成 fixture。

1. schema 1 Terminal 缺少 archived / cleanup_pending 时默认 false；反复 get / list 前后原 JSON bytes 相等，身份 raw FILETIME 大整数不失精度。
2. 显式归档 / 恢复保留 PID、raw FILETIME、scope 和退出事实；新增标志只认真正 bool。
3. 旧 Session 顶层 cbc_session_id 仍进入 adapter_config；history_epoch 稳定；缺失 Rewind sidecar 不创建目录、不改写旧 Session。
4. Job 的 ISO / 数字 / 缺失 / bool / 非法 / 非有限时间戳混合可读，读时默认值与排序不改写旧文件。

Session 既有 loader / to_dict 相对 main 未改；新增 duplicate-free 创建辅助不构成存量迁移。Rewind 是独立 sidecar。Job 变化为读时排序兼容与用户已授权的任意现存 cwd，不是全库重写。

这些证据不表示检查过每条真实用户记录，也不授权直接迁移或清理实用目录。损坏 JSON、未知未来 schema 与真实权限损坏仍应显式报错，不猜测修复。

回退代码前必须先处理新代码管理的运行期资源：旧 main 没有 Terminal / Rewind owner 消费逻辑，不能把“旧 Session 能读”当作“活终端可直接降级接管”。归档只是隐藏记录，不停止进程；删除需要整树清理证明，根 PID 死亡不等于全部资源已清。

## 回归归因

- 前端首次全量：1320 项，1317 passed / 3 failed。三项旧队列断言未包含新 optional parts 参数的显式 undefined。
- 对照 main 同三文件：82 项，79 passed / 相同 3 failed，证明不是 Terminal 生产回归。
- 候选只补三处第六参数断言；第二次全量 1319 passed / 1 个 SessionList realBackend 5 秒超时。该文件单跑 16/16，随后全量 1320/1320。
- lint：候选 85 errors / 19 warnings；main 85 errors / 18 warnings。新增 error signature 为 0；多出的是 RewindConfirmModal 共享标签常量触发 Fast Refresh 建议警告。没有把 lint 宣称为通过。
- 生产构建通过 TypeScript / Vite 与 50 个预压缩资源验证；bundle 大小警告仍存在。

初始失败、对照与复跑 JSON 都保留在 `audit/terminal/implementation/premerge-20261006/`，没有用最终绿结果覆盖首次失败。

## 真实链与遗漏边界

- 当前候选生产 bundle：Chromium 真实 Terminal 全链 passed / harnessExit 0；中文、Ctrl-C、resize、重连、browserless recovery、detach / stop。
- 自然非零退出链 passed / harnessExit 0，退出后再选中仍可操作记录，不伪造 output_complete。
- 归档链确认 archive 不停止、restore、手动工作区、close / remove；本轮使用新 label，旧 archive-scope 证据未覆盖。
- 显式添加 python-dotenv 对照补跑 quota / MCP handbook / Session usage API，26 passed；XML 后核对这 26 项已经在首次全量中执行（MCP 间接依赖已带 dotenv），**不把它们计作此前漏跑或额外唯一通过数**。临时 uv 环境不修改主环境或依赖锁。
- 显式 `pytest tests/` 不覆盖 pytest.ini 的另外两组默认 testpaths；补跑 `packages/qq packages/wechat`，166 passed（临时 QQ 依赖、全部假网关 / 离线业务，不使用真实账号）。它们不是本轮 Terminal 全库结果中的重复计数。
- 未做真实 provider Rewind 代码回滚、完整浏览器旧功能 E2E 总集、跨用户 / 跨主机、非 Windows、跨 sidecar 重启、长期压力与所有部署形态；不能用单元测试代替这些层。
- 原始目标“同一个正在运行的 Agent 无中断原生 TUI ↔ Pan GUI 切换”仍未实现；独立 Terminal 重连和 Rewind 不等价于此能力。

### 后台测试入口

按用户后续要求，长测试使用 Job 投递。当前实用服务仍运行旧 cwd 限制；直接指定
候选 worktree 被静态拒绝、没有启动任务。无需为测试重启服务：Job cwd 使用允许的
`D:/project/Pan`，argv 指向候选树绝对路径 `scripts/run_tests_isolated.py`，该入口再
切到自身仓库、清除继承 PAN_*。定向 Job `job_a254a9589ac497ba10aa462e` 终态
completed / exitCode 0 / delivered；其 XML 为 1 passed，源码与本轮生产候选相同。
通知投递不替代 XML / 退出码 / 资源核验。先前已运行的最终全量未被中断或重复启动。

## 部署前置（尚未执行）

1. 经用户授权后再推进 main / practical，并确认目标树无须保留的未提交重叠修改。
2. 备份实际 config 与数据根（Session / Job / Terminal / Rewind 及受保护凭据）；本轮未接触实用秘密。
3. 在目标环境安装 minimal Python requirements。传统 requirements.txt 经 dev-requirements.txt 包含 minimal。Windows pywinpty 与 pyte 已列入。
4. Web 执行 `pnpm install --frozen-lockfile` 与 `pnpm build`；另外在 `packages/core/terminal/emulator_sidecar/` 单独执行 `npm ci`。Web build 不安装 headless sidecar。
5. 重启前盘点活 Worker、Job、managed / detached 终端；依据身份和布局确认收尾或 detach，不按名字 / 命令行批量杀。代码合入不等于运行中的服务已加载新模块。
6. 授权重启后做实际部署 smoke：旧 Session / Job 列表、Terminal create / Chinese input / resize / Ctrl-C / archive / remove、资源和日志。远程公开访问需要单独鉴权验收。

## Python 全量

首次 `tests/` 全量：2782 passed / 3 failed / 10 skipped，18 分 23 秒。三失败均为
`test_browserless_refresh_requires_all_three_proofs` 旧夹具缺失 registry；当前 refresh
已统一委托真实 reconcile，测试改为正式构造临时 Service / TerminalRecord / state，
不 mock reconcile，保留三证明门并增补缺证明时 cleanup_pending 的断言。39 项相关
自然退出 / 归档 / 旧数据集合通过，生产代码未改。

跳过项逐一审计：3 项前端顺序探针因硬编码已消失的 sibling esbuild 而跳过；探针
现优先使用本树依赖，更新 HTTP/UI 替身导出（真实 reducer / ordering helpers 不替换），
断言未放宽。首次解锁后的 3 errors 是旧 probe 的缺导出，保留失败 XML；修后该组及
tzdata 支持的 cron 组合 46 passed。Job 日志 reparse 用例以本 fixture 两条路径创建
真实 junction 作为无 symlink 权限时的等价负控，42 项 retention 测试通过。

最终 `tests/` 全量：**2797 passed / 1 failed / 5 skipped**，17 分 52 秒，2803 项。
唯一失败是组合用例把较早的 producer total（335051）与稍后已前进的 applied
快照（335116）比较为严格相等。追平 helper 本来允许 cursor >= 旧 total，后面的
相等断言却使用旧边界。修正为有界等待 producer 稳定、取得快照后重读 total，
只有三者严格相等才接受；增加“迟到输出必须重试”和“边界未确认必须失败”两个
确定性门控。没有改生产，也没有将相等断言放宽为 >=。

修正后 Job `job_91948a3320856c0ffe2e9e0a` 执行整个组合文件，**15 passed / 0 skipped**，
退出码 0（原 13 项 + 新 2 门）。全量原始失败 XML 保留，不据定向修正声称最终
完整套件曾全绿；为减少重复消耗，没有第三次重跑全量。

五项环境 skip 为本地 skill 同步副本、三项机器私有 Kimi 会话数据、祖先 Job
限制下 detach。不会复制虚假机器私有 fixture 或绕过宿主限制来制造通过。

等待全量 XML 的 Job `job_6dc9647aefcf847237170fc4` 通知为 exit 0，但 PowerShell
默认编码读取报告时产生非终止 XML 解析错误，未正确传播测试状态；其 exit 0
**不作为测试通过证据**。最终事实以 pytest 原退出码 1、UTF-8 XML 与失败 traceback
为准。后续测试直接由 Job 运行隔离 pytest，传递真实退出码，不再使用该等待脚本。

## 收尾结论

合入前准备已完成：生产候选仍为 main 合并后的 `d34dd978`；本轮只提交测试、
探针、文档及证据修正。旧数据 8 项、前端 1320 项、QQ/WeChat 166 项、三个隔离
浏览器链与生产构建通过；Python 全量原失败和修正后受影响 15 项结果分列。
lint 85 个既有 errors 未扩大，也未解决，不称 lint 通过。

可以交用户验收并准备授权合入，但不等于已合入、部署或全部原始功能目标完成。
main / practical 仍是 `362d47ff`，服务未重启，实际数据未迁移。5 小时额度阶段核对
为已用 68%、剩余 32%（2026-10-06 04:11:42 UTC 的非过期缓存快照）。
