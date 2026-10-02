"""契约探针公共工具：结果记录、JSON 汇总、进程身份核验、依赖版本。

探针脚本分两类，报告与 JSON 里都以 ``kind`` 明确区分：
- ``mock``  —— 确定性契约逻辑测试（``ScriptedBackend``，不代表真实 CLI 行为）；
- ``real``  —— 真实 PTY / 真实进程行为测试（pywinpty + ConPTY）。
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import sys
import threading
import time
import traceback
from typing import Any, Callable, Sequence


def dep_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    try:
        import importlib.metadata as md

        for name in ("pywinpty", "pyte", "psutil"):
            try:
                out[name] = md.version(name)
            except Exception:
                out[name] = None
    except Exception:  # pragma: no cover
        pass
    return out


def process_facts(pid: int | None) -> dict[str, Any]:
    """进程身份核验：PID + 名称 + 创建时间（+ 命令行）。"""
    if not pid:
        return {"pid": pid, "exists": False}
    facts: dict[str, Any] = {"pid": pid, "exists": False}
    try:
        import psutil

        proc = psutil.Process(pid)
        with proc.oneshot():
            facts["exists"] = True
            facts["name"] = proc.name()
            facts["created_at"] = datetime.datetime.fromtimestamp(
                proc.create_time()
            ).isoformat(timespec="seconds")
            facts["cmdline"] = " ".join(proc.cmdline())[:200]
    except Exception as exc:
        facts["error"] = f"{type(exc).__name__}: {exc}"
    return facts


def child_pids(pid: int | None) -> list[dict[str, Any]]:
    if not pid:
        return []
    try:
        import psutil

        root = psutil.Process(pid)
        return [process_facts(c.pid) for c in root.children(recursive=True)]
    except Exception:
        return []


class Harness:
    """极简测试宿主：记录通过/失败/测量值，输出可粘贴的 JSON。"""

    def __init__(self, kind: str, *, note: str = "") -> None:
        self.kind = kind
        self.note = note
        self.results: list[dict[str, Any]] = []
        self.measurements: list[dict[str, Any]] = []
        self.started = time.monotonic()

    # -- 记录 ---------------------------------------------------------
    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.results.append(
            {"name": name, "status": "pass" if ok else "fail", "detail": detail}
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail is not None else ""))
        return ok

    def fail(self, name: str, detail: Any) -> None:
        self.results.append({"name": name, "status": "fail", "detail": detail})
        print(f"[FAIL] {name} :: {detail}")

    def measure(self, label: str, **data: Any) -> None:
        """记录测量值。参数名用 ``label`` 以免与 ``data`` 里的 ``name`` 冲突。"""
        entry = {"name": label, **data}
        self.measurements.append(entry)
        print(f"[MEASURE] {label} :: {json.dumps(data, ensure_ascii=False, default=str)}")

    def error(self, name: str, exc: BaseException) -> None:
        self.results.append(
            {
                "name": name,
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-1500:],
            }
        )
        print(f"[ERROR] {name} :: {type(exc).__name__}: {exc}")

    def section(self, title: str) -> None:
        print(f"\n--- {title} ---")

    # -- 运行 ---------------------------------------------------------
    def run(self, fn: Callable[["Harness"], None]) -> None:
        try:
            fn(self)
        except BaseException as exc:  # noqa: BLE001 - 探针必须继续跑完其它用例
            self.error(getattr(fn, "__name__", "unnamed"), exc)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r["status"] != "pass")

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r["status"] == "pass")

    def summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "note": self.note,
            "python": sys.version.split()[0],
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "dependencies": dep_versions(),
            "cwd": os.getcwd(),
            "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "seconds": round(time.monotonic() - self.started, 3),
            "passed": self.passed,
            "failed": self.failed,
            "results": self.results,
            "measurements": self.measurements,
        }


class Watchdog:
    """硬超时护栏：探针卡住时输出已收集结果并以非零码退出，避免静默挂起。

    ``PtyProcess.read`` 会无限阻塞（实测），任何探针都不允许在同步路径上直接读取。
    """

    def __init__(self, seconds: float, on_fire: Callable[[], None] | None = None) -> None:
        self.seconds = seconds
        self.on_fire = on_fire
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        def killer() -> None:
            time.sleep(self.seconds)
            print(f"\n[WATCHDOG] 硬超时 {self.seconds}s，强制退出（exit 9）", flush=True)
            if self.on_fire is not None:
                try:
                    self.on_fire()
                except Exception:
                    pass
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(9)

        self._thread = threading.Thread(target=killer, daemon=True, name="probe-watchdog")
        self._thread.start()

    def disarm(self) -> None:
        self._thread = None  # daemon 线程随进程退出；无需显式取消


def emit(
    harness: Harness,
    *,
    json_out: str | None = None,
    extra: dict[str, Any] | None = None,
) -> int:
    payload = harness.summary()
    if extra:
        payload.update(extra)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"\n[json] wrote {json_out}")
    print("\n=== SUMMARY ===")
    print(json.dumps({k: payload[k] for k in ("kind", "passed", "failed", "seconds")}, ensure_ascii=False))
    return 1 if harness.failed else 0


def wait_until(predicate: Callable[[], bool], timeout: float, *, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def decoded(log: Any, *, cursor: int = 0, limit: int | None = None) -> str:
    """从 OutputLog 拼出可读文本（用于探针断言，不参与契约实现）。"""
    page = log.read_from(cursor, max_bytes=limit)
    return b"".join(c.data for c in page.chunks).decode("utf-8", "replace")


def strip_ansi(text: str) -> str:
    import re

    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def cleanup_paths(paths: Sequence[str]) -> list[str]:
    """只删除探针自己创建的临时目录。"""
    import shutil

    removed: list[str] = []
    for path in paths:
        if not path:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path)
        except Exception:
            pass
    return removed
