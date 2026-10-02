"""r3 定向复现：R2A（impostor 测试在 ERROR_NO_DATA 瞬时路径上的可观测性缺口）。

**只读生产代码**；本工具是本 TA 自有的 audit 复现器（不改任何生产模块、不改 tests）。

构造（确定性，不依赖随机时序）：

1. 本进程内建一个「冒充者」``PipeServer``（实例池已建、**尚未 armed**）；
2. 一个客户端（session 的 expected identity 故意用「同 PID + FILETIME+1」）连上后
   在身份核验阶段被拒（``AuthenticationError: peer identity mismatch``），立即关闭；
3. 客户端**先连上又断开**之后才 arm ⇒ 内核返回 ``ERROR_NO_DATA`` ⇒ 走库里的
   “client left before connect” 瞬时路径：实例被丢弃重建，**不产生任何读取观测**。

于是对照两种断言逻辑：

- **旧逻辑**（R2A 的根因）：只收集「真实 accept + 读取」的列表，断言
  ``received == [b"", b""]``（两次拒绝各一个零字节读取）→ 瞬时路径下**必然失败**
  （``received`` 空或只有一个），而且 ``sum(len(...)) == 0`` 这类断言会**空集假通过**；
- **新逻辑**（r3 修复后的形式）：把「瞬时事件」与「真实读取」**分开记录**，并要求
  每次客户端身份拒绝都有**服务端可证观测**（瞬时事件 或 读取）、客户端零帧发送、
  读取零字节；对照用例（身份匹配）必须能观测到 hello，证明零字节断言非空集假通过。

运行：

    E:/software/miniforge/python.exe audit/terminal/implementation/ipc/r3_repro_r2a_transient.py

输出：stdout JSON + ``evidence/r3/pre_fix/r2a_transient_repro.json``。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from packages.core.terminal import ipc, secret_store, win_pipe  # noqa: E402
from packages.core.terminal.contracts import ProcessIdentity  # noqa: E402

EVIDENCE = Path(__file__).resolve().parent / "evidence" / "r3" / "pre_fix"
TRANSIENT_MARK = "client left before connect"


class Witness:
    """冒充者侧观测：瞬时事件 / 真实 accept / 读取 三类分开记录。"""

    def __init__(self, server: win_pipe.PipeServer) -> None:
        self.server = server
        self.accept_calls = 0
        self.accepted = 0
        self.accept_errors = 0
        self.reads: list[bytes] = []

    def transient(self) -> int:
        errors = self.server.diagnostics.as_dict().get("errors") or []
        return sum(1 for entry in errors if TRANSIENT_MARK in str(entry))

    def witnesses(self) -> int:
        return self.accepted + self.transient()

    def describe(self) -> dict:
        return {
            "accept_calls": self.accept_calls,
            "accepted": self.accepted,
            "transient": self.transient(),
            "accept_errors": self.accept_errors,
            "reads": [len(chunk) for chunk in self.reads],
        }


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    terminal_id = "term_r3a_transient"
    token = ipc.generate_token()
    own = win_pipe.current_process_identity()
    wrong_identity = ProcessIdentity(pid=int(own.pid), created_at_filetime=int(own.created_at_filetime) + 1)

    workdir = Path(os.environ.get("TEMP", ".")) / f"pan-r3a-{os.getpid()}"
    store = secret_store.SecretStore(workdir)
    store.write_secret(
        secret_store.SecretPayload(
            terminal_id=terminal_id,
            pipe_name=win_pipe.pipe_name_for(terminal_id),
            token=token,
            runner_pid=int(own.pid),
            runner_filetime=int(own.created_at_filetime),
            created_at=time.time(),
            updated_at=time.time(),
        )
    )

    impostor = win_pipe.PipeServer(terminal_id, max_active_connections=2)
    impostor.create()
    witness = Witness(impostor)
    real_accept = win_pipe.PipeServer.accept

    def counting_accept(self, **kwargs):  # noqa: ANN001 - 测试 spy
        witness.accept_calls += 1
        connection = real_accept(self, **kwargs)
        if connection is not None:
            witness.accepted += 1
        return connection

    win_pipe.PipeServer.accept = counting_accept
    send_calls = {"client": 0}
    real_send = win_pipe.PipeConnection.send_frame

    def counting_send(self, message, **kwargs):  # noqa: ANN001 - 测试 spy
        send_calls["client"] += 1
        return real_send(self, message, **kwargs)

    win_pipe.PipeConnection.send_frame = counting_send
    report: dict = {"terminal_id": terminal_id}
    try:
        # ① 客户端连上 → 身份不符被拒 → 立即断开（服务端尚未 arm）
        client = win_pipe.PipeClient(terminal_id, connect_timeout=5.0)
        connection = client.connect()
        session = ipc.IpcSession(
            connection,
            role="client",
            token=token,
            terminal_id=terminal_id,
            expected_peer_identity=wrong_identity,
            identity_probe=win_pipe.default_identity_probe,
            timeout=3.0,
        )
        try:
            session.handshake()
            report["client_rejection"] = {"rejected": False}
        except ipc.AuthenticationError as exc:
            report["client_rejection"] = {
                "rejected": True,
                "is_identity_mismatch": "identity mismatch" in str(exc),
                "error": type(exc).__name__,
            }
        connection.close(timeout=2.0)

        # ② 客户端已离开后才 arm ⇒ ERROR_NO_DATA 瞬时路径（确定性）
        accepted = impostor.accept(timeout=1.0)
        report["accept_returned_none"] = accepted is None
        if accepted is not None:
            report["unexpected_accepted"] = True
            accepted.close(timeout=1.0)

        # ③ 旧逻辑（R2A 根因）：只收「读取」列表 + 精确计数断言
        legacy_received: list[bytes] = list(witness.reads)
        legacy = {"received": [chunk.decode("latin-1") for chunk in legacy_received]}
        try:
            assert legacy_received == [b"", b""], (
                f"前两个用例必须零字节: {legacy_received!r}"
            )
            legacy["old_assertion"] = "PASSED"
        except AssertionError as exc:
            legacy["old_assertion"] = "FAILED"
            legacy["old_assertion_message"] = str(exc)[:160]
        legacy["vacuous_zero_byte_claim"] = sum(len(chunk) for chunk in legacy_received) == 0
        report["legacy_logic"] = legacy

        # ④ 新逻辑（r3 修复形式）：瞬时/读取分开 + 每次拒绝都有可证观测 + 客户端零发送
        transient = witness.transient()
        report["new_logic"] = {
            "witness": witness.describe(),
            "witnessed_rejection": witness.witnesses() >= 1,
            "zero_credential_bytes": sum(len(chunk) for chunk in witness.reads) == 0,
            "client_send_frames": send_calls["client"],
            "client_sent_nothing": send_calls["client"] == 0,
            "transient_recorded_separately": transient >= 1 and witness.accepted == 0,
            "assertion": "PASSED"
            if (witness.witnesses() >= 1 and send_calls["client"] == 0
                and all(len(chunk) == 0 for chunk in witness.reads))
            else "FAILED",
        }
    finally:
        win_pipe.PipeServer.accept = real_accept
        win_pipe.PipeConnection.send_frame = real_send
        stop = getattr(witness, "stop", None)
        del stop
        try:
            impostor.cancel_accept()
        except Exception:  # noqa: BLE001 - 清理兜底
            pass
        report["server_close"] = impostor.close(timeout=2.0).converged
        store.delete_secret(terminal_id, reason="r3-probe-cleanup", caller_responsible="probe owned files")
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    (EVIDENCE / "r2a_transient_repro.json").write_text(text, encoding="utf-8")
    return 0 if report.get("legacy_logic", {}).get("old_assertion") == "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
