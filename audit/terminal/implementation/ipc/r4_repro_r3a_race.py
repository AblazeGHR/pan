"""r4 定向复现/验证：R3-A（child-report 的 tmp+os.replace 与父进程读句柄竞态）。

**只读生产代码**；本工具是 audit 自有复现器（不改生产/不改 r2/r3 证据）。

复现的既有事实（round3 报告 §3 与探针 `probe_report_rename_race.py` 已坐实）：
Windows 上父进程 ``Path.read_text()`` 的读句柄不含 ``FILE_SHARE_DELETE``，
子进程 ``flush()`` 的 ``os.replace(tmp, report)`` 在 20ms 轮询窗口内重叠即
``PermissionError: [WinError 5]``。

本工具对照三种形态（全部真实文件、真实句柄）：

1. **旧协议**（单发：写 tmp → 一次 ``os.replace``）在**持读句柄**时 → 必失败
   （R3-A 的失败阶段，确定性，不靠时序运气）；
2. 释放句柄后旧协议 → 成功（说明失败纯由并发读句柄引起）；
3. **新协议**（``tests/test_terminal_runner_ipc.py::write_report_payload``，有界重试）
   在持读句柄时 → 重试至成功（attempts ≥ 2），释放后读取内容一致；
   再把 ``os.replace`` 注入为**持续失败** → 新协议**显式抛错**（``ReportWriteError``，
   重试耗尽不静默、不吞断言）。

运行：

    E:/software/miniforge/python.exe audit/terminal/implementation/ipc/r4_repro_r3a_race.py

输出：stdout JSON + ``evidence/r4/pre_fix/r3a_repro.json``（UTF-8）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

EVIDENCE = Path(__file__).resolve().parent / "evidence" / "r4" / "pre_fix"


def legacy_protocol(path: Path, payload: dict) -> None:
    """修复前的单发协议：写 tmp → 一次 os.replace（失败即抛）。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    report: dict = {"tool": "r4_repro_r3a_race", "platform": sys.platform}
    workdir = Path(tempfile.mkdtemp(prefix="pan-r4-r3a-"))
    target = workdir / "child.report.json"
    report["workdir"] = str(workdir)
    try:
        # ① 旧协议 + 持读句柄 → 必失败（R3-A 失败阶段）
        target.write_text(json.dumps({"round": 0}), encoding="utf-8")
        holder = open(target, "r", encoding="utf-8")  # noqa: SIM115 - 故意持有读句柄
        holder.read()
        try:
            legacy_protocol(target, {"round": 1})
            report["legacy_with_reader"] = {"failed": False}
        except PermissionError as exc:
            report["legacy_with_reader"] = {"failed": True, "error": type(exc).__name__, "winerror": exc.winerror}
        finally:
            holder.close()

        # ② 释放句柄后旧协议 → 成功（失败由并发读句柄引起）
        legacy_protocol(target, {"round": 2})
        report["legacy_after_release"] = {"ok": json.loads(target.read_text(encoding="utf-8")) == {"round": 2}}

        # ③ 新协议（有界重试）：持读句柄 → 重试成功；再注入持续失败 → 显式抛错
        report_io = None
        try:
            import test_terminal_runner_ipc as candidate  # 与 child host 使用同一实现

            if hasattr(candidate, "write_report_payload") and hasattr(candidate, "ReportWriteError"):
                report_io = candidate
        except Exception as exc:  # noqa: BLE001 - 修复前该 helper 不存在：如实记录
            report["new_protocol_available"] = {"available": False, "error": type(exc).__name__}
        if report_io is None:
            report.setdefault(
                "new_protocol_available",
                {"available": False, "error": "helper not present (pre-fix)"},
            )
        if report_io is not None:
            report["new_protocol_available"] = {"available": True}
            holder = open(target, "r", encoding="utf-8")
            holder.read()
            result: dict = {}

            def writer() -> None:
                try:
                    result["attempts"] = report_io.write_report_payload(
                        target, {"round": 3}, attempts=40, retry_seconds=0.05
                    )
                except Exception as exc:  # noqa: BLE001
                    result["error"] = type(exc).__name__

            thread = threading.Thread(target=writer, name="r4-new-protocol")
            thread.start()
            time.sleep(0.2)  # 确保 writer 至少撞上一次共享冲突
            holder.close()
            thread.join(10)
            report["new_with_reader"] = {
                "thread_done": not thread.is_alive(),
                "attempts": result.get("attempts"),
                "error": result.get("error"),
                "content_ok": json.loads(target.read_text(encoding="utf-8")) == {"round": 3},
            }
            # 注入持续失败 → 重试耗尽必须显式抛错（不静默）
            real_replace = os.replace

            def always_fail(source, destination):  # noqa: ANN001
                raise PermissionError(13, "injected sharing violation")

            os.replace = always_fail  # type: ignore[assignment]
            try:
                try:
                    report_io.write_report_payload(target, {"round": 4}, attempts=3, retry_seconds=0.01)
                    report["exhaustion"] = {"raised": False}
                except report_io.ReportWriteError as exc:
                    report["exhaustion"] = {"raised": True, "error": type(exc).__name__, "message": str(exc)[:120]}
            finally:
                os.replace = real_replace  # type: ignore[assignment]
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)

    legacy = {
        key: report[key]
        for key in ("tool", "platform", "legacy_with_reader", "legacy_after_release")
        if key in report
    }
    postfix = {
        key: report[key]
        for key in ("tool", "platform", "new_protocol_available", "new_with_reader", "exhaustion")
        if key in report
    }
    legacy_text = json.dumps(legacy, ensure_ascii=False, indent=2)
    postfix_text = json.dumps(postfix, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    (EVIDENCE / "r3a_legacy_race.json").write_text(legacy_text, encoding="utf-8")
    (EVIDENCE / "r3a_legacy_race.txt").write_text(legacy_text, encoding="utf-8")
    post_dir = EVIDENCE.parent / "post_fix"
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "r3a_protocol.json").write_text(postfix_text, encoding="utf-8")
    (post_dir / "r3a_protocol.txt").write_text(postfix_text, encoding="utf-8")
    legacy_failed = report.get("legacy_with_reader", {}).get("failed") is True
    return 0 if legacy_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
