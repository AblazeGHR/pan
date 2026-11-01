# Job 所有权冲突与维护启动

`OwnerFence.claim()` 遇到已有 canonical runtime 锁或未核实的 legacy lease，仍然拒绝认领。
这两类具名 `OwnershipConflict` 在 web 最外层 lifespan、任何 watchdog/QQ/provider/
恢复/retention/Job loop 启动之前进入维护模式。其它启动错误照常失败，不捕获所有
`ContractError`，也不清空 lease、改变 owner epoch 或自动接管。

维护模式只服务静态 React 维护页面及 `/api/job-maintenance/*`：

- `GET /status`：`mode=maintenance, ready=false`，包含原错误、原因和 owner 数据库资源名。
- `POST /login`：复用该 profile 的 `automation/user.key` 与 UserAuth，会话只在页面内存中保存。
- `GET /records`：需 UserAuth，读取版本化非执行处置清单，不加载旧 payload。
- `DELETE /records/{id}`：需 UserAuth，所有权未知时明确返回 503
  `runtime_ownership_unverified`，即使传入正确原路径对应的 SHA/revision 也不删除。

其它 `/api/*`（包括 `/api/health`）均返回上述 503。所有 WebSocket 在进入原 handler
之前以 1013 关闭。HTTP 进程可访问不等于业务 ready；canonical 写入、执行、恢复均未启动。
UI 显示原身份、相对位置、SHA、revision、不兼容原因和删除受阻原因，不把拒绝呈现为删除成功。

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

验证入口：`tests/test_job_maintenance_startup.py`、`tests/test_job_maintenance_browser.py`。
后者使用真实生产 bundle、独占临时 profile、随机 loopback 端口及 Chromium，核验
1440/390/320px、诊断可见、UserAuth、删除受阻、canonical 503 和 authority 字节不变。
