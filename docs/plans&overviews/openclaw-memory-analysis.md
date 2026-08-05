# OpenClaw 记忆系统分析——对 Pan Character 计划的移植参考

> 基于 `D:\project\openclaw` 源码和 `D:\project\MyClaw` 分析笔记。  
> 日期：2026-07-29

---

## 1. 概述

OpenClaw 是一个本地优先、多渠道、插件可扩展的个人 AI 助手（TypeScript / Node.js）。其记忆系统经过长期迭代，设计成熟且分层清晰。以下分析聚焦于记忆系统的存储、检索、分块和嵌入管道，评估其对 Pan 的 Character 记忆计划的移植价值。

项目演进：Warelay → Clawdbot → Moltbot → OpenClaw

---

## 2. 存储架构

### 2.1 目录结构

```
~/.openclaw/state/
  ├── openclaw.sqlite              # 全局共享状态
  └── memory/{agentId}.sqlite      # 每 Agent 独立记忆索引
```

### 2.2 数据库 Schema

源码位置：`packages/memory-host-sdk/src/host/memory-schema.ts`

| 表 | 用途 | 关键字段 |
|----|------|---------|
| `meta` | 键值元数据 | key (PK), value |
| `files` | 文件追踪 | path (PK), source, hash, mtime, size |
| `chunks` | 分块文本 + embedding | id (PK), path, source, text, embedding(JSON), model, start_line, end_line, hash |
| `fts` | FTS5 虚拟表 | text, id, path, source, model（tokenize=unicode61） |
| `embedding_cache` | embedding 去重 | (provider, model, provider_key, hash) 联合主键 |

### 2.3 关键设计决策

- **Embedding 向量** 以 JSON 序列化的 float 数组存于 TEXT 列（不依赖原生 vector 类型）
- **向量搜索** 通过 `sqlite-vec` 扩展实现（动态加载），提供余弦相似度等距离函数
- **FTS5** 默认启用，可选 trigram tokenizer 以改善非拉丁文本匹配
- **source 字段** 区分 `"memory"`（知识文件）和 `"sessions"`（对话记录），为未来跨 session 检索做准备

### 2.4 移植判断

**高度可移植。** 五表设计简洁且语义清晰。Pan 只需将 TypeScript 逻辑转为 Python，使用 `sqlite3` 标准库 + `sqlite-vec` Python binding。

---

## 3. 记忆源文件

### 3.1 文件发现

源码位置：`packages/memory-host-sdk/src/host/internal.ts` 函数 `listMemoryFiles`

```
workspace/
  MEMORY.md           ← 规范记忆入口（大小写敏感）
  memory/*.md         ← 记忆目录（递归扫描）
  extraPaths/         ← 可配置额外路径（per-agent）
```

### 3.2 文件去重

按 `realpath` 去重，多模态文件（image/audio）在启用 multimodal 时一并索引。

### 3.3 移植判断

**直接适用。** 映射到 Pan 的角色系统：

| OpenClaw | Pan 映射 |
|----------|----------|
| `MEMORY.md` | 角色知识库入口文件 |
| `memory/*.md` | 角色知识库分解文件（lore.md, rules.md, worldview.md...） |
| `extraPaths` | manifest.json 中的记忆目录配置 |

---

## 4. 分块策略

### 4.1 参数

```
DEFAULT_CHUNK_TOKENS = 400      # 每 chunk 的 token 上限
DEFAULT_CHUNK_OVERLAP = 80      # chunk 之间的重叠 token 数
CHARS_PER_TOKEN_ESTIMATE = 4    # 英文估算（1 token ≈ 4 chars）
CJK_CHARS_PER_TOKEN = 1         # CJK 实际（1 char ≈ 1 token）
```

### 4.2 算法

源码位置：`packages/memory-host-sdk/src/host/internal.ts` 函数 `chunkMarkdown`

```
1. 按行分割内容
2. 检测 CJK 字符比例：
   - 统计 ord(c) 在 CJK 范围内的字符
   - 若 CJK / total > 阈值 → 使用 CJK_CHARS_PER_TOKEN=1
   - 否则使用 CHARS_PER_TOKEN_ESTIMATE=4
3. max_chars = chunk_tokens × chars_per_token
4. overlap_chars = chunk_overlap × chars_per_token
5. 积累行直到当前 chunk 字符数 ≥ max_chars
6. 冲刷为 chunk：id = SHA256[:16], hash = SHA256(text)
7. 保留最后 overlap_chars 行 → 下一个 chunk 的前缀（重叠窗口）
8. 丢弃空 chunk
```

### 4.3 移植判断

**核心借鉴点，尤其是 CJK 适配。** Pan 的 QQ Bot 场景大量中文文本。如果只用 4 chars/token 估算，会严重高估 chunk 容量（一个 800 字的中文段落 ≈ 800 tokens，但估算只有 200 tokens），导致 chunk 过大、检索精度下降。必须按 CJK 模式计算。

---

## 5. Embedding 管道

### 5.1 提供者支持

源码位置：`packages/memory-host-sdk/src/host/embeddings.ts`

| 提供者 | 默认模型 | 类型 | 多模态 |
|--------|---------|------|--------|
| OpenAI | `text-embedding-3-small` (1536d) | 远程 | 否 |
| Google Gemini | — | 远程 | 是 |
| Voyage | — | 远程 | 否 |
| Mistral | — | 远程 | 否 |
| Ollama | — | 本地 | 否 |
| LM Studio | — | 本地 | 否 |

### 5.2 缓存层

`embedding_cache` 表以 `(provider, model, provider_key, hash)` 为主键，避免对相同文本重复调用 API。hash 是文本内容的 SHA256。

### 5.3 移植判断

**Phase 1 只实现 OpenAI text-embedding-3-small。**
- 价格极低（$0.02/1M tokens）
- 中文效果好（1536 维向量）
- 缓存层完全适用，节省 API 成本
- 本地 embedding（Ollama）→ Phase 2

---

## 6. 检索策略——Hybrid Search

### 6.1 参数配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `maxResults` | 6 | 返回条数上限 |
| `minScore` | 0.35 | 相似度阈值 |
| `hybrid.vectorWeight` | 0.7 | 向量余弦相似度权重 |
| `hybrid.textWeight` | 0.3 | FTS 文本匹配权重 |
| `hybrid.candidateMultiplier` | 4 | 候选池 = N × multiplier，混合后取 Top N |
| `hybrid.mmr.enabled` | false | MMR 多样性去重 |
| `hybrid.temporalDecay.enabled` | false | 时间衰减（半衰期 30 天） |

### 6.2 检索流程

```
query
  │
  ├──→ embedding(query) ──→ sqlite-vec 余弦相似度 ──→ vector_results (score: 0..1)
  │
  ├──→ extractKeywords(query) ──→ FTS5 MATCH ──→ fts_results (rank)
  │
  ▼
  vector_results (×0.7) + fts_results (×0.3)
  ▼
  weighted fusion → candidate list
  ▼
  [optional] MMR diversity rerank
  [optional] temporalDecay recency bias
  ▼
  minScore filter (≥0.35)
  ▼
  Top N (maxResults=6)
```

### 6.3 Query Expansion

源码位置：`packages/memory-host-sdk/src/host/query-expansion.ts`

对 query 做关键词提取（去掉 stop words、标点、太短的 token），用于 FTS 搜索。OpenClaw 的 stop words 是英文列表，移植时改为 jieba 分词 + 中文 stop words。

### 6.4 移植判断

**完整移植，参数可调。** 混合检索策略是工业界验证的方案。Phase 1 参数直接用 OpenClaw 默认值，不启用 MMR 和 temporalDecay。FTS5 unicode61 中文分词效果一般，Phase 2 可考虑 jieba 分词后存入 FTS 或改用 trigram tokenizer。

---

## 7. 同步策略

```
onSessionStart: true     — 会话启动时全量同步
onSearch: true           — 搜索前增量同步
watch: true              — 文件系统实时监控（debounce 1500ms）
intervalMinutes: 0       — 定期轮询（默认关闭）
sessions.deltaBytes:   100KB — 会话增量索引阈值（字节数）
sessions.deltaMessages: 50   — 会话增量索引阈值（消息数）
postCompactionForce: true    — 上下文压缩后强制重建 session 索引
```

### 7.1 移植判断

**Phase 1 仅实现文件监控 + 搜索前同步。** Session indexing 延期到 P2+（与 k3 建议 "先做检索，延期知识积累" 一致）。

---

## 8. Agent 系统概述

### 8.1 Context Engine（可插拔上下文引擎）

```typescript
interface ContextEngine {
  bootstrap?()        // 初始化 session 状态
  ingest()            // 摄入消息
  ingestBatch?()      // 摄入一轮对话
  afterTurn?()        // 回合后回调
  assemble()          // 组装 LLM 上下文
  compact()           // 压缩上下文
  prepareSubagentSpawn?()  // 子代理孵化
  onSubagentEnded?()       // 子代理结束
}
```

默认的 "legacy" engine 是直通模式——所有行为委托给现有管线。第三方 engine 可以完全接管。

### 8.2 子代理系统

- 通过 `sessions.spawn` 工具孵化
- 上下文模式：`isolated`（干净上下文）/ `fork`（继承父上下文）
- 生命周期注册表（event-driven）+ 60s 清理死进程的 sweeper

### 8.3 移植判断

**不适合移植。**

- **Context Engine** — TypeScript 领域的高度可插拔设计。Pan 的 session 模型简单（`[{role, content}]` 列表），不需要这个抽象层。
- **子代理孵化系统** — 场景根本不同。OpenClaw 的子代理是进程内任务委托，Pan 的 Worker 是外部 CLI 子进程（cbc/kimi），由 Meta-Agent 调度。

---

## 9. System Prompt 组装模式

OpenClaw 的 system prompt 从多个来源分层组装：

```
优先级：agents.md > soul.md > identity.md > user.md > tools.md > bootstrap.md > memory.md

各层职责：
  1. Identity  — SOUL.md（个性）、agent.md（规则）
  2. Workspace — bootstrap 文件
  3. Memory    — 插件注入的记忆引导语句
  4. Tools     — 工具描述
  5. Channel   — 通道特定指令
```

### 9.1 移植判断

**局部借鉴。** Pan 的 `manifest.json` → `profiles[]` 已经天然支持 `system_prompt` 字段。可以参照 OpenClaw 的分层组装思路，在编译 Worker 启动参数时合并多层 prompt：

```
基础 system_prompt（manifest）
  + 角色行为规则（profile.rules 文件）
  + 记忆检索结果（memory_search 结果注入）
  + 工具描述（MCP servers 声明）
```

---

## 10. 总体移植评估

| 模块 | 移植价值 | 理由 |
|------|---------|------|
| **存储架构** | 高 | SQLite 五表设计简洁通用，直接映射到 Python sqlite3 |
| **Hybrid Search** | 高 | 向量+FTS 是工业界标准方案，参数可调 |
| **CJK 分块** | 高 | Pan 中文场景必须的优化，OpenClaw 已实现 |
| **Embedding 缓存** | 高 | 节省 API 成本，实现简单 |
| **文件同步** | 中 | watchdog 监控 + 增量索引，Phase 1 只做文件级 |
| **System Prompt 分层** | 中 | 模式可借鉴，实现需适配 manifest.json |
| **Context Engine** | 低 | 过度抽象，Pan 不需要 |
| **子代理系统** | 极低 | 模型根本不同（外部 CLI vs 内嵌 agent） |

### 不建议移植的原因总结

- **Context Engine** — Pan 的 session 模型只是 `[{role, content}]` 列表。可插拔上下文引擎是重型抽象，增加复杂度却无实际收益。
- **子代理孵化** — Pan 的 Worker 是外部 cbc/kimi 子进程，由 Meta-Agent 通过 Pan Core 调度。OpenClaw 的进程内子代理委托模型与此完全不同。硬移植只会产生 bug 和不一致的语义。
- **会话索引** — P2+ 再考虑将 session transcripts 索引入记忆库。Phase 1 严格只索引角色知识文件。
