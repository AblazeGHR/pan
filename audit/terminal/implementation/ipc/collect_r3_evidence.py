"""r3 证据收集器（本 TA 自建；只跑本机测试，不开监听、不碰既有服务/账户）。

产出（``audit/terminal/implementation/ipc/evidence/r3/``）：

- ``r3_freeze.json``：冻结报告（R2A 复现→修复→稳定性、R2B–R2F 处置、逐运行结果与日志 sha256）；
- 默认**只解析已存在的日志**（快，作为冻结产物）；``--rerun`` 重新执行同一命令矩阵：

      直连：全量 115 · 定向 impostor ×10
      uv  ：全量 115 ×2 · 定向 impostor ×10
      pre-fix 复现：``uv_full_run_*.log``（历史，修复后不可重跑）+ 确定性复现器输出

用法：

    E:/software/miniforge/python.exe audit/terminal/implementation/ipc/collect_r3_evidence.py [--rerun]
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
R3 = Path(__file__).resolve().parent / "evidence" / "r3"
PYTHON = sys.executable
FILES = ["tests/test_terminal_runner_ipc.py", "tests/test_terminal_secret_store.py"]
UV = [
    "uv", "run", "--no-project", "--python", PYTHON,
    "--with-requirements", "minimal-requirements.txt",
    "--with", "pytest", "--with", "pytest-timeout", "--with", "pyte==0.8.2",
    "--", "python", "-m", "pytest",
]
SELECT = ["-q", "-o", "addopts=", "--tb=line", "-p", "no:cacheprovider"]
_OUTCOME = re.compile(r"^(?P<nodeid>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|SKIPPED|ERROR)\b")


def sha256(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def parse_log(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    cases = [
        {"nodeid": match.group("nodeid"), "outcome": match.group("outcome")}
        for match in (_OUTCOME.match(line.strip()) for line in text.splitlines())
        if match
    ]
    failures = [line.strip() for line in text.splitlines() if line.strip().startswith("FAILED")]
    summary = ""
    tail = text.strip().splitlines()
    if tail:
        summary = tail[-1].strip()
    has_exit = text.count("\n")
    return {
        "log": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "exists": path.exists(),
        "sha256_16": sha256(path),
        "lines": has_exit,
        "summary": summary,
        "failed": failures,
        "cases": cases,
    }


def run(argv: list[str], output: Path) -> dict:
    started = time.time()
    process = subprocess.run(argv, cwd=str(REPO_ROOT), capture_output=True, text=True)
    seconds = time.time() - started
    output.write_text((process.stdout or "") + (process.stderr or ""), encoding="utf-8")
    parsed = parse_log(output)
    parsed["exit_code"] = process.returncode
    parsed["seconds"] = round(seconds, 2)
    return parsed


def rerun() -> None:
    (R3 / "post_fix").mkdir(parents=True, exist_ok=True)
    run([PYTHON, "-m", "pytest", *FILES, *SELECT], R3 / "post_fix" / "direct_all.log")
    for index in (1, 2):
        run([*UV, *FILES, *SELECT], R3 / "post_fix" / f"uv_all_{index}.log")
    for index in range(1, 11):
        run(
            [PYTHON, "-m", "pytest", "tests/test_terminal_runner_ipc.py", *SELECT, "-k", "impostor"],
            R3 / "post_fix" / f"direct_impostor_{index}.log",
        )
    for index in range(1, 11):
        run(
            [*UV, "tests/test_terminal_runner_ipc.py", *SELECT, "-k", "impostor"],
            R3 / "post_fix" / f"uv_impostor_{index}.log",
        )


ITEMS = {
    "R2A": {
        "severity": "测试健壮性（唯一套件偶发失败根因）",
        "status": "fixed（仅测试与文档；生产 3 模块未改）",
        "root_cause": "冒充者 accept 在“客户端连上即断”时走 ERROR_NO_DATA 瞬时路径：实例被丢弃重建、"
                      "不产生读取观测；用例的精确计数断言 `received == [b'', b'']` 在该路径下必然失败，"
                      "而 `sum(len(...)) == 0` 之类断言会因集合为空而**假通过**。",
        "reproduction_natural": "本树复跑 uv 全量 3 次：run1 114 passed、run2 114 passed、"
                               "run3 1 failed/113 passed（同 R2A：`assert [b''] == [b'', b'']`）",
        "reproduction_deterministic": "audit/terminal/implementation/ipc/r3_repro_r2a_transient.py："
                                     "客户端连上→身份拒绝→断开后才 arm，稳定得到 transient=1、accepted=0；"
                                     "旧断言 FAILED（received==[]，零字节结论空集假通过），新见证逻辑 PASSED",
        "fix": "impostor 用例改为 `_ImpostorWitness`（accepted / transient / reads 三类分开记录）"
               "+ `_SendFrameCounter`（客户端零帧发送）：两次身份拒绝各自要有服务端观测（瞬时事件或"
               "真实读取），零凭据字节只在有见证前提下断言；对照段加字节级 anchor（身份匹配时**读到** hello，"
               "证明读取观测有效），另加确定性回归用例（瞬时路径 vs 真实读取）。",
        "tests": [
            "tests/test_terminal_runner_ipc.py::test_impostor_pipe_server_identity_is_rejected_before_credentials",
            "tests/test_terminal_runner_ipc.py::test_impostor_transient_connect_is_recorded_separately_from_reads",
        ],
        "stability": "定向 impostor 直连 10/10、uv 10/10 全绿；全量直连 115/115、uv 115/115 ×2",
        "not_vacuous": "断言口径：`witnesses() = accepted + transient ≥ 1` 每拒绝各自成立、"
                       "`credential_bytes() == 0`、`accept_errors == 0`、客户端 `send_frame` 计数不变；"
                       "对照段必须 `b\"hello\" in hello_bytes`（字节级锚点）。",
    },
    "R2B": {
        "severity": "文档/语义观察",
        "status": "documented（不动生产）",
        "note": "wait_response(timeout) 超时≠放弃：保留响应槽与 pending，迟到响应仍投递、可被后续 "
                "wait_response 取回；call 超时清槽并把迟到响应计入 late_responses；槽位有界 ≤32"
                "（DEFAULT_MAX_PENDING_REQUESTS）。已写入接口文档 §7-9。",
    },
    "R2C": {
        "severity": "API 语义观察（非缺陷）",
        "status": "documented（不动生产）",
        "note": "实例池只在 accept()/cancel_accept() 内按 min(max_active_connections, max_instances) 补齐；"
                "服务循环长时间不 accept 时新客户端等到下一次 accept（客户端有界重试）。已写入接口文档 §7-10。",
    },
    "R2D": {
        "severity": "表面一致性（低）",
        "status": "retained-observation（不扩生产修复）",
        "note": "PipeServer.close 重复调用 detail 恒为 \"closed\"（幂等成功、未伪造）；PipeConnection.close "
                "有 already-closed 早退。已记录于接口文档 §7-11。",
    },
    "R2E": {
        "severity": "防御性卫生（低）",
        "status": "retained-observation（不扩生产修复）",
        "note": "named_mutex 的 ReleaseMutex 失败在 finally 抛出、write/create 的 CloseHandle 失败在 finally "
                "抛出，可能遮蔽体内原异常（关闭语义本身已如实上报）。已记录于接口文档 §7-11。",
    },
    "R2F": {
        "severity": "证据卫生",
        "status": "fixed（文档）",
        "note": "pre-fix 日志中 test_f7_unknown_ace_type_and_unparsable_allow_are_rejected 在最终树改名为 "
                "test_f7_unknown_and_non_allow_ace_types_are_rejected；已在 r2 README 标注改名映射。",
    },
}


def main() -> int:
    R3.mkdir(parents=True, exist_ok=True)
    if "--rerun" in sys.argv:
        rerun()
    runs = {
        "pre_fix_natural_uv_repro": [
            parse_log(R3 / "pre_fix" / f"uv_full_run_{index}.log") for index in (1, 2, 3)
        ],
        "pre_fix_deterministic_repro": parse_log(R3 / "pre_fix" / "r2a_transient_repro.json"),
        "post_fix_full_direct": parse_log(R3 / "post_fix" / "direct_all.log"),
        "post_fix_full_uv": [
            parse_log(R3 / "post_fix" / f"uv_all_{index}.log") for index in (1, 2)
        ],
        "post_fix_targeted_direct": [
            parse_log(R3 / "post_fix" / f"direct_impostor_{index}.log") for index in range(1, 11)
        ],
        "post_fix_targeted_uv": [
            parse_log(R3 / "post_fix" / f"uv_impostor_{index}.log") for index in range(1, 11)
        ],
    }
    targeted = runs["post_fix_targeted_direct"] + runs["post_fix_targeted_uv"]
    targeted_ok = sum(1 for item in targeted if "passed" in item["summary"] and "failed" not in item["summary"])
    freeze = {
        "task": "T-TERMINAL-PTY-20261003 / P1 IPC+秘密存储 —— r3 窄任务（R2A 修复 + R2B–R2F 处置）",
        "started_from": "85d7650375c2fe702be620485dec314e0f904755（本级起点，核对 clean）",
        "review_input": {
            "round2_report": "terminal-ipc-review-20261003/docs/design/"
                             "PAN_TERMINAL_IPC_SECURITY_REVIEW_20261003_ROUND2.md（只读）",
            "round2_evidence_commit": "21d287c1b7a20f60d96b041452080205b04e6db5",
            "audit2_report_commit": "48c15231（已接受 F1–F12 安全子集）",
            "integration_commit": "8af9b0f9（e018+85d765 纳入隔离集成）",
        },
        "scope": "只改 tests/test_terminal_runner_ipc.py、接口文档、audit 自有 r3 目录/工具；"
                 "生产 3 模块（ipc/win_pipe/secret_store）零改动（git diff 可核）。",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "counts": {
            "full_direct": runs["post_fix_full_direct"]["summary"],
            "full_uv": [item["summary"] for item in runs["post_fix_full_uv"]],
            "targeted_pass_ratio": f"{targeted_ok}/{len(targeted)}",
        },
        "items": ITEMS,
        "runs": runs,
        "environment": {
            "python": sys.version,
            "python_executable": PYTHON,
            "platform": sys.platform,
        },
    }
    text = json.dumps(freeze, ensure_ascii=False, indent=2)
    (R3 / "r3_freeze.json").write_text(text, encoding="utf-8")
    print(json.dumps(freeze["counts"], ensure_ascii=False, indent=2))
    pre = runs["pre_fix_natural_uv_repro"]
    print("pre-fix natural uv:", [item["summary"] for item in pre])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
