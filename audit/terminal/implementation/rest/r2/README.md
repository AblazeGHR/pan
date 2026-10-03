# P2 REST/lifespan 第一批 —— r2 窄修证据（2026-10-04）

基线 `d8ed782e`（首版提交）。**只改**：`packages/web/terminal_api.py`、`packages/web/server.py`
（仅 lifespan 薄接入）、`tests/test_terminal_api.py`（自身测试）、接口文档、本目录。
**旧证据 `rest/README.md`·`rest/run.json`·`rest/evidence_*/` 未刷新**，任务书
`PAN_TERMINAL_REST_BRIEF_20261004.md` 保历史不改。核心 service 未改，未重跑真实 ConPTY 链/全库。

## 1. 计数（先失败 → 后修）

| 层 | 命令（要点） | 结果 |
| --- | --- | --- |
| **d8 前置**（`git archive d8ed782e` 副本 + 新测试） | `E:/software/miniforge/python.exe -m pytest tests/test_terminal_api.py -o addopts= -q --tb=line` | **13 failed, 24 passed, 1 skipped** |
| 修后直连 | 同文件（本 worktree） | **38 passed**（7.9s） |
| 修后 uv 临时环境 | `uv run --no-project --python … --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout -- python -m pytest …` | **38 passed**（8.45s） |
| 相邻纯逻辑（导入 `packages.web.server`） | `tests/test_addressing_compat.py` | **24 passed** |

d8 副本里 **1 skipped** 是真实隔离 ASGI 链（`node_modules` gitignored，archive 不含）——
**该链未被撤回**，首版证据仍有效；r2 **未重跑**它。

**正控仍 pass**：d8 上 24 passed 即 §4 全量正控（gate allowlist/host/fetch/content-type、
投影、游标、F5、错误映射、零回显、close 重试、detach、4 槽、取消、reconcile、startup、
导入纯净、真实 Pan 路由）。清单见 `evidence_d8/positive_controls.txt`。

**d8 上失败的 13 条 = r2 的靶向测试**（明细见 `evidence_d8/pytest_pre_fix.txt`，含如下
可对照的失败行）：

- `rows=501` 被 d8 接受（`assert 200 == 422`）；
- `'_StreamRequest' object has no attribute 'body'`（d8 用无界 `request.body()`）；
- `并发 shutdown 必须复用同一 task，不叠加 … assert 2 == 1`（d8 未单飞）；
- `module 'packages.web.terminal_api' has no attribute 'terminal_lifespan'`（d8 无 finally helper）；
- `report["confirmed"]` 形状、槽被偷减、关门后仍执行、畸形 Origin 未捕获等。

## 2. 源码 blob 锚定（工作树内容哈希）

| blob | 文件 |
| --- | --- |
| `853c7c1b93397d93dcf1578cd0a4e541d3e1a6e0` | `packages/web/terminal_api.py` |
| `bc666a032de6fb140bb715a0b8856494b3295598` | `packages/web/server.py` |
| `3fcaedd75f70c229219ca3dccb430fba94d90210` | `tests/test_terminal_api.py` |
| `f97a985859fd98160a3e6d26d386f5ab4e7ee928` | `docs/design/PAN_TERMINAL_REST_INTERFACES_20261004.md` |

## 3. 四项亲验缺陷的修法与门控

- **① 报告被假报 confirmed**：`_classify_shutdown` 消费 `service.shutdown` 的真实报告。
  `confirmed` 仅四条件同时成立：报告是 Mapping 且 `unconfirmed` 为**空列表**、
  `secrets_retained` **真 False**、`budget_exhausted` **真 False**、无在途请求且无未完成
  reconcile。非空 `unconfirmed` → `unconfirmed` 且**回传公共 terminal id**；`secrets_retained=True`
  → `unconfirmed`；缺证/畸形/`None`/字符串 → `unconfirmed`；只输出静态 `problems` + 计数，
  **不复制自由文本**（实测：报告里塞 `SECRET-TOKEN-XYZ` 也不出现在结果里）。
  干净报告 + 有在途请求 → 仍 `unconfirmed`（`request-in-flight`）。
- **② 槽被偷减**：`_SlotLedger` 按 token 记账；`_retire_request` 只在任务**确实持槽**时释放一次
  （重复调用 no-op）；`_retire_lifecycle`（reconcile/shutdown）**绝不**扣请求槽。实测 4 个在途
  请求时 `shutdown()` 走完 `inflight` 仍为 **4**，且重复 `_retire_request` 只减一次。
- **③ 关门后仍执行**：`call` 在**取槽与复查的同一无 await 区**再跑 `_check_ready`；关门后
  拒绝（`GateRejected(503,"closing")`），**零业务执行**；`_execute` 把 `GateRejected` 映射为
  对应 503（非 500）。端到端：gate 已过 → body（httpx 流式）暂停 → `shutdown()` 关门 →
  释放 body → 得 `503 closing` 且 `service` **零 create 调用**。
- **④ 畸形 origin 未捕获**：`normalize_origin` 捕 `urlsplit` 的 `ValueError` → `None`；
  origin **明确无 path**（尾斜杠 `/` 亦拒）。请求 `http://[broken` / `http://127.0.0.1:8768/`
  → **403 `forbidden-origin`**（非 500），非法 allowlist 配置 → fail-closed；合法正控 → 200。

## 4. 同批局部修正

- **尺寸**：REST `rows 1..500` / `cols 1..1000`（统一到核心已接受的门）。实测 `rows=500` ok、
  `501`→422、`1000`→422，`cols=1000` ok、`1001`→422。
- **流式 body**：`read_json_object` 按 `request.stream()` 逐块累计，**越 64 KiB 立即拒**
  （实测只拉取越界那一块即拒、不读完；`over.pulled == 1`）；非法 UTF-8 与深嵌套
  （`RecursionError`）→ 静态 422 `invalid-json`；非 object → `invalid-body`。均无 500、无 traceback。
- **lifespan finally**：`terminal_lifespan`（try/finally）实现 `yield` 与收尾；实测 body 抛异常
  仍请求收尾；正常路径亦收尾。等待 shield / 引用保留规则不变。
- **单飞**：并发/超时重试复用**同一在途 shutdown task**（实测并发两次调用只发起 1 次
  `service.shutdown`）；超时后迟到结果被真实消费（`tracked_tasks` 归 0）；已结束未证明时
  **允许同一 service 幂等重试**（第二次调用拿到干净报告 → `confirmed`，非缓存失败）。

## 5. 未测 / 未撤回

未重跑真实 ConPTY ASGI 链（首版证据保留）、共享 core/launcher/IPC/emulator 套件、全库。
WS / MCP / 浏览器输入-resize / 完整 Pan lifespan / 远程暴露鉴权 / 跨主机仍不在范围。

## 6. 文件

- `evidence_d8/pytest_pre_fix.txt`、`evidence_d8/positive_controls.txt`（先失败 + 正控）
- `evidence_direct/pytest_post_fix.txt`、`evidence_direct/adjacent-addressing.txt`
- `evidence_uv/pytest_post_fix.txt`
- `summary.json`（紧凑结构化计数与门控）
