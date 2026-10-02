# Terminal P1 仿真器隔离集成验收

日期：2026-10-03。结论：接受 headless 仿真器与宿主资源清理子集，不代表完整终端、Runner 组合接线或浏览器恢复已验收。

## 对象与来源

- 实现链：`1daff2a6` → `9cb550f7` → `cd876291`。
- 隔离集成对应提交：`5616aa93` → `f4214844` → `21d583fa`，分支 `audit/pr6-terminal-20261002`。
- 独立复核最终提交：`6fc61f9b`；报告 `PAN_TERMINAL_EMULATOR_REVIEW_20261003_ROUND3.md` 在独立 review 树，未复制为本树自行完成的证据。
- MA 核对实现范围与源 blob：集成对象相对 `cd876291` 的模块、sidecar、测试及接口文档 diff 为空；cherry-pick 无冲突。
- sidecar 最终落点 `packages/core/terminal/emulator_sidecar/`；依赖 exact pin `@xterm/headless@6.0.0`、`@xterm/addon-serialize@0.14.0`。本树仅在该目录安装本地依赖，未改总依赖或锁。

## 接受依据

- 最终独立复跑：42 项直连通过、42 项 uv 隔离通过；7/7 独立并发差量门控通过。源锚定 8/8、sidecar 三件不变、旧证据 hash 未刷新，清理检查无残留。
- close 的调用预算包含锁等待；锁忙有界返回合法失败报告并保 owner，不触碰另一调用的状态；缓存报告的 seconds 表示本次调用耗时。
- StartupCleanupOwner 同 owner 串行化，终止峰值为 1；锁等待计入独立预算，完成后幂等，不重复终止制造失败。
- kill_timeout 三条路径实际传参为剩余预算与配置上限的较小值；最终 root 确认不冒充整树清空，不可核验时保留 owner。
- 前轮已实测整 Job 清理先于 reader/stderr join 与流关闭，根死孙活持 stdout 场景自身收敛，无需外部 kill；构造失败携带可重试 owner。
- 失败阶段分列：r2 独立报告的 pre 为 11 failed / 1 passed；r3 对固定 `9cb550f7` 的新测试为 3 failed / 39 deselected。两者不是同一轮证据。
- 历史核心组合口径明确为六核心 129 + 广播 8；最终 r3 不重复该组合或全库。

MA 在集成树以 uv 隔离环境亲跑三项新增并发/预算测试及核心无 adapter 字面量守护：**4 passed / 53 deselected，22.84s，exit 0**（pytest 使用 `-o addopts= -q`）。未重复 42 项全量、129+8、全库或浏览器。该结果与上述独立复核分列。

## 组合接线约束

1. 输出按绝对字节偏移 `feed_at(seq, data)` 供料；快照 cursor 是 applied 位置，不是已入队 frontier。协议 A 为先恢复 serialized clean state，再从 cursor 拉原始字节，不预喂 pending tail。
2. gap、未知 VT、parser dirty、reset 未确认或 feed lag 必须诚实降级；`cursors_valid=false` 时不得拿 cursor 宣称完整续流恢复。full 只覆盖实测矩阵，不是任意 TUI 保真承诺。
3. resize_wait 只证明引擎尺寸，不能替代 PTY 与浏览器的尺寸确认；三方同步仍待 Runner/服务层组合验证。
4. startup/close 未收敛必须保留 owner 并安排重试；阻塞清理不得直接占用服务事件循环。调用总预算不等于所有 OS 原语的硬 SLA。
5. 已关 guard 的 job_verified 是受限定的语义证据链，不是 post-close 内核查询；不得省略 kill-on-close、句柄所有权及成员继承等前提而外推。
6. Node 依赖部署与启动失败面仍需服务接入验收。本次安装不构成发布部署完成。

## 未验收边界

Runner 尚在独立返工，I-1..I-4 协议映射仍为内部提案；本接受不批准 P2 接线。真实浏览器、自然供料长稳压力、跨 sidecar 重启、POSIX 宿主均未验收。DECSTBM/2026 等序列化缺口仍按 partial 处理。Ctrl-C 产品能力及 ambient Job 下真实 durable detach 仍未验收。

未合 main/practical/PR#6，未 push、build 或重启服务。
