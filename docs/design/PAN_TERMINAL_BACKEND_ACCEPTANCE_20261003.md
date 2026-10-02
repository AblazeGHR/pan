# Windows terminal backend 基础安全子集验收

日期：2026-10-03。MA 接受实现 `eabc000c673c374f6876680a35b97866b307075b` 的基础安全子集，选择性纳入隔离分支 `audit/pr6-terminal-20261002`。不表示完整终端交付、主线合入、PR #6 合并、发布或服务验收。

独立审查：`review/terminal-backend-20261003`，最终报告提交 `c3162854`，报告 `PAN_TERMINAL_WINDOWS_BACKEND_REVIEW_20261003.md` Round 6；证据位于该分支 `audit/terminal/implementation/backend-review/round6/`。审查实现副本为 `7b8dcdc1`，与原实现最终修订对应。

- 独立 TA 实跑 backend 54 项通过；核心主环境130通过/7缺pyte跳过，隔离uv+pyte0.8.2为137通过/0跳过。不同环境不得混合计数。MA核对报告与提交范围，不将其表述为MA重新实跑全部套件。
- R12：取消线程与线程句柄配对持有，关闭前确认线程退出；未收敛则有界失败并保留owner，恢复后同owner重试，句柄恰关一次。独立门控34/34通过。
- R13：取消异常脱敏可见且可重试，耗尽实时可见，取消线程退出不冒充写者退出；在途写导致runtime有界拒绝，恢复后重试成功。独立门控25/25通过。
- R3/R4/R5/R6/R10/R11回归通过；R2已证伪撤回，不作修复。

## 必须遵守的装配约束

1. `identity_probe=backend.probe`，不得用fresh PID探针替代原retained handle证据。
2. `write` 的budget是发起取消的截止，不是完成硬时限。正常路径另计join，取消持续故障时仍可能阻塞；必须保留失败所有权和恢复重试方法，不能将此限制宣传为已解决。
3. runner/service不得在事件循环内执行阻塞write/close；用可追踪的线程/任务封装，调用方超时不表示底层操作退出，不可因此释放owner或重复叠加worker。
4. 未收敛取消者与句柄配对必须一直可追踪；秘密、IPC、runner生命周期与detach另行验收。

## 未验收边界

Ctrl-C仍未达到要求；不能授予完整终端能力，不擅自降低用户需求。旧Windows build的HPCON关闭、detach宿主ambient Job寿命、真实OS CloseHandle失败、长稳与跨环境仍未验证。IPC返工未验收，浏览器与无中断原生TUI均不由本次结论覆盖。

本次仅纳入backend的六个原实现提交；独立审查分支/旧证据保持可追溯，不改写历史。最终实现文件应与审查副本逐个blob一致，MA仅补正文档中的旧硬预算措辞。
