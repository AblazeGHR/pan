# Pan Memory — Phase 1 实施计划

> 基于 OpenClaw 记忆系统分析，Phase 1 实现核心记忆引擎：SQLite 存储 + 混合检索。  
> 日期：2026-07-29 | 分支：`feature/memory`

---

## 1. 目标

实现 OpenClaw 记忆系统的核心能力移植到 Pan，作为 Character 系统的基础设施。

Phase 1 范围：
- SQLite 数据库层（五表 schema：meta, files, chunks, fts, embedding_cache）
- CJK 感知的 Markdown 分块器
- OpenAI embedding 管道 + 缓存
- 混合检索引擎（向量余弦相似度 + FTS5 全文搜索）
- 文件监控 + 增量索引
- MemoryManager 统一 API

Phase 1 不包含：
- Session transcript 索引（P2+）
- 多模态记忆
- MMR 多样性去重 / 时间衰减

---

## 2. 文件清单

```
packages/core/
  ├── memory/
  │   ├── __init__.py        # MemoryManager 统一入口
  │   ├── schema.sql         # SQLite DDL（5 表）
  │   ├── store.py           # MemoryStore 数据库操作层
  │   ├── chunker.py         # CJK 感知 Markdown 分块
  │   ├── embedder.py        # 多 provider embedding 层
  │   ├── search.py          # Hybrid search（向量 + FTS5）
  │   └── watcher.py         # watchdog 文件监控
  ├── manifest_loader.py     # Manifest.json 加载器
  └── memory_context.py      # 记忆上下文注入

packages/web/
  └── server.py (+4 端点)    # /api/memory/{index,search,stats,inject}

characters/
  ├── default/memory/        # 默认角色记忆
  └── coc-keeper/memory/     # COC 主持人角色记忆

tests/
  ├── test_memory_chunker.py    # 22 tests
  └── test_memory_search.py     # 18 tests

docs/plans&overviews/
  ├── openclaw-memory-analysis.md
  └── phase1-memory-plan.md

requirements.txt 新增：
  openai>=1.0.0
  watchdog>=4.0.0
  sentence-transformers>=5.0.0
```

---

## 3. 核心决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 存储引擎 | SQLite + 手动余弦相似度 | 零依赖，单文件，Pan 已有文件系统基础 |
| Embedding（默认） | BAAI/bge-base-zh-v1.5 (768d) | 本地运行、零费用、中文专用 |
| Embedding（可选） | OpenAI text-embedding-3-small (1536d) | 精度更高，需 API key |
| 集成模式 | Core 内部模块 | 先简单，后拆分 |
| 索引范围 | 仅 .md 知识文件 | 对齐 P1 范围，P2+ 加 session |
| CJK 分块 | CJK ratio > 0.2 → 1 char/token | 中英混合 Markdown 正确分块 |

---

## 4. Embedding Provider 配置

### Provider 对比

| Provider | 模型 | 维度 | API Key | 网络 | 中文 |
|----------|------|------|---------|------|------|
| `sentence-transformers`（默认） | BAAI/bge-base-zh-v1.5 | 768 | 不需要 | 下载一次 | 专用 |
| `sentence-transformers` | BAAI/bge-large-zh-v1.5 | 1024 | 不需要 | 下载一次 326MB | 最强 |
| `openai` | text-embedding-3-small | 1536 | 需要 | 需要 | 好 |
| `ollama` | nomic-embed-text | 768 | 不需要 | 本地服务 | 一般 |

### 切换 Provider

```python
# 方式 1：代码中直接指定
from packages.core.memory import MemoryManager, PROVIDER_SENTENCE_TRANSFORMERS

mgr = MemoryManager(
    db_path="data/memory/coc.sqlite",
    provider=PROVIDER_SENTENCE_TRANSFORMERS,
    model="BAAI/bge-large-zh-v1.5",  # 自定义模型
)

# 方式 2：环境变量
# set PAN_ST_MODEL=BAAI/bge-large-zh-v1.5
# set PAN_ST_DIMS=1024

# 方式 3：默认值（无需任何设置）
mgr = MemoryManager("data/memory/coc.sqlite")  # → BAAI/bge-base-zh-v1.5
```

### 离线使用（公司内网 / 无外网）

模型缓存目录默认为 `D:/cache/huggingface`。首次下载后即可离线使用。
如需换目录：

```
set HF_HOME=E:/path/to/cache
```

如果 hf-mirror.com 不可达，会自动回退 huggingface.co（需科学上网）。

默认已设置 `PAN_ST_MODEL` 和 `PAN_ST_DIMS` 两个环境变量。

## 4. API

```python
from packages.core.memory import MemoryManager

mgr = MemoryManager("data/memory/my_char.sqlite", api_key="sk-...")

# 索引整个目录
report = mgr.index_directory("characters/my_char/memory/")
# SyncReport(files_scanned=5, files_modified=5, chunks_upserted=42, details=[...])

# 搜索记忆
results = mgr.search("克苏鲁神话 职业创建", max_results=6, min_score=0.35)
for r in results:
    print(f"[{r.score:.2f}] {r.text[:80]}...")

# 文件监控（可选）
mgr.start_watching("characters/my_char/memory/")

# 统计
stats = mgr.stats()  # IndexStats(files=5, chunks=42)
mgr.close()
```

---

## 5. 数据流

```
角色 .md 文件 ──→ chunker ──→ embedder ──→ store
                    │              │            │
                分块 SHA256   OpenAI API   SQLite 写入
                400 tokens    1536d 向量   chunks + fts
                
用户 query ──→ embedder ──→ HybridSearcher ──→ results
                    │              │
               向量化 query    cosine(0.7) + FTS(0.3)
                              → 加权融合 → minScore → TopN
```

---

## 6. 测试覆盖

```
tests/test_memory_chunker.py — 22 tests
  ├── is_cjk()          — 5 tests (中文/日文/韩文/全角/ASCII)
  ├── CJK 检测比例        — 4 tests (纯 ASCII/纯 CJK/混合/空)
  ├── chars_per_token   — 3 tests (ASCII/CJK/阈值边界)
  └── chunk_markdown()  — 10 tests (空/短行/长行/行号/重叠/哈希)

tests/test_memory_search.py — 18 tests
  ├── empty scenarios   — 2 tests (空查询/空存储)
  ├── hybrid search     — 6 tests (匹配/过滤/限制/FTS-only/容错/低分)
  ├── cosine similarity — 3 tests (完美匹配/正交/零向量)
  ├── FTS normalization — 3 tests (正/负/缺 rank)
  └── query expansion   — 3 tests (CJK保留/标点移除/连字符)

Total: 40 tests, all passing
```

---

## 7. 后续衔接

| 步骤 | 状态 | 说明 |
|------|------|------|
| S1-S6 核心引擎 | 完成 | 本文档所在阶段 |
| S7 HTTP 端点 | 待后续 | `/api/memory/*` 路由 |
| S8 Character 衔接 | 待后续 | ManifestLoader 关联 memory dir |
| S9 Worker 注入 | 待后续 | memory_search tool → Worker context |
