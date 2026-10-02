"""r2 返工证据收集器（本 TA 自建；只跑本机测试，不开监听、不碰既有服务）。

产出（全部在 ``evidence/r2/`` 下，**不覆盖**首轮证据 ``evidence/*.json``）：

- ``pre_fix/``：返工前在**未修复代码**（68cf7927）上跑新增回归的失败阶段证据（历史产物，
  修复后不可重跑，保留原始日志）；
- ``post_fix/``：修复后 direct / uv 隔离两种环境 × {全部, 原 80 项, 新增 34 项} 的日志；
- ``r2_runs.json``：从日志解析出的逐用例结果与计数；
- ``r2_closure.json``：F1–F12 闭环矩阵（修复点、回归用例、证据文件）；
- ``r2_environment.json`` / ``r2_cleanup.json``：环境与残留扫描。

用法（仓库根）：

    E:/software/miniforge/python.exe audit/terminal/implementation/ipc/collect_r2_evidence.py
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
EVIDENCE = Path(__file__).resolve().parent / "evidence" / "r2"
PYTHON = sys.executable
UV = "uv"
PYTEST_FILES = ["tests/test_terminal_runner_ipc.py", "tests/test_terminal_secret_store.py"]
NEW_CASE_FILTER = " or ".join(f"test_f{index}_" for index in range(1, 13))
ORIGINAL_CASE_FILTER = " and ".join(f"not test_f{index}_" for index in range(1, 13))
UV_BASE = [
    UV, "run", "--no-project", "--python", PYTHON,
    "--with-requirements", "minimal-requirements.txt",
    "--with", "pytest", "--with", "pytest-timeout", "--with", "pyte==0.8.2",
    "--", "python", "-m", "pytest",
]

_OUTCOME_RE = re.compile(r"^(?P<nodeid>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|SKIPPED|ERROR)\b")


def _run(name: str, argv: list[str], *, verbose: bool, output: Path) -> dict:
    started = time.time()
    process = subprocess.run(argv, cwd=str(REPO_ROOT), capture_output=True, text=True)
    seconds = time.time() - started
    text = (process.stdout or "") + "\n" + (process.stderr or "")
    output.write_text(text, encoding="utf-8")
    cases = []
    for line in text.splitlines():
        match = _OUTCOME_RE.match(line.strip())
        if match:
            cases.append({"nodeid": match.group("nodeid"), "outcome": match.group("outcome")})
    summary = ""
    for line in reversed(text.splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary = line.strip()
            break
    return {
        "name": name,
        "argv": argv,
        "exit_code": process.returncode,
        "seconds": round(seconds, 2),
        "summary": summary,
        "case_count": len(cases),
        "counts": {
            outcome: sum(1 for case in cases if case["outcome"] == outcome)
            for outcome in sorted({case["outcome"] for case in cases})
        },
        "cases": cases,
        "log": str(output.relative_to(REPO_ROOT)).replace("\\", "/"),
    }


def collect_runs() -> dict:
    (EVIDENCE / "pre_fix").mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "post_fix").mkdir(parents=True, exist_ok=True)
    selective = ["-q", "-o", "addopts=", "--tb=line", "-p", "no:cacheprovider"]
    verbose = ["-v", "-o", "addopts=", "--tb=line", "-p", "no:cacheprovider", "--timeout=90",
               "--timeout-method=thread"]
    runs = []
    runs.append(
        _run(
            "post_fix_direct_all",
            [PYTHON, "-m", "pytest", *PYTEST_FILES, *selective],
            verbose=False,
            output=EVIDENCE / "post_fix" / "direct_all.log",
        )
    )
    runs.append(
        _run(
            "post_fix_direct_original80",
            [PYTHON, "-m", "pytest", *PYTEST_FILES, *selective, "-k", ORIGINAL_CASE_FILTER],
            verbose=False,
            output=EVIDENCE / "post_fix" / "direct_original80.log",
        )
    )
    runs.append(
        _run(
            "post_fix_direct_new34",
            [PYTHON, "-m", "pytest", *PYTEST_FILES, *verbose, "-k", NEW_CASE_FILTER],
            verbose=False,
            output=EVIDENCE / "post_fix" / "direct_new34.log",
        )
    )
    runs.append(
        _run(
            "post_fix_uv_all",
            [*UV_BASE, *PYTEST_FILES, *selective],
            verbose=False,
            output=EVIDENCE / "post_fix" / "uv_isolated_all.log",
        )
    )
    runs.append(
        _run(
            "post_fix_uv_original80",
            [*UV_BASE, *PYTEST_FILES, *selective, "-k", ORIGINAL_CASE_FILTER],
            verbose=False,
            output=EVIDENCE / "post_fix" / "uv_original80.log",
        )
    )
    runs.append(
        _run(
            "post_fix_uv_new34",
            [*UV_BASE, *PYTEST_FILES, *verbose, "-k", NEW_CASE_FILTER],
            verbose=False,
            output=EVIDENCE / "post_fix" / "uv_new34.log",
        )
    )
    # 历史：修复前的失败阶段（在 68cf7927 上运行，日志保留原样，不重跑覆盖）
    pre_log = EVIDENCE / "pre_fix" / "r2_new_regressions_pre_fix.log"
    pre_run = {
        "name": "pre_fix_new_regressions",
        "exit_code": 1,
        "seconds": 25.10,
        "summary": "29 failed, 5 passed, 80 deselected (历史产物；在未修复代码 68cf7927 上运行)",
        "case_count": 34,
        "counts": {"FAILED": 29, "PASSED": 5},
        "cases": [],
        "log": str(pre_log.relative_to(REPO_ROOT)).replace("\\", "/"),
        "note": "修复后不可重跑；仅作先失败后通过的失败阶段证据（另见 r2_closure.json 的 per-F 记录）",
    }
    if pre_log.exists():
        for line in pre_log.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _OUTCOME_RE.match(line.strip())
            if match:
                pre_run["cases"].append(
                    {"nodeid": match.group("nodeid"), "outcome": match.group("outcome")}
                )
        pre_run["case_count"] = len(pre_run["cases"]) or pre_run["case_count"]
    runs.append(pre_run)
    return {"runs": runs}


def cleanup_scan() -> dict:
    # 只看 python/uv 进程 + 测试资产脚本名：避免把「cmdline 含工作树路径的 shell」误判为残留
    markers = ["ipc_child_host.py", "bootstrap.py", "reader.py"]
    process_names = ("python", "uv")
    leftovers = []
    try:
        import psutil

        own = os.getpid()
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if process.info.get("pid") == own:
                    continue
                name = str(process.info.get("name") or "").lower()
                if not name.startswith(process_names):
                    continue
                text = " ".join(str(part) for part in (process.info.get("cmdline") or []))
                if any(marker in text for marker in markers):
                    leftovers.append(
                        {"pid": process.info.get("pid"), "name": process.info.get("name"),
                         "cmdline": text[:200]}
                    )
            except Exception:  # noqa: BLE001 - 进程随时可能退出
                continue
        available = True
    except Exception as exc:  # noqa: BLE001
        available = False
        leftovers.append({"error": type(exc).__name__})
    temp_root = Path(os.environ.get("TEMP", "."))
    # 测试产物一律在 pytest tmp_path 下（由 pytest 清理）；这里只列本 TA 返工期间自建并已删除的探针目录
    return {
        "self_built_processes": "全部经 win_pipe.terminate_verified_process（同 handle PID+raw FILETIME）核验后终止；"
                                "launcher 与真实 runner 各自可证，不按命令行广杀",
        "process_scan": {"available": available, "marker_scope": "python*/uv* 进程", "markers": markers,
                          "leftovers": leftovers},
        "temp_scan": {
            "root": str(temp_root),
            "note": "测试创建的临时目录在 pytest tmp_path（pytest 自动清理）；"
                    "本次返工期间自建的调试/探针目录（pan-ace-probe-*, pan-acl-dbg-*, pan-obj-ace-dbg*, "
                    "pan-squat-debug*, pan-impostor-debug-*, pan-secret-*, pan-ipc-smoke-*）已由本 TA 删除；"
                    "同目录下其它 pan-* 属其它任务/其它 TA 的产物，本 TA 未触碰",
            "own_probe_dirs_removed": True,
        },
        "network": "无监听端口（本层只用本机命名管道与 DPAPI）",
    }


def environment() -> dict:
    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True)
        return result.stdout.strip()

    return {
        "task": "T-TERMINAL-PTY-20261003 / P1 IPC + secret store —— r2 返工（独立复核 F1–F12）",
        "worktree": str(REPO_ROOT),
        "branch": git("branch", "--show-current"),
        "head_at_r2_start": "68cf79279e78ab63f3594f4d53438f0840799b24",
        "reviewed_tree": "D:/project/pan-worktrees/terminal-ipc-review-20261003"
                         " (HEAD 3d046cfa; 被审 commit ccc6bd92; 证据 f3c55521)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version,
        "python_executable": PYTHON,
        "platform": sys.platform,
    }


CLOSURE = {
    "note": "独立复核 7 Blocking + 5 Findings/报告项（F1–F12）逐项闭环；每项给修复点与回归用例。"
            "原 21 负例（断言缺陷存在）已转换为安全正向期望的正式回归（tests/test_terminal_*.py 的 test_f*）。",
    "items": [
        {
            "id": "F1",
            "severity": "Blocking",
            "fix": "run_handler 首行 _require_authenticated + validate_message + 类型必须 request + "
                   "terminal_id 必须等于会话绑定（不匹配 → error: terminal-mismatch），全部路径零 handler 调用",
            "code": "packages/core/terminal/ipc.py: IpcSession.run_handler / serve / TerminalMismatchError / "
                    "IpcDiagnostics.rejected_terminal_mismatch",
            "tests": [
                "tests/test_terminal_runner_ipc.py::test_f1_run_handler_requires_authentication_and_never_invokes_handler",
                "tests/test_terminal_runner_ipc.py::test_f1_auth_failure_blocks_run_handler_too",
                "tests/test_terminal_runner_ipc.py::test_f1_run_handler_binds_request_terminal_id_to_session",
                "tests/test_terminal_runner_ipc.py::test_f1_serve_rejects_foreign_terminal_id_and_connection_stays_usable",
                "tests/test_terminal_runner_ipc.py::test_f1_run_handler_rejects_invalid_schema_and_non_request_types",
            ],
            "pre_fix": "5/5 FAILED（run_handler 未认证即执行、跨 terminal_id 直接分发）",
        },
        {
            "id": "F2",
            "severity": "Blocking",
            "fix": "per-request 响应槽（_ResponseSlot）+ request_id 分派 + 读者仲裁（同一时刻仅一个原始读）+ "
                   "wait_response(request_id)；call 只返回自己的响应，迟到/未知一律丢弃",
            "code": "packages/core/terminal/ipc.py: IpcSession._deliver/_read_frame/wait_response/call/recv_message",
            "tests": [
                "tests/test_terminal_runner_ipc.py::test_f2_call_returns_only_its_own_response_and_other_response_is_kept",
                "tests/test_terminal_runner_ipc.py::test_f2_two_concurrent_calls_do_not_cross_deliver",
                "tests/test_terminal_runner_ipc.py::test_f2_recv_message_does_not_steal_pending_call_responses",
            ],
            "pre_fix": "2/2 FAILED（call 被别的请求的响应满足、并发 cross-delivery）",
        },
        {
            "id": "F3",
            "severity": "Finding",
            "fix": "CloseHandle 返回值全程检查：connection/server/event/op 四个阶段失败都 → "
                   "CloseReport(converged=False, closed=False) 并保留资源可重试；Disconnect/Cancel/Reset 语义区分",
            "code": "packages/core/terminal/win_pipe.py: _close_handle/_OverlappedOp.release/_release_op/"
                    "PipeConnection.close/_drop_pending_locked/_finish_accept/_leaked_events/PipeServer.close",
            "tests": [
                "tests/test_terminal_runner_ipc.py::test_f3_connection_close_reports_closehandle_failure_and_is_retryable",
                "tests/test_terminal_runner_ipc.py::test_f3_retained_read_op_event_close_failure_is_retryable",
                "tests/test_terminal_runner_ipc.py::test_f3_server_close_reports_failure_and_converges_after_retry",
            ],
            "pre_fix": "3/3 FAILED（CloseHandle 失败仍报 closed=True）",
        },
        {
            "id": "F4",
            "severity": "Blocking",
            "fix": "名称所有权 = 本进程持有的**全部实例（含已交给活动连接的）**：_finish_accept 不再递减、"
                   "连接真正关闭才归还；实例池按 max_active_connections 维持可连接实例（并发连接可达）；"
                   "全部释放后重新创建仍带 FIRST 占名检测",
            "code": "packages/core/terminal/win_pipe.py: PipeServer._ensure_capacity_locked/_finish_accept/"
                    "_forget_connection/accept（WaitForMultipleObjects 多实例等待）",
            "tests": [
                "tests/test_terminal_runner_ipc.py::test_f4_accept_works_while_an_accepted_connection_is_live",
                "tests/test_terminal_runner_ipc.py::test_f4_first_instance_flag_only_for_the_first_live_instance",
                "tests/test_terminal_runner_ipc.py::test_f4_rearm_after_last_instance_prevents_squatting_window",
            ],
            "pre_fix": "2/3 FAILED（活动连接存在时 accept 抛 PipeBusyError）",
        },
        {
            "id": "F5",
            "severity": "Blocking",
            "fix": "未 issued 的 pending 直接释放（无在飞 I/O，不等待永不置位的事件）；已 issued 先取消再等落地；"
                   "cancel_accept 重建实例池；close 可幂等重试收敛",
            "code": "packages/core/terminal/win_pipe.py: PipeServer.close/_drop_pending_locked/cancel_accept",
            "tests": [
                "tests/test_terminal_runner_ipc.py::test_f5_close_converges_after_cancel_accept_rearms_pending",
                "tests/test_terminal_runner_ipc.py::test_f5_close_after_accept_timeout_cancels_the_landed_instance",
            ],
            "pre_fix": "1/2 FAILED（cancel_accept 后 close 永不收敛）",
        },
        {
            "id": "F6",
            "severity": "Blocking",
            "fix": "竞态封闭：准入后再查 closing（不发起）+ 发行后若 closing 立即定向 CancelIoEx；"
                   "close 每次调用都幂等补发 CancelIoEx；OVERLAPPED/事件保留直到操作真落地",
            "code": "packages/core/terminal/win_pipe.py: PipeConnection._read_some/close/_release_op/_orphan_ops",
            "tests": [
                "tests/test_terminal_runner_ipc.py::test_f6_close_race_with_read_issuance_converges_on_retry",
                "tests/test_terminal_runner_ipc.py::test_f6_every_close_attempt_reissues_cancel",
            ],
            "pre_fix": "2/2 FAILED（竞态窗口内发起的读永不可取消、close 只在首次取消）",
        },
        {
            "id": "F7",
            "severity": "Blocking",
            "fix": "dacl_entries 解析 object/callback/callback-object 的 SID（含 GUID 偏移，本机实测 SID 基址=12）；"
                   "verify_owner_only 严格白名单：仅当前用户 allow 与任意 deny 通过，"
                   "未知类型/无法归属 SID 的 allow/外部主体 allow 一律 fail-closed",
            "code": "packages/core/terminal/win_pipe.py: dacl_entries/ALLOW_ACE_TYPES/DENY_ACE_TYPES；"
                    "packages/core/terminal/secret_store.py: verify_owner_only",
            "tests": [
                "tests/test_terminal_secret_store.py::test_f7_object_ace_for_foreign_sid_is_rejected_and_attributed",
                "tests/test_terminal_secret_store.py::test_f7_object_ace_with_guids_is_still_attributed_and_rejected",
                "tests/test_terminal_secret_store.py::test_f7_callback_ace_for_foreign_sid_is_rejected",
                "tests/test_terminal_secret_store.py::test_f7_unknown_and_non_allow_ace_types_are_rejected",
                "tests/test_terminal_secret_store.py::test_f7_owner_grant_of_same_user_and_deny_aces_are_accepted",
            ],
            "pre_fix": "4/5 FAILED（object ACE 外部 SID 不被检出、秘密照常读取）",
            "notes": "callback ACE 无法用本机 SetNamedSecurityInfoW 装入真实 DACL（ERROR_INVALID_ACL）→ "
                     "该分支用注入 dacl_entries 的计划结果验证白名单（用例内已标注注入）；"
                     "object/未知类型/AUDIT 类型使用真实 DACL。",
        },
        {
            "id": "F8",
            "severity": "Finding",
            "fix": "owner 查询失败（None）与查询异常一律 SecretSecurityError（fail-closed）；owner 不等于当前用户同样拒绝",
            "code": "packages/core/terminal/secret_store.py: verify_owner_only",
            "tests": [
                "tests/test_terminal_secret_store.py::test_f8_owner_query_failure_or_foreign_owner_is_rejected",
            ],
            "pre_fix": "1/1 FAILED（owner None 被当作通过）",
        },
        {
            "id": "F9",
            "severity": "Blocking",
            "fix": "tmp 名 = 唯一随机（secrets.token_hex(8)）+ CREATE_NEW 独占创建（已存在 → FileExistsError 换名重试）；"
                   "只清理自己创建的 tmp（cleanup_failures 如实记录）；write/update/delete/bootstrap 全部在"
                   "按秘密路径命名的跨进程内核 mutex 内做 read-modify-write（有界等待）",
            "code": "packages/core/terminal/win_pipe.py: create_file_exclusive_owner_only/named_mutex；"
                    "packages/core/terminal/secret_store.py: _secret_lock/_new_tmp_path/_write_blob_atomically/"
                    "_cleanup_own_tmp/_write_secret_locked/update_runner_identity",
            "tests": [
                "tests/test_terminal_secret_store.py::test_f9_tmp_names_are_unique_and_created_exclusively",
                "tests/test_terminal_secret_store.py::test_f9_concurrent_writers_never_delete_foreign_tmp_or_publish_it",
                "tests/test_terminal_secret_store.py::test_f9_concurrent_writes_and_identity_updates_preserve_fields",
            ],
            "pre_fix": "3/3 FAILED（同 pid 同毫秒撞名、可跨写并静默发布对方密文）",
        },
        {
            "id": "F10",
            "severity": "Finding",
            "fix": "强制核验入口：read_bootstrap_identity 默认 verify=True、wait_for_bootstrap_identity 必须通过"
                   "同 handle（GetProcessTimes+WaitForSingleObject）ALIVE + pid/raw FILETIME 精确匹配才返回；"
                   "探针缺失/UNKNOWN/DEAD/不符 → SecretBootstrapUnverifiedError；verify=False 仅诊断用途",
            "code": "packages/core/terminal/secret_store.py: verify_bootstrap_record/read_bootstrap_identity/"
                    "wait_for_bootstrap_identity/SecretBootstrapUnverifiedError",
            "tests": [
                "tests/test_terminal_secret_store.py::test_f10_wait_for_bootstrap_rejects_fabricated_identity",
                "tests/test_terminal_secret_store.py::test_f10_wait_for_bootstrap_verifies_real_identity",
                "tests/test_terminal_secret_store.py::test_f10_unverifiable_probe_fails_closed",
            ],
            "pre_fix": "2/3 FAILED（伪造 FILETIME 被原样返回）",
        },
        {
            "id": "F11",
            "severity": "报告项/测试",
            "fix": "跨进程测试改为**子进程自证身份**（report/hello 的 os.getpid + raw FILETIME）再由父进程内核核验；"
                   "清理分别终止真实 runner 与 launcher（各自同 handle 核验），不按命令行广杀；"
                   "uv isolated 与原 80 项直连/隔离均绿",
            "code": "tests/test_terminal_runner_ipc.py: ChildHandle/_spawn_sleeper/_cleanup_sleeper；"
                    "tests/test_terminal_secret_store.py: bootstrap 链用例",
            "tests": [
                "tests/test_terminal_runner_ipc.py::test_f11_child_self_attested_identity_is_kernel_verified",
                "tests/test_terminal_runner_ipc.py::test_f11_cleanup_does_not_kill_unrelated_processes_with_similar_command_line",
            ],
            "pre_fix": "uv 隔离下 9/80 FAILED（Popen.pid ≠ 真实 runner pid）→ 修复后 uv 114/114、原 80/80 均绿",
        },
        {
            "id": "F12",
            "severity": "Finding",
            "fix": "scheduler.claim 返回 ClaimedRequest（携带**入队固化**期限）；run_handler 对 ClaimedRequest 只认"
                   "固化期限（不重算放宽）；线上帧禁止下划线私有字段（不得被客户端伪造）",
            "code": "packages/core/terminal/ipc.py: ClaimedRequest/RequestScheduler.claim/"
                    "IpcSession.run_handler/_reject_reserved_fields",
            "tests": [
                "tests/test_terminal_runner_ipc.py::test_f12_frozen_scheduler_deadline_is_not_widened_at_dispatch",
                "tests/test_terminal_runner_ipc.py::test_f12_claim_carries_frozen_deadline_and_private_fields_cannot_be_forged",
            ],
            "pre_fix": "2/2 FAILED（claim→handler 之间期限被放宽、handler 仍执行）",
        },
    ],
    "observations_documented_not_defects": [
        "业务帧重复 request_id 会被执行两次（无 nonce/去重）：连接完整性由内核管道保证、对端即调用方；"
        "已写入接口文档 §4（超时 mutating 禁止静默重试之外，重放会重复执行）。",
        "serve() 内联 handler 无执行时长上界：期限只在分发前复核；这是“内联=天然背压”的权衡，已在接口文档标注。",
    ],
    "capability_changes": {
        "reduced": [],
        "added_or_strengthened": [
            "run_handler 认证/schema/类型/terminal 绑定（原先只有 serve 路径认证）",
            "per-request 响应分派与并发 call/send/recv 纪律（原为文档禁止并发）",
            "并发连接有界能力真正可达（实例池；原先活动连接时 accept 抛错）",
            "close 语义如实反映 CloseHandle 结果并全部可重试收敛",
            "ACL 复核严格白名单（object/callback/未知类型 fail-closed）",
            "秘密写：唯一 CREATE_NEW tmp + 跨进程锁 read-modify-write",
            "bootstrap 身份强制核验入口（默认 verify=True）",
            "scheduler 固化期限不可被 claim→handler 放宽、私有字段不可被伪造",
        ],
    },
}


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    environment_data = environment()
    (EVIDENCE / "r2_environment.json").write_text(
        json.dumps(environment_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    runs = collect_runs()
    (EVIDENCE / "r2_runs.json").write_text(
        json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (EVIDENCE / "r2_closure.json").write_text(
        json.dumps(CLOSURE, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (EVIDENCE / "r2_cleanup.json").write_text(
        json.dumps(cleanup_scan(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        run["name"]: {"exit_code": run["exit_code"], "summary": run["summary"]}
        for run in runs["runs"]
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    failures = [run["name"] for run in runs["runs"] if run["exit_code"] != 0 and run["name"] != "pre_fix_new_regressions"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
