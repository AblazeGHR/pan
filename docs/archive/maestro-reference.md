# Maestro 架构分析 — CLIConductor 参考文档

> 分析日期：2026-07-13 · 源码版本：v0.17.4
> 仓库：[RunMaestro/Maestro](https://github.com/RunMaestro/Maestro)
> 目的：基于源码深入分析 Maestro 的进程管理、Session 持久化、多 Agent 编排方案、CLI 工具和自动化系统，为 CLIConductor 设计提供参考

---

## 一、项目概况

Maestro 是一个跨平台 Electron 桌面应用（macOS/Win/Linux），提供"指挥家指挥台"式的多 AI Agent 并行管理体验。它不提供 AI 能力本身，而是作为多个 CLI Agent 进程的统一编排层。

**本质**：Electron 主进程通过 `child_process.spawn()` 和 `node-pty` 管理多个 CLI Agent 子进程，通过 preload 桥接安全 IPC 与 React 前端通信。

**技术栈**：Electron v41.5.0 + React 18 + TypeScript 6.0 + Zustand 5 + Tailwind CSS 3.4 + better-sqlite3 12 + xterm.js 6.0 + Fastify 4 + commander 14

**支持的 CLI Agent**（`src/main/agents/definitions.ts`）：
- Claude Code（主力，同时支持 `claude --print` API 模式和 `maestro-p` TUI 模式）
- OpenAI Codex
- OpenCode
- Factory Droid
- Copilot-CLI（Beta）
- Terminal（内置 PTY 终端）

---

## 二、架构全貌

```
┌─────────────────────────────────────────────────────────────────┐
│                        Electron Shell                            │
│                                                                  │
│  ┌──────────────────────────┐  ┌──────────────────────────────┐ │
│  │  Renderer (React)        │  │  Main Process (Node.js)       │ │
│  │                          │  │                               │ │
│  │  Zustand stores          │◄─┤  window.maestro API (preload) │ │
│  │  xterm.js                │  │                               │ │
│  │  react-markdown + KaTeX  │  │  ┌─────────────────────────┐ │ │
│  │  Recharts / ReactFlow    │  │  │  ProcessManager          │ │ │
│  │  CodeMirror 6            │  │  │  Map<sessionId, proc>    │ │ │
│  └──────────────────────────┘  │  │  ├─ ChildProcessSpawner  │ │ │
│                                │  │  └─ PtySpawner           │ │ │
│                                │  └──────────┬───────────────┘ │ │
│                                │             │ spawn/resume     │ │
│                                │  ┌──────────▼───────────────┐ │ │
│                                │  │  Agent Definitions       │ │ │
│                                │  │  (detector / path-prober) │ │ │
│                                │  └──────────┬───────────────┘ │ │
│                                │             │                  │ │
│                                │  ┌──────────▼───────────────┐ │ │
│                                │  │  Session Storage         │ │ │
│                                │  │  (electron-store JSON)   │ │ │
│                                │  │  + AgentSessionStorage   │ │ │
│                                │  │  (per-agent .jsonl)      │ │ │
│                                │  └──────────────────────────┘ │ │
│                                │                                │ │
│                                │  Fastify HTTP/WS (mobile)     │ │
│                                │  Cloudflare Tunnel            │ │
│                                └────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## 三、进程管理（源码深度）

> 全部源码位于 `src/main/process-manager/`（26 个文件）

### 3.1 两种 Spawn 路径

`ProcessManager.spawn()` 根据条件选择 spawn 方式（`ProcessManager.ts:89-101`）：

```typescript
const usePty = this.shouldUsePty(config);
if (usePty) {
    return this.ptySpawner.spawn(config);
} else {
    return this.childProcessSpawner.spawn(config);
}
```

选择逻辑（`ProcessManager.ts:98-101`）：

```typescript
private shouldUsePty(config: ProcessConfig): boolean {
    const { toolType, requiresPty, prompt } = config;
    return (toolType === 'terminal' || requiresPty === true) && !prompt;
}
```

**规则**：
- `toolType === 'terminal'` 且无 prompt → PTY（xterm.js 渲染 ANSI 控制序列）
- `requiresPty === true`（Copilot-CLI 强制要求）→ PTY
- 有 prompt 的 AI Agent → 一律 `child_process.spawn()`（管道 stdin/stdout）

### 3.2 child_process.spawn 路径

**文件**：`src/main/process-manager/spawners/ChildProcessSpawner.ts:363`

```typescript
const childProcess = spawn(spawnCommand, spawnArgs, {
    cwd,
    env,
    shell: spawnShell,
    stdio: ['pipe', 'pipe', 'pipe'],
});
```

- `stdio: ['pipe', 'pipe', 'pipe']` — stdin/stdout/stderr 全部管道化
- `shell` 按需启用（Windows `.cmd`/`.bat`、shebang 脚本）
- Agent 类型决定 `isStreamJsonMode` 和 `isBatchMode` 标志

### 3.3 node-pty 路径

**文件**：`src/main/process-manager/spawners/PtySpawner.ts:122`

```typescript
const ptyProcess = pty.spawn(ptyCommand, ptyArgs, {
    name: 'xterm-256color',
    cols: config.cols || 100,
    rows: config.rows || 30,
    cwd: cwd,
    env: ptyEnv as Record<string, string>,
});
```

- 终端 PTY 以 `-l -i`（login + interactive）启动 shell
- Windows 通过 `cmd.exe /d /s /c` 包装
- 终端 Tab（sessionId 含 `-terminal-`）输出原样透传给 xterm.js，不做 ANSI 过滤

### 3.4 stdout 解析：三模式分发

**文件**：`src/main/process-manager/handlers/StdoutHandler.ts:256`

```typescript
if (isStreamJsonMode) {
    this.handleStreamJsonData(sessionId, managedProcess, cleanedOutput);
} else if (isBatchMode) {
    managedProcess.jsonBuffer = (managedProcess.jsonBuffer || '') + cleanedOutput;
} else {
    this.bufferManager.emitDataBuffered(sessionId, cleanedOutput);
}
```

**Stream-JSON 模式**（Claude Code、Factory Droid）：按行 `\n` 分割，每行 `JSON.parse()`，分发到 `handleParsedEvent()`。对 Copilot 则使用自定义 JSON tokenizer 处理粘连 JSON 对象。

**事件提取**（`StdoutHandler.ts:435`）：
- `usage` — token 使用量（含累加到增量的自动转换）
- `sessionId` — Agent 报告的 session ID
- `tool-execution` — 工具调用（含 toolCallId 去重）
- `thinking-chunk` — 思考/推理内容
- 结果消息 — 含 per-agent 特殊处理（Codex、Copilot、OpenCode）

### 3.5 stderr 处理

**文件**：`src/main/process-manager/handlers/StderrHandler.ts`

- 累积到 `managedProcess.stderrBuffer`（100KB 上限）
- 检测 Agent 错误：`outputParser.detectErrorFromLine()` + SSH 错误匹配
- JSONL 类 Agent（Copilot、Codex、OpenCode、Factory Droid）的 stderr 被**静默抑制**（过滤 MCP/服务启动噪声）
- Codex 特殊处理：过滤 Rust tracing 行后重新作为 `data` 事件发送

### 3.6 输出缓冲

**文件**：`src/main/process-manager/handlers/DataBufferManager.ts`

- 每 **50ms** 或缓冲区超过 **8KB** 时刷新
- 减少 IPC 事件频率，避免渲染线程阻塞

### 3.7 ManagedProcess 状态结构

**文件**：`src/main/process-manager/types.ts:73-122`

关键字段：

```typescript
interface ManagedProcess {
    sessionId: string;
    toolType: string;
    ptyProcess?: IPty;           // PTY 路径下的进程引用
    childProcess?: ChildProcess;  // child_process 路径下的进程引用
    cwd: string;
    pid: number;
    isTerminal: boolean;          // 区分 PTY 和 child_process
    isBatchMode?: boolean;        // 有 prompt 的批处理模式
    isStreamJsonMode?: boolean;   // 输出为 JSONL 流
    jsonBuffer?: string;          // 未完成 JSON 累积
    jsonBufferCorrupted?: boolean; // 溢出后强制清空
    agentSessionId?: string;      // Agent 报告的 session ID
    resultEmitted?: boolean;      // 防止重复结果事件
    errorEmitted?: boolean;       // 防止重复错误事件
    startTime: number;
    outputParser?: AgentOutputParser;
    stderrBuffer?: string;
    lastUsageTotals?: UsageTotals; // 用于累计→增量转换
    usageIsCumulative?: boolean;   // 自动检测是否为累计值
    emittedToolCallIds?: Set<string>; // 工具调用去重
    dataBuffer?: string;           // 输出缓冲累积
}
```

### 3.8 Session 恢复（--resume）实现

**参数检测**（`ChildProcessSpawner.ts:221-224`）：

```typescript
const isResuming =
    args.some((arg) => arg === '--resume' || arg.startsWith('--resume=')) ||
    args.includes('--session');
```

- 恢复时设置 `MAESTRO_SESSION_RESUMED=1` 环境变量
- 通过 `config.agentSessionId` 预设 `ManagedProcess.agentSessionId`，避免恢复场景下 Agent 不重发 sessionId 导致的丢失
- Copilot 恢复：`CopilotShutdownWaiter` 解析 `events.jsonl` 找最后 `session.resume` 或 `session.start` 边界
- OpenCode 恢复：收到 `init` 事件时重置 `resultEmitted` 和 `streamedText`

### 3.9 关键常量

**文件**：`src/main/process-manager/constants.ts`

| 常量 | 值 | 用途 |
|------|-----|------|
| `MAX_BUFFER_SIZE` | 100KB | stdout/stderr 累积上限 |
| `DATA_BUFFER_FLUSH_INTERVAL` | 50ms | 缓冲刷新间隔 |
| `DATA_BUFFER_SIZE_THRESHOLD` | 8KB | 即时刷新阈值 |

### 对 CLIConductor 的启示

1. **双 spawn 路径**：Maestro 的 PTY vs child_process 区分是桌面应用特有的需求。CLIConductor 作为服务端，统一使用 `child_process.spawn` + 管道即可，不需要 PTY 路径
2. **输出解析分层**：`StdoutHandler` 的三模式（原始/批量/流式 JSON）分发值得借鉴，CLIConductor 的 Adapter 协议可以定义 `outputMode` 字段
3. **状态去重**：`resultEmitted`、`errorEmitted`、`emittedToolCallIds` 是进程管理的关键健壮性措施，CLIConductor 的 Worker 状态机应引入类似防护
4. **累计→增量转换**：`normalizeUsageToDelta()` 的单调性检测算法值得直接移植

---

## 四、Agent 定义与检测

> 源码位于 `src/main/agents/`（20 个文件）

### 4.1 Agent 定义结构

**完整的 AgentConfig**（`src/main/agents/definitions.ts:90-162`）：

```typescript
interface AgentConfig {
    // 基础
    id: string; binaryName: string; command: string; args: string[];

    // 批处理/JSON 模式
    batchModePrefix?: string[];    // 子命令，如 OpenCode 的 ['run']、Codex 的 ['exec']
    batchModeArgs?: string[];      // 批处理附加参数，如 '--skip-git-repo-check'
    jsonOutputArgs?: string[];     // JSON 输出标志，如 ['--format', 'json']

    // Session 管理
    resumeArgs?: (sessionId: string) => string[];

    // 安全/权限
    readOnlyArgs?: string[];       // 只读模式参数
    noToolsArgs?: string[];        // 禁用工具参数
    yoloModeArgs?: string[];       // 跳过所有权限确认

    // 模型和目录
    modelArgs?: (modelId: string) => string[];
    workingDirArgs?: (dir: string) => string[];
    additionalDirArgs?: (dirs: AdditionalDirectory[]) => string[];
    imageArgs?: (imagePath: string) => string[];

    // 双模式（Claude Code 特有）
    apiCommand?: string;           // API 模式二进制（claude）
    apiModeArgs?: string[];       // API 模式参数
    interactiveCommand?: string;   // TUI 模式二进制（maestro-p）
    interactiveModeArgs?: string[];  // TUI 模式参数

    // 环境变量
    defaultEnvVars?: Record<string, string>;
    readOnlyEnvOverrides?: Record<string, string>;
    batchModeEnvVars?: Record<string, string>;

    // Prompt
    promptArgs?: (prompt: string) => string[];
    noPromptSeparator?: boolean;  // 不支持的 Agent 不使用 '--' 分隔符
}
```

### 4.2 五 Agent 对比

| 维度 | Claude Code | Codex | OpenCode | Factory Droid | Copilot-CLI |
|------|-------------|-------|----------|---------------|-------------|
| **调用形式** | `claude --print --output-format stream-json --resume <id> -- "<prompt>"` | `codex -C <dir> exec --json resume <id> -- "<prompt>"` | `opencode run --format json --session <id> "<prompt>"` | `droid exec -o stream-json -s <id> "<prompt>"` | `copilot -p "prompt" --output-format json --resume=<id>` |
| **恢复语法** | `--resume <id>` | `resume <id>`（exec 子命令） | `--session <id>` | `-s <id>` | `--resume=<id>`（等号） |
| **Prompt 分隔** | `--` 分隔 | `--` 分隔 | 无分隔（yargs 限制） | 无分隔 | `-p` 标志 |
| **JSON 模式** | stream-json（逐行） | --json（JSONL） | --format json | -o stream-json | --output-format json |
| **双模式** | API / TUI（maestro-p）| 仅 batch | 仅 batch | 仅 batch | 仅 batch |
| **特殊处理** | `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` | global `-C` flag 必须在 `exec` 前 | `OPENCODE_CONFIG_CONTENT` 环境变量注入权限 | 默认只读，YOLO 模式需 `--skip-permissions-unsafe` | 必须 PTY，图片通过 `@path` 嵌入 prompt |

### 4.3 Agent 检测

**文件**：`src/main/agents/detector.ts` + `src/main/agents/path-prober.ts`

三阶段检测策略（`path-prober.ts`）：

1. **已知路径探测**（`probeWindowsPaths` / `probeUnixPaths`）：并行 `Promise.allSettled` 检查硬编码路径（Homebrew、npm global、WinGet、Chocolatey 等）
2. **自定义路径验证**（`checkCustomPath`）：用户指定路径，文件存在 + 可执行权限检查
3. **PATH 搜索**（`checkBinaryExists`）：扩展 PATH（包含 npm global、Python Scripts、Go bin 等），调用 `which`/`where`

### 4.4 Agent 能力标志（Capabilities）

**文件**：`src/shared/types.ts:44-141` + `src/main/agents/capabilities.ts`

共 26 个能力标志，关键差异：

| 能力 | Claude | Codex | OpenCode | F-Droid | Copilot |
|------|--------|-------|----------|---------|---------|
| `supportsResume` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `supportsStreamJsonInput` | ✅ | ❌ | ❌ | ✅ | ❌ |
| `supportsAppendSystemPrompt` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `supportsAdditionalDirectories` | ✅ | ✅ | ❌ | ❌ | ✅ |
| `supportsCostTracking` | ✅ | ❌ | ✅ | ❌ | ❌ |
| `usesJsonLineOutput` | ❌(stream-json) | ✅ | ✅ | ✅ | ✅ |
| `usesCombinedContextWindow` | ❌ | ✅ | ❌ | ❌ | ✅ |
| `imageResumeMode` | — | `prompt-embed` | — | — | — |

### 对 CLIConductor 的启示

1. **Agent 定义驱动 spawn**：Maestro 用声明式 `AgentConfig` + 函数式 `*Args()` 构建命令行，比硬编码 if-else 更可维护。CLIConductor 的 Adapter 协议应考虑类似的声明式参数构建能力
2. **检测优先级**：已知路径 → 自定义路径 → PATH 搜索，避免慢速 PATH 扫描
3. **能力矩阵分离**：能力和定义分离成两个独立文件，新 Agent 接入只需补充两者
4. **双模式难点**：Claude Code 的 API/TUI 双模式是最复杂的 spawn 逻辑（`claudeSpawnCore.ts`），CLIConductor 如果收钱只支持一种模式会简单很多

---

## 五、Session 持久化与历史管理

### 5.1 持久化方式：JSON 文件（非 SQLite）

**关键纠正**：Maestro **不使用 SQLite** 做 Session 持久化。全部通过 `electron-store` 库写入 JSON 文件。

**文件**：`src/main/stores/instances.ts:86-169` — 9 个独立的 JSON store：

| Store | 文件 | 内容 |
|-------|------|------|
| Bootstrap | `maestro-bootstrap.json` | 自定义同步路径 |
| Settings | `maestro-settings.json` | 全局设置 |
| **Sessions** | `maestro-sessions.json` | `StoredSession[]` Array |
| Groups | `maestro-groups.json` | 分组信息 |
| Agent Configs | `maestro-agent-configs.json` | Agent 自定义配置 |
| Agent Capabilities | `maestro-agent-capabilities.json` | 能力快照 |
| Window State | `maestro-window-state.json` | 窗口位置/大小 |
| Session Origins | `maestro-claude-session-origins.json` | Session 元数据 |
| Agent Origins | `maestro-agent-session-origins.json` | 跨 Agent Session 元数据 |

### 5.2 Session CRUD

**写入**发生在 `sessions:setMany` IPC handler（`src/main/ipc/handlers/persistence.ts:256-377`）。

增量合并策略（不丢数据）：
```
1. 读取现有 sessions 数组
2. 根据 updateMap（sessionId → 更新后的对象）逐条覆盖
3. 根据 removeSet 跳过已删除 session
4. 新增 session 追加到末尾
5. atomicWriteJson 写入文件
```

**读取**：`sessions:getAll` → `sessionsStore.get('sessions', [])`，含 base64 图片外迁修复。

**删除**：通过 `sessions:setMany` 的 `removeIds` 参数。

### 5.3 Agent Session 发现（非"导入"）

Maestro **不是"导入"**外部 Session，而是**原样读取** Agent 自己的存储文件。

**流程**：`AgentSessionStorage` 接口 → 各 Agent 实现 → IPC → Renderer。

| Agent | 存储位置 | 格式 |
|-------|---------|------|
| Claude Code | `~/.claude/projects/<encoded-path>/<id>.jsonl` | JSONL |
| Codex | `~/.codex/sessions/YYYY/MM/DD/<id>.jsonl` | JSONL |
| OpenCode | 对应项目目录 | JSONL |
| Factory Droid | 对应存储目录 | JSONL |
| Copilot | 对应存储目录 | JSONL |

**注册**（`src/main/storage/index.ts:36-42`）：
```typescript
registerSessionStorage(new ClaudeSessionStorage(claudeSessionOriginsStore));
registerSessionStorage(new OpenCodeSessionStorage());
registerSessionStorage(new CodexSessionStorage());
registerSessionStorage(new FactoryDroidSessionStorage());
registerSessionStorage(new CopilotSessionStorage());
```

**Claude Session 解析**（`src/main/storage/claude-session-storage.ts`）：
- 扫描 `~/.claude/projects/` 下的 `*.jsonl` 文件
- 正则统计 user/assistant 消息数
- 提取第一条 assistant 回复作为预览（fallback 到 user 消息）
- 计算 token 用量和费用、时长
- 最大文件扫描：100MB（`MAX_SESSION_FILE_SIZE`）
- 前后各扫描 20/10 行

### 5.4 History Manager（历史记录）

**文件**：`src/main/history-manager.ts`

**数据结构**（`src/shared/types.ts:269-302`）：

```typescript
interface HistoryEntry {
    id: string;
    type: 'AUTO' | 'USER' | 'CUE';  // 谁发起的
    timestamp: number;
    summary: string;
    fullResponse?: string;
    agentSessionId?: string;
    projectPath: string;
    sessionId?: string;            // Maestro session ID
    contextUsage?: number;          // Context 窗口使用率 (%)
    usageStats?: UsageStats;
    success?: boolean;
    elapsedTimeMs?: number;
    validated?: boolean;
    // Cue 特定
    cueTriggerName?: string;
    cueEventType?: string;
    // Claude 特定
    tokenSource?: 'interactive' | 'api';
    tokenSourceReason?: 'auto' | 'limit';
}
```

**存储**：按 session 分文件 `history/<sessionId>.json`，每个 Session 上限 5000 条记录。

**并发安全**：所有写操作通过 `createKeyedWriteQueue()` 按 `sessionId` 排队，同一 session 串行化。

**恢复机制**（5 层防护）：
1. **Migration**：旧版 `maestro-history.json` 自动按 session 拆分
2. **JSON 合并修复**：处理崩溃导致的 JSON 粘连（`findFirstJsonObjectEnd`）
3. **损坏文件保留**：不可读文件重命名为 `.corrupt-<timestamp>`，不覆盖
4. **Shape 保护**：解析后验证 `entries` 是否为数组
5. **Shrink Tripwire**：拒绝会减少条目数的写入，防止逻辑错误破坏历史

### 对 CLIConductor 的启示

1. **JSON 文件已够用**：Maestro 的 Session 量级（每个项目数十到数百个 Session）不需要 SQLite。CLIConductor 的 Memory 模块如果用 SQLite，应考虑是否过度设计
2. **Session 发现优于导入**：直接读取 Agent 自己的存储，零成本零风险，CLIConductor 可以实现 `cbc sessions list` 类似功能
3. **History 的 Cue 集成**：历史条目记录 `type: 'CUE'` 和 `cueTriggerName`，使自动化产生的历史可回溯。CLIConductor 的 Audit Log 应记录是谁/什么触发的
4. **Write Queue 模式**：per-session 写入队列 + `atomicWriteJson`，防止并发写损坏——CLIConductor 任何文件持久化都可以参考

---

## 六、CLI 工具（maestro-cli）

> 源码位于 `src/cli/`

### 6.1 架构

基于 `commander` npm 包。入口：`src/cli/index.ts`。

**两种执行模式**：

| 命令 | 机制 | 适用场景 |
|------|------|---------|
| `maestro-cli send <agent-id> <message>` | 本地 `child_process.spawn()` 直接启动 Agent CLI | CI/CD、cron、脚本 |
| `maestro-cli dispatch <agent-id> <message>` | 通过 WebSocket 把 prompt 发送到正在运行的 Maestro 桌面端 | 快速注入 prompt 到已有 UI |

**命令树**：
```
maestro-cli
├── list (groups|agents|playbooks|sessions|ssh-remotes)
├── show (agent|playbook)
├── send <agent-id> <message> [--session <id>] [--read-only] [--tab]
├── dispatch <agent-id> <message> [--tab <id>] [--force]
├── cue (trigger|list|schedule|pipeline)
├── settings (list|get|set|reset)
├── tab (new|close|rename|star|unstar)
├── session (list|show)
└── dev (tunnel)
```

### 6.2 Agent Spawn 路径（send）

**文件**：`src/cli/services/agent-spawner.ts:1096-1144`

```typescript
export async function spawnAgent(toolType, cwd, prompt, agentSessionId?, options?) {
    if (toolType === 'claude-code') {
        return spawnClaudeAgent(cwd, prompt, agentSessionId, readOnly, sshRemote, overrides, tokenSource);
    }
    if (hasCapability(toolType, 'usesJsonLineOutput')) {
        return spawnJsonLineAgent(toolType, cwd, prompt, agentSessionId, readOnly, sshRemote, overrides);
    }
    return { success: false, error: `Unsupported agent type` };
}
```

关键差异：CLI 永远不会启动 PTY/TUI 模式——全部用 `--print --verbose --output-format stream-json`（batch only）。

### 6.3 CLI 与 Electron 的差异

| 维度 | CLI (send) | Electron Desktop |
|------|-----------|------------------|
| Spawn | 直接 `child_process.spawn()` | 通过 `ProcessManager` + IPC |
| Token Source | `getUsageSnapshot: () => null`（无 quota 感知）| 完整 SQLite usage store |
| Mode | 强制 batch 模式 | batch 或 TUI（maestro-p）|
| System Prompt | `--append-system-prompt` 或 temp file | 相同机制 |
| Playbook | 流式事件输出到 stdout | 渲染进度到 UI |

### 6.4 Playbook 执行

**文件**：`src/cli/commands/run-playbook.ts`

Playbook 是保存的 Auto Run 配置——一组 markdown 文档（含 checkbox 任务清单）加可选的循环配置。

执行流程：
1. `findPlaybookById()` 跨所有 Agent 查找
2. `checkAgentBusy()` 检查 Agent 是否忙碌
3. `executePlaybook()` 异步生成器，逐个执行任务
4. 事件流输出到 stdout（`--json` 则为 JSON lines）

### 对 CLIConductor 的启示

1. **send vs dispatch 双模式是自然结果**：桌面应用需要 `dispatch` 把 CLI 连接到 GUI，CLIConductor 作为服务端本身就是中心节点，只需 `send` 模式
2. **Playbook 的"文件驱动任务清单"模型**简洁有效——markdown checkbox → Agent 自动执行 → 勾选完成。CLIConductor 的自动化可以考虑类似的声明式任务描述
3. **SSH 远程 spawn** 在 CLI 和桌面使用相同路径（`wrapSpawnWithSsh`），CLIConductor 如果要支持远程 Worker 可以直接参考

---

## 七、Cue 事件驱动自动化

> 源码位于 `src/main/cue/`

### 7.1 事件类型

**文件**：`src/shared/cue/contracts.ts:22-33`

```typescript
type CueEventType =
    | 'app.startup'         // Cue 引擎启动
    | 'time.heartbeat'      // 定时心跳
    | 'time.scheduled'      // 定时任务（cron-like）
    | 'time.once'           // 一次性定时（到期自毁）
    | 'file.changed'        // 文件变更（chokidar）
    | 'agent.completed'     // Agent 完成（链式编排）
    | 'github.pull_request' // PR 事件（轮询）
    | 'github.issue'        // Issue 事件（轮询）
    | 'task.pending'        // Markdown 待办任务
    | 'cli.trigger';        // CLI 手动触发
```

### 7.2 动作类型

```typescript
type CueAction = 'prompt' | 'command' | 'notify';
// prompt  → 发送 prompt 给 AI Agent（默认）
// command → 执行 shell 命令或 maestro-cli 调用
// notify  → 发送 toast 通知
```

### 7.3 YAML 配置格式

缩略示例：

```yaml
settings:
  timeout_minutes: 30
  max_concurrent: 1
  queue_size: 512       # 默认队列大小
  owner_agent_id: xxx   # 可选：绑定到指定 Agent

subscriptions:
  - name: morning-report
    event: time.scheduled
    schedule_times: ["09:00"]
    schedule_days: ["mon", "tue", "wed", "thu", "fri"]
    prompt: "Summarize yesterday's changes"
    enabled: true

  - name: deploy-check
    event: agent.completed
    source_session: build-agent
    prompt: "The build is done. Verify the deployment."
    chain: true          # 链式传播

  - name: pr-review
    event: github.pull_request
    repo: myorg/myrepo
    poll_minutes: 5
    prompt: "Review this PR: {{GITHUB_PR_TITLE}}"
```

### 7.4 引擎架构

```
CueEngine
├── TriggerSource[]          → 事件源（文件监听、定时器、GitHub 轮询）
├── CueSessionRuntimeService → 为每个项目管理 Cue 生命周期
├── CueRunManager            → 并发控制、队列、执行
├── CueDispatchService       → 订阅→Agent 调度（含 fan-out）
├── CueCompletionService     → agent.completed → 链式传播
├── CueSpawnBuilder          → Cue 配置 → 具体 SpawnSpec
├── CueRecoveryService       → 睡眠检测、错过事件回放
└── CueFanInTracker          → 多事件等待后统一触发
```

### 7.5 与 Agent Spawn 的对接

`CueSpawnBuilder.buildSpawnSpec()` 生成 `SpawnSpec`，走与 `ProcessManager.spawn()` 相同的 `buildAgentArgs` 管道，但 Cue 强制 `yoloMode: true` + `forceBatchMode: true`。

链式传播最多 10 层（`MAX_CHAIN_DEPTH = 10`）。

### 对 CLIConductor 的启示

1. **事件类型设计**涵盖了常见自动化场景，CLIConductor 的自动化模块可以直接参考 als event type 枚举
2. **fan-out 模式**：一个订阅 → 多个 Agent 并行执行，天然适合多 Agent 编排
3. **递归限制**：`MAX_CHAIN_DEPTH = 10` 是务实的做法，CLIConductor 的 pipeline 也需要类似的 guard
4. **YAML 配置驱动** vs 代码驱动：CLIConductor 可能更适合 API 驱动（因为是多通道服务），但 YAML 作为持久化格式仍然适用

---

## 八、Group Chat（Symphony）

> 源码位于 `src/main/group-chat/`

### 8.1 对话流程

**严格顺序**：`User → Moderator → Participants → Synthesis → User`

每个 Agent 在自已的轮次中**独立 spawn 为批处理进程**（非长驻、非 TUI），处理完后退出。

### 8.2 Moderator 角色

- 阅读用户消息 → 决定分派给哪些 Participant
- 语法：`@agent-name` 提及，`!autorun @agent:task.md` 触发 Playbook
- 所有 Participant 响应后，Moderator 再次 spawn 做合成总结
- 始终以只读模式运行（不可修改文件）
- 超时：10 分钟（`MODERATOR_RESPONSE_TIMEOUT_MS`）

### 8.3 架构

```
GroupChatRouter
├── routeUserMessage
│   ├── 提取 @mentions → 自动添加 Participant
│   └── spawnGroupChatAgent (spawn Moderator, 批处理)
├── routeModeratorResponse
│   ├── 提取 @mentions → spawnGroupChatAgent (每个 Participant)
│   └── 提取 !autorun → 发送到 Renderer 的 Auto Run 处理器
├── routeAgentResponse → 记录日志 + 更新统计
└── spawnModeratorSynthesis → 所有 Participant 响应后合成
```

**统一 spawn helper**（`src/main/group-chat/spawnGroupChatAgent.ts`）：
- 所有 spawn（Moderator / Participant / Synthesis / Recovery）走同一函数
- 含 SSH remote 探测、Windows shell 配置、token source 决策

### 8.4 防护机制

| 类型 | 超时 | 行为 |
|------|------|------|
| Participant 响应 | 10 分钟 | 超时后强制标记为"已响应"，继续合成 |
| Moderator 响应 | 10 分钟 | 超时后结束本轮 |
| 最大链深度 | 10 层 | 防止无限循环 |

### 对 CLIConductor 的启示

1. **每轮独立 spawn**模式很简单——不需要维护长连接的状态机。CLIConductor 的 Meta-Agent 可以参考同样的"短生命周期 + 上下文传递"方式
2. **@mention 分发**：用 AI 判断分派目标是务实的做法，但存在不可靠性。CLIConductor 的 dispatch 可以显式指定 target Worker
3. **超时即继续**：宁可合成不完整也不阻塞用户，是合理的取舍

---

## 九、总结：CLIConductor vs Maestro 对比

### 相同的核心能力（方向正确性验证）

| | Maestro | CLIConductor |
|---|---------|-------------|
| 多 CLI Agent 进程管理 | ✅ child_process.spawn | ✅ Worker Pool |
| 多后端支持 | ✅ 5 种 Agent | ✅ 计划中 |
| Session 持久化 | ✅ electron-store JSON | ✅ 已有 |
| Session 发现/恢复 | ✅ 读取 Agent 原生存储 | ⏳ |
| 自动化 | ✅ Cue 系统 | ⏳ P2 |
| 多 Agent 协作 | ✅ Group Chat | ✅ Meta-Agent |
| 远程访问 | ✅ Fastify + Cloudflare Tunnel | ✅ Remote 模块 |

### 架构分歧：桌面 IDE vs 服务端调度器

| 维度 | Maestro | CLIConductor |
|------|---------|-------------|
| 进程模型 | 每 Agent 双进程（AI + PTY）| 每 Worker 单 CLI 进程 |
| UI | Rich React 桌面端 | Web Dashboard + QQ |
| 通信 | Electron IPC（进程内）| HTTP/WebSocket（网络）|
| 终端 | xterm.js PTY | takeover 外部窗口 |
| Git | 深度集成（worktrees/diff/PR）| 不在范围 |
| Memory/知识 | 无跨 Session 记忆 | 核心模块 |

### CLIConductor 可以借鉴的具体技术点

| 借鉴项 | 来源 | 难度 |
|--------|------|------|
| Agent 定义驱动 spawn（声明式 `AgentConfig`）| `src/main/agents/definitions.ts` | 低 |
| Session 发现而非导入（读原生文件）| `AgentSessionStorage` 接口 | 低 |
| Write Queue + atomicWriteJson 模式 | `HistoryManager` | 中 |
| 累计→增量 Usage 转换算法 | `normalizeUsageToDelta()` | 低 |
| Cue 事件类型作为自动化设计参考 | `src/shared/cue/contracts.ts` | — |
| 超时+继续的 Group Chat 防护 | `group-chat-router.ts` | 低 |
| Playbook 的"文件驱动任务清单"模型 | `src/cli/commands/run-playbook.ts` | 中 |
