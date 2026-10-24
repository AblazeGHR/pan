# 旧 Job 过渡入口：最新 main 组合候选

- 工作树：`D:/project/pan-worktrees/legacy-job-latest-20261024`。
- 分支：`integration/legacy-job-latest-20261024`。
- 起点：`4e1d7fa1307388623dc698938092ede59dece4a6`，包含授权 f03e、安全删除 9418 与最新性能/字体优化。
- 精确受测源码/测试 SHA：`7c29dd28e882ce242c7e3e8ff083efb70c96879d`。后续证据提交不改产品或测试。
- 选择性整合原候选 `0998277b` 的 UI/transport/测试契约，没有 cherry-pick 原候选的过时后端门禁或历史通过证据。
- main/practical 保持 `4e1d7fa1` / `53dff07b`。未移动分支、merge、push、部署或操作既有服务；由 MA 独立验收后决定合入。

## 已实现与原候选返修

1. 现有 Panel 分成 Unified Jobs 与 Legacy Jobs / 旧 Job，搜索、kind 和状态筛选各自保留；列表与徽标仍共享原混合只读 store。兼容 projection 留在旧标签，不提供转换或投影执行入口。
2. 旧标签新增 New legacy Job，复用原 NewJobForm、kind metadata 与 `POST /api/jobs`；旧详情使用原 `/api/background-jobs/{id}`，取消/重试仍走原端点，retryOf 仍指向另一个旧 Job。旧编辑无新 Job 授权面板；旧历史不显示新 Action/Run/Attempt 控制。
3. **未引入 `packages/jobs/legacy_management.py`**，没有 1 秒出生容差，没有进程内取消观察表，没有对运行中 DELETE 再叠加 status-only 拒绝。DELETE 原样到最新生产 `background_jobs.delete_job` 和 `process_cleanup.terminate_owned_jobs`，保留持久 cleanup_pending、retained handle、零容差出生身份及 Job Object 的安全证明路径。
4. UI 增加 cleanup_pending/deleting 类型及状态提示；单条 DELETE 失败后只 GET 最新状态，不自动重删或自动 retry。unknown 清理失败时记录与 ID 保留，cancel/retry 不能绕过清理 admission。
5. 保留最新新 Job 列表/详情授权、token 重新加载、protected API/revision/快照逻辑；未给 background/broadcast MCP 增加权限矩阵项。双轨文档详列 API、registry 可共用物理容器、状态/执行语义及不迁移/不自动转换约束。

产品后端下列文件相对基线逐一 diff 为零：background_jobs.py、background_runner.py、process_cleanup.py、jobs/api.py、scheduler/api.py、web/server.py、authorization.py、auth.py。额度策略、OAuth/private exporter、字体/性能路径也没有变更。

## 实测发现及修复

真实 GUI 在已登录时新建旧消息首次返回 422：main 的 request() 附加 Authorization 后，requestBody() 的 options.headers 覆盖默认 JSON Content-Type，后端收到字符串而非 dict。失败证据保存在 [red-create](red-create/http.log)，包含页面截图和请求清单；不是掩盖或跳过。

本次仅修复通用请求的默认/调用者 header 合并，保留 Bearer 和 JSON 类型；不更改服务器鉴权。补登录态旧 create/patch transport 回归，并让 GUI 真正创建旧消息成功。原生 Headers 规范化后的普通 API 30 项回归也通过。

## 冻结源码验证

全部针对 `7c29dd28e882ce242c7e3e8ff083efb70c96879d`，具体命令在 [commands.json](commands.json)。不是全量测试。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| Python 安全删除/快照组合/旧 API/授权权限与 runtime | 124 passed、0 failed/error/skipped、exit 0 | [api.xml](api.xml)、[api.log](api.log) |
| Vitest Panel 搜索/双轨/详情、原创建表单、store、API transport | 97 passed、0 failed/error/skipped、exit 0 | [ui.xml](ui.xml)、[ui.log](ui.log) |
| TypeScript + Vite production build + 压缩校验 | exit 0；92 个压缩资源 | [build.log](build.log) |
| 随机 HTTP + production Chromium 153.0.8010.12 | 1 passed、0 skipped、exit 0 | [http.xml](http.xml)、[http.log](http.log)、[http.json](http.json)、[browser.json](browser.json) |
| 1280/390/320px 旧标签 | 无横向溢出；截图包含预期的拒绝与通知提示 | [1280](legacy-1280.png)、[390](legacy-390.png)、[320](legacy-320.png) |
| 自有实例清理 | 端口 65460 已释放，身份匹配进程和 Chromium profile 进程均无残留 | [verification.json](verification.json) |

GUI 使用随机端口、临时 data/profile 和真正构建的产品前端，没有 API routing mocks、没有真实模型 Worker。实测：

- 未登录时新 Job 列表隐藏、直接新详情返回 401；用户登录后新记录可见。旧记录不混入新标签。进入未登录的新创建页时可能出现生产现有 protected registry 401 toast，随后正常登录；没有把该预期鉴权拒绝当作页面异常。
- GUI 原表单创建旧消息后仍是 compatibility 记录；两标签搜索输入互不覆盖。旧详情无 Unified execution/Action reference 控制。
- 原后台 get/cancel/retry 路径实际发出，旧 retry 创建新旧 Job 与 retryOf，不发新 Action Job mutation。
- 对**另一个仍运行并有明确子进程 PID**的旧 Job，GUI DELETE 实际经过最新生产安全删除，原任务/runner/后代全部退出后记录消失；这证明没有旧入口门禁挡住最新 Job Object 清理。
- 缺少可证明 ownership 的 unknown Job 在 DELETE 后仍为持久 cleanup_pending，再次 GUI DELETE 返回 job_busy，UI 重读显示 cleanup_pending，原 ID 保留；cancel 返回 cancel_unsafe。
- 新 Action Job revision 保持 1，未因旧操作而改变。protected 旁路与快照/身份的负控由组合 API 测试继续验证。

## 文档与验收边界

双轨能力/数据/registry/API/状态清单与收口条件：[legacy-job-dual-track-transition.md](../../docs/design/legacy-job-dual-track-transition.md)。直到用户明确确认迁移完成，两套语义继续并存；本候选不迁移数据、不自动复制 schedule/Action 参数、不把旧 Job 变成新可执行对象、不重放 unknown/uncertain。

保留最新安全删除本身的边界：无可信 Job Object、身份不可读或进程退出未确认时保留 cleanup_pending，后续显式再核对；不恢复旧候选的服务内观察门禁。没有把真实服务接管、供应商消费、可选独立 Job service 或所有历史数据迁移称为已验证。

既有环境警告：当前 Python 未安装 pytest-timeout（unknown timeout config/mark）及依赖弃用警告，测试自身 HTTP/subprocess timeout 仍在；Vite large-chunk advisory。所有本轮计数均 0 skipped。未访问 8767/8768、用户真实数据、现有服务或其他工作树。

这是交给 MA 的 clean candidate，不是 MA 验收或 practical 已合入的声明。
