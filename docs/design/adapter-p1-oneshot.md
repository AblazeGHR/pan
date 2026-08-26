# Pan Adapter P1：执行模式（output_mode）显式化方案

> 状态：设计方案（仅文档，未实现）
> 日期：2026-08-26
> 关联：docs/design/adapter-architecture.md §2（P1 建议 4）、§5、§8
> 目标：把 worker.py 里 cbc 特定的执行模式判定（`_use_oneshot_mcp` 矩阵、`_consumer_mcp` 拼装、`hasattr(adapter,'base_args_stream'/'mcp_args')` 探测）收编进协议层，并让 session 的 output_mode 成为**显式、可持久化、前端可设置**的设置项；one-shot-only 的 adapter 在前端禁止切换到 stream。

---

## 0. 关键结论（TL;DR）

1. **`execution_modes` 的语义 = worker 对 adapter 的"驱动方式"，不是 adapter 内部 CLI 的机制。**
   - `cbc` = `["stream", "oneshot"]`：worker 既能常驻 stdin/stdout，也能逐任务 spawn 一个 `-p <prompt>` 短进程。
   - `kimi` = `["stream"]`：worker 只通过 wrapper 长驻进程驱动（stdin 写 `{"text":...}`）。wrapper 内部再调 `kimi -p` 是 **adapter 内部实现细节，对 worker 透明**，所以 kimi 在协议层只有 `stream`，**不暴露 oneshot**。
   - `opencode` = `["stream"]`：**当前实现同样是 wrapper 长驻**（worker 视角是 stream）。理由同 kimi。
   - 结论：**kimi/opencode 的"内部一次性"不算 worker 层的 oneshot**。`oneshot_args(s, text)` 只对 worker 直接 spawn 短进程的 adapter 有意义——目前只有 cbc。若未来决定去掉 wrapper、让 worker 直接逐任务 spawn `opencode run`，再把 `opencode.execution_modes` 改为 `["oneshot"]` 并实现 `oneshot_args`，属后续演进，不在本方案。

2. **`output_mode` 持久化位置**：存 `session.adapter_config["output_mode"]`（已如此）。新增 `execution_modes` 是 **adapter 类常量**（按 adapter 名计算，不落盘）。两者在 worker 层合并为"本次会话实际用的执行模式"。

3. **迁移规则（output_mode 未设置的旧 session）**：默认解析为 adapter 的"默认执行模式"。
   - 单一模式 adapter（kimi/opencode）→ 该唯一模式。
   - 多模式 adapter（cbc）→ 默认 `"stream"`（cbc ≥ 2.137 的 stream+MCP 路径是主流、且无需逐任务 spawn）。
   - 现有已显式 `output_mode=="oneshot"` 的 cbc session **原样保留**，不强制改写。

4. **后端约束**：session 创建/更新时若 `output_mode` 不在该 adapter 的 `execution_modes` 内 → 返回 400（不静默 clamp，避免产生"不可能"的配置）。worker 层再做一次防御性 clamp 兜底。

5. **前端**：`/api/adapter/config` 新增 `executionModes`；设置弹窗按 `executionModes` 渲染 output mode 选择器。`executionModes` 长度 == 1 → 只有唯一选项（one-shot-only adapter 时 stream 直接不出现/禁用）；长度 > 1 → 渲染 stream / oneshot 两个选项。

---

## 1. 协议层（CliAdapter）

在 `packages/core/adapters/base.py` 的 `CliAdapter` Protocol 增加两个成员：

```python
# ── 执行模式（P1 建议 4）──

@property
def execution_modes(self) -> list[str]:
    """Worker 对该 adapter 的可用"驱动方式"。

    取值子集：``"stream"``（worker 起一个常驻进程，跨消息复用 stdin/stdout）、
    ``"oneshot"``（worker 逐任务 spawn 一个一次性进程，prompt 作末参）。

    语义边界（关键）：
    - 这是 **worker 与 adapter 之间的传输契约**，描述 worker 如何驱动 adapter，
      而非 adapter 内部 CLI 是否"一次性"。
    - kimi/opencode 当前用 wrapper 长驻，worker 只走 stream 路径，故声明
      ``["stream"]``；其 wrapper 内部再调 CLI 的一次性语义对 worker 透明，
      不在此暴露。
    - 只有 worker 会直接 spawn 短进程（cbc 的 `_consumer_oneshot`）的 adapter
      才声明 ``"oneshot"``。
    默认实现返回 ``["stream"]``（最保守；所有 adapter 至少支持 stream）。
    """

def oneshot_args(self, s: Session, text: str) -> list[str]:
    """构建"一次性执行"的完整 argv（仅当 ``"oneshot"`` in execution_modes）。

    把原先 worker `_consumer_mcp` 里 cbc 特定的拼装（base_args_stream →
    model_args / permission_mode_args / effort_args → resume_args → mcp_args →
    --system-prompt（仅首条）→ prompt 作末参）**搬进 adapter**。worker 的统一
    oneshot consumer 只负责：``oneshot_args(s,text)`` → spawn（无 stdin）→
    收集 stdout → 走既有 ``parse_event`` 事件模型。

    不在 execution_modes 中的 adapter 不应被调用本方法；为安全，默认实现抛
    NotImplementedError，提示"该 adapter 不支持 oneshot 执行"。
    ...
```

### 各 adapter 的声明值

| adapter | `execution_modes` | 实现 `oneshot_args`? | 说明 |
|---|---|---|---|
| `cbc` | `["stream", "oneshot"]` | **是** | 唯一 worker 直驱 oneshot 的 adapter |
| `kimi` | `["stream"]` | 否（默认 NotImplementedError） | wrapper 长驻，内部一次性对 worker 透明 |
| `opencode` | `["stream"]` | 否（默认 NotImplementedError） | 同 kimi |

`base_args_stream` / `mcp_args` 不再被 worker 通过 `hasattr` 探测——它们降级为 **cbc adapter 的私有辅助方法**，只被 `CbcAdapter.oneshot_args` 内部调用。协议外方法泄漏问题随之一并消除。

### CbcAdapter.oneshot_args 拼装规则（从 `_consumer_mcp` 搬入，逐条对齐）

```python
def oneshot_args(self, s: Session, text: str) -> list[str]:
    # 1) 无 --input-format stream-json 的 one-shot 基参
    args = list(self.base_args_stream())          # 旧 _consumer_mcp 的 hasattr 分支
    args.extend(self.model_args(s))
    args.extend(self.permission_mode_args(s))
    args.extend(self.effort_args(s))               # 注意：跳过 thinking_args（MCP init 冲突，旧注释）
    if s.cli_session_id and self.supports_resume:
        args.extend(self.resume_args(s))           # --resume
    args.extend(self.mcp_args(s))                  # --mcp-config（写入 data/mcp-configs/<id>.mcp.json）
    # 2) system-prompt 仅首条（before cli_session_id 捕获）注入
    if s.system_prompt and not s.cli_session_id:
        args.extend(["--system-prompt", s.system_prompt])
    # 3) prompt 作末参
    args.append(text)
    return args
```

> 与旧 `_consumer_mcp:1207-1237` 逐行等价，保证行为零变化（验证见 §8 步骤 1）。

可选收尾：`_extract_cbc_error`（worker.py:1165）是 cbc 专属错误提取，建议一并搬进 adapter 作为可选方法 `extract_oneshot_error(self, output: bytes) -> str | None`，worker 通用 oneshot consumer 用 `getattr(adapter, "extract_oneshot_error", None)` 调用；非 cbc adapter 不提供即走通用启发式。本方案将其列为可选，不强求。

---

## 2. execution_modes 语义细化（回答"wrapper 内部再调 CLI"的疑问）

worker 的 stream 长驻循环（`_consumer_stream` + `_read_stdout` + stdin `encode_user_message`）假设：

- 有一个**常驻进程**，worker 跨消息复用；
- worker 通过 `encode_user_message` 把每条消息写进该进程 stdin；
- 进程 stdout 是 `parse_event` 能解析的事件流。

对 cbc：这个常驻进程 = `cbc -p --output-format stream-json --input-format stream-json`，天然满足。
对 kimi/opencode：这个常驻进程 = **wrapper.py**（不是原生 CLI）。wrapper 在内部循环里把 worker 的 stdin 消息翻译成对原生 CLI 的一次性调用（`kimi -p` / `opencode run`），并把原生 stdout 事件转发回 worker stdout。**从 worker 视角，wrapper 就是一个 stream 长驻进程**——`encode_user_message` 写 `{"text":...}`、wrapper stdout 是事件流，全部成立。

因此：

- kimi/opencode 在 worker 层**只有 `stream` 一种驱动方式**。它们的"内部一次性"发生在 wrapper 里，是 adapter 实现细节，不被 worker 的执行模式概念建模。
- `execution_modes` 表达"worker 能怎样驱动该 adapter"，而不是"adapter 内部 CLI 是不是一次性的"。这正是它和 `oneshot_args` 配合的边界：`oneshot_args` 仅在 worker 真的要自己去 spawn 短进程时才有意义（cbc）。
- **副作用**：kimi/opencode 永远不会进入 worker 的新 `_consumer_oneshot` 路径，它们的 oneshot 行为完全由 wrapper 保证。本方案对 kimi/opencode 是**零行为变化**，风险仅集中在 cbc 与 worker 重构。

> 未来若想让 worker 直接驱动 `opencode run`（去掉 wrapper），只需：(a) `OpencodeAdapter.execution_modes = ["oneshot"]`；(b) 实现 `oneshot_args`；(c) wrapper 路径降级/移除。属独立立项，不在本方案。

---

## 3. Session 层：output_mode 显式持久化

### 存储形态（不变 + 新增计算值）

- **持久化字段**：`session.adapter_config["output_mode"]`，取值 `"stream"` / `"oneshot"` / 缺失（未设置）。
  - 已有该字段的 cbc session（历史 `output_mode=="oneshot"`）**原样保留**，无需数据迁移。
- **计算字段（不落盘）**：`execution_modes` 由 `get_adapter(s.adapter).execution_modes` 得出。

### 实际执行模式解析（worker 与 API 共用）

新增纯函数（建议放在 `worker.py` 或一个共享 `adapters/exec.py`）：

```python
def resolve_execution_mode(adapter, s) -> str:
    """合并 adapter.execution_modes 与 session.output_mode → 实际模式。"""
    modes = adapter.execution_modes or ["stream"]
    requested = (s.adapter_config.get("output_mode") or "").strip()
    # 1) 显式且合法 → 直接用
    if requested in modes:
        return requested
    # 2) 未设置 / 非法 → 默认模式
    #    多模式默认 stream；单模式默认其唯一项
    if "stream" in modes:
        return "stream"
    return modes[0]
```

该解析在三个点统一调用：`_create_worker`（决定是否 spawn 长驻进程）、`_consumer`（决定走 stream 还是 oneshot consumer）、以及任何"当前是否 oneshot"的判断（替代旧的 `_use_oneshot_mcp`）。

### 创建 / 编辑 / 迁移规则

- **创建**（`api_create_session` → `_build_session_params`）：允许传 `outputMode`。若不传 → 不写 `output_mode`（= 未设置），运行时按 §3 解析默认。模板（`session_template`）若锁定 MCP（`mcp_mode`），不影响 output_mode（二者正交）。
- **编辑**（`api_update_session` → `_apply_session_updates` → `_apply_output_mode`）：
  - 传 `null` / `""` / `"auto"` → 清除 `output_mode`（恢复自动/默认）。
  - 传 `"stream"` / `"oneshot"` → 若该值 ∈ `adapter.execution_modes` 则写入；否则 **抛 `ValueError` → API 返回 400**（拒绝"不可能"配置，如给 one-shot-only adapter 设 stream）。
  - 变更 output_mode 与变更 mcp_servers 一样，触发 `requireRestart`（已有逻辑 `_PROCESS_AFFECTING_FIELDS` 已含 `outputMode`，无需改）。
- **迁移**：旧 session 若 `output_mode` 已存在则沿用；不存在则运行时按 §3 默认解析，**无需离线迁移脚本**（计算值，加载即生效）。`execution_modes` 是 adapter 类常量，亦无需迁移。

---

## 4. Worker 层：去 cbc 化

### 4.1 删除 / 替换

| 旧 | 新 |
|---|---|
| `_use_oneshot_mcp(s)`（:305） | `resolve_execution_mode(adapter, s)`（见 §3），返回 `"stream"`/`"oneshot"` |
| `_consumer_mcp`（:1188，cbc 形状 + 一堆 `hasattr`） | `_consumer_oneshot`（通用）：`args = adapter.oneshot_args(s, text)` → spawn（无 stdin）→ 收集 stdout → 走既有 `parse_event`/`extract_*` 事件模型 |
| `_consumer` 里的 `use_mcp = _use_oneshot_mcp(s)` | `mode = resolve_execution_mode(adapter, s)`；`mode=="oneshot"` → `_consumer_oneshot`，否则 `_consumer_stream` |
| `_create_worker` 里的 `mcp_on`/`use_mcp` 分支 | `mode = resolve_execution_mode(adapter, s)`；`mode=="oneshot"` → `proc=None`（不 spawn 长驻、不起 `_read_stdout`）；否则 `_spawn_process` |
| `hasattr(adapter,'base_args_stream')` / `hasattr(adapter,'mcp_args')` 探测 | 不再需要；cbc 的 `oneshot_args` 内部直接调用这些私有方法 |

### 4.2 通用 `_consumer_oneshot`（从 `_consumer_mcp` 提炼）

保留 `_consumer_mcp` 的全部"通用"骨架：
- `w.status="running"` + `worker.status` 广播（同 :1196-1202）；
- `proc = await asyncio.create_subprocess_exec(*args, cwd=s.workdir, stdout=PIPE, stderr=STDOUT)`（**无 stdin=PIPE**——旧代码已经建了 stdin pipe 但不用，新代码省掉）；
- 用户消息后置落盘（同 :1262-1268）；
- 输出分块读取 + 16MB 上限 + 超时 kill（同 :1270-1308）；
- 逐行 `adapter.parse_event` 解析、收集 `result`/`init(session_id)`/`assistant` 事件（同 :1321-1347，已用 `adapter.*` 通用方法，保留）；
- `cli_session_id` 幂等绑定（同 :1364-1377）；
- `s.history` 追加 assistant 块（同 :1378-1380）；
- 失败面（超时 / 非零退出 / 零输出 → `adapter.extract_oneshot_error` 或通用启发式）（同 :1382-1409，仅错误提取改为可插拔）；
- `worker.stream` 实时广播 assistant 事件（同 :1412-1421，保证前端实时显示，非裸 `[DONE]`）；
- `worker.result` 广播 + result_waiter + 报告入队 + taskId 幂等（同 :1423-1449）。

**唯一变化的拼装来源**：argv 由 `adapter.oneshot_args(s, text)` 提供，而非 worker 自己探测 `base_args_stream`/`mcp_args` 并手动拼 `--system-prompt` + prompt 末参。

### 4.3 `_create_worker` / `_spawn_process` 调整

- `mode=="oneshot"` → `proc=None`，**不**起 `_read_stdout`；watchdog 仍启用（仅做空闲回收，同旧 `use_mcp` 分支）。
- `mode=="stream"` → 同旧 stream 路径（`_spawn_process` + `_read_stdout` + watchdog + `_consumer`）。
- 其余（dedup / recover_pending_signals / system_prompt 注入）逻辑不变。注意 system_prompt 注入守卫（`mcp_on` 判断）应改为按 `mode` 判断：oneshot 模式下 system_prompt 已由 `oneshot_args` 内部 `--system-prompt` 注入，故 `_create_worker` 的 system_prompt 注入分支对 oneshot 应跳过（避免重复注入）。

### 4.4 防御性 clamp（兜底）

`resolve_execution_mode` 已保证返回合法值；但为防老数据/并发，worker 在 `mode=="oneshot"` 且 `adapter` 未实现 `oneshot_args` 时：记录 warning 并**回退 stream**（而非崩溃），保证可用性。

---

## 5. 约束规则（one-shot-only adapter）

- **声明**：某 adapter 设 `execution_modes = ["oneshot"]`（当前三个 adapter 都不是，但机制必须支持未来的 gemini/codex/纯 oneshot adapter）。
- **前端**：`/api/adapter/config` 返回 `executionModes=["oneshot"]` → 设置弹窗的 output mode 选择器**只渲染 oneshot 一项**，stream 选项不出现（或置灰禁用）。用户无法选到 stream。
- **后端（创建/更新）**：若请求 `outputMode=="stream"` 而 adapter 不支持 → `_apply_output_mode` 抛 `ValueError` → API 返回 400，错误消息明确"该 adapter 仅支持 oneshot"。
- **worker 兜底**：即便非法值绕过 API 落库，`resolve_execution_mode` 会因 `"stream" not in modes` 而返回 `modes[0]`（=oneshot），不会尝试走不存在的 stream 路径。

> 当前三个 adapter 均支持 stream，本约束暂时不触发；但机制一次到位，避免后续新增 oneshot-only adapter 时再补。

---

## 6. 前端

### 6.1 `/api/adapter/config`（server.py:1202）响应新增

```python
return {
    "adapter": a.name,
    "models": a.supported_models,
    "defaultModel": a.default_model,
    "effortValues": list(a.effort_values),
    "permissionModes": a.permission_modes,
    "defaultPermissionMode": a.default_permission_mode,
    "supportedSettings": getattr(a, "supported_settings", [...]),
    "executionModes": list(a.execution_modes),   # 新增：["stream"] / ["stream","oneshot"]
}
```

### 6.2 `_session_to_api`（server.py:231）新增

```python
"outputMode": ac.get("output_mode"),          # 已有
"executionModes": list(a.execution_modes),    # 新增：前端据此渲染选择器
```

### 6.3 React 设置弹窗（SettingsPopover.tsx）

复用既有 `supportsSetting(config, name)` 机制（`config.supportedSettings.includes(name)`），新增一个概念"output mode 可用当且仅当 `executionModes.length > 1`"：

```tsx
const execModes = config.executionModes || ["stream"];
const showOutputMode = execModes.length > 1;   // 单模式 adapter 不显示切换
...
{showOutputMode && (
  <div>
    <label>Output Mode</label>
    <select
      value={s.outputMode ?? (execModes.includes("stream") ? "stream" : execModes[0])}
      onChange={(e) => applySetting("outputMode", e.target.value)}
    >
      {execModes.map((m) => (
        <option key={m} value={m}>{m}</option>
      ))}
    </select>
  </div>
)}
```

- **one-shot-only adapter**（`executionModes == ["oneshot"]`）：`showOutputMode` 为 `1 > 1 = false` → **整个选择器不渲染**；若希望"可见但不可改"，可改为始终渲染、单选项 disabled。本方案倾向"不渲染"（更干净），如需可见可加 `disabled` 变体。
- **cbc**（`["stream","oneshot"]`）：渲染两个选项，用户可切；切换触发 `PATCH /api/sessions/{id}` 带 `outputMode`，后端返回 `requireRestart`，前端提示"配置将在下次空闲时生效 / 手动重启"。
- **model/permission 等随 adapter/模式联动**：`supportedSettings` 已按 adapter 驱动 UI（kimi 仅 model）。output mode 与 model/permission 是**正交设置**，互不隐藏；但需注意：cbc 的 `oneshot` 路径实际上会跳过 `thinking_args`（MCP init 冲突），故切到 oneshot 时前端若展示了 Thinking 开关，应在说明文案里提示"oneshot 模式不生效"，或按 adapter 提供的 `modeAffectingSettings` 动态隐藏（**进阶，本方案仅标注，不强制实现**）。

### 6.4 类型（packages/web/src/types/index.ts）

- `AdapterConfig` 增加 `executionModes?: string[]`。
- `Session`（API 形态）已有 `outputMode?: string | null`（核对 types:199 附近有无，无则补）。
- `services/api.ts` 的 adapter config 解析补 `executionModes`。

### 6.5 Legacy（packages/web/ts/app.ts）

- `adapterConfig` 类型补 `executionModes: string[]`（:132）；`supportedSettings()` 旁新增 `executionModes()` 取值。
- 设置渲染处（:2752 起的 show/hide 逻辑）增加 output mode 选择器：当 `executionModes().length > 1` 显示；one-shot-only 时只渲染唯一项。
- 创建 session 时把 `outputMode` 一并 POST（现有 `_build_session_params` 已读 `outputMode`，legacy 前端补 UI 控件即可）。
- 优先级：React 为主，legacy 跟随；若时间紧可只做 React。

---

## 7. 兼容与迁移

- **`output_mode` 字段已存在**：历史 cbc session 若已存 `output_mode=="oneshot"` → 继续走 oneshot 路径（现在由 `resolve_execution_mode` 判定，等价旧 `_use_oneshot_mcp` 的 `mcp && output_mode=="oneshot"`，但**不再要求 MCP 已配置**——见下）。
  - ⚠️ 行为变化点：旧 `_use_oneshot_mcp` 要求"**MCP 已配置且 output_mode==oneshot**"才 oneshot；新方案 `output_mode=="oneshot"` 即 oneshot，**与 MCP 是否配置解耦**（MCP 是叠加属性，§3 已确认）。若某旧 session 设了 `oneshot` 但没配 MCP，旧逻辑会走 stream（无 MCP），新逻辑走 oneshot（无 MCP，prompt 作末参）——这是**更合理的语义**，且 cbc 无 MCP 时 oneshot 也完全合法。低风险，但应在验证步骤明确覆盖。
- **未设置 output_mode 的旧 session**：按 §3 默认解析 → cbc 默认 stream（含 stream+MCP，cbc≥2.137）。这与当前 `_use_oneshot_mcp`（unset → stream+MCP）**一致**，无回归。
- **kimi/opencode session**：从不使用 output_mode（对其无副作用），默认 stream → 行为不变。
- **API 向后兼容**：`outputMode` 字段已暴露，继续保留；新增 `executionModes` 为**加法字段**，旧前端忽略即可。`_apply_output_mode` 的非法值从"仅校验 stream/oneshot"升级为"校验 ∈ adapter.execution_modes"——对旧合法请求（stream/oneshot）无影响。
- **无需离线迁移脚本**：`output_mode` 与 `execution_modes` 均为"加载即解析"的计算/已有字段，老 JSON 直接兼容。
- **协议方法移除**：`base_args_stream`/`mcp_args` 从"被 worker hasattr 探测"降级为 cbc 私有方法；其他 adapter 本就不实现，移除探测无影响。

---

## 8. 任务分解（可独立验证的小步）

> 每一步可单独 commit / 单独验证；建议按顺序，因依赖关系递进。

### 步骤 1 — 协议加成员 + cbc 实现 oneshot_args（纯 adapter，不碰 worker）
- **文件**：`packages/core/adapters/base.py`（加 `execution_modes` property + `oneshot_args` 方法签名）、`packages/core/adapters/cbc/adapter.py`（`execution_modes = ["stream","oneshot"]` + 实现 `oneshot_args`，内部调 `base_args_stream/model_args/permission_mode_args/effort_args/resume_args/mcp_args`）。
- **验证**：
  - 单元测试：构造一个带 `mcp_servers` + `system_prompt` 且 `cli_session_id=None` 的 fake Session，断言 `CbcAdapter().oneshot_args(s, "hello")` 的 argv 与旧 `_consumer_mcp` 拼装结果**逐元素相等**（可临时保留旧函数做对照，或在测试里手写期望 argv）。
  - 断言 `KimiAdapter().execution_modes == ["stream"]`、`OpencodeAdapter().execution_modes == ["stream"]`。

### 步骤 2 — worker 去 cbc 化（核心重构，风险最高）
- **文件**：`packages/core/worker.py`
  - 新增 `resolve_execution_mode(adapter, s)`（放在 `_use_oneshot_mcp` 附近，旧函数保留为 deprecated 别名/或直接删除并改所有调用点）。
  - 新增 `_consumer_oneshot`（通用，从 `_consumer_mcp` 提炼，argv 来源改为 `adapter.oneshot_args`；移除所有 `hasattr` 探测；错误提取改 `getattr(adapter,"extract_oneshot_error",None)` 兜底通用启发式）。
  - `_consumer`：`use_mcp = _use_oneshot_mcp(s)` → `mode = resolve_execution_mode(adapter, s)`；分派到 `_consumer_oneshot` / `_consumer_stream`。
  - `_create_worker`：`use_mcp` 分支 → `mode` 分支；oneshot 时 `proc=None`、不起 `_read_stdout`；system_prompt 注入按 `mode` 守卫（oneshot 跳过，因 `oneshot_args` 已注入）。
  - 删除或保留 `_consumer_mcp`（建议保留为 thin wrapper 一版过渡，下个 PR 删除）。
- **验证**：
  - cbc session：配 MCP + `output_mode=oneshot` → 发消息，确认逐任务 spawn 短进程、结果/历史/实时 stream 广播与改造前一致（端到端对照）。
  - cbc session：配 MCP + `output_mode=stream` → stream+MCP 长驻路径仍正常。
  - cbc session：无 MCP → stream 正常。
  - kimi/opencode session → 仍走 wrapper stream（零回归）。
  - watchdog 空闲回收在 oneshot 模式仍生效（旧行为保持）。

### 步骤 3 — server 层校验 + 暴露 executionModes
- **文件**：`packages/web/server.py`
  - `/api/adapter/config` 响应加 `executionModes`。
  - `_session_to_api` 加 `executionModes`。
  - `_apply_output_mode`：非法值校验改为 `mode not in adapter.execution_modes`（通过 `get_adapter(s.adapter)` 取）；越界 → `ValueError` → 400。
- **验证**：
  - `GET /api/adapter/config?adapter=cbc` 含 `executionModes:["stream","oneshot"]`；kimi/opencode 含 `["stream"]`。
  - `PATCH /api/sessions/{id}` 带 `outputMode:"stream"`（cbc）→ 200；带 `outputMode:"oneshot"` 给一个模拟的 oneshot-only adapter 配置 → 400。
  - 现有 cbc session `GET` 仍返回 `outputMode` 原值。

### 步骤 4 — React 前端设置 UI
- **文件**：`packages/web/src/types/index.ts`、`services/api.ts`、`components/chat/SettingsPopover.tsx`（必要时 `InputRow.tsx`）。
- **验证**：
  - `pnpm build`（CODEBUDDY.md 要求）通过。
  - 手动：cbc session 设置弹窗出现 Output Mode 选择（stream/oneshot）；kimi/opencode 不出现（单模式）；切换 cbc 模式后端返回 `requireRestart` 且前端提示。
  - one-shot-only adapter（临时把某 adapter 设成 `["oneshot"]` 验证）→ 选择器不渲染或单选项。

### 步骤 5 — Legacy 前端跟随（可选，低优先）
- **文件**：`packages/web/ts/app.ts`
- **验证**：`npx tsc --noEmit`（项目根）通过；手动核对设置项出现。

### 步骤 6 — 文档与收尾
- 更新 `docs/design/adapter-architecture.md` §2 标注"已立项实现于 adapter-p1-oneshot.md"；删除其中过时的"kimi=oneshot"措辞（改为"worker 层 stream，wrapper 内部一次性"）。
- 确认 `base_args_stream`/`mcp_args` 不再被任何 worker 代码 `hasattr` 引用（grep 验证）。

---

## 9. 风险

### 9.1 改动 worker.py 主循环（高风险区）
- `_consumer` / `_create_worker` / `_consumer_mcp` 是消息驱动核心，任何 argv 拼装偏差都会导致 cbc+MCP 用户全部失效。
- **缓解**：
  - 步骤 1 用单元测试逐元素比对 `oneshot_args` 与旧拼装，先把"拼装正确性"钉死，再动 worker。
  - 步骤 2 先保留 `_consumer_mcp` 为 thin wrapper（调用通用 `_consumer_oneshot`），灰度确认后再删。
  - 端到端对照测试：同一 session + 同一 MCP，改造前后各跑一轮，断言 stdout 事件流 / history / `worker.result` 一致。

### 9.2 语义解耦带来的行为变化（中风险）
- 旧 oneshot 要求"MCP 已配"，新方案 `output_mode==oneshot` 即 oneshot（与 MCP 解耦）。无 MCP 的 oneshot 在 cbc 上合法，但属于新行为。
- **缓解**：步骤 3 的验证显式覆盖"oneshot 但无 MCP"用例；若担心，可在 `oneshot_args` 里对无 MCP 的 oneshot 保持原样（cbc 原生支持），无需特殊处理。

### 9.3 回归面（kimi/opencode）
- 本方案对 kimi/opencode 是**零行为变化**（它们始终 stream、不实现 oneshot_args）。但 `_consumer` 分派改动若写错条件，可能误把它们导向 oneshot。
- **缓解**：步骤 2 验证明确包含 kimi/opencode 正常收发消息；`resolve_execution_mode` 对两者永远返回 `"stream"`（其 `execution_modes` 不含 oneshot）。

### 9.4 默认模式切换（低-中风险）
- 旧 `unset → stream+MCP`（当前 `_use_oneshot_mcp` 实际行为）与方案默认一致，**无回归**；但旧 `_apply_output_mode` 文档说"unset → oneshot"，属 stale 文档，应在步骤 2/3 顺手修正 docstring，避免混淆。

### 9.5 并发 / 迁移竞态
- `output_mode` 与 `execution_modes` 均为加载即解析，无离线迁移脚本，不存在迁移窗口竞态。
- 唯一注意：步骤 3 改 `_apply_output_mode` 校验后，旧前端若发了非法值会收到 400；旧前端只会发 stream/oneshot，均在合法集内，安全。

---

## 10. 验收清单（Done 标准）

- [ ] `CliAdapter` 含 `execution_modes` 与 `oneshot_args`；cbc 实现、kimi/opencode 仅 stream。
- [ ] worker 无 `hasattr(adapter, 'base_args_stream'|'mcp_args')` 探测；`_consumer_mcp` 被通用 `_consumer_oneshot` 取代。
- [ ] `output_mode` 显式持久化；未设置按 §3 默认解析；旧 oneshot session 保留。
- [ ] 后端对越界 `output_mode` 返回 400；worker 防御性 clamp。
- [ ] `/api/adapter/config` 与 `_session_to_api` 暴露 `executionModes`。
- [ ] React 设置弹窗按 `executionModes` 渲染 output mode；one-shot-only 不出现 stream。
- [ ] 步骤 1/2/3 各有对应验证（单测 + 端到端对照）通过。
- [ ] `pnpm build` 与 `npx tsc --noEmit` 通过。
- [ ] kimi/opencode 回归测试通过（零行为变化）。
