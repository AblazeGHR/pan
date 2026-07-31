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
