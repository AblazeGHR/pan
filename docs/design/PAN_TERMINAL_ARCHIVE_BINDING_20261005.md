# 终端归档、外部退出与绑定规则

## 用户可见行为

- 归档：只隐藏默认列表中的记录，不终止进程、不丢弃清理 owner、不删除凭据。
  勾选“已归档”可以查看记录，并“恢复到列表”。运行、清理未确认、已退出均可归档。
- 删除：永久删除终端记录，不删除用户工作目录。运行中的终端先请求关闭；
  清理未确认、仍有凭据或仍保留 owner 时拒绝删除，并保留记录供重试或归档。
- 外部终止：原保留进程句柄的 PID + raw FILETIME 匹配且已 signaled 时，
  显示 `exited`，不让 fresh PID 查询的 UNKNOWN 覆盖原句柄的死亡证明。
  没有 runner 整树清理报告和引擎收尾确认时，另标 `cleanup_pending=true`。
  该标记不是“进程仍活着”，也不是清理成功；前端仍允许归档/恢复/重试。
- PID 不符、FILETIME 不符或只有 fresh UNKNOWN 不伪造成死亡；包装 launcher/shim
  死亡也不能证明真实 runner 已死亡。此类记录仍可归档，但不允许强行删除 owner。
- 已绑定 Session：REST 创建或“应用绑定”时读取真实 Session 的有效工作区，
  包括 managed-tree 根工作区；不采用同时提交的任意工作区覆盖它。
  未知 Session 返回静态 422，原绑定不变。
- 手动输入工作区：立即清除表单中的 Session；点击“应用绑定”后持久化该工作区
  和 `session_id=null`。输入 Session 则清空手动工作区，服务器成功后回填有效工作区。
  这是关联元数据，不授予权限，也不会对已经运行的 shell 执行 `cd`。

## 持久化与接口

记录增加 `archived`、`cleanup_pending` 两个布尔字段；旧 schema 1 记录缺字段时默认 false。
公共 REST 视图严格投影这两个字段，不透出 pipe/token。

- `POST /api/terminals/{id}/archive`：`{"archived":true|false}`；空对象默认归档。
- `POST /api/terminals/{id}/scope`：可选 `workspace_id` / `session_id`；空值可解除绑定。

两条接口沿用 Origin/Host/Fetch gate、body 限制、未知字段拒绝和调用槽预算。
清理事实与归档元数据分开；查询不会仅为了刷新状态而发送 stop。

## 界面整理

创建/关联区域与操作工具栏分组，加边框、背景与间距；显示未归档/已归档数量。
增加归档筛选与恢复动作，外部退出的清理未确认使用独立提示，禁止向已退出终端
请求输入控制或重连。列表每 5 秒刷新进程事实；已退出时不承诺历史屏幕持久化。

## 验收步骤

1. 创建终端，归档：默认列表消失，但进程仍活着；勾选已归档并恢复。
2. 选中 Session 绑定并应用：工作区自动回填它的有效工作区。
3. 手动输入另一工作区：Session 清空；应用后刷新仍保持新工作区与空 Session。
4. 从外部终止自有 runner：最多一次列表刷新后显示已退出；缺清理证明时提示
   清理尚未确认，但归档和恢复仍可用，凭据与 owner 不被悄悄丢弃。
5. 正常关闭后删除：记录消失；重复删除返回不存在，不删除用户工作目录。

自动化证据在 `audit/terminal/implementation/browser/`：新增边界测试覆盖归档
恢复、身份死亡与清理区分、真实外部终止、旧记录兼容、绑定与 HTTP gate；
React 测试覆盖两类交互。`archive-check.mjs` 是隔离真实浏览器/PTY 链，
`archive-scope/result.json` 与截图记录归档不停止、恢复、手动绑定和终止删除。
开发期第一次浏览器脚本重试依据旧 alert 抢跑，误点已完成删除的禁用按钮；
修正为等待操作结束（按钮重新可用或选项清空）后通过，未将它算作产品缺陷。

本批不修改 main/practical，不重启用户验收服务；使用最新源码需重启其验收实例。

## 本批验证结果

- service / rework / registry / REST / remove / 新边界文件整组合：**158 passed**，
  313.74s，`terminal-boundaries-final-uv.xml`。随后追加两个跨重启记录门控，
  新边界整文件 **11 passed**，`terminal-archive-supplement-uv.xml`；
  不是再次运行整个新增后的 160 项，也不把重复用例相加称为不同用例数。
- React 面板 + WS 客户端：**26 passed**。
- `pnpm build`：TypeScript、Vite、36 个压缩产物校验通过。
- 隔离 Chromium + 真实 TerminalService / PTY / REST：归档不停止、恢复、
  手动工作区绑定、关闭并删除通过，无页面异常，harness 正常退出。
- 真实外部终止测试使用 PID + FILETIME 核验的自有 runner；预先保留自有后代
  句柄，终止后逐个同句柄确认 DEAD，并释放测试句柄，不按名字批量杀进程。

保留的开发期 `terminal-archive-external-uv.xml` 有一次失败：正向测试监听了
错误的 secret 删除 helper（真实路径调用 store.delete_secret），测试探针修正
后通过；这是测试自身错误，不是以它声称修复前产品缺陷。
旧的“根 DEAD 即可删秘密”断言改为“整树未证明就保留”，配套新增正向门控
确认全部清理证明具备时仍删除，未撤销身份 mismatch / ALIVE / UNKNOWN 负例。
