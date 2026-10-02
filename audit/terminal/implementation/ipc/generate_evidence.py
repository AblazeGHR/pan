"""Pan Terminal IPC/秘密存储证据生成器（本 TA 自建，仅本机、只用自有临时目录）。

用法：

    E:/software/miniforge/python.exe audit/terminal/implementation/ipc/generate_evidence.py

行为：

1. 实跑两份测试文件（`-v`），把逐用例结果解析成 JSON；
2. 记录环境（Python/平台/当前用户 SID/HEAD/时间/tmp 根）；
3. 记录本 TA 在实现期**先复现后修复**的缺陷清单（人工维护的事实，来自本次工作）；
4. 记录安全边界：哪些是结构性证据、哪些**未实测**（不得当作已解决）；
5. 清理核查：确认没有遗留自建子进程（按命令行的测试资产路径匹配）；
6. 全部写入 ``evidence/``（不覆盖历史：文件名带固定后缀，重跑即重新生成）。

本脚本不打开任何监听端口、不接触任何既有服务/凭据、不改账户权限。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"
PYTHON = sys.executable

TEST_FILES = {
    "ipc": REPO_ROOT / "tests" / "test_terminal_runner_ipc.py",
    "secret_store": REPO_ROOT / "tests" / "test_terminal_secret_store.py",
}
#: 本 TA 自建子进程脚本名（遗留核查用）
CHILD_ASSET_MARKERS = ("ipc_child_host.py", "bootstrap.py", "reader.py")

_OUTCOME_RE = re.compile(r"^(?P<nodeid>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b")


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True)
    return result.stdout.strip()


def run_suite(name: str, path: Path) -> dict:
    started = time.time()
    process = subprocess.run(
        [
            PYTHON,
            "-m",
            "pytest",
            str(path),
            "-v",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",  # 清掉 pytest.ini 的 -q，保留逐用例结果
            "--tb=short",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    seconds = time.time() - started
    cases: list[dict] = []
    for line in (process.stdout or "").splitlines():
        match = _OUTCOME_RE.match(line.strip())
        if match:
            cases.append({"nodeid": match.group("nodeid"), "outcome": match.group("outcome")})
    summary_line = ""
    for line in reversed((process.stdout or "").splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary_line = line.strip()
            break
    return {
        "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "exit_code": process.returncode,
        "seconds": round(seconds, 2),
        "summary": summary_line,
        "case_count": len(cases),
        "counts": {
            outcome: sum(1 for case in cases if case["outcome"] == outcome)
            for outcome in sorted({case["outcome"] for case in cases})
        },
        "cases": cases,
        "stderr_tail": "\n".join((process.stderr or "").splitlines()[-20:]),
    }


def environment() -> dict:
    return {
        "task": "T-TERMINAL-PTY-20261003 / P1 IPC + secret store",
        "worktree": str(REPO_ROOT),
        "branch": _git("branch", "--show-current"),
        "head_before_commit": _git("rev-parse", "HEAD"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version,
        "python_executable": PYTHON,
        "platform": sys.platform,
        "cwd": os.getcwd(),
        "temp_roots": {
            "pytest_basetemp": os.environ.get("PYTEST_DEBUG_TEMPROOT", "(pytest default under %TEMP%)"),
        },
        "network": {
            "listening_sockets_opened_by_this_ta": [],
            "note": "本层只用本机命名管道；未打开任何监听端口，未运行任何服务",
        },
    }


DEFECTS = [
    {
        "id": "D1",
        "title": "CreateNamedPipeW/CreateFileW 失败伪句柄是无符号 2**64-1，旧判定与 -1 比较永不成立",
        "impact": "创建失败被当成成功：占名检测失效、客户端可能把打开失败当成已连接",
        "reproduced_by": "tests/test_terminal_runner_ipc.py::test_pipe_name_squatting_detected_and_retryable（先 DID NOT RAISE PipeBusyError）",
        "fix": "win_pipe.is_invalid_handle() 识别 0/-1/INVALID_HANDLE_VALUE/2**64-1，替换三处判定",
        "regression_test": "tests/test_terminal_runner_ipc.py::test_invalid_handle_values_are_detected",
    },
    {
        "id": "D2",
        "title": "cancel_accept 关闭仍有在飞 I/O 的实例句柄，且取消后不重建实例",
        "impact": "取消方与等待方句柄竞态；取消后管道名短暂无人持有，客户端连不上",
        "reproduced_by": "tests/test_terminal_runner_ipc.py::test_accept_timeout_cancel_and_capacity_are_bounded（connect 超时失败）",
        "fix": "取消后等事件落地再关句柄（_drop_pending_locked 返回是否收敛）+ 立即重建实例重新做占名检测",
        "regression_test": "tests/test_terminal_runner_ipc.py::test_accept_timeout_cancel_and_capacity_are_bounded",
    },
    {
        "id": "D3",
        "title": "RequestScheduler 的本地期限在 claim 时重算，排队越久预算越宽",
        "impact": "可能推迟执行已过期请求（违背“不迟到执行”）",
        "reproduced_by": "tests/test_terminal_runner_ipc.py::test_scheduler_caps_remote_deadline_by_local_timeout（claim 返回了应丢弃的请求）",
        "fix": "入队时固化 min(sender_deadline, 入队时刻 + timeout_ms)，claim 只与固化值比较",
        "regression_test": "tests/test_terminal_runner_ipc.py::test_scheduler_caps_remote_deadline_by_local_timeout",
    },
    {
        "id": "D4",
        "title": "build_error/build_response 无脱敏词表",
        "impact": "调用方把含 token 的异常文本直接传入时 token 会原样上线",
        "reproduced_by": "tests/test_terminal_runner_ipc.py::test_describe_and_redact_never_expose_secrets_or_payload",
        "fix": "两个构造函数新增 secrets_ 参数；IpcSession.run_handler 统一传 _secrets（含 token）",
        "regression_test": "tests/test_terminal_runner_ipc.py::test_serve_never_dispatches_expired_request_and_survives_handler_errors",
    },
    {
        "id": "D5",
        "title": "取消后提前释放 OVERLAPPED/事件导致内核写已释放内存（access violation）",
        "impact": "压力下 Python 进程崩溃（exit 139）；保留的读操作/取消未落地的写操作都会命中",
        "reproduced_by": "同一命令连跑多次出现 'Windows fatal exception: access violation'（崩溃线程停在 Condition wait）",
        "fix": "_OverlappedOp.wait_complete()+ 未落地则保留引用（_orphan_ops / 保留 pending 实例）并 CloseReport(converged=False) 重试",
        "regression_test": "tests/test_terminal_runner_ipc.py::test_blocking_read_is_cancelled_converged_and_joined + test_slow_reader_cancel_close_converges_and_is_retryable（连跑 5 次无崩溃）",
    },
    {
        "id": "D6",
        "title": "ConnectNamedPipe 的 ERROR_NO_DATA（客户端连上又立刻断开）被当作致命错误",
        "impact": "accept 循环被打死，服务端此后不再接受任何连接",
        "reproduced_by": "tests/test_terminal_runner_ipc.py::test_impostor_pipe_server_identity_is_rejected_before_credentials（循环线程异常退出）",
        "fix": "accept 把 ERROR_NO_DATA 当瞬时状态：丢实例 + 重建 + 同一预算内重试",
        "regression_test": "tests/test_terminal_runner_ipc.py::test_client_that_leaves_before_connect_call_does_not_break_accept",
    },
]


SECURITY_BOUNDARIES = {
    "verified_with_real_evidence": [
        "owner-only DACL 从创建时生效：secrets 目录/秘密文件/自建 .hello 的 DACL 枚举只有当前用户 SID 的 allow ACE（tests/test_terminal_secret_store.py::test_secrets_dir_and_files_are_owner_only_from_creation）",
        "文件 ACL 被加宽（加入 Everyone allow ACE）后读取/校验一律拒绝：SecretSecurityError（test_foreign_ace_on_secret_file_is_rejected）",
        "管道创建参数带 FILE_FLAG_FIRST_PIPE_INSTANCE（首实例）与 dwPipeMode 的 PIPE_REJECT_REMOTE_CLIENTS，并传入非空 owner-only SECURITY_ATTRIBUTES（monkeypatch spy：test_pipe_creation_parameters_carry_owner_only_dacl_and_reject_remote）",
        "管道名被外部进程占用 → PipeServer.create() 抛 PipeBusyError，绝不静默共用同名管道（test_pipe_name_squatting_detected_and_retryable）",
        "冒充者持有同名管道：PID 不符 / PID 相同但 raw FILETIME 不符 → 客户端在发送任何字节前拒绝（test_impostor_pipe_server_identity_is_rejected_before_credentials）",
        "DPAPI 用户作用域：密文可跨进程解密且不含明文 token；跨用途 entropy 不匹配解不开（test_secret_is_decryptable_from_another_process / test_protector_reports_user_scope）",
    ],
    "structurally_verified_only_not_live_tested": [
        "其它 Windows 用户 / 另一安全上下文连接被拒：需要第二账户或第二安全上下文；本 TA 未创建账户、未改账户权限，只有 DACL 结构性证据（无其它 SID 的 allow ACE）",
        "PIPE_REJECT_REMOTE_CLIENTS 的跨主机拒绝：无第二主机、未开任何网络监听，只有创建参数 spy + 常量断言",
    ],
    "not_claimed": [
        "同 Windows 用户下的恶意进程：可解密该用户作用域的 DPAPI 秘密、也可连接本用户管道。首版信任边界 = 同一 Pan 用户，不承诺防同用户恶意软件",
        "token 轮换/吊销：本层未实现（计划 §13 记为可选、默认不 rotate）",
        "runner 生命周期（PTY/Job/lease/detach/stop）与 Web/MCP 入口检查：不在本 TA 范围",
    ],
    "credential_layering": "IPC token（DPAPI 秘密）与 attachment lease（revocation_id/generation）互不通用；用 lease 值冒充 IPC 凭据认证失败（test_ipc_token_is_independent_from_attachment_lease）",
}


SCOPE_CONFLICTS = [
    {
        "file": "tests/test_terminal_driver.py",
        "case": "test_core_has_no_adapter_menu_literals",
        "detail": "该用例断言 packages/core/terminal/ 下 .py 文件恰好 9 个（P0 不变式）；P1 按计划新增 ipc.py/win_pipe.py/secret_store.py 后必然变化",
        "owner": "核心 TA / MA（本 TA 写范围不含该文件）",
        "suggested_change": "把 len(files) == 9 改为按计划文件集断言（或更新为 12 并保留字面量检查）",
        "verified_not_caused_by_content": "新增三个模块不含 cbc/codex/never mind/restore and fork 字面量（grep 计数 0）",
    },
]


def leftover_processes() -> dict:
    try:
        import psutil
    except Exception as exc:  # noqa: BLE001 - 环境缺依赖时如实记录
        return {"available": False, "error": type(exc).__name__}
    leftovers: list[dict] = []
    own_pid = os.getpid()
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = process.info.get("cmdline") or []
            if process.info.get("pid") == own_pid:
                continue
            text = " ".join(str(part) for part in cmdline)
            if any(marker in text for marker in CHILD_ASSET_MARKERS):
                leftovers.append(
                    {"pid": process.info.get("pid"), "name": process.info.get("name"), "cmdline": text[:200]}
                )
        except Exception:  # noqa: BLE001 - 进程随时可能退出
            continue
    return {"available": True, "markers": list(CHILD_ASSET_MARKERS), "leftovers": leftovers}


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    environment_data = environment()
    (EVIDENCE_DIR / "environment.json").write_text(
        json.dumps(environment_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    results: dict[str, dict] = {}
    for name, path in TEST_FILES.items():
        results[name] = run_suite(name, path)
        (EVIDENCE_DIR / f"{name}_tests.json").write_text(
            json.dumps(results[name], ensure_ascii=False, indent=2), encoding="utf-8"
        )

    (EVIDENCE_DIR / "defects.json").write_text(
        json.dumps(
            {"note": "先复现后修复；每条都给出复现用例与回归用例", "defects": DEFECTS},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (EVIDENCE_DIR / "security_boundaries.json").write_text(
        json.dumps(SECURITY_BOUNDARIES, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (EVIDENCE_DIR / "scope_conflicts.json").write_text(
        json.dumps(SCOPE_CONFLICTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    leftovers = leftover_processes()
    (EVIDENCE_DIR / "cleanup.json").write_text(
        json.dumps(
            {
                "self_built_processes": "全部由测试/脚本 spawn，结束前用 win_pipe.terminate_verified_process（同 handle PID+raw FILETIME 核验）终止",
                "leftover_scan": leftovers,
                "temp_dirs": "全部在 pytest tmp_path / %TEMP% 下，由 pytest 清理；本 TA 未写项目内 data/ 目录",
                "network_listeners": "无（未调用任何 listen/bind）",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary = {
        "generated_at": environment_data["generated_at"],
        "head": environment_data["head_before_commit"],
        "suites": {
            name: {
                "exit_code": data["exit_code"],
                "summary": data["summary"],
                "case_count": data["case_count"],
                "seconds": data["seconds"],
            }
            for name, data in results.items()
        },
        "leftovers": leftovers.get("leftovers", []),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(data["exit_code"] == 0 for data in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
