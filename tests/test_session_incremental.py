"""方案 4 e2e：history 增量持久化（append-only jsonl）验证。

覆盖：
- 完整会话生命周期：create → 多次 append 消息/流式块 → result → 冷启动重载
  → history 完整、顺序正确、元数据一致
- 增量语义：jsonl 行数 == history 条数；主文件只含尾部；重复保存不重复追加
- 新旧格式混合：旧格式（history 内嵌主文件）与新格式（jsonl）都能加载
- append 后重启不丢；崩溃尾部半行容错
- save_full（reimport 整体替换 history）语义
- worker 级 e2e（MockProcess 驱动 _read_stdout，走防抖 flush 落盘路径）
- 长 history 性能对比：增量追加 vs 旧全量重写
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import worker, session as _sess
from packages.core.adapters import CbcAdapter


# ── 与 test_worker_history.py 同构的 mock cbc 工具 ──

def _make_event(event_type: str, **fields) -> bytes:
    return (json.dumps({"type": event_type, **fields}) + "\n").encode("utf-8")


def _assistant_event(text: str = None) -> bytes:
    content = []
    if text:
        content.append({"type": "text", "text": text})
    return _make_event("assistant", message={"role": "assistant", "content": content})


def _result_event(result: str = "ok") -> bytes:
    return _make_event("result", result=result, is_error=False)


def _system_init_event(cbc_sid: str = "cbc-123", model: str = "test-model") -> bytes:
    return _make_event("system", subtype="init", session_id=cbc_sid, model=model)


class MockProcess:
    """Mock asyncio.subprocess.Process：一次一个事件行，EOF 返回 b""。"""

    def __init__(self, events: list[bytes], pid: int = 1000):
        self._events = list(events)
        self.returncode = None
        self.pid = pid
        self.stdin = AsyncMock()
        self.stdout = self

    async def read(self, n=-1):
        if self._events:
            return self._events.pop(0)
        return b""


def _cleanup():
    _sess._cache.clear()
    _sess._all_loaded = False
    worker.workers.clear()
    worker.set_broadcaster(None)


def _jsonl_lines(sid: str) -> list[dict]:
    p = _sess._history_path(sid)
    if not p.exists():
        return []
    out = []
    for l in p.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            continue  # 崩溃半行：跳过（与 _read_jsonl 一致）
    return out


# ══════════════════════════════════════════════════════════════════════════ #
#  完整生命周期 + 冷启动重载                                                  #
# ══════════════════════════════════════════════════════════════════════════ #


def test_lifecycle_create_append_result_reload(tmp_path, monkeypatch):
    """create → 多条 user/assistant → result → 冷启动重载：全对。"""
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")

    s = _sess.create(name="e2e")
    sid = s.id
    # 模拟用户消息 + 流式块 + result（对齐 worker 路径：result 补存 assistant）
    for i in range(3):
        s.history.append({"role": "user", "content": f"q{i}"})
        _sess.save(s)
        s.history.append({"role": "assistant", "content": f"a{i}"})
        _sess.save(s)
    s.last_result = {"status": "done", "result": "a2", "timestamp": "t"}
    s.history.append({"role": "assistant", "content": "a2"})  # result 补存
    s.managed_by = "ses_manager"
    s.qq_subscriptions.add("user:12345")
    s.name = "e2e-renamed"
    _sess.save(s)

    # 磁盘格式断言：jsonl 与 history 等长；主文件保留全部（未超尾部阈值）
    assert len(_jsonl_lines(sid)) == len(s.history) == 7
    main = json.loads(_sess._path(sid).read_text(encoding="utf-8"))
    assert len(main["history"]) == 7

    # 冷启动：清缓存 = 新进程重新加载
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s2 = _sess.get(sid)
    assert s2 is not None
    assert s2.name == "e2e-renamed"
    assert s2.managed_by == "ses_manager"
    assert s2.qq_subscriptions == {"user:12345"}
    assert s2.last_result == {"status": "done", "result": "a2", "timestamp": "t"}
    assert s2.history == [
        {"role": "user", "content": "q0"}, {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"},
        {"role": "assistant", "content": "a2"},  # result 补存的 assistant
    ]
    # 加载后游标就位：继续 append 只追加新条目，不重复
    s2.history.append({"role": "user", "content": "q3"})
    _sess.save(s2)
    lines = _jsonl_lines(sid)
    assert len(lines) == 8 and lines[-1] == {"role": "user", "content": "q3"}
    _cleanup()


def test_append_tail_kept_main_file_constant(tmp_path, monkeypatch):
    """history 超尾部阈值后：主文件保持尾部常量，jsonl 持续增长。"""
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s = _sess.create(name="long")
    sid = s.id
    n = 50  # > _MAIN_HISTORY_TAIL(20)
    for i in range(n):
        s.history.append({"role": "user", "content": f"m{i}"})
        _sess.save(s)
    assert len(_jsonl_lines(sid)) == n
    # 纯 history append 不重写主文件：主文件仍是创建时的小快照
    assert _sess._path(sid).stat().st_size < 20 * 1024
    # 元数据变更 → 主文件重写，只含尾部常量（20 条）
    s.name = "long-renamed"
    _sess.save(s)
    main = json.loads(_sess._path(sid).read_text(encoding="utf-8"))
    assert main["name"] == "long-renamed"
    assert len(main["history"]) == 20
    assert main["history"][-1] == {"role": "user", "content": "m49"}
    _cleanup()


def test_delete_removes_both_json_and_jsonl(tmp_path, monkeypatch):
    """双文件格式下 delete 必须同时删除 <id>.json 与 <id>.history.jsonl，
    避免 jsonl 成为孤儿残留（旧实现只删单文件）。"""
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s = _sess.create(name="delme")
    sid = s.id
    for i in range(30):  # 超过尾部阈值，确保 jsonl 成为唯一完整 history 真源
        s.history.append({"role": "user", "content": f"d{i}"})
        _sess.save(s)
    assert _sess._path(sid).exists()
    assert _sess._history_path(sid).exists()
    assert len(_jsonl_lines(sid)) == 30

    _sess.delete(sid)
    assert not _sess._path(sid).exists(), "delete 后 <id>.json 应被移除"
    assert not _sess._history_path(sid).exists(), "delete 后 <id>.history.jsonl 应被移除"
    # 孤儿 jsonl 不应再被 list_all / get 看到
    assert _sess.get(sid) is None
    assert sid not in {x.id for x in _sess.list_all()}
    # 幂等：重复 delete 不报错
    _sess.delete(sid)
    _cleanup()


def test_delete_legacy_json_only_no_crash(tmp_path, monkeypatch):
    """旧格式 session（只有 <id>.json、无 jsonl）delete 正常清理且不报错。"""
    _cleanup()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_sess, "SESSION_DIR", session_dir)
    legacy = {
        "id": "ses_legacy_del", "name": "legacy", "adapter": "cbc",
        "history": [{"role": "user", "content": "old1"}],
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
    }
    (session_dir / "ses_legacy_del.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    assert _sess.get("ses_legacy_del") is not None
    _sess.delete("ses_legacy_del")
    assert not (session_dir / "ses_legacy_del.json").exists()
    assert _sess.get("ses_legacy_del") is None
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  新旧格式混合加载                                                           #
# ══════════════════════════════════════════════════════════════════════════ #


def test_legacy_and_incremental_formats_coexist(tmp_path, monkeypatch):
    """旧格式（history 内嵌主文件、无 jsonl）+ 新格式同时存在于同一目录。"""
    _cleanup()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_sess, "SESSION_DIR", session_dir)

    # 旧格式：手写完整内嵌 history 的主文件，无 jsonl
    legacy_hist = [{"role": "user", "content": "old1"}, {"role": "assistant", "content": "old2"}]
    legacy = {
        "id": "ses_legacy", "name": "legacy", "adapter": "cbc",
        "history": legacy_hist, "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    (session_dir / "ses_legacy.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    # 新格式：通过正常流程创建（自动生成 jsonl）
    s = _sess.create(name="incr")
    s.history.append({"role": "user", "content": "new1"})
    _sess.save(s)
    sid_new = s.id

    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", session_dir)
    all_s = _sess.list_all()
    by_id = {x.id: x for x in all_s}
    assert set(by_id) == {"ses_legacy", sid_new}

    leg = by_id["ses_legacy"]
    assert leg.history == legacy_hist, "legacy history not fully loaded"
    # 旧格式首次保存 → 自动迁移：jsonl 生成且包含完整历史
    leg.managed_by = "ses_manager"
    _sess.save(leg)
    assert len(_jsonl_lines("ses_legacy")) == 2
    assert leg.history == legacy_hist

    nw = by_id[sid_new]
    assert nw.history == [{"role": "user", "content": "new1"}]
    _cleanup()


def test_crash_partial_line_tolerated(tmp_path, monkeypatch):
    """jsonl 尾部半行（append 崩溃）→ 半行被跳过，后续新记录自动补换行可恢复。"""
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s = _sess.create(name="crash")
    sid = s.id
    for i in range(5):
        s.history.append({"role": "user", "content": f"x{i}"})
        _sess.save(s)
    # 模拟崩溃：追加一条不完整 JSON 半行（无换行结尾）
    with open(_sess._history_path(sid), "ab") as f:
        f.write(b'{"role": "assistant", "content": "partial')
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s2 = _sess.get(sid)
    assert len(s2.history) == 5
    assert s2.history[-1] == {"role": "user", "content": "x4"}
    # 崩溃后继续 append：自动补换行，y0 不粘在半行上 → 可被恢复
    s2.history.append({"role": "user", "content": "y0"})
    _sess.save(s2)
    assert len(_jsonl_lines(sid)) == 6
    assert _jsonl_lines(sid)[-1] == {"role": "user", "content": "y0"}
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  save_full：整体替换 history（reimport 语义）                               #
# ══════════════════════════════════════════════════════════════════════════ #


def test_save_full_replaces_history_wholesale(tmp_path, monkeypatch):
    """history 整体替换必须 save_full，避免增量游标跳过新历史头部。"""
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s = _sess.create(name="re")
    sid = s.id
    for i in range(5):
        s.history.append({"role": "user", "content": f"old{i}"})
        _sess.save(s)
    assert len(_jsonl_lines(sid)) == 5

    # 整体替换为更短的 history（长度 < 旧游标 → 普通 save 也会兜底全量重写）
    s.history = [{"role": "user", "content": "new0"}, {"role": "user", "content": "new1"}]
    _sess.save(s)
    assert _jsonl_lines(sid) == [
        {"role": "user", "content": "new0"}, {"role": "user", "content": "new1"}]

    # 替换为等长 history（游标检测不到 → 必须显式 save_full）
    s.history = [
        {"role": "user", "content": "new-a"}, {"role": "user", "content": "new-b"}]
    _sess.save_full(s)
    assert _jsonl_lines(sid) == [
        {"role": "user", "content": "new-a"}, {"role": "user", "content": "new-b"}]

    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  worker 级 e2e：MockProcess 驱动 _read_stdout（走防抖 flush 落盘路径）      #
# ══════════════════════════════════════════════════════════════════════════ #


def test_concurrent_save_async_no_duplication(tmp_path, monkeypatch):
    """并发 save_async（防抖 flush + consumer 用户消息同时落盘）不重复、不丢。"""
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s = _sess.create(name="conc")
    sid = s.id

    async def scenario():
        # 并发空 flush（无新条目，不应写坏文件）
        await asyncio.gather(*[_sess.save_async(s) for _ in range(10)])
        # append + save 交错
        for i in range(20):
            s.history.append({"role": "user", "content": f"c{i}"})
            await _sess.save_async(s)
        # 并发 flush（模拟防抖任务与 consumer 同时落盘同一 session）
        await asyncio.gather(*[_sess.save_async(s) for _ in range(5)])

    asyncio.run(scenario())

    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s2 = _sess.get(sid)
    expected = [{"role": "user", "content": f"c{i}"} for i in range(20)]
    assert s2.history == expected
    assert len(_jsonl_lines(sid)) == 20
    _cleanup()


def test_queue_ops_do_not_scale_with_history(tmp_path, monkeypatch):
    """send_task append / _consume_pending pop 只写小主文件，不被 history 拖累。

    设计要点：热路径 3 次 save 有 2 次是 queue 操作——queue 独立在 json 后，
    queue 变更只写元数据+队列（KB 级），history 走 jsonl 追加互不干扰。
    """
    _cleanup()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_sess, "SESSION_DIR", session_dir)

    def make(n):
        s = _sess.Session(id=f"ses_q_{n}", name="q", adapter="cbc")
        s.history = [{"role": "user", "content": f"m{i}", "extra": "x" * 120}
                     for i in range(n)]
        s._hist_persisted = n
        _sess._write_jsonl(_sess._history_path(s.id), s.history)
        return s

    small, big = make(10), make(5000)

    def queue_append_ms(s, reps=7):
        times = []
        for i in range(reps):
            s.queue_pending.append({"type": "task", "id": f"t{i}", "text": "x" * 200})
            t0 = time.perf_counter()
            _sess.save(s)
            times.append((time.perf_counter() - t0) * 1000)
        return sorted(times)[reps // 2]

    def queue_pop_ms(s, reps=7):
        times = []
        for _ in range(reps):
            s.queue_pending.pop(0)
            t0 = time.perf_counter()
            _sess.save(s)
            times.append((time.perf_counter() - t0) * 1000)
        return sorted(times)[reps // 2]

    a_small, a_big = queue_append_ms(small), queue_append_ms(big)
    p_small, p_big = queue_pop_ms(small), queue_pop_ms(big)
    print(f"\n    queue append: hist10={a_small:.3f}ms hist5000={a_big:.3f}ms; "
          f"queue pop: hist10={p_small:.3f}ms hist5000={p_big:.3f}ms")
    # queue 操作耗时与 history 规模无关（旧实现下 5000 条会被全量序列化拖到 ~3ms+）
    assert a_big < a_small * 5 + 0.5, \
        f"queue append should not scale with history: {a_small:.3f}→{a_big:.3f}ms"
    assert p_big < p_small * 5 + 0.5, \
        f"queue pop should not scale with history: {p_small:.3f}→{p_big:.3f}ms"
    # jsonl 不受 queue 操作影响（行数不变 = history 未被序列化）
    assert len(_jsonl_lines(big.id)) == 5000

    # 正确性：queue_pending 落盘 + 冷启动重载完整，history 不受影响
    s3 = _sess.create(name="q3")
    s3.history.append({"role": "user", "content": "h1"})
    _sess.save(s3)
    s3.queue_pending = [{"type": "task", "id": "t1", "text": "go"}]
    _sess.save(s3)
    sid3 = s3.id
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", session_dir)
    r = _sess.get(sid3)
    assert r.queue_pending == [{"type": "task", "id": "t1", "text": "go"}]
    assert r.history == [{"role": "user", "content": "h1"}]
    assert len(_jsonl_lines(sid3)) == 1
    _cleanup()


def test_worker_path_saves_incrementally_and_reloads(tmp_path, monkeypatch):
    """真实 worker 读取路径（init → 流式块 → result）→ 落盘 → 冷启动重载。"""
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s = _sess.create(name="wkr")
    w = worker.Worker(
        worker_id="worker-e2e",
        session_id=s.id,
        adapter=CbcAdapter(),
        status="idle",
        process=MagicMock(),
        pending_signal=asyncio.Queue(),
        _replaying=False,
        _hist_flush_event=asyncio.Event(),
    )
    worker.workers[w.worker_id] = w
    w.process = MockProcess([
        _system_init_event(cbc_sid="cbc-e2e"),
        _assistant_event(text="hello"),
        _result_event(result="hello"),
    ])
    asyncio.run(worker._read_stdout(w))
    sid = s.id
    assert s.history == [{"role": "assistant", "content": "hello"}]
    assert s.last_result["status"] == "done"
    assert len(_jsonl_lines(sid)) == 1
    assert _jsonl_lines(sid)[0] == {"role": "assistant", "content": "hello"}

    # 冷启动重载：完整
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    s2 = _sess.get(sid)
    assert s2.history == [{"role": "assistant", "content": "hello"}]
    assert s2.cli_session_id == "cbc-e2e"
    assert s2.last_result["status"] == "done"
    _cleanup()


# ══════════════════════════════════════════════════════════════════════════ #
#  性能对比：增量追加 vs 旧全量重写                                           #
# ══════════════════════════════════════════════════════════════════════════ #


def _old_full_save_ms(s: _sess.Session, path: Path) -> float:
    """复刻旧 _save_sync：全量 json.dumps(to_dict) + write_text。"""
    t0 = time.perf_counter()
    s.updated_at = "t"
    path.write_text(
        json.dumps(s.to_dict(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    return (time.perf_counter() - t0) * 1000


def _new_incremental_save_ms(s: _sess.Session) -> float:
    """新热路径：追加 1 条新 history 后 save（测量真实增量成本）。"""
    s.history.append({"role": "user", "content": "x"})
    t0 = time.perf_counter()
    _sess.save(s)
    return (time.perf_counter() - t0) * 1000


def test_perf_incremental_vs_full(tmp_path, monkeypatch):
    """量化：追加 1 条的增量保存 vs 同规模全量重写（越大越悬殊）。"""
    _cleanup()
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_sess, "SESSION_DIR", session_dir)

    sizes = [100, 500, 1295, 5000]
    print("\n  N       full(ms)   incr+1(ms)  speedup   jsonl(KB)  main(KB)")
    for n in sizes:
        s = _sess.Session(id=f"ses_perf_{n}", name="perf", adapter="cbc")
        s.history = [{"role": "user", "content": f"m{i}",
                      "extra": "x" * 120} for i in range(n)]
        s._hist_persisted = n  # jsonl 已镜像 n 条（模拟历史存在）
        main_path = _sess._path(s.id)
        _sess._write_jsonl(_sess._history_path(s.id), s.history)

        # 旧全量：5 次中位数
        old_times = sorted(_old_full_save_ms(s, main_path) for _ in range(5))
        old_ms = old_times[len(old_times) // 2]

        # 新增量：每次追加 1 条，5 次中位数
        new_times = sorted(_new_incremental_save_ms(s) for _ in range(5))
        new_ms = new_times[len(new_times) // 2]

        jsonl_kb = _sess._history_path(s.id).stat().st_size / 1024
        main_kb = main_path.stat().st_size / 1024
        speedup = old_ms / new_ms if new_ms > 0 else float("inf")
        print(f"  {n:<5} {old_ms:8.3f} {new_ms:8.3f}  {speedup:6.1f}x"
              f"   {jsonl_kb:7.1f} {main_kb:7.1f}")

    # 断言（抗测量抖动，用中位数 + 宽松阈值）：
    # 1) 旧全量随 N 线性增长；增量基本持平（O(1) append，与 history 规模无关）
    # 2) 5000 条时增量明显快于全量
    s100 = _sess.Session(id="ses_chk_100", name="perf", adapter="cbc")
    s100.history = [{"role": "user", "content": f"m{i}"} for i in range(100)]
    s100._hist_persisted = 100
    _sess._write_jsonl(_sess._history_path(s100.id), s100.history)
    new100 = sorted(_new_incremental_save_ms(s100) for _ in range(7))[3]
    old100 = sorted(_old_full_save_ms(s100, _sess._path(s100.id)) for _ in range(7))[3]

    s5k = _sess.Session(id="ses_perf_5000", name="perf", adapter="cbc")
    s5k.history = [{"role": "user", "content": f"m{i}"} for i in range(5000)]
    s5k._hist_persisted = 5000
    _sess._write_jsonl(_sess._history_path(s5k.id), s5k.history)
    new5k = sorted(_new_incremental_save_ms(s5k) for _ in range(7))[3]
    old5k = sorted(_old_full_save_ms(s5k, _sess._path(s5k.id)) for _ in range(7))[3]

    assert old5k > 3 * old100, \
        f"old full save should scale with N (100→5000), got {old100:.3f}→{old5k:.3f}ms"
    assert new5k < 3 * new100, \
        f"incremental append should stay ~flat, got {new100:.3f}→{new5k:.3f}ms"
    assert new5k * 3 < old5k, \
        f"expected >=3x speedup at 5000 entries, got {old5k:.3f} vs {new5k:.3f}ms"
    print(f"    [check] 100→5000: old {old100:.3f}→{old5k:.3f}ms (scales), "
          f"incr {new100:.3f}→{new5k:.3f}ms (flat); speedup@5000 = "
          f"{old5k / new5k:.1f}x")
    # 新格式最终落盘：save_full 后主文件仍是元数据 + 尾部（常量），不是全量
    _sess.save_full(s5k)
    assert _sess._path(s5k.id).stat().st_size < 50 * 1024, \
        "main file should stay small (metadata + tail)"
    _cleanup()


if __name__ == "__main__":
    print("run via: pytest tests/test_session_incremental.py -v")
