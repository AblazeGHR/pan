"""Pan Terminal runner 客户端（P1，供 P2 服务层接线）。

冻结接口见 ``docs/design/PAN_TERMINAL_RUNNER_INTERFACES_20261003.md`` §7.2。

- ``build_runner_argv`` / ``complete_bootstrap``：P2 的 **spawn + 首绑** 两步
  （argv 只含 ``--terminal-id`` / ``--secret-file``，**无 token**；bootstrap 自证
  经内核实证后写 DPAPI 秘密）。
- ``RunnerClient``：读 DPAPI 秘密 → 连命名管道 → **先核验 runner 身份**（pid + raw
  FILETIME + 同 handle `Wait`）再发 hello → HMAC 握手 → 8 个命令
  （describe/read/input/resize/close/detach/owner-heartbeat/snapshot）。

纪律：

- token 只进入内存与 HMAC 域；错误与异常信息只带类型名，不带 token；
- mutating 命令（input/resize/lease/stop）超时抛 ``ipc.RequestTimeout``（结果未知）：
  调用方**不得静默重试**，应先 ``describe()`` / ``snapshot()`` 重新对齐；
- ``release_connection()`` 只释放连接，**不**触碰 runtime、**不**删除秘密
  （detach 闭环依赖秘密跨 Pan 重启仍可解密）。
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import ipc, secret_store, win_pipe

__all__ = [
    "RunnerClientError",
    "RunnerSecretUnavailable",
    "RunnerAttachError",
    "build_runner_argv",
    "complete_bootstrap",
    "RunnerClient",
]


class RunnerClientError(RuntimeError):
    """客户端错误基类（消息只允许静态串与类型名，不含 token）。"""


class RunnerSecretUnavailable(RunnerClientError):
    """DPAPI 秘密缺失/不可解密（bootstrap 未完成或销毁）。"""


class RunnerAttachError(RunnerClientError):
    """管道连接 / 服务器身份核验 / HMAC 握手失败（fail-closed，未进入业务）。"""


def build_runner_argv(
    terminal_id: str,
    secret_file: str | Path,
    *,
    python: str | None = None,
    rows: int | None = None,
    cols: int | None = None,
) -> list[str]:
    """spawn 参数（argv **无 token**；是否 DETACHED_PROCESS 由 P2 决定）。

    注意：调用方以仓库根为 cwd（或等价 PYTHONPATH），使
    ``python -m packages.core.terminal.runner`` 可解析。
    """
    argv = [
        str(python or sys.executable),
        "-m",
        "packages.core.terminal.runner",
        "--terminal-id",
        str(terminal_id),
        "--secret-file",
        str(Path(secret_file)),
    ]
    if rows is not None:
        argv += ["--rows", str(int(rows))]
    if cols is not None:
        argv += ["--cols", str(int(cols))]
    return argv


def complete_bootstrap(
    store: secret_store.SecretStore,
    terminal_id: str,
    *,
    timeout: float = 10.0,
    token: str | None = None,
    pipe_name: str | None = None,
    poll: float = 0.05,
) -> secret_store.SecretPayload:
    """P2 首绑：等 runner 自证（**默认强制核验**）→ 生成 token → 写 DPAPI 秘密。

    自证核验失败（``SecretBootstrapUnverifiedError``）与超时都不重试掩盖：
    由调用方回收记录并显式报错。
    """
    record = store.wait_for_bootstrap_identity(terminal_id, timeout=timeout, poll=poll)
    now = time.time()
    payload = secret_store.SecretPayload(
        terminal_id=str(terminal_id),
        pipe_name=pipe_name or win_pipe.pipe_name_for(str(terminal_id)),
        token=token or ipc.generate_token(),
        runner_pid=int(record.pid),
        runner_filetime=int(record.filetime),
        created_at=now,
        updated_at=now,
    )
    store.write_secret(payload)
    return payload


class RunnerClient:
    """一条 runner 控制连接（命令语义见接口文档 §3.3）。"""

    def __init__(
        self,
        terminal_id: str,
        *,
        data_root: str | Path | None = None,
        connect_timeout: float = 5.0,
        request_timeout_ms: int = 10_000,
        close_timeout_ms: int = 15_000,
        transport: Any | None = None,
        identity_probe: Callable[[int], Any] | None = None,
        secret: secret_store.SecretPayload | None = None,
        client_id: str | None = None,
    ) -> None:
        self.terminal_id = str(terminal_id)
        self._data_root = data_root
        self._connect_timeout = float(connect_timeout)
        self._request_timeout_ms = int(request_timeout_ms)
        self._close_timeout_ms = int(close_timeout_ms)
        self._transport = transport
        self._identity_probe = identity_probe
        self._secret = secret
        self._client_id = client_id or f"pan-{os.getpid()}-{secrets.token_hex(4)}"
        self._session: ipc.IpcSession | None = None
        self._pipe_client: win_pipe.PipeClient | None = None

    # ------------------------------------------------------------ 连接
    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def authenticated(self) -> bool:
        return self._session is not None and self._session.authenticated

    @property
    def peer_pid(self) -> int | None:
        return None if self._session is None else self._session.peer.pid

    @property
    def session(self) -> ipc.IpcSession:
        """底层已认证会话（高级用法/协议自检；业务命令请用具体方法）。"""
        return self._require_attached()

    def attach(self) -> "RunnerClient":
        """读秘密 → 连管道 → 核验服务器身份 → HMAC 握手（失败 fail-closed）。"""
        if self._session is not None:
            return self
        secret = self._secret
        if secret is None:
            try:
                secret = secret_store.SecretStore(self._data_root).read_secret(self.terminal_id)
            except Exception as exc:  # noqa: BLE001 - 只带类型名
                raise RunnerSecretUnavailable(
                    f"secret unavailable: {type(exc).__name__}"
                ) from exc
        transport = self._transport
        temporary = transport is None
        if transport is None:
            self._pipe_client = win_pipe.PipeClient(
                self.terminal_id, connect_timeout=self._connect_timeout
            )
            try:
                transport = self._pipe_client.connect()
            except Exception as exc:  # noqa: BLE001
                raise RunnerAttachError(f"pipe connect failed: {type(exc).__name__}") from exc
        session = ipc.IpcSession(
            transport,
            role="client",
            token=secret.token,
            terminal_id=self.terminal_id,
            expected_peer_identity=secret.runner_identity(),
            identity_probe=self._identity_probe or win_pipe.default_identity_probe,
            client_id=self._client_id,
        )
        try:
            session.handshake()
        except Exception as exc:  # noqa: BLE001 - 未认证绝不进入业务
            if temporary:
                try:
                    transport.close(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
            raise RunnerAttachError(f"handshake failed: {type(exc).__name__}") from exc
        self._secret = secret
        self._session = session
        return self

    def release_connection(self) -> None:
        """断连只释放连接（不触碰 runtime、不删除秘密）；可重连。"""
        session, self._session = self._session, None
        if session is not None:
            try:
                session.close(timeout=2.0)
            except Exception:  # noqa: BLE001 - 关闭失败不改变已释放事实
                pass
        self._transport = None

    # ------------------------------------------------------------ 命令
    def _require_attached(self) -> ipc.IpcSession:
        if self._session is None:
            raise RunnerClientError("client is not attached: call attach() first")
        return self._session

    def _call(
        self,
        op: str,
        payload: Mapping[str, Any],
        *,
        timeout_ms: int | None = None,
        io_slack: float = 1.0,
    ) -> dict[str, Any]:
        session = self._require_attached()
        timeout = int(timeout_ms if timeout_ms is not None else self._request_timeout_ms)
        frame = session.call(op, payload, timeout_ms=timeout, io_slack=io_slack)
        if frame.get("type") == ipc.MessageType.ERROR.value:
            raise RunnerClientError(f"runner rejected request: {frame.get('error')}")
        return dict(frame.get("payload") or {})

    def call(
        self,
        op: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
        io_slack: float = 1.0,
    ) -> dict[str, Any]:
        """底层命令调用（原始响应 payload）：协议自检/诊断与未来 op 的接线口。

        业务命令请用 ``describe/read/input/...``；mutating 超时语义同 §doc（禁止静默重试）。
        """
        return self._call(op, payload or {}, timeout_ms=timeout_ms, io_slack=io_slack)

    @staticmethod
    def _detail(payload: Mapping[str, Any]) -> dict[str, Any]:
        text = payload.get("detail")
        if not isinstance(text, str) or not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"detail_parse_error": True}
        return parsed if isinstance(parsed, dict) else {"detail_parse_error": True}

    @staticmethod
    def _gap(payload: Mapping[str, Any]) -> tuple[int, int] | None:
        gap = payload.get("gap")
        if gap is None:
            return None
        if isinstance(gap, (list, tuple)) and len(gap) == 2:
            try:
                return (ipc.parse_exact_int(gap[0], name="gap[0]"), ipc.parse_exact_int(gap[1], name="gap[1]"))
            except ipc.ProtocolError:
                return None
        return None

    def describe(self) -> dict[str, Any]:
        """状态探测：``read(cursor=0, max_bytes=1)`` + 解析统一 detail 摘要。"""
        payload = self._call(ipc.OP_READ, {"cursor": "0", "max_bytes": 1})
        out = dict(self._detail(payload))
        out["status"] = payload.get("status")
        out["total_bytes"] = ipc.payload_int(payload, "total_bytes", default=0)
        out["first_retained_seq"] = ipc.payload_int(payload, "first_retained_seq", default=0)
        return out

    def read(self, cursor: int = 0, *, max_bytes: int | None = None) -> dict[str, Any]:
        payload_in: dict[str, Any] = {"cursor": ipc.encode_exact_int(int(cursor))}
        if max_bytes is not None:
            payload_in["max_bytes"] = int(max_bytes)
        payload = self._call(ipc.OP_READ, payload_in)
        data = ipc.parse_b64_bytes(payload.get("data_b64", ""))
        return {
            "data": data,
            "seq": ipc.payload_int(payload, "seq", default=int(cursor)),
            "size": len(data),
            "next_cursor": ipc.payload_int(payload, "next_cursor", default=int(cursor)),
            "gap": self._gap(payload),
            "truncated": bool(payload.get("truncated", False)),
            "total_bytes": ipc.payload_int(payload, "total_bytes", default=0),
            "first_retained_seq": ipc.payload_int(payload, "first_retained_seq", default=0),
            "status": payload.get("status"),
            "describe": self._detail(payload),
        }

    def input(
        self, data: bytes, *, seq: int | None = None, timeout_ms: int | None = None
    ) -> dict[str, Any]:
        """输入（mutating）：返回 runner 的接受/完成/busy 状态（``size``=已写字节）。"""
        payload_in: dict[str, Any] = {"data_b64": ipc.encode_payload_bytes(bytes(data))}
        if seq is not None:
            payload_in["seq"] = ipc.encode_exact_int(int(seq))
        payload = self._call(ipc.OP_INPUT, payload_in, timeout_ms=timeout_ms)
        return {
            "status": payload.get("status"),
            "ok": payload.get("ok"),
            "size": ipc.payload_int(payload, "size", default=0),
            "describe": self._detail(payload),
        }

    def resize(self, rows: int, cols: int) -> dict[str, Any]:
        """尺寸变更（mutating）；不声明物理 PTY/仿真器尺寸已同步（看 detail）。"""
        payload = self._call(
            ipc.OP_RESIZE, {"rows": str(int(rows)), "cols": str(int(cols))}
        )
        return {
            "status": payload.get("status"),
            "rows": ipc.payload_int(payload, "rows", default=int(rows)),
            "cols": ipc.payload_int(payload, "cols", default=int(cols)),
            "describe": self._detail(payload),
        }

    def snapshot(self, *, timeout_ms: int = 5000) -> dict[str, Any]:
        """快照协议 A：serialized 屏幕 + applied cursor（无引擎 → 无 data/cursor）。"""
        payload = self._call(ipc.OP_SNAPSHOT, {"timeout_ms": str(int(timeout_ms))})
        out = dict(self._detail(payload))
        out["status"] = payload.get("status")
        if "data_b64" in payload:
            serialized = ipc.parse_b64_bytes(payload.get("data_b64"))
            out["serialized_screen"] = serialized.decode("utf-8", errors="replace")
            out["cursor"] = ipc.payload_int(payload, "cursor", default=0)
            out["rows"] = ipc.payload_int(payload, "rows", default=0)
            out["cols"] = ipc.payload_int(payload, "cols", default=0)
            out["size"] = ipc.payload_int(payload, "size", default=len(serialized))
        return out

    def heartbeat(self, *, generation: int | None = None, timeout_ms: int | None = None) -> dict[str, Any]:
        """所有者心跳（lease/control）：P2 每 1s 调用；唯一续约路径。"""
        payload_in: dict[str, Any] = {"client_id": self._client_id, "role": "control"}
        if generation is not None:
            payload_in["generation"] = ipc.encode_exact_int(int(generation))
        payload = self._call(ipc.OP_LEASE, payload_in, timeout_ms=timeout_ms)
        return {"status": payload.get("status"), "ok": payload.get("ok"), "describe": self._detail(payload)}

    def register_observer(self, *, timeout_ms: int | None = None) -> dict[str, Any]:
        """观察者登记（lease/observer）：不续约、不改变 runtime。"""
        payload = self._call(
            ipc.OP_LEASE,
            {"client_id": self._client_id, "role": "observer"},
            timeout_ms=timeout_ms,
        )
        return {"status": payload.get("status"), "ok": payload.get("ok"), "describe": self._detail(payload)}

    def detach(self) -> dict[str, Any]:
        """显式 detach（stop/reason=detach）；能力不足时 runner 显式拒绝。"""
        payload = self._call(ipc.OP_STOP, {"reason": "detach"})
        detail = self._detail(payload)
        return {
            "status": payload.get("status"),
            "ok": payload.get("ok"),
            "durability": detail.get("durability"),
            "describe": detail,
        }

    def close(self, *, reason: str = "explicit-close") -> dict[str, Any]:
        """关闭终端（mutating；整树终止）。超时后先 describe 对齐，禁止静默重试。"""
        payload = self._call(
            ipc.OP_STOP, {"reason": str(reason)}, timeout_ms=self._close_timeout_ms
        )
        return {"status": payload.get("status"), "ok": payload.get("ok"), "describe": self._detail(payload)}
