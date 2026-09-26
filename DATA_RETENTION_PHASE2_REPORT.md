# T-DATA-retention-20260927 follow-up 交付报告

## 2026-09-27 Architecture follow-up

- 独立修正提交：`0d747d77`（`fix(data): use canonical Jobs retention contract`）。
- Data retention API 现在只读写 `data_retention` 中的 Data 策略：GET 不再返回 `jobsPolicy`，PUT 只接受 `{policies}`；保存时移除早期草稿遗留的 `data_retention.jobs`，保留顶层 `config.jobs` 不变。
- Data 前端类型、fetch、demo backend 和 App Settings 已移除 Jobs 通用更新字段及“共享策略”表述。Jobs 记录卡片仍保留 `jobsRetentionSlot`，只显示待接入可复用组件的说明，不渲染第二套 Jobs 表单。
- Catalog 将 Jobs 类别标为由 Jobs API 管理，并注明 canonical `GET/PUT /api/jobs/settings/completed-retention` 与 `config.jobs`。五规则的默认关闭、空天数不清理来自 Jobs TA 给定的 API/config 契约；本分支未实现或独立验证 Jobs API。
- 本次复验：`python -m pytest tests/test_data_retention.py tests/test_data_catalog.py -q` 21 项通过；`AppSettingsModal.test.tsx` 30 项通过；`pnpm build` 通过，仍有 Vite 大 chunk 提示；`git diff --check` 与 staged diff check 通过。该 worktree 的依赖 Junction 中没有 Vitest 命令，因此前端 Vitest/build 在已准备的临时源码验证副本中运行，并同步了本次前端改动。

## 交付范围

- worktree：`D:\project\pan-worktrees\data-retention-audit-20260927`
- 分支：`investigate/data-retention-audit-20260927`
- 实现提交：`cde5835bb6901eea70db3e15e581c6f1dacd8992`（`feat(data): add conservative retention policies`）
- 未操作 main、practical、8768；没有 push 或 merge。清理测试只在 pytest 临时目录构造数据。

## 策略与实现

- `data_retention` 保存 Sessions、attachments、QQ history、QQ media、Pan logs 五类策略。全部默认 `enabled=false, days=null`；空天数不启动扫描，代码没有 30 天回退。
- Data 页保留路径目录与可清理策略分区。每个受管策略展示开关、天数、最近扫描时间、扫描/删除/跳过计数和跳过原因。Jobs 使用自己的 completed-retention API/config；Data 只保留可复用 UI 组件接入槽位，不新增 Jobs store、Jobs 清理器或第二套表单。
- 扫描最多每日执行一轮，在专用后台线程运行。Session 元数据/history 删除复用 `sess.release` 与 `sess.delete`；磁盘操作在线程完成，`session.deleted` 在 event loop 发出。状态锁不跨越扫描或 Session 删除，避免 Data 设置读取等待清理完成。异常按类别隔离，不向 Worker/Scheduler 传播。

## 分类与路径安全边界

| 类别 | 清理语义 | 注册路径与限制 |
|---|---|---|
| Sessions、history、queue | 独立保留期；以可信更新时间与最近 history activity 较新值判闲置。缺失、损坏、未来时间跳过。 | `data/sessions`；通过 Session store 整组删除 JSON 与 JSONL。活 Worker、queue、待投递通知、managed/managedBy、活动/未来启用 Job 引用及不明确 Session 引用均阻止删除。 |
| Pan workdirs | 随到期 Session 有条件清理，不是独立策略。 | 只接受 `data/workdirs` 下直接普通子目录；逐层拒绝 symlink、junction/reparse、硬链接文件和路径别名。其他 Session 或 Workspace 路径与候选目录存在相等/祖先/后代关系时保留；外部目录、嵌套路径一律保留。 |
| MCP per-Session config、Kimi isolated HOME | 仅随安全删除的 Session 清理对应 ID 项。清理不明确则跳过并记录原因。 | 固定 `data/mcp-configs/<id>.mcp.json` 与 `data/kimi-homes/<id>`；MCP 限普通文件，Kimi HOME 递归前逐层检查普通目录/文件。 |
| 上传附件及 sidecar | 与 Session 独立；过期后允许旧链接失效。Sidecar 的可验证 `createdAt` 优先，否则用普通文件 mtime；删除文件后同步移除 owner sidecar 项。 | `data/attachments`；只处理登记的完整 upload 普通文件，校验 owner、文件名、配置根与每个候选 resolve 结果；临时、不完整、跨来源归属不清、链接/别名均跳过。Session 删除不触附件。 |
| QQ history | 独立保留期；按每条记录自己的时间移除过期项，保留较新记录并原子写回。格式或任一时间不明确时保留整个文件。 | `data/qq_history`；Pan writer 与清理器共用文件锁，避免清理覆盖并发追加。 |
| QQ media | 独立保留期，按 mtime 清理普通文件；被 QQ history 或 Session 引用时仍可到期失效。 | `data/qq_media`；只遍历受限深度，不跟随链接/重解析点/硬链接；跳过隐藏、`.part`、`.tmp`。`data/qq_inbox` 只展示，永不进入清理器。 |
| Pan logs | 独立保留期，清理过期轮转日志。 | 目录投影取当前 logging handler 的实际文件及父目录；只清理固定 `data/logs` 中同 basename 的 `.数字` / `.YYYYMMDD` 普通文件。活动日志因仍在写入而跳过；外部配置日志不清理。 |
| Jobs records / logs / runs / templates | 规则由 `GET/PUT /api/jobs/settings/completed-retention` 与 `config.jobs` 管理。Data 仅预留可复用组件槽位，不读写或投影 Jobs 策略。 | 目录投影同时列出 `PAN_SCHEDULER_DIR`、`PAN_BACKGROUND_JOBS_DIR` 各自实际根及默认/生命周期根，覆盖两环境变量指向不同目录的情形。没有改动普通 Jobs `_root()` 或其记录读写路径。 |
| Workspaces、Characters/memory、Codex quota cache、QQ inbox、WeChat、startup recovery、config.json、外部 provider/auth HOME、其他 `data/**` | 不自动清理。MCP/Kimi 的 provider HOME 与上述 Session 隔离目录分别处理。 | Catalog 只列代码登记的实际绝对路径、来源/覆盖与 external 标记；目录不存在也展示预期路径。不递归浏览，不读取凭据内容，不接受任意 path/glob。`data/` 内未登记用户自建目录明确排除。 |

目录 API 仍为只读、无 path/query 参数；只调用路径解析和存在性判断，不创建目录。Retention 状态只保存受限计数与原因，不保存文件内容或凭据。普通 Jobs registry 的路径兼容未被清理安全检查改变。

## 验证

- `python -m pytest tests/test_data_retention.py tests/test_data_catalog.py -q`：21 项通过。覆盖默认空值/持久化、时间边界、Worker/queue/managed/Job 保护、Session 整组删除与附属清理、共享 workdir、附件 sidecar、QQ history/media/inbox、路径外逃、重复/失败扫描及最近结果。
- `python -m py_compile packages/core/data_retention.py packages/core/data_catalog.py packages/web/server.py packages/qq/plugin.py`：通过。
- AppSettingsModal 定向 Vitest：30 项通过；`pnpm build`：通过。由于 worktree 的既有 `node_modules` Junction 指向另一 checkout 且缺少 Vite 命令，本次在临时源码副本用当前 `pnpm-lock.yaml` 安装依赖后运行这两项；没有更改 Junction 指向的目录。提交 hook 另报告 `React frontend OK`。
- `git diff --check` 与 staged diff check：通过。
- `packages/qq/test_qq_api.py`、`packages/qq/test_channels.py` 无法收集：Python 环境未安装 `nonebot`（`ModuleNotFoundError`）。因此 QQ plugin 的真实 bot/跨进程并发写入路径未得到原生模块测试验证；核心 QQ retention 扫描本身由上述临时目录测试覆盖。

## 未验证与限制

- 没有连接正在运行的 Pan 服务、调用运行中 API、访问 8768 或做真实浏览器/provider 验收。
- Jobs canonical API/config 契约已提供，Data 页的 Jobs 可复用组件尚未接入；本分支未修改 Jobs 产品代码，也未独立验证 Jobs API 的运行行为。
- 活动 Pan 日志文件会保留；外部日志路径不自动清理。附件到期可能使历史链接失效，这是已确认语义。
- 删除前的路径/引用检查是保守的运行时复核；无法确认的记录会跳过。没有对多进程并发修改 Session/Workspace 元数据做真实服务压力验证。
- 为避开既有 Junction，本地生成了未提交的验证依赖目录 `packages/web/.retention-validation-modules/`，以及临时 UI 验证副本 `C:\Users\14709\AppData\Local\Temp\pan-data-retention-ui-validation-20260927`。自动策略拒绝了对这两个目录的递归删除请求，因此它们仍留在本机，未加入提交；`packages/web/.pan-validation-node-modules.junction` 原样保留。
