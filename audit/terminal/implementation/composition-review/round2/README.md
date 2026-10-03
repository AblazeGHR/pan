# 组合 r2 审查证据（3a579da6）

- 被审对象：`3a579da64c249af35fb20fbfcab6340263d10fa6`（父 `7e94f13a`；seed `26b7086b` 含
  已接受 emulator r4（63e 链）与 Runner F5 最终 `29d535bc`；首轮 `7e6b30cb` 以 cherry-pick -x
  导入为历史）——"修正后真实组合验收（ROUND2）—— F1 硬断言/F2 容量口径/F5 真机字段/驱逐边界/宿主收尾"。
- 审查树 `D:/project/pan-worktrees/terminal-composition-r2-review-20261003`
  （branch `review/terminal-composition-r2-20261003`，HEAD==3a，clean 预检）。
- 审查报告：`docs/design/PAN_TERMINAL_COMPOSITION_SECURITY_REVIEW_20261003_ROUND2.md`。
- 本目录：**composition-review/round2 新增**审查产物（首轮/上层旧证据零覆盖）。
- 纪律：被审生产/tests/doc/旧证据只读；旧 runner-review/composition-review/observability-review
  及实现树未动；唯一写动作 = sidecar 专属目录 `npm ci`（exact pin、未改 lock，node_modules gitignored）；
  自建资源同 handle raw FILETIME+Wait 核验清理；硬看门狗；未跑 50/47/48/core/全库/浏览器/长稳；
  无广杀/stash/reset/子代理/全局记忆写/主线操作。

## 1. 目录

| 路径 | 内容 |
| --- | --- |
| `scripts/rerun13.py` | 13 项组合套件独立复跑（直连 + uv；`-o addopts= -q`，UTF-8 .txt 分流） |
| `logs/` | 复跑 stdout/stderr + `summary.json`（含汇总行） |
| `evidence/direct13/`、`evidence/uv13/` | 每用例 JSON（PAN_TERMINAL_COMPOSITION_EVIDENCE_DIR） |
| `gates/v_common.py` | round2 门控设施（进程内探针 + 真机会话；复用旧审查树本审查自产 launcher，只读） |
| `gates/v1_f1_states.*` | F1 四态（spawn 前/EOF/fatal/**timeout**）owner + retry 收敛 |
| `gates/v2_capacity.*` | F2 单 monotonic（首次提交→applied==total，不扣 probe）；ops/批分列；OSC partial |
| `gates/v3_live_session.*` | 真机：驱逐 gap/fresh-view、追平重取、resize 分列、独立心跳、正常收尾、硬死布局 |
| `gates/v4_scope_evidence.*` | MA 静态注意裁定（launcher 单 close / close-budget 归属 / §3 表述）+ 证据审计 |

## 2. 独立复跑（分列）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 直连 | `pytest tests/test_terminal_composition.py -o addopts= -q -rA` | **13 passed / rc 0**（59.47s；`13 passed in 58.84s`） |
| uv 隔离 | uv 0.9.14 `--no-project` + minimal-requirements + pytest(-timeout) | **13 passed / rc 0**（59.69s；`13 passed in 58.63s`） |

## 3. 复跑方式

```bash
cd D:/project/pan-worktrees/terminal-composition-r2-review-20261003
E:/software/miniforge/python.exe audit/terminal/implementation/composition-review/round2/scripts/rerun13.py
cd audit/terminal/implementation/composition-review/round2/gates
for v in v1_f1_states v2_capacity v3_live_session v4_scope_evidence; do
  E:/software/miniforge/python.exe $v.py
done
```

## 4. 分级（摘要；详见报告）

- **实测（真机组合）**：13×2 复跑 + v3（自有会话：驱逐/心跳/resize/收尾/硬死）；
  **实测（真实引擎探针）**：v2；**实测（真实 node 进程）**：v1 四态；**静态/文本核验**：v4。
- **推断**：非本机环境吞吐/时序不外推；**未测**：真实浏览器、provider/账号/服务、真实 durable、
  Ctrl-C、跨用户/主机、长稳、生产 launcher 闭环（未实现且未批准）。
