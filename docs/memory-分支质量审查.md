# feature/memory 分支质量审查

> 审查日期：2026-07-31
> 审查范围：`feature/memory` 分支相对 `main` 的全部差异（47 commits ahead, 3 behind）
> 审查方法：对 memory 子系统、worker dual-mode、MCP server、character 框架、测试套件进行逐文件代码审查

## 总体评价

**memory 分支的核心功能基本是坏的，且存在多个安全漏洞。** 主要问题：

- memory 注入（worker 路径）因 embedding provider 不一致，实际返回垃圾结果或空
- FTS5 排序方向反了，即使 provider 一致文本搜索也是错的
- SessionIndexer 写入直接抛异常（违反 CHECK 约束）
- MCP 模式进程泄漏 + 错误吞没，稳定性差
- 路径穿越、任意目录读取、无认证三连

建议：要么投入一轮集中修复，要么先合入独立可用的部分（character 框架数据模型、MCP server API 包装），把 memory 子系统和 worker dual-mode 留在分支继续打磨。

---

## 严重（合并前必修）

### 1. Embedding provider 不一致导致 memory 注入返回垃圾结果

**位置**：
- `packages/web/server.py:1370` — `_get_memory_manager` 用 `PROVIDER_OPENAI`（1536 维，默认）
- `packages/core/memory_context.py:48` — `search_and_format` 用 `PROVIDER_SENTENCE_TRANSFORMERS`（768 维）
- `packages/core/character.py:261` — `CharacterManager.get_memory_manager` 用 `PROVIDER_SENTENCE_TRANSFORMERS`（768 维）

**问题**：同一个 `data/memory/{character_id}.sqlite` 被三个调用点用不同 provider 读写：
- 通过 web API `/api/memory/index` 索引 → 写入 1536 维 OpenAI embedding
- worker 注入时 `search_and_format` → 计算 768 维 ST query embedding
- `HybridSearcher._cosine_similarity` 用 `zip(a, b)` **静默截断到短向量**，`norm_b` 却在全部 1536 维上计算，结果是 `dot_768 / (norm_a_768 * norm_b_1536)` —— 无意义的垃圾分数

**影响**：memory 注入功能实际是坏的。要么返回不相关片段，要么因 `min_score=0.35` 阈值过滤掉全部结果静默返回空。无任何错误日志。

**修复**：统一 provider。或在 `meta` 表记录索引时的 provider/dims，打开 DB 时校验，不一致则报错。

### 2. FTS5 分数归一化方向反了

**位置**：`packages/core/memory/search.py:187`

**问题**：公式 `scores[id] = 1.0 / (1.0 + (-rank))`。但 SQLite FTS5 的 rank 越负越好（BM25 × -1）：

| 匹配质量 | rank | -rank | 归一化得分 |
|---------|------|-------|-----------|
| 更好 | -5.0 | 5.0 | 1/6 = **0.167** |
| 更差 | -1.0 | 1.0 | 1/2 = **0.500** |

更好匹配反而得分更低。混合公式 `vector_weight * vs + text_weight * ts` 中，差匹配贡献更大，FTS 排序完全错乱。

**修复**：改为 `(-rank) / (1.0 + (-rank))`，映射 `[0, ∞) → [0, 1)`，更好匹配（更大 `-rank`）得分更高。

### 3. MCP 模式在途进程泄漏

**位置**：`packages/core/worker.py:385, 562-563`

**问题**：`_consumer_mcp` 的 `proc` 是局部变量，未存到 Worker。MCP 模式下 `w.process = None`。`_kill_process_tree` 见到 `w.process` 为 falsy 直接返回：

```python
async def _kill_process_tree(w: Worker) -> None:
    if not w.process:
        return
```

整个清理链（`kill_worker`、`shutdown_all`、`interrupt_worker`、`session_delete`）在 MCP 模式下都是 no-op。取消 `_consume_task` 时 `CancelledError` 穿过只捕获 `TimeoutError` 的 try 块，`proc.kill()` 永远不到达，局部 `proc` 引用被丢弃，cbc 子进程（及其 MCP server 孙进程）被孤儿化。

**影响**：每次 `kill_worker`/`restart_worker`/`interrupt_worker`/`session_delete`/进程退出，只要 MCP 任务在途，就泄漏一个 cbc 进程。繁忙服务器上快速累积。

**修复**：把在途 `proc` 存到 Worker（如 `w._mcp_proc`），在 `_kill_process_tree` 或 `_consumer_mcp` 的 `finally` 块中 kill。read/wait 包 `try/finally: proc.kill()`。

### 4. SQLite 跨线程共享连接无锁

**位置**：`packages/core/memory/store.py:21`

**问题**：`sqlite3.connect(db_path, check_same_thread=False)` 创建单一连接，全线程共享，但 `MemoryStore` 无任何 `threading.Lock`。`with self._conn:` 只管事务提交/回滚，不串行化访问。

- `watcher.py:71-73` — `watchdog.Observer` 在后台线程触发 `_on_change` → `index_file` → `store.insert_chunk`，与主线程共享同一连接
- FastAPI sync endpoint 在 threadpool 中并发写同一连接

并发写可触发 `sqlite3.ProgrammingError: Recursive use of cursors not allowed` 或破坏事务状态。无 `busy_timeout`，`database is locked` 无重试。

**修复**：加 `threading.Lock` 包裹所有写操作；`PRAGMA busy_timeout=5000`；或每线程独立连接。

### 5. 路径穿越：character_id 未校验

**位置**：`packages/web/server.py:1358-1370`

**问题**：`/api/memory/search` 和 `/api/memory/stats` 的 `character_id` 是 query param，`/api/memory/index` 和 `/api/memory/inject` 是 body 字段。`_get_memory_manager` 直接拼接：

```python
db_path = str(DATA_DIR / "memory" / f"{character_id}.sqlite")
```

请求 `GET /api/memory/search?character_id=../../etc/pwned&q=x` → 解析为 `data/memory/../../etc/pwned.sqlite` = `etc/pwned.sqlite`。handler 还会 `mkdir(parents=True, exist_ok=True)` 创建父目录。

**影响**：可在任意位置创建/读写 SQLite 文件。`character_id` 从不校验格式（应为 `^char_[0-9a-f]{16}$`）。

**修复**：校验 `character_id` 格式；或 `if "/" in character_id or ".." in character_id: reject`。

### 6. 任意目录读取：dir_path 未限制

**位置**：`packages/web/server.py:1497`

**问题**：`/api/memory/index` 接受任意 `dir_path`，直接传给 `mgr.index_directory(dir_path)`，无任何限制（对比 `/api/fs/read` 用 `_resolve_fs_path` 限制在 session workdir 内）。

**影响**：客户端可索引服务器上任意目录的所有 `.md` 文件到 character 的可搜索 memory，再通过 `/api/memory/search` 读取内容 —— 信息泄露。

**修复**：限制 `dir_path` 到允许的根目录，类似 `_resolve_fs_path` 的 `target.relative_to(root)` 检查。

### 7. Embedding 失败后永久破坏索引

**位置**：`packages/core/memory/__init__.py:193-213`

**问题**：`_index_single_file` 顺序错误：
1. 先删旧 chunks（line 193）
2. 更新 file hash 为新内容（line 194-200）
3. **再** 调 `embed_batch`（line 213）

如果 embedding API 失败，异常传播，文件留下：hash 已更新为新内容，但 chunks 为空。`index_file` 靠 hash 判断是否跳过（line 186），后续调用看到 hash 匹配直接跳过 —— **该文件永久无法被索引**，直到内容再次变化。

**修复**：用事务包裹，hash 更新放到 embedding 成功之后。

---

## 高（影响功能正确性）

### 8. MCP 模式超时用户无感知

**位置**：`packages/core/worker.py:400-413`

**问题**：120s 读超时只 `_log.warning`，用户看到 `"(no output)"` 而非超时提示。消息丢失无重试。session history 追加了 user turn 但无对应 assistant turn。

超时后还要再等 10s `proc.wait()`，进程几乎必然还活着，再 `proc.kill()`。单次超时阻塞 worker 130s，期间所有排队消息无响应。

**修复**：超时时显式设置 `s.last_result = {"status": "error", "result": "Task timed out after 120s and was killed"}` 并广播。

### 9. cbc 非零退出被吞

**位置**：`packages/core/worker.py:454-460`

**问题**：MCP 模式从不读 `proc.returncode`。崩溃但吐了部分 result 时报 `done`；无 result 时报 `"(no output)"`。真实 stderr（merged into stdout）只进 `.pan-cbc-raw.jsonl` debug 文件，用户看不到。

对比 stream 模式有显式的非零退出处理和 `worker.crashed` 广播，MCP 模式无等价物。

**修复**：`proc.wait()` 后读 `proc.returncode`，非零且无 result 时把 returncode 和 output 末尾 ~2KB 放进 `s.last_result`。

### 10. 删除的 session 被在途任务复活

**位置**：`packages/core/worker.py:449, 461`

**问题**：`_consumer` 取 `s = _session(w)` 一次，传给 `_consumer_mcp`。如果并发 `DELETE /api/sessions/{id}` 删除 session，`_consumer_mcp` 仍操作陈旧 `s` 引用：`s.history.append(...)`、`s.cli_session_id = ...`、`await _sess.save_async(s)` —— **把删除的 session 重新写回磁盘**。

**修复**：`_consumer_mcp` 保存前重新 `s = _sess.get(w.session_id)`，None 则放弃。

### 11. cli_session_id 清除逻辑失效

**位置**：`packages/core/worker.py:493-497`

**问题**：`create_worker` 里的 stale session 清除依赖 `find_worker_by_session` 找到活 worker：

```python
old = find_worker_by_session(session_id)
if old:
    await kill_worker(old.worker_id)
    if s.cli_session_id:
        s.set_adapter_field("cli_session_id", None)
```

但调用方（`/api/spawn`、`/api/task` 自动重启）总是**先 `kill_worker` 再 `create_worker`**：

```python
existing = worker.find_worker_by_session(session_id)
if existing:
    await worker.kill_worker(existing.worker_id)  # 从 workers dict 移除
result = await worker.create_worker(session_id)    # find_worker_by_session → None
```

`kill_worker` 后 worker 已从 dict 弹出，`create_worker` 内 `find_worker_by_session` 返回 None，清除被跳过，新 worker 用着 stale 的 `--resume` ID。

**修复**：无条件清除 `cli_session_id`，或让 `kill_worker` 负责清除。

### 12. mcp_mode 锁可被异常绕过

**位置**：`packages/web/server.py:337-357`

**问题**：`_apply_mcp_enabled` 的异常处理：

```python
try:
    char = _character_manager.get_character(s.character_id)
    if char:
        # 检查 mcp_mode，违反则 raise ValueError
except ValueError:
    raise
except Exception:      # 吞掉所有其他异常
    pass
s.set_adapter_field("mcp_enabled", enable)   # 异常时执行，绕过锁
```

任何非 ValueError 异常（PermissionError、未来重构的 KeyError 等）都会绕过 `always`/`never` 锁。

**修复**：移除 `except Exception: pass`，或缩窄到特定异常。

### 13. 相同文本 chunk ID 碰撞

**位置**：`packages/core/memory/chunker.py:193,197`

**问题**：chunk hash = `sha256(chunk_text)`，ID = `hash[:16]`，**不含 `source_path`**。文件 A 和 B 含相同段落 → 相同 chunk ID → `INSERT OR REPLACE INTO chunks` 用 B 的 `path` 覆盖 A 的 chunk，静默丢数据。

**修复**：`sha256(source_path + ":" + chunk_text)`。

### 14. 切换 embedding provider 静默坏结果

**位置**：`packages/core/memory/search.py:170`

**问题**：`_cosine_similarity` 用 `zip(a, b)`，长度不同时静默截断到短向量。无 `len(a) == len(b)` 校验。从 OpenAI（1536）切到 Ollama（768），旧 chunks 1536 维，新 query 768 维，`zip` 只算前 768 维 —— 垃圾分数，无错误。

**修复**：维度不匹配时 raise，或记录 provider/dims 在 DB 拒绝跨 provider 查询。

### 15. 删除的文件永不从索引移除

**位置**：`packages/core/memory/watcher.py:144-154`

**问题**：`_Handler` 只处理 `on_modified`/`on_created`（且这两个是死代码，见低危项），无 `on_deleted`。`sync`/`index_directory` 也只遍历磁盘现有文件，不删除索引中已不存在的文件条目。索引无限积累孤儿数据。

**修复**：实现 `on_deleted`；`sync` 对比索引与磁盘，删除孤儿。

### 16. 无认证 / 无 rate limit

**位置**：`packages/web/server.py` 全局

**问题**：所有 character/memory endpoint 无 auth。`GET /api/characters` 列出所有 character ID，`DELETE /api/characters/{id}` 可删任意 character。无 rate limit。绑定 127.0.0.1 是唯一屏障，多用户机器或 DNS rebinding 仍可攻击。

**修复**：至少加 shared-secret token；显式绑定 127.0.0.1；文档警告 SSE transport 不可暴露到公网。

### 17. spawn/settings 端点未捕获 ValueError

**位置**：`packages/web/server.py:812, 1208`

**问题**：`POST /api/worker`（spawn）和 `POST /api/worker/{id}/settings` 调用 `_apply_session_updates` 无 `try/except ValueError`。mcp 锁违反时 raise → FastAPI 返回 HTTP 500 而非 422/400。

对比 `PATCH /api/sessions/{id}`（line 590-593）正确捕获了。锁本身仍有效（异常在 `sess.save` 前抛出），但客户端体验差。

**修复**：在 spawn 和 settings 端点加 `try/except ValueError` 返回 `{"error": ...}`。

---

## 中（设计缺陷 / 性能）

### 18. SessionIndexer 违反 CHECK 约束

**位置**：`packages/core/memory/session_indexer.py:91` vs `packages/core/memory/schema.sql:13,23`

**问题**：写入 `source="sessions:xxx"`，但 schema 的 CHECK 约束是 `source IN ('memory','sessions')`。整个 SessionIndexer 写入会抛 `IntegrityError`。

**修复**：放宽 CHECK 约束，或改 `source` 值。

### 19. session temp 文件 chunks 永久累积

**位置**：`packages/core/memory/session_indexer.py:82-95`

**问题**：每次 `index_history` 用新 `NamedTemporaryFile` 路径索引，temp 文件删了但 `files`/`chunks` rows 留着。每次 re-index 新 path → 新 rows，旧 rows 不清理。DB 无限增长。

**修复**：用固定虚拟路径（如 `session://{session_id}`），re-index 前删旧 rows。

### 20. sentence-transformers 模型每次消息重载

**位置**：`packages/core/memory_context.py:71-80`

**问题**：`search_and_format` 每次新建 `MemoryManager` 并 `close()`，ST 模型加载 3-10s/次（`BAAI/bge-base-zh-v1.5`）。worker 路径无缓存，每条用户消息都重载。

对比 `web/server.py:1356-1374` 按 `character_id` 缓存 `MemoryManager`，worker 路径无此缓存。

**修复**：worker 侧也按 `character_id` 缓存 `MemoryManager`，或复用 `_get_memory_manager`。

### 21. 每次搜索全表扫描

**位置**：`packages/core/memory/store.py:151-157`

**问题**：`get_chunks_for_search` 加载全部 chunks（含 JSON 序列化的 embedding）到内存，`search.py:104-118` 再 JSON 反序列化所有 embedding 算纯 Python 余弦。O(n) 每次搜索，撑不过几千 chunks。无 ANN 索引。

**修复**：引入 `sqlite-vec` 或 `faiss`；或至少在 SQL 层做粗筛。

### 22. Path.cwd() vs DATA_DIR 分歧

**位置**：`packages/core/worker.py:41` vs `packages/web/server.py:1364`

**问题**：worker 用 `str(Path.cwd() / "data" / "memory")`，web 用 `DATA_DIR`（从 `__file__` 解析的绝对路径）。如果 worker 从不同 cwd 启动（如通过 `-d workdir`），两边打到不同 DB，索引和搜索完全脱节。

**修复**：统一用 `DATA_DIR` 绝对路径。

### 23. FTS5 id 列被全文索引

**位置**：`packages/core/memory/schema.sql:38`

**问题**：`fts` 表的 `id` 是普通列（被索引），应为 `id UNINDEXED`。16 位 hex chunk ID 被 token 化，查询含 `abc`/`dead` 等会假匹配 chunk ID。浪费索引空间。

**修复**：改为 `id UNINDEXED`。

### 24. INSERT OR REPLACE 对 FTS5 无效

**位置**：`packages/core/memory/store.py:121-133`

**问题**：FTS5 虚表 `id` 无 UNIQUE/PK 约束，`INSERT OR REPLACE` 实际等同 `INSERT`（只在 UNIQUE/PK 冲突时 replace）。重复索引会累积重复 FTS 行，导致重复搜索结果和虚高分数。当前仅靠"先 delete 再 insert"的隐式契约避免，schema 层不保证。

**修复**：先 `DELETE FROM fts WHERE id = ?` 再 insert，或用 rowid 管理。

### 25. 无迁移系统

**位置**：`packages/core/memory/store.py:31-42`, `schema.sql:4-7`

**问题**：`_ensure_schema` 只检查 `meta` 表是否存在，存在就返回，不应用任何更新。`meta` 表设计用于版本管理但**从不读写**。schema 改动对旧 DB 静默忽略，运行时查询失败才暴露。

**修复**：在 `meta` 记录 schema_version，打开时检查并运行迁移。

### 26. 无 busy_timeout

**位置**：`packages/core/memory/store.py` 全局

**问题**：WAL 模式允许并发读，但并发写仍串行。无 `PRAGMA busy_timeout`，并发写直接 `sqlite3.OperationalError: database is locked`，无重试。

**修复**：`PRAGMA busy_timeout=5000`。

### 27. FTS5 操作符注入

**位置**：`packages/core/memory/search.py:191-215`

**问题**：`_expand_query` 的正则保留 `\w`，包含 `OR`/`AND`/`NOT` 等 FTS5 操作符。查询 `"cat OR dog"` 被解释为布尔操作，可能抛 `fts5: syntax error`。

**修复**：每个 term 用双引号包裹强制字面匹配。

### 28. chunks.model 永远记 OpenAI 模型名

**位置**：`packages/core/memory/__init__.py:29,224`

**问题**：`EMBEDDING_MODEL = OPENAI_DEFAULT_MODEL` 用于所有 chunk 的 `model` 字段，不管实际用哪个 provider。`model` 列失去调试/过滤意义。

**修复**：用 `embedder.model_name` 记录实际模型。

### 29. output += chunk 无上限

**位置**：`packages/core/worker.py:405`

**问题**：MCP 模式下 `output += chunk` 无限累积 cbc stdout，行为异常的 cbc 或冗长的 MCP server 可导致 OOM。

**修复**：设上限（如 16MB），超限中止。

### 30. memory_context.py 连接泄漏

**位置**：`packages/core/memory_context.py:65-73`

**问题**：`mgr.search()` 抛异常时跳到 `except` 块，`mgr.close()` 不到达。`MemoryManager` 支持 `__enter__`/`__exit__`，应用 `with` 语句。

### 31. memory_dir 可逃逸 plugin 目录

**位置**：`packages/core/manifest_loader.py:144-148`

**问题**：只有相对路径被 join 到 `plugin_dir`，绝对路径（`"/etc"`）原样使用，相对 `..` 路径 join 后由 `index_directory` 解析。无 `relative_to(plugin_dir)` 容纳检查。恶意 manifest 可指向任意目录，`index_directory` 读取该目录所有 `.md` 到可搜索 DB —— 信息泄露。

**修复**：resolve 后 `relative_to(plugin_dir)` 校验。

### 32. CharacterManager 无并发锁

**位置**：`packages/core/character.py:241-267`

**问题**：`get_memory_manager` 的 check-then-act 非原子。Thread A 删除 character（pop 并 close manager），Thread B 在 `if character_id in self._memory_managers` 之后但 insert 之前，会为已删除 character 创建新 manager 并插入，泄漏指向已删除 DB 文件的连接。

**修复**：加 `threading.Lock` 保护 `_memory_managers` 操作。

### 33. 删除 character 后 server 缓存残留

**位置**：`packages/web/server.py:1356`

**问题**：`DELETE /api/characters/{id}` 调 `CharacterManager.delete_character` 关闭其缓存，但 server 的 `_memory_managers` 仍持有该 character 的打开 `MemoryManager`，指向已 unlink 的 `.sqlite`。后续 `/api/memory/search?character_id=<deleted>` 复用陈旧连接（fd 仍有效），返回陈旧数据而非 "not found"。

**修复**：删除 character 时同步从 `_memory_managers` 清理。

### 34. health_check URL 在 stdio 模式不可达

**位置**：`packages/mcp/manifest.json:6`

**问题**：`health_check: "http://127.0.0.1:9740/health"`，但 server 只在 SSE/streamable-http transport 下起 HTTP server。manifest 的 `args` 无 `--transport` 标志（默认 stdio），无 HTTP server，无 `/health` 端点，健康检查永远失败。

**修复**：manifest 指定 `--transport sse --port 9740`，或移除 stdio 模式的 health_check。

---

## 低（代码异味 / 测试问题）

### 35. 死代码

- `packages/core/worker.py:307` — `_format_history_for_context` 定义后从不调用
- `packages/core/memory/watcher.py:129,133-142` — `_Handler.on_modified`/`on_created` 是死代码（`dispatch` 被覆盖），`FileSystemEventHandler` 死导入

### 36. watcher 递归 vs index_directory 非递归

`watcher.py:72` 用 `recursive=True`，但 `__init__.py:129` 用 `p.glob("*.md")`（非递归）。子目录新建文件会被 watcher 索引但被 `sync()` 漏掉，行为不一致。

### 37. 假测试（无 assert）

- `tests/test_memory_chunker.py::test_overlap_exists` — 计算 `combined`/`all_lines` 但从不 assert，overlap 完全坏也能过
- `tests/test_character.py::test_get_memory_manager_no_api_key` — 只验证不抛异常，无任何 assert

### 38. 搜索测试全用 mock

`tests/test_memory_search.py` 全部用 `MagicMock` store，monkeypatch `_cosine_similarity`。从不测真实 SQLite/FTS5/SQL。FTS5 查询字符串、tokenizer 配置、schema 回归都测不到。

### 39. 预构建 SQLite 被提交到 git

`memory/char_*.sqlite{,-shm,-wal}` 6 个文件是运行时残留（空 schema，非 test fixture）。`.sqlite-wal` 文件每次访问都变，会持续产生 git diff。

**修复**：从 git 删除，加到 `.gitignore`。

### 40. _dedup_append 同名 profile 静默覆盖

`packages/core/manifest_loader.py:194` — 两个插件声明同名 profile，后者静默替换前者（含 `system_prompt`/`mcp_mode`/`memory_dir`），无警告。后安装的插件可 shadow 内置 profile，运维无感知。

**修复**：替换时 `log.warning`。

### 41. --pan-url 覆盖脆弱

`packages/mcp/server.py:216` — `__main__._pan_api_url = ...` 设置在 `__main__` 上，只在 `python -m packages.mcp.server` 时生效（此时 `__main__` 就是该模块）。作为库导入时覆盖失效，静默用默认 URL。

**修复**：`global _pan_api_url; _pan_api_url = ...`。

### 42. chunker 无代码块/标题感知

`packages/core/memory/chunker.py` 纯按行/字符长度切分，不识别 ```` ``` ```` 代码块，可能在代码块中间断开破坏语法。不按标题边界切分，chunk 可能缺少所属章节标题（标题常含最重要关键词）。

### 43. embed_batch 无分批

`packages/core/memory/embedder.py:133-161` — 所有未缓存文本一次性发给 OpenAI，大文件可能超 API 输入限制。`_embed_local` 甚至一个一个 embed，无批处理。

---

## 合并前必修清单

按优先级排序：

1. **#1** 统一 embedding provider（或加 provider 校验）
2. **#2** 修复 FTS5 归一化公式
3. **#5** 校验 `character_id` 格式
4. **#6** 限制 `/api/memory/index` 的 `dir_path`
5. **#3** MCP 进程存到 Worker 并在清理时 kill
6. **#7** embedding 失败时事务回滚或后置 hash 更新
7. **#4** SQLite 加锁 + busy_timeout
8. **#39** 删除 git 中的 `.sqlite` 文件并 gitignore
9. **#12** 移除 `except Exception: pass`
10. **#17** spawn/settings 端点加 ValueError 捕获

其余高/中危项可在合并后逐步修复，但 #1-#7 直接影响核心功能可用性和安全性，建议合并前解决。

---

# 复查报告（2026-08-04，HEAD=52aef16）

> 复查范围：`2fb34f6..52aef16`，即修复提交 `8da29c2`（声称修复 #1-#7 #12 #17 #18 #19 #22 #29 #39）+ 合并提交 `52aef16`（`725e680` branch 继承 + `f9a28da` coldstart profile + `manifest.json`）。
> 验证：`pytest` 85 passed ✓；legacy `tsc --noEmit` ✓；React `pnpm build` 因本地缺 `node_modules` 失败（环境问题，非本分支回归，见下）。

## 结论

**上一轮的 10 项合并前必修项中，9 项已真正修复，1 项（#39）只做了一半。但本轮合入的新代码引入了 3 个新的合并阻塞问题（manifest 绝对路径、character 端点路径穿越、fork 回退不一致）。**

核心 memory 子系统的正确性修复（#1/#2/#7/#14/#18/#19）质量良好：provider 统一到 ST、meta 记录维度、embedding 失败不落库、FTS 归一化公式改对，且有对应测试。建议合入前先解决下方「新发现的合并阻塞项」。

## 已确认修复（8da29c2）

| # | 项 | 状态 |
|---|----|------|
| 1 | embedding provider 统一 | ✓ server `_get_memory_manager` 改用 ST；meta 表记录 provider/dims，打开时校验，不一致 raise。worker/character 路径本就是 ST，三处一致 |
| 2 | FTS5 归一化公式 | ✓ `(-rank)/(1+(-rank))`，更好匹配得分更高；测试已同步更新 |
| 6 | dir_path 任意目录 | ✓ `_PROJECT_DIR` 容纳检查（`target.relative_to`） |
| 7 | embedding 失败破坏索引 | ✓ embed 先于任何 DB 写；`replace_file_chunks` 事务内删旧写新 |
| 12 | mcp_mode 锁被宽泛 except 绕过 | ✓ 移除 `except Exception: pass` |
| 14 | 维度不匹配 zip 截断 | ✓ 搜索时校验 `len(emb) == query_dims`，不符则跳过并告警；有回归测试 |
| 17 | spawn/settings ValueError → 500 | ✓ 两端点捕获 ValueError 返回 `{"error":...}` |
| 18 | SessionIndexer 违反 CHECK | ✓ `source="sessions"` |
| 19 | session temp 文件孤儿累积 | ✓ 稳定虚拟路径 `session://{id}` + 整体替换 |
| 26 | 无 busy_timeout | ✓ `PRAGMA busy_timeout=5000` |
| 29 | MCP 输出无上限 | ✓ 16MB 截断并 kill |
| 3 | MCP 在途进程泄漏 | ✓ `w._mcp_proc` 记录在途 proc，`_kill_process_tree` 双路径处理，`finally` 兜底 kill |
| 22 | worker/server 路径分歧 | ✓ 基本修复：worker 与 `/api/memory/inject` 都用项目根 `data/memory`（`search_and_format` 的 cwd 默认值仍在，但无残余调用者） |

## 部分修复 / 未达承诺

### #39 只做了一半 — sqlite 仍被 git 跟踪
`8da29c2` 提交信息声称「git 删除 runtime sqlite 残留」，但 `git ls-files` 仍列出 6 个文件：
```
memory/char_0dbe7e52ad06c3cd.sqlite{,-shm,-wal}
memory/char_2692a6d29f0f4736.sqlite{,-shm,-wal}
```
只加了 `.gitignore` 条目（且 `memory/char_*.sqlite` 模式不匹配新路径 `data/memory/`，不过 `data/` 本就已忽略，无实际影响）。`-wal` 文件每次访问都变，仍会持续产生 diff。
**修复**：`git rm --cached memory/char_*.sqlite{,-shm,-wal}`。

### #4 SQLite 锁只覆盖了写
RLock + busy_timeout 已加，但读方法（`get_chunks_for_search`、`search_fts`、`get_meta`、`get_file`、`get_stats`）未加锁。watcher 线程写事务与 API 线程池读并发时，同一连接上仍可能触发 `sqlite3.ProgrammingError: Recursive use of cursors not allowed`。低概率竞态，未完全关闭。

## 新发现的合并阻塞项（来自 52aef16）

### N1. manifest.json 硬编码绝对路径（合入 main 即坏）
`manifest.json` 的 `mcp_servers.rulewhisper`：
```json
"command": "D:/project/ai_coc/.venv/Scripts/python",
"cwd": "D:/project/ai_coc"
```
这是**本机**另一项目的 venv 绝对路径，已提交进 git。其他机器/CI 克隆后：
- rulewhisper MCP server 无法启动；
- 而 `coc-keeper-coldstart` profile 的 `system_prompt` 明确「禁止验证系统信息、禁止重新发现 MCP 工具、已连接 RuleWhisper」——模型会在工具实际不存在时自信地调用幻觉工具，产生错误结果且无法自纠。

**修复**：manifest 的 MCP server 定义改为相对/可配置（如环境变量占位 `$AI_COC_ROOT`），或从默认 manifest 移除、放入用户级 `~/.codebuddy/mcp.json`（Kimi adapter 已是这种模式）。

### N2. system_prompt 含拼写错误
`coc-keeper-coldstart` prompt 中 `ddata/sessions/（会话存储）` —— 应为 `data/sessions/`。信息注入错误，会误导模型关于项目结构。

### N3. fork_cbc_session 回退后 custom-title 写到错误目录
`fork_cbc_session` 经 `_find_session_jsonl` 找到父 session 后把 `proj_dir` 重指到 `found.parent`（sessions.py:559），新 session 文件 + meta 写在该目录；但随后 `write_custom_title(new_id, name, cwd)`（sessions.py:582）仍用 `_project_dir(cwd)` 解析路径 → 与 `proj_dir` 不一致时，标题事件被静默丢弃（`if not path.exists(): return`）。

### N4. branch 后 Pan 侧 history/usage 为空
`api_branch_session`（server.py:681-682）用 `cbc_sessions.parse_cbc_history(new_cli_id, cwd)` / `get_raw_usage(new_cli_id, cwd)` 解析新 session，两者仍走 `_project_dir(cwd)`，**没有** `_find_session_jsonl` 回退。当 fork 走了跨目录回退（这正是该提交要解决的场景），新文件不在 `_project_dir(cwd)` 下 → 返回空 history / 空 usage。cbc `--resume` 仍能靠 JSONL 恢复模型上下文，所以功能"能跑"，但 Pan Session 的历史列表与用量统计为空。
**修复**：把 `_find_session_jsonl` 的解析逻辑下沉为一个共用的 `_resolve_session_path(session_id, cwd)`，fork/parse/get_raw_usage/write_custom_title 统一调用。

### N5. character GET/DELETE 端点未校验 character_id（#5 修复不完整）
`#5` 只修了 memory 四个端点，`/api/characters/{id}` 的 GET（server.py:1486）和 DELETE（server.py:1515）仍直接拼路径：
```python
# character.py:191 / 211-212
file_path = self._characters_dir / f"{character_id}.json"
json_path = self._characters_dir / f"{character_id}.json"
```
经 `%2F` 编码的 `/` 会在路由匹配后被解码，`DELETE /api/characters/..%2F..%2Ffoo` → `data/characters/../../foo.json` → **可删除仓库内任意以 `.json` 结尾的文件**；GET 端可读取任意 `.json` 文件内容（需为合法 JSON dict）。无认证 + 默认绑 127.0.0.1 时，本地任意进程/被 XSS 的浏览器标签页可直接利用。
**修复**：`api_characters_get/delete` 入口同样调用 `_validate_character_id`（`char_[0-9a-f]{16}|default` 白名单）。

## 其余仍开放项（未在本轮承诺内，维持原判）

高：#16 已按策略解决（绑 loopback + 非回环告警；未来暴露公网需 shared-secret）。
中：#21 已落地 numpy 向量化（端到端仍受 JSON 反序列化瓶颈限制；真·ANN 留待数据量爆发）。
低：#42 部分（代码块已感知，标题边界未做）、#43 部分（已分批，token 预算未做）。

## 验证记录

- `python -m pytest tests/ -x -q` → **85 passed**（与提交声称一致）。
- `packages/web`：`npx tsc --noEmit` 通过。
- `packages/web`：`pnpm build` 失败，根因是 `node_modules` 未安装（`ELIFECYCLE Command failed` + `TS2688 vite/client`），**非代码回归**。pre-commit hook 仅在被暂存文件触及 `packages/web/ts/app.ts` / `packages/web/src/` 时才跑前端检查，本分支改动均为 Python/manifest/docs，不触发，故不构成合并阻塞；但合入前建议在干净环境装依赖跑一次完整 pre-commit。

## 合入前建议清单（本轮新增）

1. **N1** manifest 绝对路径改可配置/移除（否则其他环境 MCP 必坏且模型幻觉）
2. **N5** character GET/DELETE 加 `character_id` 校验（任意 `.json` 删除漏洞）
3. **#39** `git rm --cached` 6 个 sqlite 残留
4. **N4/N3** fork 路径解析统一（branch 后 history/usage 空、标题丢失）
5. **N2** prompt 拼写 `ddata` → `data`

以上 5 项工作量都很小，建议在本分支内解决后再合并 main。核心 memory 修复（#1/#2/#7/#14/#18/#19）经复查是正确的。

---

# 修复状态（2026-08-04 起）

按「合入前建议清单」逐项修复，状态实时更新：

- [x] **#39** git rm --cached 6 个 sqlite 残留
- [x] **N2** 复查为**误报**——提交的 manifest.json 中本就是 `data/sessions/`，`ddata` 是 `git show` 终端换行折行的显示假象，无需修改
- [x] **N1** manifest 绝对路径已移除：根 `manifest.json` 的 `mcp_servers` 置空；`rulewhisper` 由 `config.json` 的 `plugin_manifests` 加载的 `../ai_coc/pan_plugin/manifest.json`（`${PLUGIN_DIR}` 占位，可移植）提供，`create_character` 对未解析名字优雅跳过
- [x] **N5** `api_characters_get/delete` 增加 `_validate_character_id` 校验（`char_[0-9a-f]{16}|default`），封堵 `%2F` 路径穿越任意 `.json` 删除/读取
- [x] **N4/N3** `sessions.py` 新增 `_resolve_session_file(session_id, project_cwd, project_dir)`，`fork_cbc_session`/`write_custom_title`/`parse_cbc_history`/`get_raw_usage` 统一走该解析（显式 project_dir → cwd 派生目录 → 全目录扫描回退）
- [x] 验证：`pytest` **85 passed**（修复后重跑全绿）；`ast`/`json` 语法检查通过
- [ ] 提交（可选）：建议单独提交「fix: 合并前复查 5 项阻塞修复」

---

# 第 2 轮修复（2026-08-04）

按「其余仍开放项」继续修，聚焦可机械修复的高/中危项；#16（认证）、#34（health_check）、#21（ANN 索引）、#25（迁移系统）属设计决策/较大改造，暂缓。

> 补充（#20）：`memory_context` 新增按 `(db_path, provider, model_path, api_key)` 的进程级 `MemoryManager` 缓存 + 双检锁；`search_and_format` 命中复用不再 close，ST 模型每个 character 只加载一次。`Embedder._ensure_st_model` 加加载锁防并发双载；server 关闭时 `clear_memory_manager_cache()` 释放模型。冒烟验证通过。

## 已修复

### worker.py（#8 #9 #10 #11 #35）
- **#8** MCP 超时用户可见：`timed_out` 标记，超时且无 result 时 `last_result` 明确报 "Task timed out after 120s and the process was killed" 并广播
- **#9** 非零退出被吞：`returncode` 非零且无 result 时，`last_result` 携带 returncode + 输出尾部 2KB
- **#10** 删除的 session 被复活：解析结果先收集到局部列表，保存前重新 `_sess.get(w.session_id)`，None 则丢弃结果（不再通过陈旧引用写盘）
- **#11** cli_session_id 清除死代码：**按设计解决**——清除逻辑被调用方顺序架空空转；若强行激活会破坏本分支核心的 `--resume` 上下文连续性。已移除误导性死代码并注释说明
- **#35** 删除死代码 `_format_history_for_context`

### memory（#13 #24 #27 #28 #30 #23 #4 #15 #36）
- **#13** chunk ID 含 `source_path`：`sha256(f"{source_path}:{chunk_text}")`，同文本跨文件不再碰撞
- **#24** `insert_chunk` FTS 先 DELETE 再 INSERT（活动路径 `replace_file_chunks` 本已如此）
- **#27** FTS 操作符注入：`_expand_query` 每词加双引号强制字面匹配；空词表跳过 FTS
- **#28** `chunks.model` 记录 `embedder.model_name`（真实模型），不再恒为 OpenAI 默认名
- **#30** `memory_context.search_and_format` 用 try/finally 保证 `mgr.close()`，修复连接泄漏
- **#23** schema：fts `id UNINDEXED`（新库生效）
- **#4** store 读方法（get_meta/get_file/get_chunks_for_search/search_fts/get_embedding_cache/get_stats 等）全部加 RLock，跨线程共享连接竞态关闭
- **#15** 删除文件清理索引：watcher 支持 `FileDeletedEvent` → `remove_file`（删 chunks+FTS+files）；`index_directory`/`sync` 孤儿清理（比对磁盘，删除不存在的索引条目）
- **#36** `index_directory` 改 `rglob("*.md")` 递归，与 watcher `recursive=True` 一致

### character/server（#32 #33 #40）
- **#32** `CharacterManager` 加 `_memory_managers_lock`，`get_memory_manager` 的 check-then-act 原子化（双检+重复创建时 close）
- **#33** `api_characters_delete` 同步清理 server 侧 `_memory_managers` 缓存并 close
- **#40** `_dedup_append` 同名覆盖时 `log.warning`

## 验证
- `pytest` **85 passed**
- 内存功能冒烟：递归扫描 ✓、跨文件同文本 chunk ID 唯一 ✓、删文件后孤儿清理（2→1）✓、FTS `"OR"` 查询不再报语法错 ✓
- 待提交

---

# 第 3 轮修复（2026-08-04）

继续处理中/低危可机械修复项。#16（认证）、#21（ANN 索引）仍属设计决策/大改造，暂缓。

## 已修复

### #31 memory_dir 可逃逸（security）
`manifest_loader._parse_profile` 将 `memory_dir` resolve 后做 `relative_to(plugin_dir)` 容纳校验，逃逸则忽略并 `log.error`。恶意 manifest 无法再指向任意目录。

### #34 health_check + #41 --pan-url
- **#34**：`packages/mcp/manifest.json` 移除 `health_check`——该字段 loader 不消费，且默认 stdio 传输下无 HTTP server，健康检查永远失败
- **#41**：`packages/mcp/server.py` 用 `global _pan_api_url` 覆盖，替代无效的 `__main__` 属性写法（库导入时不再静默失效）

### #37 假测试（补真实断言）
- `test_memory_chunker.py::test_overlap_exists`：逐对断言相邻 chunk 有重叠行
- `test_character.py::test_get_memory_manager_no_api_key`：断言缓存一致性（重复调用同实例）+ 优雅降级

### #43 embed 分批
`embed_batch` 按 `EMBED_BATCH_SIZE=128` 分批发给 provider，大文件不再产生单次超大请求。

### #25 迁移系统（schema_version）
- `SCHEMA_VERSION=2` + `_MIGRATIONS`（事务内逐版本执行，失败回滚可重试）
- 新库直接写 `schema_version`；旧库自动迁移：v2 重建 FTS 表使 `id UNINDEXED`
- 冒烟验证：v1 库迁移后行保留、`"恐怖"` 可检索、`id UNINDEXED` 生效

### #42 chunker 代码块感知
- 代码围栏（``` / ~~~）内不切分、不按行内拆分，围栏块整体累积
- 重叠 carry 若落在围栏内（奇数 fence 计数），自动补开围栏保持语法闭合
- 冒烟验证：纯块/混合/多块/小块均围栏平衡，chunker 22 项测试通过

### #38 真实 SQLite 搜索集成测试（+ 修复测试自身泄漏）
- 新增 `TestRealSqliteIntegration`：真实 `MemoryStore`+FTS5 schema 跑 `HybridSearcher`，验证向量排序、FTS 操作符查询不抛错、stats 与 FTS 镜像
- **顺带修复既有测试泄漏**：`orig_cos = mod.HybridSearcher._cosine_similarity` 取到的是未包装的原始函数，finally 还原成普通类属性后 `staticmethod` 失效，后续所有未 mock 的搜索调用都会 `TypeError`。改为 `__dict__["_cosine_similarity"]` 捕获描述符本体

## 验证
- `pytest` **88 passed**（+3 集成测试）
- 待提交

---

# 第 4 轮修复（2026-08-04）——#16 / #21 按建议落地

## #21 搜索向量评分 numpy 向量化
- `search.py`：向量评分改 numpy 批量矩阵余弦（`mat @ q / (norms * q_norm)`，零范数置 1 防除零，`max(0)` 夹紧），替换逐 chunk 纯 Python 余弦
- 结果与纯 Python 完全一致（20k×768 冒烟：top-5 顺序与分数逐项吻合；端到端 7.7s→6.1s，余弦计算本身 ~80x）
- **残留瓶颈**：`json.loads` 反序列化 embedding 占主导，端到端仍 JSON-bound。要根治需改 embedding 存储格式（如 BLOB/内存缓存），属后续改造
- numpy 缺失时回退纯 Python 循环；测试改为真实正交向量断言（不再依赖 `_cosine_similarity` monkeypatch）

## #16 无认证——按"零代码+策略"解决
- 现状评估：默认绑 127.0.0.1，消费者为同源前端 + pan MCP server（独立进程）+ WS
- **决策**：不引入 token 认证（会波及 2 个前端 + MCP server + WS，收益对本机单用户场景有限）
- **落地**：`main.py` 增加非回环绑定告警——`PAN_HOST` 设为非 loopback 时启动打印醒目 WARNING（"Pan API has NO authentication…"），防止误绑公网裸奔
- 策略：保持绑 127.0.0.1；如未来需要暴露公网，再上 shared-secret（见第 3 轮决策点记录）

## 验证
- `pytest` **88 passed**
- 待提交

---

# 第 5 轮修复（2026-08-04）——残留限制 A/C/G

## A. ST 模型全局单例（多 character 内存）
- `embedder.py` 新增 `_st_model_pool`（按 `(model, cache_dir)` 模块级单例）+ `_get_or_create_st_model`，所有 Embedder 共享同一 `SentenceTransformer` 实例
- 修复前：N 个被检索的 character = N 份 ~400MB 模型副本（#20 缓存带来的内存副作用）
- 修复后：同模型同缓存目录只加载一份；冒烟验证同 key 返回同实例、不同 cache_dir 各一份

## C. 旧库 provider/dims 不 fail-fast
- `store.probe_embedding_dims()` 读取首个 chunk 的实际 embedding 维度
- `MemoryManager.__init__`：meta 缺失（#1 之前建的库）时先 probe，若与当前 provider 维度不一致 → 打开即 `ValueError` 提示 re-index，不再静默盖章导致搜索时逐 chunk 跳过
- 冒烟验证：4 维旧库开 768 维 provider 抛错；维度一致正常打开

## G. `search_and_format` db_dir 默认值
- 默认值由 `Path.cwd()/data/memory` 改为模块路径解析的仓库根 `data/memory`，消除 cwd 分歧坑
- 验证：生产模块解析出 `D:\project\Pan-memory\data\memory`

## 验证
- `pytest` **88 passed**
- 冒烟：池去重 ✓ / 维度 fail-fast ✓ / 默认路径 ✓

---

# 最终状态总结（2026-08-04，HEAD=a09fda9）

## 审查结论

原报告 43 项发现 + 复查新增 5 项（N1-N5）**已全部处理**（修复 / 按设计解决 / 策略落地），第 5 轮又收掉了 3 项残留限制（A/C/G）。`feature/memory` 分支已具备合并条件。

## 修复方式分布

| 方式 | 项 |
|------|----|
| 代码修复 | #1-#9、#12-#15、#17-#20、#22-#30、#32、#33、#36、#37、#39-#43、N1、N3-N5、A、C、G |
| 按设计解决 | #11（`--resume` 连续性优先）、#16（loopback 绑定 + 非回环告警）、#21（numpy 向量化，端到端仍 JSON-bound）、#34（移除无效 health_check）、N2（误报） |
| 暂缓（记录触发条件） | ANN/embedding BLOB（单 character >~1 万 chunk）、chunker 标题边界、embed token 预算、worker MCP E2E 测试基建 |

## 关键防护网（合并后长期有效）

- **安全**：#5/N5 校验 `character_id`（防路径穿越/任意 `.json` 删除）、#6 限制 `dir_path`、#31 `memory_dir` 容纳校验、#16 非回环绑定告警
- **正确性**：#1 meta 记录 provider/dims 打开校验 + 旧库 probe fail-fast、#2 FTS 归一化、#7 先 embed 后写库、#13 chunk ID 含 source_path、#14 维度守卫、#25 schema 迁移系统
- **稳定性**：#3 MCP 进程防泄漏、#8/#9 超时/退出码用户可见、#10 不复活已删 session、#4 全方法加锁、#20 manager 缓存 + #A 模型单例
- **数据卫生**：#15 删除清理 + 孤儿清扫、#18/#19 session 索引合法化、#39 清理 git 中的 runtime sqlite

## 测试

- `pytest`：**88 passed**（chunker 22 + search 22 + character 13 + mcp_integration 20 + worker_history 7 + kimi_adapter 4）
- 搜索层含真实 SQLite 集成测试（`TestRealSqliteIntegration`，非 mock）
- 遗留：worker MCP one-shot 路径无 E2E 测试（依赖真实 cbc 进程），属测试基建缺口

## 合并前注意（非阻塞）

- `config.json`（gitignored）中 `plugin_manifests` 依赖 `../ai_coc/pan_plugin/manifest.json` 提供 `rulewhisper` MCP server——**换机器需自行配置该插件路径**，根 manifest 不再包含本机路径（N1 修复的代价）
- 旧 memory DB 首次打开会触发 v1→v2 FTS 重建迁移；维度不一致的旧库会打开即报错并提示 re-index（预期行为）
- `coc-keeper-coldstart` profile 的 system_prompt 断言"已连接 RuleWhisper MCP"，若插件未配置会导致模型幻觉调用——该 profile 依赖插件就绪

## 后续建议（不阻塞合并）

1. embedding 存储改 BLOB + 内存矩阵缓存，解决搜索 JSON 反序列化瓶颈
2. worker MCP 路径 E2E 测试（本地 mock cbc 进程）
3. chunker 标题边界感知（提升检索质量）
