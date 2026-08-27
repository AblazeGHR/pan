# 设计文档：`session_import` MCP 工具（导入历史会话）

- 状态：**已实现（2026-08-21 设计 → 已合入 main，commit `401446a`）**
- 日期：2026-08-21
- 范围：`packages/mcp/server.py` 新增工具；`packages/web/server.py` 少量扩展
- 实现要点：四 action 工具（`list_projects` / `list_workspaces` / `list_sessions` / `import`）已落地于 `packages/mcp/server.py:308`；后端两个 import 端点已支持 `sessionTemplate` / `panAccess`（复用 `_build_session_params`）；`_session_summary` 已含 `cliSessionId` 供 reimport 预检；import 超时放宽到 120s。详见 §11「后续实现步骤」勾选状态。

## 1. 背景与目标

meta-agent 目前只能通过 `session_create` 创建全新会话，无法把 cbc/kimi 历史会话导入成 Pan session 继续复用上下文。vanilla 前端已有完整导入能力（ImportModal），后端已有对应 HTTP 端点，缺的只是 MCP 层暴露。

目标：新增 MCP 工具 `session_import`，支持「发现可导入来源 → 列出候选会话 → 导入成 Pan session」三段式调用链，导入后可选套用 sessionTemplate / panAccess，并接入现有编排主链（`session_create → worker_assign → report_subscribe → session_get → session_delete`）。

## 2. 现状调研

### 2.1 后端 HTTP 端点（`packages/web/server.py`）

| 端点 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `GET /api/cbc/projects` | 无（配置 `cbc_import.import_recent_days` 默认 30、`min_resume_bytes` 默认 200） | `{projects: [{project_dir, session_count, resumable_count, path_hint, drive, short_label}]}` | 扫描 `~/.codebuddy/projects/`，排除 0 个可恢复会话的项目 |
| `GET /api/cbc/sessions?project_dir=&cwd=&all=` | `project_dir`（cbc 项目目录名）或 `cwd`（绝对路径）二选一 | `{sessions: [{session_id, project_dir, title, message_count, first_timestamp, last_timestamp, model, forked_from}], total, shown}` | 按 `cbc_import` 配置过滤（`exclude_workdir_patterns` / `min_message_count=5` / `max_sessions_shown=30`），按 `last_timestamp` 倒序 |
| `GET /api/cbc/browse?path=&limit=&offset=&q=` | 树路径（`""`=根/盘符/更深）、limit/offset 分页、`q` 标题过滤 | `{breadcrumbs, folders, sessions, total, has_more}` | 文件树式浏览（前端"浏览全部"用） |
| `POST /api/cbc/sessions/import` | body `{session_id, project_dir?, cwd?, name?}` | `_session_to_api(s)` 或 `{error}` | 解析 history+usage → 按 `cli_session_id` 去重（已存在则覆盖历史=reimport，存活 worker 原子覆盖/陈旧 worker 被 kill；历史为空则拒覆盖）→ 新建 session（adapter 默认 cbc，workdir=cbc 项目路径，name=标题或 `cbc-<id8>`） |
| `GET /api/kimi/workspaces` | 无 | `{workspaces: [{root, name, workspace_id, session_count}]}` | |
| `GET /api/kimi/sessions?cwd=` | `cwd`=workspace root 绝对路径 | `{sessions: [{session_id, workspace_id, title, workDir, createdAt, updatedAt, message_count, model}]}` | |
| `POST /api/kimi/sessions/import` | body `{session_id, cwd?, name?}` | 同上 | 去重需同时匹配 `cli_session_id == session_id and adapter == "kimi"`；新建时 adapter 固定 kimi，workdir=cwd |

要点：

- **导入 = 建 session（不 spawn worker）**。workdir 固定为外部项目/工作区路径：cbc 为 `project_dir_to_path(project_dir)`（原 cbc 项目的真实工作目录），kimi 为 workspace root。**不在 `data/workdirs/` 下**。
- **reimport 语义**：同一 `cli_session_id` 已存在 → 原地覆盖其 history/raw_usage/total_usage。命中**存活 worker 时原子覆盖并保留进程**（`_replaying=True` 防竞态，见 server.py:1538）；仅对陈旧 worker 对象执行 `kill_worker`。前端「Reimport」菜单（`reimportSession`，app.ts:1813）走的正是同一个 import 端点。
- **import 端点不走 `_build_session_params`**（server.py:351）：新建时 `sess.create(name, cli_session_id, history, raw_usage, total_usage, workdir)`，不带 model / permission_mode / session_template / pan_access / mcp_servers。与 `POST /api/sessions` 不同，导入的会话**默认无 MCP、无模板**（`mcpEnabled=false`；读时 model 才 fallback 到 adapter config）。

### 2.2 前端调用链（`packages/web/ts/app.ts`）

ImportModal（drive→project→session 三级钻取）：

```
openImport(adapter)
  → switchImportAdapter
    cbc: fetchCbcProjects → buildDriveSelect(drive) → buildProjectSelect(project_dir)
         → fetchCbcSessions(projectDir) → render 列表
    kimi: fetchKimiWorkspaces → buildKimiWorkspaceSelect(root) → fetchKimiSessions(cwd) → render 列表
  → 点击条目 → importCbcSession(sid, pd) / importKimiSession(sid, cwd)
      成功 → 关 modal → refreshSessions → selectSession(result.id)
```

`reimportSession(id)`：取 `session.cliSessionId` + `session.workdir` → 调同一 import 端点 `{session_id, cwd}` → 用返回结果替换 modelData 中的旧 session。

### 2.3 MCP 工具风格（`packages/mcp/server.py`）

- 统一走 `_api(method, path, body, timeout)` HTTP 透传；错误归一为 `{ok: false, error: {code, message}}` 或透传后端 `{error}`。
- `_strip_usage()` 剥掉 rawUsage/totalUsage（上下文预算）。
- 访问控制：`_caller_identity()`（PAN_AGENT_SESSION_ID 注入）→ `_check_access(session_id, claim)`（managed 隔离）+ `_auto_claim(session_id)`（新 session 自动归管）。
- 参数 snake_case；docstring 含英文 Args + 中文「调用链」段落 + 「完整编排流程见 /pan skill」。
- 创建类工具（session_create）返回完整 session dict（剥 usage）。

## 3. 工具设计总览

**命名：`session_import`**——进入 `session_*` 家族，与 session_create / list / get / delete 并列，语义是"从外部来源导入一个 session"。

**一个工具、四个 action**（避免工具爆炸，且把"发现→导入"链路收拢在一个入口里引导）：

- `list_projects`（adapter=cbc）→ 可导入项目列表
- `list_workspaces`（adapter=kimi）→ 可导入工作区列表
- `list_sessions` → 某项目/工作区下的候选会话列表
- `import` → 导入成 Pan session

备选命名 `import_session`：更贴近动作语义，但偏离现有 `session_*` 前缀约定，不推荐。

## 4. 工具签名与参数表

```python
@mcp.tool()
def session_import(
    action: str,                          # "list_projects" | "list_workspaces" | "list_sessions" | "import"
    adapter: str = "cbc",                 # "cbc" | "kimi"
    project_dir: str | None = None,       # cbc 项目目录名（list_sessions / import 用）
    cwd: str | None = None,               # 绝对路径：cbc 可替代 project_dir；kimi 必填（workspace root）
    query: str | None = None,             # 标题过滤（cbc list_sessions）
    limit: int = 30,                      # 分页提示（list_sessions 受后端 max_sessions_shown 封顶）
    session_id: str | None = None,        # import 必填：外部会话 id
    name: str | None = None,              # import 可选：覆盖会话名（默认取标题或 cbc-<id8>）
    session_template: str | None = None,  # import 可选：套用模板（需后端支持，见 §8）
    pan_access: dict | None = None,       # import 可选：能力标志（restrictToManaged 等）
) -> dict
```

| 参数 | 类型 | 必填 | 适用 action | 说明 |
|---|---|---|---|---|
| `action` | str | 是 | 全部 | 枚举四选一 |
| `adapter` | str | 否（默认 cbc） | 全部 | `cbc` / `kimi` |
| `project_dir` | str | 条件 | list_sessions / import（cbc） | cbc 项目目录名（如 `d-project-Pan`），来自 list_projects |
| `cwd` | str | 条件 | list_sessions / import | kimi 必填（workspace root）；cbc 可选（替代 project_dir，直接给绝对路径） |
| `query` | str | 否 | list_sessions | 标题过滤（cbc） |
| `limit` | int | 否 | list_sessions | 分页提示；后端 `max_sessions_shown` 封顶 |
| `session_id` | str | import 必填 | import | 外部会话 id，来自 list_sessions |
| `name` | str | 否 | import | 覆盖会话名（无空格、唯一，建议显式传） |
| `session_template` | str | 否 | import | 套用模板（model / permission_mode / MCP / pan_access 默认值） |
| `pan_access` | dict | 否 | import | `{restrictToManaged, canClaimUnmanaged, autoClaimCreated}` |

入参校验：

- `action` 非法 → `{ok: false, error: {code: "invalid_action", ...}}`
- import 缺 `session_id` → `missing_params`
- kimi 缺 `cwd` → `missing_params`
- list_sessions 同时缺 `project_dir` 和 `cwd` → `missing_params`

## 5. 返回结构与示例

- `list_projects` → 透传 `{"projects": [...]}`。
- `list_sessions` → 透传 `{"sessions": [...], "total": N, "shown": N}`（cbc）/ `{"sessions": [...]}`（kimi）。
- `import` → `_strip_usage(_session_to_api(s))` 基础上做两点裁减（上下文预算）：
  - 剥 `history`，换 `historyCount`。导入历史可能上千条，全量回传会撑爆工具输出；需要明细时 agent 再 `session_get(session_id, limit=N)`。
  - 加 `imported: true`、`reimportedExisting: bool`（true = 覆盖了已存在的 Pan session）。

**import 成功·新建**：

```json
{
  "id": "ses_2f9a1b3c7d4e5f60",
  "name": "explore-parser-options",
  "adapter": "cbc",
  "cliSessionId": "b7c21a3f-9d01-4e2a-8f3c-0a1b2c3d4e5f",
  "model": "hy3",
  "permissionMode": null,
  "panAccess": {"restrictToManaged": false, "canClaimUnmanaged": false, "autoClaimCreated": false},
  "sessionTemplate": null,
  "mcpEnabled": false,
  "workdir": "D:\\PROJECT\\CLIConductor",
  "workerStatus": null,
  "historyCount": 214,
  "imported": true,
  "reimportedExisting": false
}
```

**import 成功·reimport（覆盖已有）**：id 不变（后端原地覆盖），`reimportedExisting: true`。

```json
{
  "id": "ses_88c0d1e2f3a4b5c6",
  "cliSessionId": "b7c21a3f-...",
  "workerStatus": null,
  "historyCount": 231,
  "imported": true,
  "reimportedExisting": true
}
```

**失败**：`{ok: false, error: {code, message}}`（归一化后端 `{error: "..."}`）。

## 6. 调用链流程

```
# 第 1 步：发现来源（cbc 为例）
session_import(action="list_projects")
  → [{project_dir: "d-project-Pan", resumable_count: 8, drive: "D:", ...}]

# 第 2 步：列出候选会话
session_import(action="list_sessions", project_dir="d-project-Pan")
  → [{session_id, title, message_count, last_timestamp, model, ...}]

# 第 3 步：导入成 Pan session（仅建 session，不 spawn worker）
session_import(action="import", session_id="b7c21a3f-...",
               project_dir="d-project-Pan", name="my-imported",
               session_template="meta-agent", pan_access={"autoClaimCreated": true})
  → {id: "ses_...", workdir: "D:\\PROJECT\\Pan", historyCount: 214, imported: true}

# 第 4 步：接编排主链继续
report_subscribe(session_id="ses_...")              # 可选：订阅完成报告（import 即接管）
worker_assign(session_id="ses_...", text="继续之前的话题...")
session_get(session_id="ses_...", limit=30)          # 查结果
session_delete(session_id="ses_...")                 # 收尾
```

kimi 对应：`list_workspaces` → `list_sessions(cwd=workspace.root)` → `import(session_id, cwd=...)`。

## 7. 与现有工具的关系

- **session_create**：并列关系。create 从零新建（workdir 默认 `data/workdirs/<name>`，走 `_build_session_params` 套模板）；import 从外部会话带历史/usage 迁入（workdir=外部项目路径，默认无模板）。二者产物都是普通 Pan session，后续主链完全相同。**模板/能力字段语义对齐**：import 的 `session_template` / `pan_access` 与 session_create 同名参数同语义（显式字段 > 模板值 > 默认值），见 §8。
- **report_subscribe / worker_assign**：import 后直接进入主链。订阅关系按 manager 持久化在 session 上，reimport 只覆盖 history/usage，**不破坏已有订阅**；仅当命中陈旧 worker 对象被 kill 时需重新 `worker_assign` 才能继续跑。
- **session_get / session_delete / session_history / session_update**：导入会话是普通 session，一律适用。

## 8. 与 HTTP 导入的映射

| MCP action / 参数 | HTTP 调用 |
|---|---|
| `list_projects` | `GET /api/cbc/projects` |
| `list_workspaces` | `GET /api/kimi/workspaces` |
| `list_sessions`（cbc） | `GET /api/cbc/sessions?project_dir=...`（或 `?cwd=`） |
| `list_sessions`（kimi） | `GET /api/kimi/sessions?cwd=...` |
| `import` | `POST /api/cbc/sessions/import` 或 `/api/kimi/sessions/import`，body `{session_id, project_dir?/cwd?, name?}` |
| import 后 `_auto_claim` | `POST /api/claim`（调用者 autoClaimCreated 时） |
| `session_template` / `pan_access`（需后端扩展） | 见下 |

### 8.1 sessionTemplate / panAccess 的落地（实现步骤 1，后端）

当前 import 端点不走 `_build_session_params`，无法套模板/能力标志。建议两个 import 端点的 body 接受可选 `sessionTemplate` / `panAccess`，新建分支复用模板解析逻辑（显式 template 或 default 模板 + pan_access 优先级 `显式 > 模板 > 默认`）：

```python
# 伪代码：api_cbc_sessions_import / api_kimi_sessions_import 新建分支
params = _build_session_params({
    "adapter": adapter, "name": name,
    "sessionTemplate": data.get("sessionTemplate"),
    **({"panAccess": data["panAccess"]} if "panAccess" in data else {}),
})
s = sess.create(
    name=name, cli_session_id=session_id,
    history=history, raw_usage=raw_usage, total_usage=total_usage,
    workdir=cwd,                 # 保留外部项目/工作区路径，不用 data/workdirs/<name>
    model=params.get("model"),
    permission_mode=params.get("permission_mode"),
    session_template=params.get("session_template"),
    pan_access=params.get("pan_access"),
    adapter_config=params.get("adapter_config"),   # 含 mcp_servers 等模板派生值
)
```

reimport 分支（覆盖已有）只更新 history/usage 等，**不重套模板**（保持现有行为）。

**MVP 过渡方案**：MCP 层 import 后用 `session_update` 补 model / permission_mode / mcp_servers（`session_update` 暂不支持 sessionTemplate / panAccess，故仅作过渡；MCP body 仍设计为直接透传这两个字段，后端落地后即零改动生效）。

### 8.2 reimport 预检（实现步骤 2，MCP 层）

`restrictToManaged` 的调用者对已有 session 覆盖前需先做访问检查。当前 `_session_summary`（`?summary=1`，server.py:248）**不含 cliSessionId**，MCP 层无法廉价定位"将被覆盖的 session"。两个方案：

- **推荐（后端 1 行）**：`_session_summary` 增加 `cliSessionId` 字段；MCP 层在 import 前若 caller 受限，先 `GET /api/sessions?summary=1` 按 `cliSessionId` 找目标——命中且不在 caller managed 列表 → 拒绝；未命中（纯新建）→ 放行。
- **MVP（纯 MCP）**：用全量 `GET /api/sessions`（history 已截断 50 条）定位，代价是传输较大，不推荐长期。

## 9. 错误处理与边界

- **会话不存在**：后端 `#import-guard` 拒绝（`session not found on disk` / 历史为空拒覆盖），MCP 透传并归一为 `{ok: false, error}`。
- **managed 隔离**：新建场景不涉及已存在 session，`_auto_claim` 按 autoClaimCreated 归管；reimport 场景按 §8.2 预检——受限 caller 只能 reimport 自己管理的 session；无身份/非受限 caller 不预检。
- **workdir 边界**：导入会话的 workdir = **外部项目/工作区路径**（cbc 为原项目真实目录，kimi 为 workspace root），不在 `data/workdirs/` 下——刻意保留（cli_session_id 上下文 + resume 依赖）。manifest/workdir 隔离审查时需把"外部路径 workdir"视为已知模式，与 session_create 的沙箱 workdir 区分。
- **adapter 区分**：cbc 去重只看 `cli_session_id`（不查 adapter）；kimi 去重同时匹配 `adapter == "kimi"`。文档需注明避免误覆盖。
- **不自动 spawn**：import 只建 session；派活由 agent 显式 `worker_assign`。reimport 对存活 worker 原子覆盖历史（进程保留）；仅陈旧 worker 对象被 kill，此时需重新 assign 才继续跑。
- **名字唯一性**：默认名来自外部标题（可能含空格/重名）；建议 agent 显式传 `name`（无空格、唯一）。后端 `sess.create` 不校验名字（与 session_create 的 `_check_session_name` 不一致），列为实现步骤 3（可选补上）。
- **超时**：导入解析可能较慢（大 history），`_api` 默认 timeout 30s 偏紧；import action 建议放宽（如 120s）。
- **分页**：list_sessions 受后端 `max_sessions_shown`（默认 30）封顶；`limit` 只是提示。真正分页浏览走 `GET /api/cbc/browse`；MCP 层暂不暴露，量大时可按需加 `action="browse"`。

## 10. MCP tool docstring 草稿

```python
"""Import an external cbc/kimi session into Pan, or list what's importable.

Args:
    action: "list_projects" (cbc) / "list_workspaces" (kimi) /
        "list_sessions" / "import"
    adapter: Source adapter ("cbc" or "kimi")
    project_dir: cbc project dir name (from list_projects)
    cwd: Absolute path — kimi requires the workspace root; cbc accepts it
        in place of project_dir
    query: Title filter for cbc list_sessions
    limit: Pagination hint (backend caps at max_sessions_shown)
    session_id: External session id to import (required for action="import")
    name: Override imported session name
    session_template: Session template to apply (model / permission_mode /
        MCP / pan_access defaults; explicit fields still override template)
    pan_access: Capability flags {restrictToManaged, canClaimUnmanaged,
        autoClaimCreated}

调用链（导入历史会话）：
1. session_import(action="list_projects") 或 (action="list_workspaces") 发现可导入来源；
2. session_import(action="list_sessions", project_dir=...) 按项目/工作区列出候选会话；
3. session_import(action="import", session_id=..., project_dir=.../cwd=...,
   name?/session_template?/pan_access?) 导入成 Pan session —— 仅建 session 不
   spawn worker；workdir 为外部项目路径。同一 cli_session_id 重复导入 = reimport，
   覆盖原 Pan session 历史（受限 caller 只能覆盖自己管理的）。
4. 接编排主链：report_subscribe（订阅完成报告）→ worker_assign（派发任务）→
   session_get（查结果）→ session_delete（收尾）。完整编排流程见 /pan skill。
"""
```

## 11. 后续实现步骤

> 已全部实现（2026-08-23 核对代码确认）。

后端（`packages/web/server.py`）：

1. ✅ import 端点（cbc + kimi）body 支持 `sessionTemplate` / `panAccess`，新建分支复用 `_build_session_params` 模板解析（§8.1，server.py:1712-1721 / 1834-1842）。
2. ✅ `_session_summary` 增加 `cliSessionId` 字段，供 reimport 预检（§8.2，server.py:251-264）。
3. ⚠️ （可选）import 端点补 `_check_session_name` 校验——**未做**，名字唯一性仍依赖调用方显式传 `name`。

MCP（`packages/mcp/server.py`）：

4. ✅ 新增 `session_import` 工具（四 action 分发，import 放宽 `_api` timeout=120s，server.py:308-424）。
5. ✅ reimport 预检逻辑（受限 caller + cliSessionId 定位，§8.2，`_reimport_precheck`）。
6. ✅ import 成功后 `_auto_claim(result["id"])`。
7. ✅ 更新 `docs/skills/pan/SKILL.md`：工具清单表 + 新增「导入历史会话」小节（2026-08-23 文档维护补齐）；`packages/mcp/server.py` 模块 docstring 的 Tools exposed 列表同步。

## 12. 未决问题（已决）

- ✅ `session_template` 由 import 端点支持（§8.1 推荐方案）——已落地，无需 MCP 层 `session_update` 补丁过渡。
- ❓ 是否需要 `action="browse"`（文件树浏览，覆盖大量项目场景）？——未实现，量大时仍走 `GET /api/cbc/browse`（HTTP 直调）。
- ⚠️ 导入会话名是否强制唯一/无空格校验（对齐 session_create）？——未做，见步骤 3。
