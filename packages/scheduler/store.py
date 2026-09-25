"""定时任务插件的全部落盘 —— 单一出入口。

目录布局（默认在项目根 ``data/scheduler/`` 下，可用 ``PAN_SCHEDULER_DIR``
或重定向模块级常量 :data:`DEFAULT_ROOT` 改写，测试即靠后者隔离）::

    data/scheduler/tasks/<task_id>.json   一任务一文件
    data/scheduler/runs.jsonl             执行历史（append-only，滚动上限 500）

全部写操作走 :func:`_atomic_write_json`（tmp + ``os.replace``，Windows
``PermissionError`` 有界重试），避免进程在写中途被杀留下截断的 JSON —— 范式照抄
``packages/wechat/store.py:47-85`` 与 ``packages/core/background_jobs.py:58-75``。

task_id 一律经 :func:`sanitize` 消毒，杜绝目录穿越。

命名纪律：调度任务主键叫 **task_id**；派发幂等键叫 **dispatch_key**
（``f"{task_id}:{int(fire_at.timestamp())}"``），两者绝不可混用。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from packages.scheduler import cron

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 数据根；测试用 ``monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)`` 重定向。
DEFAULT_ROOT = PROJECT_ROOT / "data" / "scheduler"

#: runs.jsonl 的滚动上限
RUNS_MAX_ENTRIES = 500

#: 任务 id 前缀 + 12 位 hex
TASK_ID_PREFIX = "sch_"

#: 可选 misfire 策略
MISFIRE_POLICIES = ("fire_now", "skip")

_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-]")

_write_lock = threading.RLock()

# leader 选主状态（进程内记忆；真正的互斥由 background_jobs 的命名锁提供）
_leader_claimed = False
_leader_release: threading.Event | None = None


# ── 根目录（照抄 packages/wechat/store.py:47-64）──


def data_root() -> Path:
    """数据根目录：PAN_SCHEDULER_DIR > DEFAULT_ROOT。"""
    env = os.environ.get("PAN_SCHEDULER_DIR")
    return Path(env) if env else Path(DEFAULT_ROOT)


def sanitize(value) -> str:
    """把 id 消毒成安全文件名片段（防目录穿越）。"""
    return _SAFE_RE.sub("_", str(value))[:128] or "_"


def tasks_dir() -> Path:
    return data_root() / "tasks"


def runs_path() -> Path:
    return data_root() / "runs.jsonl"


def _task_path(task_id: str) -> Path:
    return tasks_dir() / f"{sanitize(task_id)}.json"


# ── 原子写（照抄 background_jobs._atomic_write:58-75）──


def _atomic_write_json(path: Path, payload) -> None:
    """写 JSON：先写 .tmp 再 os.replace，保证读到的永远是完整文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Windows 文件扫描器 / 正在关闭的读端可能短暂拒绝 replace：有界重试，
    # 绝不回退成截断写。
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.01 * (attempt + 1))


def _load_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default
    if default is None:
        return data  # type(None) 判定会把 dict 也判成不匹配
    return data if isinstance(data, type(default)) else default


# ── 时间 ──


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def iso(value: datetime | None) -> str | None:
    """本地朴素 datetime → ISO-8601 字符串（秒精度）。"""
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def _new_task_id() -> str:
    return TASK_ID_PREFIX + uuid.uuid4().hex[:12]


# ── schedule 规范化 ──


def _normalize_schedule(raw) -> dict:
    """校验并清洗 schedule；非法一律 ValueError（API 层映射成 invalid_schedule）。

    ``interval_sec`` 与 ``intervalSec`` 同时接受（PLAN §2.1 用 snake_case，
    §4.4 的出口字段名是 camelCase），落盘时两个键都写，读写两侧都不会踩空。
    """
    if not isinstance(raw, dict):
        raise ValueError("schedule 必须是对象")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ("once", "interval", "cron"):
        raise ValueError("schedule.kind 必须是 once / interval / cron")

    out: dict = {"kind": kind}
    tz = raw.get("timezone")
    if tz:
        tz = str(tz).strip()
        if tz:
            out["timezone"] = tz

    if kind == "once":
        at = cron.parse_datetime(raw.get("at"))
        if at is None:
            raise ValueError("kind=once 需要合法的 at（ISO-8601 本地时间）")
        out["at"] = iso(at)
    elif kind == "interval":
        raw_sec = raw.get("interval_sec", raw.get("intervalSec"))
        try:
            sec = float(raw_sec)
        except (TypeError, ValueError):
            raise ValueError("kind=interval 需要 interval_sec（> 0）") from None
        if sec <= 0:
            raise ValueError(f"interval_sec 必须 > 0，收到 {raw_sec!r}")
        sec = int(sec) if float(sec).is_integer() else sec
        out["interval_sec"] = sec
        out["intervalSec"] = sec
        anchor = cron.parse_datetime(raw.get("anchor"))
        if anchor is not None:
            out["anchor"] = iso(anchor)
    else:
        expr = str(raw.get("cron") or "").strip()
        cron.parse_cron(expr)  # 非法表达式 -> ValueError
        out["cron"] = expr
    return out


def effective_spec(task: dict) -> dict:
    """给 cron 求值时用的 spec：补上 interval 缺失的 anchor（= created_at）。"""
    schedule = dict(task.get("schedule") or {})
    if schedule.get("kind") == "interval" and not schedule.get("anchor"):
        created = cron.parse_datetime(task.get("created_at"))
        if created is not None:
            schedule["anchor"] = iso(created)
    return schedule


def compute_next_fire(task: dict, after: datetime | None = None) -> str | None:
    """按锚点算出 next_fire_at（ISO 字符串或 None）。disabled 恒为 None。"""
    if not task.get("enabled"):
        return None
    when = after or _now()
    spec = effective_spec(task)
    tz = (task.get("schedule") or {}).get("timezone")
    try:
        point = cron.next_fire_after(spec, when, tz_name=tz)
    except ValueError:
        return None
    return iso(point)


# ── CRUD ──


def list_tasks(include_disabled: bool = True) -> list[dict]:
    """列出全部任务；``include_disabled=False`` 时只返回 enabled 的。"""
    directory = tasks_dir()
    if not directory.exists():
        return []
    tasks: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        data = _load_json(path, None)
        if isinstance(data, dict) and data.get("id"):
            tasks.append(data)
    if not include_disabled:
        tasks = [t for t in tasks if t.get("enabled")]
    tasks.sort(key=lambda t: (str(t.get("created_at") or ""), str(t.get("id") or "")))
    return tasks


def get_task(task_id: str) -> dict | None:
    if not task_id:
        return None
    data = _load_json(_task_path(task_id), None)
    if not isinstance(data, dict):
        return None
    if data.get("id") != task_id:
        return None
    return data


def save_task(task: dict) -> dict:
    """整体写回（内部用；会补 updated_at）。"""
    task["updated_at"] = iso(_now())
    with _write_lock:
        _atomic_write_json(_task_path(str(task.get("id") or "_")), task)
    return task


def create_task(payload: dict) -> dict:
    """创建任务：生成 id / 时间戳 / next_fire_at 后落盘。

    Raises:
        ValueError: 目标 session、任务文本、schedule 或 misfire_policy 非法。
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")

    now = _now()
    target = str(payload.get("target_session_id") or "").strip()
    if not target:
        raise ValueError("target_session_id 不能为空")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("text 不能为空")

    misfire = str(payload.get("misfire_policy") or "fire_now")
    if misfire not in MISFIRE_POLICIES:
        raise ValueError("misfire_policy 必须是 fire_now / skip")

    max_runs = payload.get("max_runs")
    if max_runs is not None and max_runs != "":
        try:
            max_runs = int(max_runs)
        except (TypeError, ValueError):
            raise ValueError("max_runs 必须是整数或 null") from None
    else:
        max_runs = None

    created_at = payload.get("created_at") or iso(now)
    schedule = _normalize_schedule(payload.get("schedule"))
    if schedule["kind"] == "interval" and not schedule.get("anchor"):
        anchor = cron.parse_datetime(created_at) or now
        schedule["anchor"] = iso(anchor)

    task: dict = {
        "id": str(payload.get("id") or _new_task_id()).strip(),
        "name": str(payload.get("name") or "").strip() or "未命名定时任务",
        "target_session_id": target,
        "text": text,
        "enabled": bool(payload.get("enabled", True)),
        "paused": bool(payload.get("paused", False)),
        "schedule": schedule,
        "next_fire_at": None,
        "last_fire_at": None,
        "last_status": None,
        "last_error": None,
        "run_count": int(payload.get("run_count") or 0),
        "max_runs": max_runs,
        "misfire_policy": misfire,
        "created_at": created_at,
        "updated_at": iso(now),
    }
    task["next_fire_at"] = compute_next_fire(task, now)
    save_task(task)
    return task


def update_task(task_id: str, patch: dict) -> dict | None:
    """局部更新；任务不存在返回 ``None``。

    - ``schedule`` 变更、``enabled`` 置 True → 按锚点重算 ``next_fire_at``；
    - ``enabled`` 置 False → ``next_fire_at`` 置 None（不参与扫描）；
    - patch 显式给了 ``next_fire_at`` → 原样采用，不重算。
    """
    task = get_task(task_id)
    if task is None:
        return None
    if not isinstance(patch, dict):
        return task

    schedule_changed = False
    for key, value in patch.items():
        if key in ("id", "created_at"):
            continue
        if key == "schedule":
            schedule = _normalize_schedule(value)
            if schedule["kind"] == "interval" and not schedule.get("anchor"):
                anchor = cron.parse_datetime(task.get("created_at")) or _now()
                schedule["anchor"] = iso(anchor)
            task["schedule"] = schedule
            schedule_changed = True
            continue
        if key == "misfire_policy" and value is not None:
            if str(value) not in MISFIRE_POLICIES:
                raise ValueError("misfire_policy 必须是 fire_now / skip")
            value = str(value)
        if key == "max_runs":
            if value is None or value == "":
                value = None
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ValueError("max_runs 必须是整数或 null") from None
        task[key] = value

    if "next_fire_at" in patch:
        pass  # 调用方显式指定（engine 推进/过期），不覆盖
    elif patch.get("enabled") is False:
        task["next_fire_at"] = None
    elif schedule_changed or patch.get("enabled") is True:
        task["next_fire_at"] = compute_next_fire(task, _now())

    return save_task(task)


def delete_task(task_id: str) -> bool:
    path = _task_path(task_id)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


# ── 执行历史 ──


def append_run(record: dict) -> None:
    """append 一行到 runs.jsonl；超过 500 条滚动保留最新部分。"""
    if not isinstance(record, dict):
        return
    path = runs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _write_lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        _roll_runs(path)


def _roll_runs(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= RUNS_MAX_ENTRIES:
        return
    keep = lines[-RUNS_MAX_ENTRIES:]
    tmp = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.01 * (attempt + 1))


def list_runs(task_id: str | None = None, limit: int = 100) -> list[dict]:
    """读取执行历史，**最新的在前**；``task_id`` 给定则只返回该任务的记录。"""
    path = runs_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if task_id and record.get("task_id") != task_id:
            continue
        records.append(record)
    records.reverse()
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 100
        if limit > 0:
            records = records[:limit]
    return records


# ── leader 选主 ──


def claim_leader(timeout: float = 0.5) -> bool:
    """争取「scheduler-leader」，拿到才允许起调度循环。

    直接复用 ``packages/core/background_jobs.py`` 的命名锁（**不复制** Windows
    mutex 代码，那里有 msvcrt 踩坑注释）。该锁是阻塞式的，所以放在守护线程里
    等：主线程最多等 ``timeout`` 秒，等不到就放弃并让线程拿到后立刻释放，
    避免留下幽灵占锁。

    Returns:
        True = 本实例是 leader（锁在进程存活期间一直持有）。
    """
    global _leader_claimed, _leader_release
    if _leader_claimed:
        return True

    try:
        from packages.core.background_jobs import _registry_lock

        ctx = _registry_lock("scheduler-leader", registry_root=str(data_root()))
    except Exception:
        # 拿不到选主能力时退化为「单实例」假设，避免整块功能不可用。
        _leader_claimed = True
        return True

    acquired = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        try:
            with ctx:
                acquired.set()
                # 进程存活期间一直持有；进程崩溃由内核对象自动释放。
                release.wait()
        except Exception:
            pass

    thread = threading.Thread(target=_hold, name="scheduler-leader", daemon=True)
    thread.start()
    if not acquired.wait(timeout=timeout):
        # 没抢到：让线程一旦拿到就立刻释放，不阻塞后续真正的 leader。
        release.set()
        return False
    _leader_claimed = True
    _leader_release = release
    return True


def release_leader() -> None:
    """释放 leader 锁（仅测试 / 关闭流程使用）。"""
    global _leader_claimed, _leader_release
    if _leader_release is not None:
        _leader_release.set()
        _leader_release = None
    _leader_claimed = False
