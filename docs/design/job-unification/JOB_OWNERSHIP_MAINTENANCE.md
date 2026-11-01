# Job 所有权冲突与维护启动

`OwnerFence.claim()` 遇到已有 canonical runtime 锁或未核实的 legacy lease，仍然拒绝认领。
这两类具名 `OwnershipConflict` 在 web 最外层 lifespan、任何 watchdog/QQ/provider/
恢复/retention/Job loop 启动之前进入维护模式。其它启动错误照常失败，不捕获所有
`ContractError`，也不清空 lease、改变 owner epoch 或自动接管。

平台锁只将确证竞争分类为 `RuntimeLockBusy`：Windows 使用 `LockFileEx` 的原生
`ERROR_LOCK_VIOLATION`，避免 CRT `EACCES` 混淆；POSIX 仅在 `flock(LOCK_NB)` 调用处
识别 EACCES/EAGAIN。mkdir/open/权限/磁盘及其它锁 API 故障原样抛出。
依据：[Microsoft LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)、
[Python flock](https://docs.python.org/3/library/fcntl.html#fcntl.flock)。

维护模式只服务静态 React 维护页面及 `/api/job-maintenance/*`：

- `GET /status`：`mode=maintenance, ready=false`，包含原错误、原因和 owner 数据库资源名。
- `POST /login`：复用该 profile 的 `automation/user.key` 与 UserAuth，会话只在页面内存中保存。
- `GET /records`：需 UserAuth，读取版本化非执行处置清单，不加载旧 payload。
- `DELETE /records/{id}`：需 UserAuth，所有权未知时明确返回 503
  `runtime_ownership_unverified`，即使传入正确原路径对应的 SHA/revision 也不删除。

其它 `/api/*`（包括 `/api/health`）均返回上述 503。所有 WebSocket 在进入原 handler
之前以 1013 关闭。HTTP 进程可访问不等于业务 ready；canonical 写入、执行、恢复均未启动。
UI 显示原身份、相对位置、SHA、revision、不兼容原因和删除受阻原因，不把拒绝呈现为删除成功。

## 正常 owner 下的历史入口与显式删除

正常 canonical 管理页同样展示 `PreservedDataPanel`，复用上述非执行清单、同一 UserAuth
和实际 canonical owner。清单不是新 Job store，也不会执行或自动导入旧 payload。

正常 owner 下的 DELETE 接线到 `migration_history.delete_preserved`，复用
`FinalService.transaction`、`OwnerFence.writer(canonical=True)` 和原 `retire` operation
receipt。只接受显式 UserAuth 请求；必须匹配清单的原相对路径、SHA、revision 和 requestKey。
该动作只对独立 `background_jobs/jobs/*.json` 或 `scheduler/tasks/*.json` 文件执行：
先检查独立 pin 元数据、路径无链接/越界和原文件身份，再将**不解析的原字节** fsync 到
同 profile 的 `preserved-history` 审计备份，最后移除原文件并记录非执行 receipt。
共享 registry 拒绝整文件删除；原 owner 未核实仍返回维护 503，不通过此入口接管或删除。
审计备份无自动 GC；32 MiB 以上文件返回明确容量错误，不做无界内存归档。

正常所有权只证明此删除已由当前 owner 准入，不证明历史资源已停止或 unknown 效果已结算。
返回始终包含 `unknownSettlementAttempted=false`。归档和原历史身份不转换为 Run/Attempt。
同 key 同内容返回原 operationId；不同内容冲突。断在 unlink 后、receipt commit 前时，
只按原路径/hash 的已存在备份核对本地数据处置，不调用任何 executor。UI 网络结果未知时
仅用原 `operation=retire, requestKey` 查询已有 operation，不自动重投 DELETE。

## 离线处置清单

`scripts.job_final_migration.dispositions` 复用既有 inventory，只导出复杂/未知条目的
ID、原相对位置/hash、revision 和原因。无新 Job/Step、旧执行注册或自动迁移。
源始终只读；输出必须在源根之外。真实数据只能使用获准的私有副本，不直接扫描运行 profile。

```powershell
python -m scripts.job_final_migration.dispositions --root C:/private/source-copy --out C:/private/job-dispositions.json --allow-real-profile
```

完成审阅后，将清单作为 `job-dispositions.json` 放入**获准的隔离运行根**。未提供清单显示
缺失，不假称零旧数据；坏清单受控返回 409。启动不会扫描旧 payload 生成清单，不把清单条目
当成可执行定义。简单实例仍遵守原白名单迁移；缺少存储必须单列空初始化。

## 运行中副本与删除边界

稳定文件/SQLite backup 证明副本一致性范围，不证明原 owner 已停。运行中采样得到的
legacy lease 必须原状保留；用新 owner 初始化成功不能代替完整复制 profile 门禁。
完整复制 profile 可以进入维护 web，但源 owner、原 unknown 效果及原文件删除仍 blocked。
恢复生产运行需要沿原离线交接/原身份查询契约获得证据，不能通过修改副本 authority 冒充完成。
实际删除应在原 owner 授权与原路径/hash/revision一致的安全边界内完成；本维护接口不提供绕过。

验证入口：`tests/test_job_maintenance_startup.py`、`tests/test_runtime_lock_errors.py`、
`tests/test_job_preserved_data.py`、`tests/test_job_maintenance_browser.py`。
后者使用真实生产 bundle、独占临时 profile、随机 loopback 端口及 Chromium，核验
1440/390/320px、诊断可见、UserAuth、删除受阻、canonical 503 和 authority 字节不变。
正常 owner 浏览器场景使用独立无旧 writer fixture，同一生产 bundle 与真实 HTTP 服务，
验证正常管理入口、显式确认删除、字节备份，以及丢失成功响应后仅查询原 receipt（DELETE 次数仍为1）。
