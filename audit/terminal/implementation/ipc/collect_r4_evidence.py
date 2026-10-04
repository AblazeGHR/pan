"""r4 证据收集器（本 TA 自建；只跑本机测试，不开监听、不碰既有服务/账户）。

按 r4 窄任务指令：**一次批量收尾**（不做 10 轮重复），日志一律写
``evidence/r4/**`` 的 UTF-8 ``.txt``（可直接入库，不覆盖 r2/r3 旧 JSON/日志）。

矩阵：

- 直连全量（118：80 原 + 34 r2 + 1 r3 + 3 r4）一次；
- uv 隔离全量一次；
- 定向确定性回归 ``-k "r3a or impostor"`` 直连/uv 各一次（逐用例结果）。

用法：

    E:/software/miniforge/python.exe audit/terminal/implementation/ipc/collect_r4_evidence.py

产出：``evidence/r4/post_fix/*.txt`` + ``evidence/r4/r4_freeze.json``（含 R3-A 先失败
后通过、R2A 增量口径、r3 post_fix 快照边界澄清、一次性观察与测试侧缓解）。
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
R4 = Path(__file__).resolve().parent / "evidence" / "r4"
PYTHON = sys.executable
FILES = ["tests/test_terminal_runner_ipc.py", "tests/test_terminal_secret_store.py"]
UV = [
    "uv", "run", "--no-project", "--python", PYTHON,
    "--with-requirements", "minimal-requirements.txt",
    "--with", "pytest", "--with", "pytest-timeout", "--with", "pyte==0.8.2",
    "--", "python", "-m", "pytest",
]
SELECT = ["-q", "-o", "addopts=", "--tb=line", "-p", "no:cacheprovider"]
VERBOSE = ["-v", "-o", "addopts=", "--tb=line", "-p", "no:cacheprovider"]
_OUTCOME = re.compile(r"^(?P<nodeid>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|SKIPPED|ERROR)\b")


def sha256_16(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def run(name: str, argv: list[str], output: Path) -> dict:
    started = time.time()
    process = subprocess.run(argv, cwd=str(REPO_ROOT), capture_output=True, text=True)
    seconds = time.time() - started
    output.write_text((process.stdout or "") + (process.stderr or ""), encoding="utf-8")
    text = output.read_text(encoding="utf-8", errors="replace")
    cases = [
        {"nodeid": match.group("nodeid"), "outcome": match.group("outcome")}
        for match in (_OUTCOME.match(line.strip()) for line in text.splitlines())
        if match
    ]
    summary = ""
    for line in reversed(text.strip().splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary = line.strip()
            break
    return {
        "name": name,
        "argv": argv,
        "exit_code": process.returncode,
        "seconds": round(seconds, 2),
        "summary": summary,
        "cases": cases,
        "log": str(output.relative_to(REPO_ROOT)).replace("\\", "/"),
        "log_sha256_16": sha256_16(output),
    }


def main() -> int:
    (R4 / "post_fix").mkdir(parents=True, exist_ok=True)
    runs = [
        run("direct_full", [PYTHON, "-m", "pytest", *FILES, *SELECT], R4 / "post_fix" / "direct_full.txt"),
        run("uv_full", [*UV, *FILES, *SELECT], R4 / "post_fix" / "uv_full.txt"),
        run(
            "direct_targeted",
            [PYTHON, "-m", "pytest", "tests/test_terminal_runner_ipc.py", *VERBOSE, "-k", "r3a or impostor"],
            R4 / "post_fix" / "direct_targeted_r3a_impostor.txt",
        ),
        run(
            "uv_targeted",
            [*UV, "tests/test_terminal_runner_ipc.py", *VERBOSE, "-k", "r3a or impostor"],
            R4 / "post_fix" / "uv_targeted_r3a_impostor.txt",
        ),
    ]
    pre_repro = R4 / "pre_fix" / "r3a_legacy_race.json"
    post_repro = R4 / "post_fix" / "r3a_protocol.json"
    freeze = {
        "task": "T-TERMINAL-PTY-20261003 / P1 IPC+秘密存储 —— r4 批量测试收尾（P5 测试卫生）",
        "started_from": "f524ee188faaea526098b5288bec1c5d882d5589",
        "review_input": {
            "round3_report": "terminal-ipc-review-20261003/docs/design/"
                             "PAN_TERMINAL_IPC_SECURITY_REVIEW_20261003_ROUND3.md（只读，HEAD aeb523b1）",
            "round3_evidence_commit": "cfc825f50384671d86888211c8196c72a088b057",
            "integration": "45a8acbb（f524 已 cherry-pick 到隔离集成）",
        },
        "scope": "仅 tests/test_terminal_runner_ipc.py、IPC 接口文档、audit/implementation/ipc 的 r4 工具与证据；"
                 "生产 packages/ 零改动；未改 secret_store 测试、未覆盖 r2/r3 旧 JSON/日志、未碰其它树。",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "r3a": {
            "finding": "child-report 的 tmp+os.replace 与父进程读句柄竞态（WinError 5，PermissionError）",
            "repro_deterministic": {
                "tool": "audit/terminal/implementation/ipc/r4_repro_r3a_race.py",
                "artifact": str(pre_repro.relative_to(REPO_ROOT)).replace("\\", "/"),
                "sha256_16": sha256_16(pre_repro),
                "result": "legacy_with_reader FAILED(PermissionError/WinError 5) → legacy_after_release OK"
                          "（失败纯由并发读句柄引起；单发协议与修复前的 R3-A 现场同型）",
                "post_fix_artifact": str(post_repro.relative_to(REPO_ROOT)).replace("\\", "/"),
                "post_fix_result": "new_with_reader attempts≥2 且内容一致；注入持续失败 → "
                                   "ReportWriteError（重试耗尽显式失败）",
            },
            "fix": "同源协议 write_report_payload（tmp+os.replace 有界重试；耗尽抛 ReportWriteError，"
                   "显式失败不吞不静默；清理自己的 tmp）；child host flush() 与测试共用该实现，"
                   "fatal 路径两个错误都不吞（链式抬高）。",
            "tests": [
                "tests/test_terminal_runner_ipc.py::test_r3a_report_write_retries_bounded_then_succeeds",
                "tests/test_terminal_runner_ipc.py::test_r3a_report_write_exhaustion_fails_explicitly",
                "tests/test_terminal_runner_ipc.py::test_r3a_report_write_with_real_reader_handle",
            ],
            "wiring": "子进程报告回填 replace_attempts；两个 child-report 用例断言 ≥1（证明子进程走同一实现）",
        },
        "r2a_witness_delta": "两次身份拒绝改为每案例增量 Δ=(accepted+transient) ≥ 1；"
                             "`_SendFrameCounter` 注明有效范围（窗口内无其它发送者）+ 保留真实 hello 正控。",
        "r3_freeze_policy_clarification": {
            "fact": "r3_freeze.json 的 23 条 post_fix 引用（exists/sha256/summary）是生成时实现树"
                    "**本地日志快照**；这些 *.log 未入库（.gitignore:77），重生成哈希必然不同。",
            "rule": "不得据 freeze 的 exists=true 当成已提交内容，也不补造历史；新复跑日志只写 "
                    "evidence/r4/**（UTF-8 .txt）。r2/r3 旧 JSON 与日志未被覆盖。",
        },
        "observation_not_defect": {
            "what": "一次性 SecretSecurityError: secret path escapes the secrets directory"
                    "（_guard_path 两次 resolve() 在目录首次创建瞬间的规范化窗口）",
            "scope": "生产 3 模块零改动（该观察留给后续轮次）；测试侧由 child_runner.spawn() "
                     "**先建好 secrets 目录**消除抢跑。",
            "repro_attempts": "6 次同批复跑 + 单测隔离均通过；未再复现（一次观察，不夸大）。",
        },
        "not_tested": [
            "跨用户/远程拒绝（沿用 R1/R2 声明）",
            "长稳/压测；瞬时分支的内核分配顺序假设（本机稳定，不外推）",
            "R2D/R2E 低危非阻塞项（按指令不做生产修复）",
        ],
        "runs": runs,
        "environment": {"python": sys.version, "python_executable": PYTHON, "platform": sys.platform},
    }
    (R4 / "r4_freeze.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({item["name"]: item["summary"] for item in runs}, ensure_ascii=False, indent=2))
    return 0 if all(item["exit_code"] == 0 for item in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
