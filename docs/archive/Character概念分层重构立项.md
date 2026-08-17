# Character 概念分层重构 — 立项

> 状态：立项阶段（决策已敲定，**不改代码**） | 创建：2026-08-17
> 目标：拆开 `Profile` 身上过载的双重身份，把 character 重新定位为「拥有记忆 + 资产的持久实体」，session 的配置交给独立的 `session_template`。

---

## 一、背景与动机

当前 `Profile`（`manifest.json` 的 `profiles[]`）一个概念干了两件事：

1. **是 character 的模板**——`create_character` 从 profile 复制 `system_prompt`/`model`/`mcp_mode`/`mcp_servers`，`character.profile_name` 指回模板，形成双向绑定。
2. **直接提供 session 的提示词**——`_build_session_params` 用 character（即 profile）的 `system_prompt` 覆盖 session 参数。

后果：character 沦为「profile 的实例化副本」，character 与 session 的提示词被绑死。而现实中**有些 session 不需要 character、但需要一套 session 配置**（提示词/adapter/model/mcp）——这两者本不该绑在一起。

---

## 二、新概念分层（四层）

| 概念 | 职责 | 是否含 system_prompt |
|------|------|:---:|
| `session_template`（旧 `Profile` 改名） | 开一次 session 的全部配置：`system_prompt` / `adapter` / `model` / `permission_mode` / `mcp_mode` / `mcp_servers` | ✅ 是 |
| `character_template`（新） | bootstrap 一个 character：初始资产 + 初始记忆 + 引用的 session_template(s) | ❌ 否 |
| `character`（持久实体） | 拥有**记忆**（可检索）+ **资产**（普通文件），跨 session 共享；自身无 prompt，通过复用 session_template 获得 session 配置 | ❌ 否 |
| `session`（会话实例） | 一次会话；**可选**绑定 character；**可选**指定 session_template | —（来自 template） |

---

## 三、关系图

```
character_template ──bootstrap──► character ──(1:N)──► session
        │                          │ 记忆 + 资产          ▲
        │ 引用                      │                     │
        ▼                          ▼                     │
  session_template ◄──────复用──────┘                     │
        │                                                │
        └──────────── 直接创建（无需 character）──────────┘
```

**session 的配置来源**（按优先级兜底）：

```
1. 显式指定 session_template   → 用它的 system_prompt/adapter/model/mcp
2. 未指定（或无 character）     → 隐式 default template = config.json 里的 session 配置
```

`session_template` 因此是**可选**的：不选时落回 config.json 的 `cbc.model / permission_mode / effort / thinking / mcp_enabled` 等（即现在 `_build_session_params` 里 `config.get(adapter_name, {})` 那套默认值）。落盘表述采用「**存在一个内置 `default` session_template，内容即 config.json 的 session 配置，作为所有 session 的兜底**」——保持「session 总有一个 template」的模型一致性，同时承认 default 来自 config.json、无需在 manifest 里显式声明。

---

## 四、关键决策记录

1. **命名**：新 character 模板定名 `character_template`（不复用 `profile` 一词，避免与历史文档/API/代码注释打架）。
2. **session 绑定**：有 character 的 session 保留 `character_id`。解绑 character↔session_template 的动机——「有些 session 不需要 character 但需要 session_template」。
3. **记忆 vs 资产边界**：
   - **记忆（memory）** = 可检索的知识/长期沉淀（走现有 `MemoryManager` + embedding，SQLite 索引）。
   - **资产（asset）** = character 目录下的普通文件（日程、草稿等），character 跨 session 直接读写经营，不一定进检索索引。
4. **system_prompt 归属**：`character_template` 不含 system_prompt。与 session 有关的内容全部交给 `session_template`；character 通过复用 session_template 获得 session 相关配置。
5. **session_template 可选**（2026-08-17 补充）：session 不必选 template；不选则用内置 `default` session_template（= config.json session 配置）。

---

## 五、目录结构（目标态）

```
data/
├── session_templates/      # session_template 定义（manifest 内，或独立）
├── character_templates/    # character_template 定义
├── characters/<id>/        # character 自包含目录
│   ├── character.json      #   character 元数据
│   ├── memory/             #   可检索记忆（sqlite + 知识文件）
│   └── assets/             #   资产（日程、草稿等普通文件，LLM 跨 session 经营）
├── sessions/               # 扁平，character_id 指针（不塞进 character 目录）
└── mcp-configs/
```

**设计要点**：session 保持扁平在 `sessions/`，通过 `character_id` 指针关联——session 是顶层实体，character 是它的可选属性（多对一 + 可空），物理嵌套会把「可选属性」错误建模成「强聚合根」。character 目录只放 character 自己的东西（json + memory + assets），删除语义天然自包含。

---

## 六、对现有代码/数据的影响

| 项 | 现状 | 目标态 |
|----|------|--------|
| `Profile` dataclass（`manifest_loader.py`） | 角色/会话双用模板 | 改名 `session_template`，保留 system_prompt/model/mcp_mode/mcp_servers |
| `Character` dataclass（`character.py`） | 复制 profile 的 prompt/mcp/role，`profile_name` 指回 | 去掉 `profile_name`/`system_prompt`/`mcp_mode`/`mcp_servers`/`role`；保留 `id`/`name`/`memory_db_path`/`memory_dir`/资产目录 |
| 新增 `character_template` | 无 | 定义 bootstrap 资产/记忆 + 引用的 session_template(s)，**不含 system_prompt** |
| `manifest.json` | `profiles[]` | `session_templates[]` + `character_templates[]`（`mcp_servers`/`command_routes` 不动） |
| `role` 字段 | 已在 Profile/Character/Session（P4 已实现） | **取消**——拆解为 session_template 能力字段（`restrict_to_managed` / `can_claim_unmanaged` / `auto_claim_created`），见 `Role字段取消与能力字段拆解立项.md` |
| `_build_session_params`（`server.py`） | character 覆盖 system_prompt/mcp | 改为：session 配置来自 session_template（显式或 default），character 只提供 memory/assets 挂靠 |
| API `GET /api/characters/profiles` | 列出 Profile | 语义改为列出 session_template（端点名待定） |
| 无 character 的 session 记忆 | `character_id: "default"` 兜底 | 待定：退化为「隐式 default character」或取消（见「待决策」） |

---

## 七、待决策（实现时确认）

1. **无 character 的 session 记忆归哪**：现在代码有 `character_id: "default"` 兜底约定。新模型下这个 `default` 是退化成「隐式 default character」（仍有记忆），还是**取消**（无 character 的 session 无记忆）？——倾向后者更纯粹，但需评估对现有普通 session 记忆能力的影响。
2. **资产目录与 workdir 的关系**：character 的 `assets/` 目录是否成为其 session 的 workdir 一部分？秘书 character 跨 session 经营日程文件，意味着 LLM 需能读写 character 的 assets 目录——涉及 workdir 定位，需单独展开。
3. **manifest 组织**：`session_templates[]` 与 `character_templates[]` 是否都放 manifest.json，还是 character_template 作为独立文件/目录？
4. **API 端点命名**：`/api/characters/profiles` 现有端点是否改名 `/api/session-templates`，还是保留兼容别名。

---

## 八、任务拆解（若立项通过）

- [ ] `Profile` → `session_template` 全链路改名（manifest_loader / character / session / server / 前端 / 测试）
- [ ] 新增 `character_template` 数据模型 + manifest 解析 + bootstrap 逻辑
- [ ] `Character` 瘦身：去掉 `profile_name`/`system_prompt`/`mcp_mode`/`mcp_servers`/`role`，新增资产目录
- [ ] `_build_session_params` 改造：session 配置走 session_template（显式或 default），character 只挂靠 memory/assets
- [ ] 内置 `default` session_template（= config.json session 配置）
- [ ] character 自包含目录 `characters/<id>/{character.json,memory/,assets/}`
- [ ] 删除语义自包含：删 character 级联处理关联 session（`character_id` 置空或提示）
- [ ] 文档纠正（见下节清单）

---

## 九、受影响文档纠正清单

以本立项为事实基准，以下活跃文档存在矛盾，已同步纠正（`archive/` 下历史快照不动）：

- [x] `RuleWhisper联动方案.md` §一 概念表——「profile = Character 创建模板」错误，拆为 session_template + character_template
- [x] `目标与范围.md` §5.4 API 端点 + §5.6「profiles（character 模板）」表述
- [x] `MCP启用单一事实源收敛立项.md` §二/§四——mcp_mode/mcp_servers 从 character 移到 session_template
- [x] `阶段计划与进度.md` §1.1「Character/Profile | profile → character → memory」
- [x] `docs/archive/Profile权限字段与MetaAgent管理Session立项.md`——role 字段取消 + 术语（见 `Role字段取消与能力字段拆解立项.md`）

---
