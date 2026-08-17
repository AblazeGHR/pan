# Role 字段取消与能力字段拆解 — 立项

> 状态：立项阶段（方案已敲定，**不改代码**） | 创建：2026-08-17
> 目标：取消 `role` 这个笼统的枚举标签，把它背后承载的具体行为拆成 session_template 里的自解释能力字段，字段名直接说出能力。

---

## 一、背景与动机

当前 `role` 是一个 Pan 内部枚举（`default` / `meta-agent`），下放链路 `manifest → Profile → Character → Session`。它把三个具体行为（受隔离、能 claim、自动 claim）打包进一个不透明的标签——要理解 `meta-agent` 意味着什么，必须查代码，它不是自解释的。

拆解原则：**不用任何"身份/管理者"这类还要二次定义的词，直接把 role 控制的每个具体行为，映射成一个字段名即语义的能力字段。**

---

## 二、role 承载的行为 → 能力字段

| role 控制的具体行为 | 对应代码 | 能力字段（默认 `false`） |
|---|---|---|
| 操作其他 session 时，是否被 `managed` 列表约束 | `mcp/server.py:113` `_check_access` | `restrict_to_managed` |
| 能否把「尚未被任何 session claim 的」session 收归自己 | `server.py:1272` `claim` 接口 | `can_claim_unmanaged` |
| 通过 `session_create` 建的新 session 是否自动归自己 | `mcp/server.py:150` `_auto_claim` | `auto_claim_created` |

三个字段**独立、正交、不合并**：

- `restrict_to_managed` → 「这个 session 碰别的 session 时，要过 managed 检查」
- `can_claim_unmanaged` → 「这个 session 能把无主 session claim 到自己名下」
- `auto_claim_created` → 「这个 session 建的 session 自动归它」（省 LLM 一次手动 claim）

meta-agent 的 session_template 取三者全 `true`；普通模板缺省 `false`。

---

## 三、字段语义与组合

| 字段 | 语义 | meta-agent |
|------|------|:---:|
| `restrict_to_managed` | 操作其他 session 时，仅限自己 `managed` 列表里的 | `true` |
| `can_claim_unmanaged` | 能把「尚未被任何 session claim 的」session 收归自己（有主的不可抢） | `true` |
| `auto_claim_created` | `session_create` 建的新 session 自动 claim 归自己 | `true` |

**正交性价值**：三者可独立组合，role 枚举表达不出来的档案现在可声明。例：

- `restrict_to_managed=true, can_claim_unmanaged=true` → 只能碰自己 claim 过的，且能收编无主 session（meta-agent 现状）
- `restrict_to_managed=false, can_claim_unmanaged=true` → 不受隔离，能收编任意无主 session
- `restrict_to_managed=true, can_claim_unmanaged=false` → 只能碰已 claim 的，不能收编新的

---

## 四、判定点替换

### 4.1 `_check_access`（`mcp/server.py`）

```python
def _check_access(session_id, claim=False):
    caller = _caller_identity()
    if not caller:                          # 无身份（外部协调者）→ 放行
        return None
    if session_id == caller.get("id"):      # 操作自己 → 放行
        return None
    if not caller.get("restrictToManaged"): # 不受隔离 → 放行
        return None
    if session_id in (caller.get("managed") or []):  # 已 claim 的 → 放行
        return None
    if claim and caller.get("canClaimUnmanaged"):    # 允许收编无主 session → 尝试 claim
        ...   # 现有 claim 分支（有主且非己 → 拒绝；无主/己 → POST /api/claim）
    return 拒绝
```

### 4.2 其余判定点

| 位置 | 现状 | 改为 |
|------|------|------|
| `mcp/server.py:150` `_auto_claim` | `caller.get("role") == "meta-agent"` | `caller.get("autoClaimCreated")` |
| `server.py:1272` `claim` 接口 | `manager.role != "meta-agent"` | `not manager.can_claim_unmanaged` |

---

## 五、字段存储与下放链路

- 字段声明在 **session_template**（`session_templates[]`，当前代码阶段为 `profiles[]`）。
- 下放链路 **template → session**，**不经过 character**（character 不承载能力字段——它们是 session 的配置）。
- Session 顶层新增三字段，与 `managed`/`managed_by` 并列（同属管理关系范畴）。
- `_session_to_api` 暴露 camelCase：`restrictToManaged` / `canClaimUnmanaged` / `autoClaimCreated`（`mcp/server.py` 的 `_caller_identity` 依赖它们）。

**依赖关系**：当前代码 `_build_session_params` 从 character 读 `role` 下放。能力字段要从 template 读，需配套 `Character概念分层重构立项.md` 的「`_build_session_params` 改为从 session_template 读配置」改造。两立项同步实施，或本立项先行、字段暂经 Profile 下放（待改名后直连 session）。

---

## 六、影响面（代码落点清单）

| 文件 | 落点 | 改动 |
|------|------|------|
| `packages/core/manifest_loader.py` | `:52`/`:179` | 删 `role`；`_parse_profile` 增解析三字段 |
| `packages/core/character.py` | `:55`/`:71`/`:89`/`:189` | 删 `Character.role`（能力字段不经 character） |
| `packages/core/session.py` | `:34`/`:115`/`:142`/`:174` | 删 `Session.role`；增 `restrict_to_managed`/`can_claim_unmanaged`/`auto_claim_created` |
| `packages/web/server.py` | `:215` `_session_to_api` | 删 `role`，增三个 camelCase 字段 |
| `packages/web/server.py` | `:369` `_build_session_params` | 删 `role` 透传；从 template 读三字段下放 |
| `packages/web/server.py` | `:953` 重建透传 | 删 `role`，改三字段 |
| `packages/web/server.py` | `:1272` `claim` 接口 | `manager.role` → `manager.can_claim_unmanaged` |
| `packages/mcp/server.py` | `:113`/`:150` | `caller.get("role")` → `restrictToManaged`/`autoClaimCreated` |
| `packages/mcp/manifest.json` | `:32` | 删 `"role": "meta-agent"`，增三字段 `true` |
| `packages/web/ts/app.ts` | `:4` | 前端接口删 `role` |
| `packages/web/src/types/index.ts` | `:4` | 删 `role: string` |

**注意**：消息的 `role`（user/assistant/thinking/tool）与本次无关，**不动**。

---

## 七、任务拆解（若立项通过）

- [ ] `manifest_loader.py`：删 `role`，增三字段解析（默认 `false`）
- [ ] `character.py`：删 `Character.role`（含序列化/下放）
- [ ] `session.py`：删 `Session.role`，增三字段（默认 `false`）
- [ ] `server.py`：`_session_to_api` 删 `role` 增三 camelCase；`_build_session_params` 从 template 下放；`claim` 接口改 `can_claim_unmanaged`
- [ ] `mcp/server.py`：`_check_access` 按 4.1 重写；`_auto_claim` 改 `autoClaimCreated`
- [ ] `mcp/manifest.json`：meta-agent 删 `role` 增三字段 `true`
- [ ] 前端 types 删 `role`
- [ ] 测试：`test_character.py` / MCP 隔离测试更新断言
- [ ] 文档：同步 `Profile权限字段立项.md`（4.1 role 决策被取代）、本立项收尾

---

## 八、关联

- `Character概念分层重构立项.md` —— 能力字段属 session_template 层，下放不经 character，依赖其 `_build_session_params` 改造
- `docs/archive/Profile权限字段与MetaAgent管理Session立项.md` —— 原 4.1「role 权限字段」决策被本立项取代
- `MCP启用单一事实源收敛立项.md` —— 三字段与 `mcp_servers`/`managed` 同为 session 配置，共同决定 MCP 管理边界

---
