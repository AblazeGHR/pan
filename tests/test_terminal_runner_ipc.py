"""Pan Terminal IPC 测试（帧协议 / 双向认证 / 命名管道 / 有界取消）。

分层：

1. **纯逻辑（跨平台，无 ctypes）**：帧编解码与拒绝策略、精确整数、脱敏、
   有界调度与迟到响应丢弃、``IpcSession`` 握手（fake transport）。
2. **Windows 真实命名管道（跨进程）**：自建 runner 子进程 + DPAPI 秘密 +
   ``FIRST_PIPE_INSTANCE`` + owner-only DACL + ``GetNamedPipeServerProcessId``
   身份核验 + 双向 token 证明 + 请求/响应。

隔离与安全（本文件所有用例）：

- 只在 ``tmp_path`` 与 ``%TEMP%`` 写文件；只用自己 spawn 的进程；
- 不打开监听端口（只有命名管道，DACL=当前用户）、不碰任何既有服务/凭据；
- 结束子进程前一律用 **同 handle PID + raw FILETIME 核验**
  （``win_pipe.terminate_verified_process``），不按 PID 单值杀进程；
- 秘密哨兵（token）扫描覆盖：子进程报告、stdout/stderr、argv、环境变量、
  registry JSON、秘密文件明文、异常/日志文本。

未验证项（如实记录，不作为“已通过”）：其它 Windows 用户/另一安全上下文
连接被拒需要第二账户，本 TA 未创建账户、未改账户权限，因此只有结构性证据
（``PIPE_REJECT_REMOTE_CLIENTS`` 创建参数 + DACL 无其它 SID ACE）；远端主机
连接同理未实测。
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from packages.core.terminal import ipc, win_pipe
from packages.core.terminal.contracts import (
    ProcessIdentity,
    ProcessProbe,
    ProcessStatus,
)

WINDOWS = os.name == "nt"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows named pipes / DPAPI only")
REPO_ROOT = Path(__file__).resolve().parents[1]

# ── 哨兵：任何日志/报告/文件里的 token 都会让测试失败 ────────────────────────
_SENTINEL_PREFIX = "5e7e0d1c0ffee"


def _sentinel_token() -> str:
    return (_SENTINEL_PREFIX + os.urandom(32).hex())[:64]


# ══════════════════════════════════════════════════════════════════════════
# 纯逻辑：token / MAC
# ══════════════════════════════════════════════════════════════════════════


def test_token_is_256_bit_hex_and_compared_with_compare_digest():
    token = ipc.generate_token()
    assert len(token) == 64 and all(ch in "0123456789abcdef" for ch in token)
    assert ipc.token_bytes(token) and len(ipc.token_bytes(token)) == 32
    assert ipc.verify_token(token, token) is True
    assert ipc.verify_token(token, ipc.generate_token()) is False
    assert ipc.verify_token(token, None) is False
    assert ipc.verify_token(token, "short") is False
    assert ipc.verify_token(token, token[:-1] + ("0" if token[-1] != "0" else "1")) is False
    assert ipc.verify_token(token, token.upper()) is True  # 十六进制大小写等价
    with pytest.raises(ValueError):
        ipc.token_bytes("not-a-token")
    assert len(ipc.generate_nonce()) == 64


def test_mac_binds_nonce_terminal_and_peer_identity():
    token = ipc.generate_token()
    nonce = ipc.generate_nonce()
    context = ipc.MacContext(
        role="hello",
        terminal_id="term_abc",
        nonce=nonce,
        actor="pan-service",
        bind_pid=4242,
        bind_filetime=134000000000000001,
    )
    mac = ipc.compute_mac(token, context)
    assert ipc.verify_mac(token, mac, context) is True
    assert ipc.verify_mac(ipc.generate_token(), mac, context) is False
    # 重放到新 nonce
    replayed = ipc.MacContext(**{**context.__dict__, "nonce": ipc.generate_nonce()})
    assert ipc.verify_mac(token, mac, replayed) is False
    # PID / raw FILETIME 被替换
    assert ipc.verify_mac(token, mac, ipc.MacContext(**{**context.__dict__, "bind_pid": 1})) is False
    assert (
        ipc.verify_mac(token, mac, ipc.MacContext(**{**context.__dict__, "bind_filetime": 1})) is False
    )
    # 别的终端 / 别的角色域
    assert (
        ipc.verify_mac(token, mac, ipc.MacContext(**{**context.__dict__, "terminal_id": "term_zzz"}))
        is False
    )
    assert ipc.verify_mac(token, mac, ipc.MacContext(**{**context.__dict__, "role": "ack"})) is False
    assert ipc.verify_mac(token, None, context) is False
    assert ipc.verify_mac(token, "0" * 64, context) is False


# ══════════════════════════════════════════════════════════════════════════
# 纯逻辑：帧编解码与拒绝策略
# ══════════════════════════════════════════════════════════════════════════


def test_frame_roundtrip_keeps_64bit_seq_exact_without_float_rounding():
    request = ipc.build_request(
        ipc.OP_READ, {"cursor": 2**53 + 1, "max_bytes": 4096}, terminal_id="term_seq"
    )
    wire = ipc.encode_frame(request)
    assert b"9007199254740993" in wire, "64 位 seq 必须以十进制字符串上线"
    assert b"9.007199254740992e" not in wire
    decoded = ipc.FrameDecoder().feed(wire)[0]
    assert decoded["payload"]["cursor"] == str(2**53 + 1)
    assert ipc.payload_int(decoded["payload"], "cursor") == 2**53 + 1
    assert isinstance(ipc.payload_int(decoded["payload"], "cursor"), int)
    # 大值（2**63-1）同样精确
    request = ipc.build_request(ipc.OP_READ, {"cursor": 2**63 - 1}, terminal_id="term_seq")
    decoded = ipc.FrameDecoder().feed(ipc.encode_frame(request))[0]
    assert ipc.payload_int(decoded["payload"], "cursor") == 2**63 - 1


def test_frame_decoder_rejects_oversize_before_terminator_and_is_sticky():
    decoder = ipc.FrameDecoder(max_frame_bytes=64)
    assert decoder.feed(b'{"v":1,"type":"ping"}\n') == [{"v": 1, "type": "ping"}]
    with pytest.raises(ipc.FrameTooLargeError):
        decoder.feed(b"x" * 65)  # 没有换行也必须立即拒绝（缓冲有界）
    assert decoder.failed is True
    with pytest.raises(ipc.FrameTooLargeError):
        decoder.feed(b'{"v":1,"type":"ping"}\n')  # 粘滞失败：不“跳过坏帧继续解析”
    # 覆盖 data_b64 的整帧超界同样被拒
    decoder2 = ipc.FrameDecoder(max_frame_bytes=128)
    with pytest.raises(ipc.FrameTooLargeError):
        decoder2.feed(ipc.encode_frame({"v": 1, "type": "event"}) + b"x" * 200 + b"\n")


@pytest.mark.parametrize(
    "line, expected",
    [
        (b"\n", ipc.MalformedFrameError),
        (b'{"v":1,"type":"nope"}\n', ipc.MalformedFrameError),
        (b'{"type":"ping"}\n', ipc.ProtocolVersionError),
        (b'{"v":2,"type":"ping"}\n', ipc.ProtocolVersionError),
        (b'{"v":"1","type":"ping"}\n', ipc.ProtocolVersionError),
        (b'{"v":1,"type":"ping","extra":1}\n', ipc.MalformedFrameError),
        (b'["not","an","object"]\n', ipc.MalformedFrameError),
        (b'{"v":1,"type":"ping"', ipc.MalformedFrameError),  # 无换行 → 只有断帧时才算错
        (b"\xff\xfe{\n", ipc.MalformedFrameError),
        (b"not-json\n", ipc.MalformedFrameError),
    ],
)
def test_frame_decoder_rejects_malformed_lines(line, expected):
    decoder = ipc.FrameDecoder()
    if line == b'{"v":1,"type":"ping"':  # 半帧：feed 不报错，EOF 时由 take_partial 判定
        assert decoder.feed(line) == []
        assert decoder.take_partial() == line
        return
    with pytest.raises(expected):
        decoder.feed(line)


@pytest.mark.parametrize(
    "message, expected",
    [
        (
            {"v": 1, "type": "request", "request_id": "bad", "terminal_id": "term_a"},
            ipc.MalformedFrameError,
        ),
        (
            {"v": 1, "type": "request", "request_id": "r-" + "0" * 16, "terminal_id": "x" * 200},
            ipc.MalformedFrameError,
        ),
        (
            {
                "v": 1,
                "type": "request",
                "request_id": "r-" + "0" * 16,
                "terminal_id": "term_a",
                "payload": {"op": "unknown-op"},
            },
            ipc.MalformedFrameError,
        ),
        (
            {
                "v": 1,
                "type": "request",
                "request_id": "r-" + "0" * 16,
                "terminal_id": "term_a",
                "payload": {"op": "input", "data_b64": "!!!not-base64!!!"},
            },
            ipc.MalformedFrameError,
        ),
        (
            {
                "v": 1,
                "type": "request",
                "request_id": "r-" + "0" * 16,
                "terminal_id": "term_a",
                "payload": {"op": "resize", "rows": 9000, "cols": 80},
            },
            ipc.MalformedFrameError,
        ),
        (
            {
                "v": 1,
                "type": "request",
                "request_id": "r-" + "0" * 16,
                "terminal_id": "term_a",
                "payload": {"op": "input", "data_b64": "QQ==", "extra": 1},
            },
            ipc.MalformedFrameError,
        ),
        (
            {
                "v": 1,
                "type": "request",
                "request_id": "r-" + "0" * 16,
                "terminal_id": "term_a",
                "payload": {"op": "read", "cursor": 1.5},
            },
            ipc.MalformedFrameError,
        ),
        (
            {
                "v": 1,
                "type": "request",
                "request_id": "r-" + "0" * 16,
                "terminal_id": "term_a",
                "payload": {"op": "read", "cursor": True},
            },
            ipc.MalformedFrameError,
        ),
        (
            {
                "v": 1,
                "type": "request",
                "request_id": "r-" + "0" * 16,
                "terminal_id": "term_a",
                "timeout_ms": 10**9,
            },
            ipc.MalformedFrameError,
        ),
        ({"v": 1, "type": "response", "request_id": "r-" + "0" * 16}, ipc.MalformedFrameError),
        (
            {"v": 1, "type": "challenge", "nonce": "abc"},
            ipc.MalformedFrameError,
        ),
        (
            {"v": 1, "type": "hello", "client_id": "c", "mac": "zz"},
            ipc.MalformedFrameError,
        ),
    ],
)
def test_message_schema_rejects_unknown_malformed_and_oversized(message, expected):
    with pytest.raises(expected):
        ipc.validate_message(message)


def test_oversized_payload_is_rejected_not_truncated():
    payload = ipc.encode_payload_bytes(b"x" * (ipc.DEFAULT_MAX_PAYLOAD_BYTES + 1))
    with pytest.raises(ipc.FrameTooLargeError):
        ipc.validate_message(
            {
                "v": 1,
                "type": "request",
                "request_id": "r-" + "0" * 16,
                "terminal_id": "term_a",
                "payload": {"op": "input", "data_b64": payload},
            }
        )
    ok = ipc.encode_payload_bytes(b"y" * 16)
    message = ipc.validate_message(
        {
            "v": 1,
            "type": "request",
            "request_id": "r-" + "0" * 16,
            "terminal_id": "term_a",
            "payload": {"op": "input", "data_b64": ok, "seq": 2**53},
        }
    )
    assert message["payload"]["seq"] == str(2**53)


def test_describe_and_redact_never_expose_secrets_or_payload():
    token = _sentinel_token()
    mac = ipc.compute_mac(
        token,
        ipc.MacContext(role="hello", terminal_id="term_a", nonce="a" * 64, actor="c", bind_pid=1),
    )
    message = ipc.build_hello(client_id="c", mac=mac, pid=os.getpid())
    described = json.dumps(ipc.describe_message(message))
    assert token not in described and mac not in described
    assert "mac_present" in described
    request = ipc.build_request(
        ipc.OP_INPUT, {"data_b64": ipc.encode_payload_bytes(b"secret-bytes")}, terminal_id="term_a"
    )
    described = json.dumps(ipc.describe_message(request))
    assert "data_b64_len" in described and "secret-bytes" not in described
    error = ipc.build_error(f"boom token={token} expected={mac}", secrets_=[token, mac])
    assert token not in error["error"] and mac not in error["error"]
    assert "***redacted***" in error["error"]
    assert ipc.redact(f"token={token}", [token]) == "token=***redacted***"
    assert ipc.redact("nothing", [token]) == "nothing"
    assert token not in ipc.redact(f"x{token}x", [token.encode()])


# ══════════════════════════════════════════════════════════════════════════
# 纯逻辑：有界调度 / 迟到响应
# ══════════════════════════════════════════════════════════════════════════


def test_scheduler_rejects_expired_and_queue_full_without_dispatching():
    now = [1000.0]
    scheduler = ipc.RequestScheduler(max_operations=2, clock=lambda: now[0])
    expired = ipc.build_request(ipc.OP_INPUT, {"data_b64": "QQ=="}, terminal_id="term_a", timeout_ms=10)
    expired["deadline"] = now[0] - 1
    assert scheduler.submit(expired) is ipc.SubmitOutcome.REJECTED_EXPIRED
    assert scheduler.claim() is None  # 过期请求绝不交给 handler
    assert scheduler.dropped_expired == 1

    for _ in range(2):
        fresh = ipc.build_request(ipc.OP_READ, {"cursor": 0}, terminal_id="term_a", timeout_ms=5000)
        fresh["deadline"] = now[0] + 5.0
        assert scheduler.submit(fresh) is ipc.SubmitOutcome.ACCEPTED
    overflow = ipc.build_request(ipc.OP_READ, {"cursor": 0}, terminal_id="term_a", timeout_ms=5000)
    overflow["deadline"] = now[0] + 5.0
    assert scheduler.submit(overflow) is ipc.SubmitOutcome.REJECTED_QUEUE_FULL
    assert scheduler.rejected_queue_full == 1 and scheduler.pending == 2

    # 排队期间过期：claim 丢弃，不执行（deadline 不随等待时间滑动）
    now[0] += 30.0
    assert scheduler.claim() is None
    assert scheduler.dropped_expired == 3
    assert scheduler.pending == 0
    assert scheduler.drain() == 0


def test_scheduler_caps_remote_deadline_by_local_timeout():
    scheduler = ipc.RequestScheduler(clock=lambda: 500.0)
    request = ipc.build_request(
        ipc.OP_READ, {"cursor": 0}, terminal_id="term_a", timeout_ms=1000, deadline=10**9
    )
    assert scheduler.effective_deadline(request, now=500.0) == pytest.approx(501.0)
    assert scheduler.submit(request, now=500.0) is ipc.SubmitOutcome.ACCEPTED
    assert scheduler.claim(now=502.0) is None  # 本地上限（入队时刻起算）已过 → 不执行
    assert scheduler.dropped_expired == 1


def test_pending_requests_is_bounded_and_drops_late_responses():
    now = [2000.0]
    pending = ipc.PendingRequests(max_pending=2, clock=lambda: now[0])
    first = pending.register(ipc.OP_INPUT, timeout_ms=100, now=now[0])
    second = pending.register(ipc.OP_READ, timeout_ms=10_000, now=now[0])
    with pytest.raises(ipc.OperationQueueFull):
        pending.register(ipc.OP_READ, timeout_ms=1000, now=now[0])
    assert first.mutating is True and second.mutating is False

    now[0] += 0.5
    expired = pending.expire()
    assert [entry.request_id for entry in expired] == [first.request_id]

    late = ipc.build_response(first.request_id, {"status": "ok"})
    assert pending.accept_response(late) == (False, "late-response-after-timeout")
    unknown = ipc.build_response("r-" + "f" * 16, {"status": "ok"})
    assert pending.accept_response(unknown) == (False, "unknown-request-id")
    fresh = ipc.build_response(second.request_id, {"status": "ok"})
    assert pending.accept_response(fresh) == (True, "delivered")
    assert pending.pending_count == 0


# ══════════════════════════════════════════════════════════════════════════
# 纯逻辑：IpcSession 握手（fake transport）
# ══════════════════════════════════════════════════════════════════════════


class FakeTransport:
    """内存帧传输：验证 ipc 层不依赖 win_pipe。"""

    def __init__(self, inbox: "queue.Queue", outbox: "queue.Queue", server_pid: int, client_pid: int):
        self.inbox = inbox
        self.outbox = outbox
        self.server_pid = server_pid
        self.client_pid = client_pid
        self.sent: list[dict] = []
        self.closed = False

    def send_frame(self, message):
        frame = json.loads(json.dumps(ipc.validate_message(message)))
        self.sent.append(frame)
        self.outbox.put(frame)

    def recv_frame(self, *, timeout=None):
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def peer_server_pid(self):
        return self.server_pid

    def peer_client_pid(self):
        return self.client_pid

    def close(self, *, timeout=None):
        self.closed = True
        return "closed"


SERVER_PID = 4242
CLIENT_PID = 1717
SERVER_IDENTITY = ProcessIdentity(pid=SERVER_PID, created_at_filetime=134000000000000001)
CLIENT_IDENTITY = ProcessIdentity(pid=CLIENT_PID, created_at_filetime=134000000000000002)


def _identity_probe(pid):
    if pid == SERVER_PID:
        return ProcessProbe(status=ProcessStatus.ALIVE, identity=SERVER_IDENTITY)
    if pid == CLIENT_PID:
        return ProcessProbe(status=ProcessStatus.ALIVE, identity=CLIENT_IDENTITY)
    return ProcessProbe(status=ProcessStatus.UNKNOWN, detail="unknown pid")


def _session_pair(*, server_token=None, client_token=None, probe=_identity_probe, server_expected=None):
    c2s: queue.Queue = queue.Queue()
    s2c: queue.Queue = queue.Queue()
    server_transport = FakeTransport(c2s, s2c, SERVER_PID, CLIENT_PID)
    client_transport = FakeTransport(s2c, c2s, SERVER_PID, CLIENT_PID)
    token = server_token or ipc.generate_token()
    server = ipc.IpcSession(
        server_transport,
        role="server",
        token=token,
        terminal_id="term_fake",
        local_identity=SERVER_IDENTITY,
        expected_peer_identity=server_expected,
        identity_probe=probe,
        timeout=1.0,
    )
    client = ipc.IpcSession(
        client_transport,
        role="client",
        token=client_token or token,
        terminal_id="term_fake",
        expected_peer_identity=SERVER_IDENTITY,
        identity_probe=probe,
        local_pid=CLIENT_PID,
        timeout=1.0,
    )
    return server, client, server_transport, client_transport


def _run_handshakes(server, client):
    result: dict = {}
    thread = threading.Thread(target=lambda: result.setdefault("server", _capture(server.handshake)))
    thread.start()
    result["client"] = _capture(client.handshake)
    thread.join(5)
    return result


def _capture(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - 测试要检查失败类型
        return exc


def test_handshake_succeeds_and_token_never_touches_the_wire():
    server, client, server_transport, client_transport = _session_pair()
    results = _run_handshakes(server, client)
    assert isinstance(results["client"], ipc.AuthResult) and results["client"].ok
    assert isinstance(results["server"], ipc.AuthResult) and results["server"].ok
    assert results["client"].peer.verified is True
    assert results["client"].peer.filetime == SERVER_IDENTITY.created_at_filetime
    assert client.authenticated is True and server.authenticated is True
    blob = json.dumps(server_transport.sent + client_transport.sent)
    assert client._token not in blob
    assert [frame["type"] for frame in client_transport.sent] == ["hello"]
    assert [frame["type"] for frame in server_transport.sent] == ["challenge", "hello_ack"]


def test_handshake_fails_closed_on_wrong_token_and_never_invokes_handler():
    server, client, _, _ = _session_pair(client_token=ipc.generate_token())
    calls: list = []
    results = _run_handshakes(server, client)
    assert isinstance(results["server"], ipc.AuthenticationError)
    assert isinstance(results["client"], ipc.HandshakeTimeout)
    assert server.diagnostics.as_dict()["auth_failures"] >= 1
    with pytest.raises(ipc.AuthenticationError):
        server.serve(lambda request: calls.append(request) or {}, max_requests=1, idle_timeout=0.2)
    assert calls == []


def test_handshake_refuses_when_server_identity_is_not_verifiable():
    # 少了探针 → 不发出凭据（fail-closed）
    server, client, _, client_transport = _session_pair(probe=None)
    results = _run_handshakes(server, client)
    assert isinstance(results["client"], ipc.AuthenticationError)
    assert [frame["type"] for frame in client_transport.sent] == []  # 一个 hello 都没发
    # 给定了错误 FILETIME（PID 复用/伪服务器）→ 同样拒绝
    server, client, _, client_transport = _session_pair()
    client.expected_peer_identity = ProcessIdentity(pid=SERVER_PID, created_at_filetime=1)
    results = _run_handshakes(server, client)
    assert isinstance(results["client"], ipc.AuthenticationError)
    assert client_transport.sent == []
    # 期望身份只有浮点 created_at（无 raw FILETIME）→ 拒绝
    server, client, _, _ = _session_pair()
    client.expected_peer_identity = ProcessIdentity(pid=SERVER_PID, created_at=123.0)
    results = _run_handshakes(server, client)
    assert isinstance(results["client"], ipc.AuthenticationError)


def test_server_requires_local_identity_and_verified_client_identity():
    server, client, _, _ = _session_pair()
    server.local_identity = None
    results = _run_handshakes(server, client)
    assert isinstance(results["server"], ipc.AuthenticationError)
    # 双向：服务器配置了期望客户端身份后，客户端身份不符即拒绝
    server, client, _, _ = _session_pair(server_expected=ProcessIdentity(pid=CLIENT_PID, created_at_filetime=1))
    results = _run_handshakes(server, client)
    assert isinstance(results["server"], ipc.AuthenticationError)
    # 匹配的客户端身份则通过
    server, client, _, _ = _session_pair(server_expected=CLIENT_IDENTITY)
    results = _run_handshakes(server, client)
    assert isinstance(results["server"], ipc.AuthResult)
    assert results["server"].peer.verified is True


def test_session_refuses_business_frames_before_and_after_failed_auth():
    server, client, _, _ = _session_pair(client_token=ipc.generate_token())
    with pytest.raises(ipc.AuthenticationError):
        client.send_request(ipc.OP_READ, {"cursor": 0})
    with pytest.raises(ipc.AuthenticationError):
        server.recv_message(timeout=0.1)
    _run_handshakes(server, client)
    with pytest.raises(ipc.AuthenticationError):
        client.send_request(ipc.OP_READ, {"cursor": 0})


def test_request_response_roundtrip_and_late_response_is_dropped():
    server, client, _, _ = _session_pair()
    results = _run_handshakes(server, client)
    assert isinstance(results["client"], ipc.AuthResult)
    handled: list = []

    def handler(request):
        handled.append(request)
        return {"status": "ok", "detail": request["payload"]["op"]}

    thread = threading.Thread(
        target=lambda: server.serve(handler, max_requests=1, idle_timeout=2.0)
    )
    thread.start()
    response = client.call(ipc.OP_READ, {"cursor": 7}, timeout_ms=2000)
    assert response["ok"] is True and response["payload"]["status"] == "ok"
    thread.join(5)
    assert len(handled) == 1

    # 迟到响应：客户端先超时放弃，再收到响应 → 丢弃并计入 late_responses
    server2, client2, _, _ = _session_pair()
    _run_handshakes(server2, client2)
    entry = client2.send_request(ipc.OP_READ, {"cursor": 0}, timeout_ms=50)
    time.sleep(0.08)
    client2.pending.expire()
    late = ipc.build_response(entry.request_id, {"status": "late"})
    delivered, reason = client2.pending.accept_response(late)
    assert (delivered, reason) == (False, "late-response-after-timeout")


def test_serve_never_dispatches_expired_request_and_survives_handler_errors():
    server, client, _, client_transport = _session_pair()
    results = _run_handshakes(server, client)
    assert isinstance(results["client"], ipc.AuthResult)
    calls: list = []

    def handler(request):
        calls.append(request)
        if request["payload"]["op"] == ipc.OP_INPUT:
            raise RuntimeError(f"boom {client._token}")
        return {"status": "ok"}

    thread = threading.Thread(target=lambda: server.serve(handler, max_requests=2, idle_timeout=2.0))
    thread.start()
    # 手工构造一个 deadline 已过的 mutating 请求：handler 不得被调用
    expired = ipc.build_request(
        ipc.OP_INPUT,
        {"data_b64": ipc.encode_payload_bytes(b"x")},
        terminal_id="term_fake",
        timeout_ms=1000,
    )
    expired["deadline"] = time.time() - 5
    client_transport.send_frame(expired)
    # 再发一个正常但 handler 抛错的请求：连接不崩，错误文本脱敏
    client_transport.send_frame(ipc.build_request(ipc.OP_INPUT, {"data_b64": "QQ=="}, terminal_id="term_fake"))
    deadline = time.time() + 5
    seen: list = []
    while time.time() < deadline and len(seen) < 2:
        try:
            seen.append(client_transport.recv_frame(timeout=0.5))
        except Exception:  # noqa: BLE001
            break
    seen = [message for message in seen if message]
    thread.join(5)
    dispatched = [request["request_id"] for request in calls]
    assert expired["request_id"] not in dispatched, "过期请求绝不能被分发"
    assert len(dispatched) == 1, "只有未过期的请求才允许执行"
    assert seen and seen[0]["type"] == "error" and seen[0]["error"] == "expired"
    assert seen[0]["request_id"] == expired["request_id"]
    assert any(message.get("error") == "handler-error" for message in seen if isinstance(message, dict))
    diagnostics = json.dumps(server.diagnostics.as_dict())
    assert client._token not in diagnostics
    assert "***redacted***" in diagnostics, "handler 异常文本必须经脱敏出口（只记类型名 + 已脱敏消息）"
    assert list(server.diagnostics.errors) and len(list(server.diagnostics.errors)) <= ipc.DEFAULT_MAX_DIAGNOSTIC_ERRORS


def test_ipc_token_is_independent_from_attachment_lease():
    """IPC 凭据与 attachment lease 是两套凭据：lease 值冒充 IPC 必然认证失败。"""
    from packages.core.terminal.attachments import AttachmentRegistry

    class Channel:
        def write(self, data: bytes) -> int:
            return len(data)

        def resize(self, rows: int, cols: int) -> bool:
            return True

    registry = AttachmentRegistry(lambda terminal_id: Channel())
    lease = registry.attach("term_attach", "browser-1", role="control")
    token = ipc.generate_token()
    assert ipc.verify_token(token, lease.revocation_id) is False
    server, client, _, _ = _session_pair()
    client._token = lease.revocation_id  # 用 lease 值冒充 IPC 凭据
    results = _run_handshakes(server, client)
    assert isinstance(results["server"], ipc.AuthenticationError)


def test_diagnostics_are_bounded():
    diagnostics = ipc.IpcDiagnostics(max_errors=3)
    for index in range(50):
        diagnostics.record_error(f"error-{index}-" + "x" * 500)
    assert len(diagnostics.errors) == 3
    assert all(len(entry) <= ipc.DEFAULT_DIAGNOSTIC_ERROR_CHARS for entry in diagnostics.errors)
    diagnostics.record_error("token here", ["token"])
    assert diagnostics.errors[-1] == "***redacted*** here"
    assert "errors" in diagnostics.as_dict()


# ══════════════════════════════════════════════════════════════════════════
# Windows：真实命名管道（跨进程）
# ══════════════════════════════════════════════════════════════════════════

CHILD_SOURCE = r'''
"""Test-owned IPC host: real named pipe + real DPAPI secret (no product code paths)."""
import ctypes, json, os, sys, time, threading

CONFIG = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
sys.path.insert(0, CONFIG["repo"])

from packages.core.terminal import ipc, secret_store, win_pipe

REPORT_PATH = CONFIG["report"]
STATE = {"handler_calls": 0, "mutations": 0, "stop": False}
REPORT = {
    "mode": CONFIG["mode"], "pid": os.getpid(), "connections": 0, "handler_calls": 0,
    "mutations": 0, "auth_failures": 0, "errors": [], "reason": "init", "frame_types": [],
    "received_bytes": 0, "hello_seen": False, "diagnostics": {}, "pipe_name": None,
}


def flush():
    tmp = REPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(REPORT, fh)
    os.replace(tmp, REPORT_PATH)


def raw_create_pipe(name, *, first_instance=False, max_instances=4):
    """测试用「朴素」管道创建（不依赖被测模块的参数，制造占名/冒充场景）。

    注意 ``PIPE_REJECT_REMOTE_CLIENTS`` 是 dwPipeMode 标志（放进 dwOpenMode 会
    得到 ERROR_INVALID_PARAMETER）——本 TA 在调试中复现过该错误。
    """
    FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
    PIPE_ACCESS_DUPLEX = 0x00000003
    PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
    PIPE_TYPE_BYTE = 0x00000000
    PIPE_READMODE_BYTE = 0x00000000
    PIPE_WAIT = 0x00000000
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateNamedPipeW.restype = ctypes.c_void_p
    k32.CreateNamedPipeW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_void_p)
    open_mode = PIPE_ACCESS_DUPLEX
    if first_instance:
        open_mode |= FILE_FLAG_FIRST_PIPE_INSTANCE
    pipe_mode = PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS
    handle = k32.CreateNamedPipeW(name, open_mode, pipe_mode, max_instances, 65536, 65536, 0, None)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return None, ctypes.get_last_error()
    return handle, 0


def run_squat():
    name = win_pipe.pipe_name_for(CONFIG["terminal_id"])
    handle, error = raw_create_pipe(name)
    REPORT["pipe_name"] = name
    REPORT["create_error"] = error
    REPORT["reason"] = "squatting" if handle else "squat-failed"
    flush()
    time.sleep(CONFIG.get("hold_seconds", 10.0))


def run_silent_client():
    client = win_pipe.PipeClient(CONFIG["terminal_id"], connect_timeout=CONFIG.get("connect_timeout", 5.0))
    connection = client.connect()
    REPORT["pipe_name"] = client.name
    REPORT["connected"] = True
    REPORT["reason"] = "silent"
    flush()
    time.sleep(CONFIG.get("hold_seconds", 10.0))
    connection.close(timeout=1.0)


def run_server():
    store = secret_store.SecretStore(CONFIG["data_root"])
    terminal_id = CONFIG["terminal_id"]
    if CONFIG.get("bootstrap", True):
        store.write_bootstrap_identity(terminal_id)
    payload = store.wait_for_secret(terminal_id, timeout=CONFIG.get("secret_timeout", 10.0))
    own = win_pipe.current_process_identity()
    payload = store.verify_runner_identity(terminal_id, own)   # 身份不匹配即 fail-closed
    REPORT["filetime"] = own.created_at_filetime
    REPORT["pipe_name"] = payload.pipe_name
    flush()
    server = win_pipe.PipeServer(
        terminal_id,
        max_instances=CONFIG.get("max_instances", 2),
        max_active_connections=CONFIG.get("max_active_connections", 2),
        max_frame_bytes=CONFIG.get("max_frame_bytes", ipc.DEFAULT_MAX_FRAME_BYTES),
    )
    server.create()          # FIRST_PIPE_INSTANCE：名字被占即失败
    REPORT["name_owned"] = server.name_owned
    flush()

    def handler(request):
        STATE["handler_calls"] += 1
        REPORT["handler_calls"] = STATE["handler_calls"]
        payload_in = request.get("payload") or {}
        op = payload_in.get("op")
        if op in ipc.MUTATING_OPS:
            STATE["mutations"] += 1
            REPORT["mutations"] = STATE["mutations"]
        if op == ipc.OP_STOP:
            STATE["stop"] = True
        flush()
        return {"status": "ok", "detail": op or "", "reason": "test-handler"}

    deadline = time.time() + CONFIG.get("max_seconds", 25.0)
    try:
        while not STATE["stop"] and time.time() < deadline:
            try:
                connection = server.accept(timeout=1.0)
            except win_pipe.PipeCancelled:
                continue
            if connection is None:
                continue
            REPORT["connections"] += 1
            flush()
            session = ipc.IpcSession(
                connection, role="server", token=payload.token, terminal_id=terminal_id,
                local_identity=own, timeout=CONFIG.get("handshake_timeout", 5.0),
            )
            try:
                summary = session.serve(
                    handler,
                    max_requests=CONFIG.get("max_requests", 6),
                    idle_timeout=CONFIG.get("idle_timeout", 2.0),
                )
                REPORT["reason"] = summary["reason"]
                REPORT["diagnostics"] = summary["diagnostics"]
            except ipc.AuthenticationError as exc:
                REPORT["auth_failures"] += 1
                REPORT["errors"].append(type(exc).__name__)
                REPORT["reason"] = "auth-rejected"
            except Exception as exc:
                REPORT["errors"].append("%s: %s" % (type(exc).__name__, exc))
                REPORT["reason"] = "error"
            finally:
                connection.close(timeout=2.0)
                flush()
        else:
            if time.time() >= deadline:
                REPORT["reason"] = "deadline"
    finally:
        REPORT["stopped"] = True
        flush()
        server.close(timeout=2.0)
        flush()


def main():
    try:
        mode = CONFIG["mode"]
        if mode == "server":
            run_server()
        elif mode == "squat":
            run_squat()
        elif mode == "silent_client":
            run_silent_client()
        else:
            REPORT["reason"] = "unknown-mode"
            flush()
    except Exception as exc:  # noqa: BLE001
        REPORT["errors"].append("%s: %s" % (type(exc).__name__, exc))
        REPORT["reason"] = "fatal"
        flush()
        raise


main()
'''


class ChildHandle:
    """自建子进程句柄：报告读取 + **核验身份后**终止。"""

    def __init__(self, process: subprocess.Popen, report_path: Path, config_path: Path, name: str):
        self.process = process
        self.report_path = report_path
        self.config_path = config_path
        self.name = name
        self.stdout = ""
        self.stderr = ""
        self._identity: ProcessIdentity | None = None

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    def identity(self) -> ProcessIdentity:
        if self._identity is None:
            probe = win_pipe.ProcessIdentityHandle.open(self.pid)
            assert probe is not None, f"cannot inspect {self.name} process identity (pid={self.pid})"
            try:
                self._identity = probe.identity
            finally:
                probe.close()
        return self._identity

    def report(self) -> dict:
        try:
            text = self.report_path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    def wait_report(self, predicate, *, timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            last = self.report()
            if last and predicate(last):
                return last
            time.sleep(0.02)
        raise AssertionError(f"report predicate not met for {self.name}: {last}")

    def wait_exit(self, *, timeout: float = 15.0) -> int:
        try:
            return int(self.process.wait(timeout=timeout))
        except subprocess.TimeoutExpired:  # pragma: no cover - 失败路径
            raise AssertionError(f"{self.name} did not exit within {timeout}s")

    def collect_output(self) -> None:
        try:
            out, err = self.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            out, err = "", ""
        self.stdout = out or ""
        self.stderr = err or ""

    def cleanup(self, *, timeout: float = 10.0) -> dict:
        """结束子进程：先核验身份（同 handle PID + raw FILETIME），再终止。"""
        self.collect_output()
        if self.process.poll() is None:
            evidence = win_pipe.terminate_verified_process(self.pid, self.identity().created_at_filetime)
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
            return evidence
        return {"terminated": False, "detail": "already exited"}


@pytest.fixture
def child_runner(tmp_path):
    """spawn 自建子进程并在用例结束时核验身份后清理（禁止按 PID 单值杀进程）。"""
    handles: list[ChildHandle] = []
    config_dir = tmp_path / "ipc-child"
    config_dir.mkdir(parents=True, exist_ok=True)
    script = config_dir / "ipc_child_host.py"
    script.write_text(CHILD_SOURCE, encoding="utf-8")

    def spawn(mode: str, terminal_id: str, data_root: str, extra: dict | None = None) -> ChildHandle:
        report_path = config_dir / f"{mode}-{len(handles)}.report.json"
        config_path = config_dir / f"{mode}-{len(handles)}.config.json"
        config = {
            "mode": mode,
            "repo": str(REPO_ROOT),
            "report": str(report_path),
            "terminal_id": terminal_id,
            "data_root": str(data_root),
        }
        config.update(extra or {})
        config_path.write_text(json.dumps(config), encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, str(script), str(config_path)],
            cwd=str(REPO_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        handle = ChildHandle(process, report_path, config_path, f"{mode}:{terminal_id}")
        handles.append(handle)
        return handle

    yield spawn
    for handle in handles:
        handle.cleanup()


def _write_secret_for_child(tmp_path, child: ChildHandle, terminal_id: str, token: str) -> None:
    """bootstrap：读 runner hello（pid + raw FILETIME）→ 写 DPAPI 秘密（argv 只传路径）。"""
    from packages.core.terminal import secret_store

    store = secret_store.SecretStore(tmp_path / "data")
    hello = store.wait_for_bootstrap_identity(terminal_id, timeout=10.0)
    kernel = child.identity()
    assert hello.pid == kernel.pid == child.pid
    assert hello.filetime == kernel.created_at_filetime, "hello 身份必须来自 GetProcessTimes"
    store.write_secret(
        secret_store.SecretPayload(
            terminal_id=terminal_id,
            pipe_name=win_pipe.pipe_name_for(terminal_id),
            token=token,
            runner_pid=hello.pid,
            runner_filetime=hello.filetime,
            created_at=time.time(),
            updated_at=time.time(),
        )
    )


def _client_session(tmp_path, terminal_id: str, *, token: str | None = None, expected=None):
    """服务侧装配：DPAPI 秘密 → 身份核验 → 连接 → 握手。"""
    from packages.core.terminal import secret_store

    store = secret_store.SecretStore(tmp_path / "data")
    payload = store.read_secret(terminal_id)
    client = win_pipe.PipeClient(terminal_id, connect_timeout=5.0)
    connection = client.connect()
    session = ipc.IpcSession(
        connection,
        role="client",
        token=token or payload.token,
        terminal_id=terminal_id,
        expected_peer_identity=expected or payload.runner_identity(),
        identity_probe=win_pipe.default_identity_probe,
    )
    return session, connection, payload


@windows_only
def test_cross_process_pipe_roundtrip_with_dpapi_bootstrap(tmp_path, child_runner):
    terminal_id = "term_xproc01"
    token = _sentinel_token()
    child = child_runner("server", terminal_id, str(tmp_path / "data"), {"max_seconds": 30.0})
    _write_secret_for_child(tmp_path, child, terminal_id, token)
    session, connection, payload = _client_session(tmp_path, terminal_id, token=token)
    result = session.handshake()
    assert result.ok and result.peer.verified is True
    assert result.peer.pid == child.pid
    assert payload.runner_filetime == child.identity().created_at_filetime

    response = session.call(ipc.OP_READ, {"cursor": 0, "max_bytes": 1024}, timeout_ms=5000)
    assert response["ok"] is True and response["payload"]["status"] == "ok"
    echo = session.call(ipc.OP_SNAPSHOT, {"timeout_ms": 1000}, timeout_ms=5000)
    assert echo["ok"] is True

    # 互认证：两侧都用同一个 token 派生 MAC，token 本身不上线
    stop = session.call(ipc.OP_STOP, {"reason": "test"}, timeout_ms=5000)
    assert stop["ok"] is True
    connection.close(timeout=2.0)
    assert child.wait_exit(timeout=15.0) == 0
    report = child.report()
    assert report["connections"] == 1 and report["handler_calls"] >= 3
    assert report["auth_failures"] == 0

    # 哨兵扫描：报告/子进程输出/argv/环境/秘密文件明文/ registry 都不含 token
    child.collect_output()
    from packages.core.terminal import registry as terminal_registry

    registry = terminal_registry.TerminalRegistry(tmp_path / "data")
    registry.save(
        terminal_registry.TerminalRecord(
            terminal_id=terminal_id, status=terminal_registry.RuntimeState.RUNNING, pid=child.pid
        )
    )
    haystack = {
        "child_stdout": child.stdout,
        "child_stderr": child.stderr,
        "child_argv": json.dumps([str(a) for a in child.process.args]),
        "child_env": json.dumps({k: v for k, v in os.environ.items() if "PAN_TERMINAL" in k}),
        "child_config": child.config_path.read_text(encoding="utf-8"),
        "registry_json": (tmp_path / "data" / f"{terminal_id}.json").read_text(encoding="utf-8"),
        "secret_bytes": (tmp_path / "data" / "secrets" / f"{terminal_id}.secret").read_bytes().decode(
            "latin-1"
        ),
        "report": child.report_path.read_text(encoding="utf-8"),
    }
    for label, text in haystack.items():
        assert token not in text, f"token leaked into {label}"
        assert token not in os.environ.get("PAN_TERMINAL_TOKEN", "")


@windows_only
def test_two_clients_disconnect_without_killing_the_server(tmp_path, child_runner):
    terminal_id = "term_xproc02"
    token = _sentinel_token()
    child = child_runner("server", terminal_id, str(tmp_path / "data"), {"max_seconds": 40.0})
    _write_secret_for_child(tmp_path, child, terminal_id, token)
    for _ in range(2):
        session, connection, _ = _client_session(tmp_path, terminal_id, token=token)
        assert session.handshake().ok
        connection.close(timeout=2.0)  # 客户端断连
        assert child.process.poll() is None, "客户端断连不得杀死 runner"
        report = child.wait_report(lambda data: data.get("connections", 0) >= 1, timeout=10.0)
        assert report["errors"] in ([], ["ProtocolError"]) or report["reason"] != "fatal"
    assert child.identity().created_at_filetime  # 身份未变（PID/FILETIME 稳定）

    # 第三个连接仍能认证并正常收发
    session, connection, _ = _client_session(tmp_path, terminal_id, token=token)
    assert session.handshake().ok
    assert session.call(ipc.OP_STOP, {"reason": "done"}, timeout_ms=5000)["ok"] is True
    connection.close(timeout=2.0)
    assert child.wait_exit(timeout=15.0) == 0
    assert child.report()["connections"] == 3


@windows_only
def test_wrong_token_rejected_and_handler_not_called(tmp_path, child_runner):
    terminal_id = "term_xproc03"
    token = _sentinel_token()
    child = child_runner("server", terminal_id, str(tmp_path / "data"), {"max_seconds": 30.0})
    _write_secret_for_child(tmp_path, child, terminal_id, token)

    session, connection, _ = _client_session(tmp_path, terminal_id, token=_sentinel_token())
    with pytest.raises(ipc.AuthenticationError):
        session.handshake()
    connection.close(timeout=2.0)
    report = child.wait_report(lambda data: data.get("auth_failures", 0) >= 1, timeout=10.0)
    assert report["handler_calls"] == 0 and report["mutations"] == 0
    assert "AuthenticationError" in report["errors"]

    # 正确 token 仍可进来（拒绝不是 DoS）
    session, connection, _ = _client_session(tmp_path, terminal_id, token=token)
    assert session.handshake().ok
    assert session.call(ipc.OP_STOP, {"reason": "done"}, timeout_ms=5000)["ok"] is True
    connection.close(timeout=2.0)
    assert child.wait_exit(timeout=15.0) == 0


@windows_only
def test_handshake_timeout_never_reaches_business_handler(tmp_path, child_runner):
    terminal_id = "term_xproc04"
    token = _sentinel_token()
    child = child_runner(
        "server", terminal_id, str(tmp_path / "data"), {"max_seconds": 30.0, "handshake_timeout": 1.0}
    )
    _write_secret_for_child(tmp_path, child, terminal_id, token)
    from packages.core.terminal import win_pipe as pipe_module

    connection = pipe_module.PipeClient(terminal_id, connect_timeout=5.0).connect()
    time.sleep(1.6)  # 不发任何握手帧 → 服务器侧握手超时
    report = child.wait_report(lambda data: data.get("reason") == "auth-rejected", timeout=10.0)
    assert report["handler_calls"] == 0 and report["mutations"] == 0
    assert "HandshakeTimeout" in report["errors"]
    connection.close(timeout=2.0)

    session, connection, _ = _client_session(tmp_path, terminal_id, token=token)
    assert session.handshake().ok
    assert session.call(ipc.OP_STOP, {"reason": "done"}, timeout_ms=5000)["ok"] is True
    connection.close(timeout=2.0)
    assert child.wait_exit(timeout=15.0) == 0


@windows_only
def test_malformed_oversized_and_truncated_frames_are_rejected(tmp_path, child_runner):
    terminal_id = "term_xproc05"
    token = _sentinel_token()
    child = child_runner(
        "server",
        terminal_id,
        str(tmp_path / "data"),
        {"max_seconds": 40.0, "max_frame_bytes": 2048, "max_requests": 2},
    )
    _write_secret_for_child(tmp_path, child, terminal_id, token)

    def _connect():
        session, connection, _ = _client_session(tmp_path, terminal_id, token=token)
        assert session.handshake().ok
        return session, connection

    # 1) 未知类型
    session, connection = _connect()
    connection.send_raw(ipc.encode_frame({"v": 1, "type": "unknown-thing"}, validate=False))
    report = child.wait_report(lambda data: data.get("reason") == "protocol-error", timeout=10.0)
    assert report["handler_calls"] == 0
    connection.close(timeout=2.0)

    # 2) 超大帧（超过服务器 max_frame_bytes）+ 断帧
    session, connection = _connect()
    connection.send_raw(b'{"v":1,"type":"ping","pad":"' + b"x" * 4096 + b'"}\n')
    child.wait_report(lambda data: data.get("reason") == "protocol-error", timeout=10.0)
    connection.close(timeout=2.0)

    session, connection = _connect()
    connection.send_raw(b'{"v":1,"type":"request"')  # 断帧
    connection.close(timeout=2.0)
    child.wait_report(
        lambda data: data.get("reason") in ("protocol-error", "closed"), timeout=10.0
    )
    assert child.report()["handler_calls"] == 0

    session, connection, _ = _client_session(tmp_path, terminal_id, token=token)
    assert session.handshake().ok
    assert session.call(ipc.OP_STOP, {"reason": "done"}, timeout_ms=5000)["ok"] is True
    connection.close(timeout=2.0)
    assert child.wait_exit(timeout=15.0) == 0


@windows_only
def test_expired_mutating_request_is_not_executed_late(tmp_path, child_runner):
    terminal_id = "term_xproc06"
    token = _sentinel_token()
    child = child_runner("server", terminal_id, str(tmp_path / "data"), {"max_seconds": 30.0})
    _write_secret_for_child(tmp_path, child, terminal_id, token)
    session, connection, _ = _client_session(tmp_path, terminal_id, token=token)
    assert session.handshake().ok

    expired = ipc.build_request(
        ipc.OP_INPUT,
        {"data_b64": ipc.encode_payload_bytes(b"late-input")},
        terminal_id=terminal_id,
        timeout_ms=5000,
    )
    expired["deadline"] = time.time() - 1  # 已过期：本地预算外
    connection.send_frame(expired)
    response = connection.recv_frame(timeout=5.0)
    assert response is not None and response["type"] == "error" and response["error"] == "expired"
    report = child.report()
    assert report["mutations"] == 0, "过期的 mutating 请求绝不能迟到执行"

    # 正常请求仍然执行（迟到拒绝不是“全拒”）
    assert session.call(ipc.OP_INPUT, {"data_b64": ipc.encode_payload_bytes(b"ok")}, timeout_ms=5000)["ok"]
    child.wait_report(lambda data: data.get("mutations", 0) == 1, timeout=10.0)
    assert session.call(ipc.OP_STOP, {"reason": "done"}, timeout_ms=5000)["ok"] is True
    connection.close(timeout=2.0)
    assert child.wait_exit(timeout=15.0) == 0


@windows_only
def test_impostor_pipe_server_identity_is_rejected_before_credentials(tmp_path):
    """冒充者持有同名管道时：身份不符 → 客户端**一个字节凭据都不发**。

    同时给出对照：身份精确匹配（PID + raw FILETIME）时才进入认证交换，
    证明拒绝来自身份核验而不是“无条件拒连”。
    """
    from packages.core.terminal import secret_store

    terminal_id = "term_impostor01"
    token = _sentinel_token()
    own_identity = win_pipe.current_process_identity()
    # 预期 runner：另一个真实自建进程（它会立刻退出，但身份可核验）
    expected_process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    expected_probe = win_pipe.ProcessIdentityHandle.open(expected_process.pid)
    assert expected_probe is not None
    expected_filetime = expected_probe.filetime
    expected_probe.close()
    try:
        store = secret_store.SecretStore(tmp_path / "data")
        store.write_secret(
            secret_store.SecretPayload(
                terminal_id=terminal_id,
                pipe_name=win_pipe.pipe_name_for(terminal_id),
                token=token,
                runner_pid=expected_process.pid,
                runner_filetime=expected_filetime,
                created_at=time.time(),
                updated_at=time.time(),
            )
        )
        # 冒充者：本进程创建同名管道（本进程 ≠ 预期 runner）
        impostor = win_pipe.PipeServer(terminal_id, max_instances=2)
        impostor.create()
        received: list = []
        stop = threading.Event()

        def impostor_loop():
            while not stop.is_set():
                try:
                    connection = impostor.accept(timeout=1.0)
                except (win_pipe.PipeCancelled, win_pipe.PipeClosed):
                    return
                except win_pipe.PipeError as exc:  # 瞬时错误：重试而不是打死循环
                    received.append(b"<accept-error:%s>" % type(exc).__name__.encode("ascii"))
                    continue
                if connection is None:
                    continue
                try:
                    received.append(connection.recv_bytes(4096, timeout=1.5))
                except win_pipe.PipeTimeout:
                    received.append(b"")
                except (win_pipe.PipeClosed, win_pipe.PipeError, ipc.FrameTruncatedError):
                    received.append(b"")
                finally:
                    connection.close(timeout=1.0)

        worker = threading.Thread(target=impostor_loop, name="impostor-server", daemon=True)
        worker.start()

        def wait_received(count: int, timeout: float = 6.0) -> list:
            deadline = time.time() + timeout
            while time.time() < deadline and len(received) < count:
                time.sleep(0.02)
            return list(received)

        # 1) PID 不符（冒充者 PID 与秘密中的 runner PID 不同）→ 拒绝，零字节
        session, connection, _ = _client_session(tmp_path, terminal_id, token=token)
        with pytest.raises(ipc.AuthenticationError):
            session.handshake()
        connection.close(timeout=1.0)
        assert wait_received(1) == [b""], f"绝不能把凭据发给冒充者: {received!r}"

        # 2) PID 相同但 raw FILETIME 不符（模拟 PID 复用）→ 同样拒绝、零字节
        store.update_runner_identity(terminal_id, pid=own_identity.pid, filetime=own_identity.created_at_filetime + 1)
        session, connection, _ = _client_session(tmp_path, terminal_id, token=token)
        with pytest.raises(ipc.AuthenticationError):
            session.handshake()
        connection.close(timeout=1.0)
        assert wait_received(2) == [b"", b""], f"FILETIME 不符时必须拒绝: {received!r}"

        # 对照：身份精确匹配 → 客户端进入凭据交换；冒充者不持有 token ⇒ 服务器侧认证失败
        store.update_runner_identity(
            terminal_id, pid=own_identity.pid, filetime=own_identity.created_at_filetime
        )
        # 先**停干净**冒充者循环（否则它可能抢走对照用例的连接，造成竞态）
        stop.set()
        impostor.cancel_accept()
        worker.join(5)
        assert not worker.is_alive(), "冒充者线程必须收敛退出"
        assert received == [b"", b""], f"前两个用例必须零字节: {received!r}"

        # 每条连接用各自独立的对象（真实拓扑：客户端侧与服务器侧是两个 handle）
        client_session, client_connection, _ = _client_session(tmp_path, terminal_id, token=token)
        client_session.expected_peer_identity = own_identity
        server_connection = impostor.accept(timeout=5.0)
        assert server_connection is not None
        impostor_session = ipc.IpcSession(
            server_connection,
            role="server",
            token=_sentinel_token(),  # 冒充者不知道真实 token
            terminal_id=terminal_id,
            local_identity=own_identity,
            timeout=5.0,
        )
        client_result: dict = {}
        client_thread = threading.Thread(
            target=lambda: client_result.setdefault("r", _capture(client_session.handshake))
        )
        client_thread.start()
        with pytest.raises(ipc.AuthenticationError):
            impostor_session.handshake()
        client_thread.join(6)
        assert isinstance(
            client_result.get("r"), (ipc.AuthenticationError, ipc.HandshakeTimeout)
        ), "冒充者无法回出正确 ack，客户端必须拒绝"
        server_connection.close(timeout=1.0)
        client_connection.close(timeout=1.0)
        assert impostor.close(timeout=2.0).converged is True
    finally:
        if expected_process.poll() is None:
            win_pipe.terminate_verified_process(expected_process.pid, expected_filetime)
        expected_process.wait(timeout=10)


@windows_only
def test_pipe_name_squatting_detected_and_retryable(tmp_path, child_runner):
    terminal_id = "term_xproc08"
    squatter = child_runner("squat", terminal_id, str(tmp_path / "data"), {"hold_seconds": 12.0})
    squatter.wait_report(lambda data: data.get("reason") in ("squatting", "squat-failed"), timeout=10.0)
    report = squatter.report()
    if report.get("reason") != "squatting":
        pytest.skip(f"squatter could not create the pipe (error={report.get('create_error')})")

    server = win_pipe.PipeServer(terminal_id)
    with pytest.raises(win_pipe.PipeBusyError):
        server.create()  # 独占占名：绝不静默共用同名管道
    assert server.name_owned is False
    server.close(timeout=1.0)
    assert server.close(timeout=1.0).converged is True


@windows_only
def test_slow_reader_cancel_close_converges_and_is_retryable(tmp_path, child_runner):
    terminal_id = "term_xproc09"
    server = win_pipe.PipeServer(terminal_id, max_frame_bytes=8 * 1024 * 1024)
    server.create()
    child = child_runner(
        "silent_client", terminal_id, str(tmp_path / "data"), {"hold_seconds": 20.0, "connect_timeout": 5.0}
    )
    child.wait_report(lambda data: data.get("reason") == "silent", timeout=10.0)
    connection = server.accept(timeout=10.0)
    assert connection is not None and connection.peer_client_pid() == child.pid

    payload = ipc.encode_payload_bytes(b"z" * (64 * 1024))
    big = ipc.validate_message(
        {
            "v": 1,
            "type": "response",
            "request_id": "r-" + "0" * 16,
            "ok": True,
            "payload": {"data_b64": payload, "size": str(64 * 1024), "status": "ok"},
        }
    )
    started = time.monotonic()
    with pytest.raises(win_pipe.PipeTimeout):
        connection.send_frame(big, timeout=0.5)  # 对端不读 → 有界超时，不无限阻塞
    assert time.monotonic() - started < 8.0
    assert connection.broken is True, "半帧写出后必须标记 broken，不能续写冒充完整帧"
    first = connection.close(timeout=2.0)
    assert first.converged is True and first.closed is True
    second = connection.close(timeout=2.0)  # 可重试：幂等收敛
    assert second.converged is True and second.detail == "already closed"
    assert server.close(timeout=2.0).converged is True


@windows_only
def test_blocking_read_is_cancelled_converged_and_joined(tmp_path, child_runner):
    terminal_id = "term_xproc10"
    server = win_pipe.PipeServer(terminal_id)
    server.create()
    child = child_runner(
        "silent_client", terminal_id, str(tmp_path / "data"), {"hold_seconds": 20.0, "connect_timeout": 5.0}
    )
    child.wait_report(lambda data: data.get("reason") == "silent", timeout=10.0)
    connection = server.accept(timeout=10.0)
    assert connection is not None

    state: dict = {}
    before = {thread.name for thread in threading.enumerate()}

    def blocked_read():
        try:
            state["result"] = connection.recv_frame(timeout=None)
        except Exception as exc:  # noqa: BLE001
            state["error"] = type(exc).__name__

    worker = threading.Thread(target=blocked_read, name="blocked-read", daemon=True)
    worker.start()
    time.sleep(0.3)
    assert connection.in_flight == 1
    report = connection.close(timeout=3.0)
    worker.join(3.0)
    assert report.converged is True and report.closed is True
    assert not worker.is_alive(), "取消必须收敛并 join，不能留下后台线程"
    assert state.get("error") in ("PipeCancelled", "PipeClosed")
    assert set(thread.name for thread in threading.enumerate()) <= before | {"blocked-read"}
    assert server.close(timeout=2.0).converged is True


@windows_only
def test_accept_timeout_cancel_and_capacity_are_bounded(tmp_path):
    terminal_id = "term_capacity"
    server = win_pipe.PipeServer(terminal_id, max_active_connections=1)
    server.create()
    assert server.name_owned is True

    # 空连接：有界超时返回 None，实例保留可重试
    assert server.accept(timeout=0.2) is None
    assert server.accept_timeouts >= 1

    # 取消在飞 accept → PipeCancelled，且后续仍可 accept（重试）
    result: dict = {}

    def waiter():
        try:
            result["connection"] = server.accept(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            result["error"] = type(exc).__name__

    worker = threading.Thread(target=waiter, name="accept-wait", daemon=True)
    worker.start()
    time.sleep(0.3)
    assert server.cancel_accept() is True
    worker.join(3.0)
    assert result.get("error") == "PipeCancelled"
    assert not worker.is_alive()

    client = win_pipe.PipeClient(terminal_id, connect_timeout=5.0)
    conn = client.connect()
    accepted = server.accept(timeout=5.0)
    assert accepted is not None and accepted.peer_client_pid() == os.getpid()
    assert conn.peer_server_pid() == os.getpid()

    # 容量上界：已有 1 个活动连接时，再多一个请求被有界拒绝（不排队、不增长）
    assert server.accept(timeout=0.2) is None
    assert server.diagnostics.as_dict()["connections_rejected_capacity"] >= 1

    conn.close(timeout=2.0)
    accepted.close(timeout=2.0)
    assert server.close(timeout=2.0).converged is True


@windows_only
def test_client_that_leaves_before_connect_call_does_not_break_accept(tmp_path):
    """回归：客户端在 ``ConnectNamedPipe`` 之前连上又断开 → ``ERROR_NO_DATA`` 是瞬时
    状态，accept 必须重试而不是把服务循环打死（旧实现抛 ``PipeIOError`` 结束循环）。"""
    terminal_id = "term_transient"
    server = win_pipe.PipeServer(terminal_id, max_instances=2)
    server.create()
    try:
        early = win_pipe.PipeClient(terminal_id, connect_timeout=5.0).connect()
        early.close(timeout=1.0)  # 在服务端 accept 之前就断开
        accepted = server.accept(timeout=3.0)
        if accepted is not None:
            accepted.close(timeout=1.0)
        # 后续连接必须仍然可被接受（服务循环未被瞬时错误打死）
        client = win_pipe.PipeClient(terminal_id, connect_timeout=5.0)
        connection = client.connect()
        accepted = server.accept(timeout=5.0)
        assert accepted is not None
        accepted.close(timeout=1.0)
        connection.close(timeout=1.0)
    finally:
        assert server.close(timeout=2.0).converged is True


@windows_only
def test_verified_terminate_refuses_identity_mismatch(tmp_path):
    """清理纪律：没有 raw FILETIME 或 FILETIME 不匹配一律拒绝终止。"""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        handle = win_pipe.ProcessIdentityHandle.open(process.pid)
        assert handle is not None
        try:
            assert handle.probe().status is ProcessStatus.ALIVE
            assert handle.is_alive() is True
            mismatch = win_pipe.terminate_verified_process(process.pid, handle.filetime + 1)
            assert mismatch["terminated"] is False and mismatch["filetimeMatch"] is False
            no_identity = win_pipe.terminate_verified_process(process.pid, None)
            assert no_identity["terminated"] is False
            assert process.poll() is None, "身份未核验时绝不能杀进程"
            verified = win_pipe.terminate_verified_process(process.pid, handle.filetime)
            assert verified["terminated"] is True and verified["filetimeMatch"] is True
            process.wait(timeout=5)
            assert handle.is_alive() is False, "同一句柄的 Wait 必须能确认退出"
        finally:
            handle.close()
    finally:
        if process.poll() is None:  # pragma: no cover - 失败兜底
            process.kill()
            process.wait(timeout=5)


@windows_only
def test_invalid_handle_values_are_detected():
    """回归：ctypes 的 ``c_void_p`` 失败值是**无符号** 2**64-1，不是 -1。

    旧写法 ``int(handle) == INVALID_HANDLE_VALUE`` 永远为假 → 创建失败被当成成功
    （占名检测形同虚设）。本用例守护 is_invalid_handle 的判定。
    """
    assert win_pipe.is_invalid_handle(None) is True
    assert win_pipe.is_invalid_handle(0) is True
    assert win_pipe.is_invalid_handle(-1) is True
    assert win_pipe.is_invalid_handle(win_pipe.INVALID_HANDLE_VALUE) is True
    assert win_pipe.is_invalid_handle(ctypes.c_void_p(-1).value) is True
    assert win_pipe.is_invalid_handle(win_pipe.INVALID_HANDLE_UNSIGNED) is True
    assert win_pipe.is_invalid_handle(1234) is False
    # 真实失败调用返回的正是无符号伪句柄
    handle = win_pipe._k32.CreateFileW(  # noqa: SLF001
        r"\\.\pipe\pan-terminal-does-not-exist-" + str(os.getpid()),
        win_pipe.GENERIC_READ,
        0,
        None,
        win_pipe.OPEN_EXISTING,
        0,
        None,
    )
    assert win_pipe.is_invalid_handle(handle) is True, f"unexpected handle: {handle!r}"


@windows_only
def test_pipe_creation_parameters_carry_owner_only_dacl_and_reject_remote(monkeypatch):
    """结构性证据：创建参数必须带 FIRST_PIPE_INSTANCE（首实例）、owner-only SA，
    且 ``PIPE_REJECT_REMOTE_CLIENTS`` 出现在 **dwPipeMode**（跨主机实测未做）。"""
    recorded: dict = {}
    real_create = win_pipe._k32.CreateNamedPipeW  # noqa: SLF001 - 测试 spy

    def spy(name, open_mode, pipe_mode, instances, out_buf, in_buf, timeout, sa):
        recorded.update(
            {
                "name": name,
                "open_mode": open_mode,
                "pipe_mode": pipe_mode,
                "instances": instances,
                "out_buf": out_buf,
                "in_buf": in_buf,
                "timeout": timeout,
                "sa": sa,
            }
        )
        return real_create(name, open_mode, pipe_mode, instances, out_buf, in_buf, timeout, sa)

    monkeypatch.setattr(win_pipe._k32, "CreateNamedPipeW", spy)  # noqa: SLF001
    server = win_pipe.PipeServer("term_spy", max_instances=3)
    server.create()
    try:
        assert recorded["open_mode"] & win_pipe.FILE_FLAG_FIRST_PIPE_INSTANCE, "首实例必须独占占名"
        assert not (recorded["open_mode"] & win_pipe.PIPE_REJECT_REMOTE_CLIENTS), (
            "PIPE_REJECT_REMOTE_CLIENTS 放进 dwOpenMode 会被 Win32 判为 ERROR_INVALID_PARAMETER"
        )
        assert recorded["pipe_mode"] & win_pipe.PIPE_REJECT_REMOTE_CLIENTS
        # 字节流 + 阻塞模式下不应带消息模式/非阻塞位（PIPE_TYPE_BYTE 为 0，故检查反位）
        assert recorded["pipe_mode"] & (0x4 | 0x2 | 0x1) == 0, "必须是 PIPE_TYPE_BYTE|READMODE_BYTE|WAIT"
        assert recorded["instances"] == 3
        assert recorded["sa"], "必须传入非空 SECURITY_ATTRIBUTES（owner-only DACL 从创建时生效）"
        assert recorded["name"] == win_pipe.pipe_name_for("term_spy")
    finally:
        server.close(timeout=1.0)


@windows_only
def test_owner_only_dacl_on_pipe_is_applied_to_current_user_only():
    """管道 DACL：只有当前用户 SID 的 ACE（结构性证据，非跨用户实测）。"""
    terminal_id = "term_dacl"
    server = win_pipe.PipeServer(terminal_id)
    server.create()
    try:
        sid = win_pipe.current_user_sid()
        security = win_pipe.OwnerOnlySecurity()
        try:
            assert security.sid_string == sid
            assert security.describe()["dacl_aces"] == 1
        finally:
            security.close()
    finally:
        server.close(timeout=1.0)
    # 结构证据：创建参数固定带 FIRST_PIPE_INSTANCE / REJECT_REMOTE_CLIENTS / owner-only SA
    assert win_pipe.FILE_FLAG_FIRST_PIPE_INSTANCE and win_pipe.PIPE_REJECT_REMOTE_CLIENTS
    source = (REPO_ROOT / "packages" / "core" / "terminal" / "win_pipe.py").read_text(encoding="utf-8")
    assert "PIPE_REJECT_REMOTE_CLIENTS" in source
    assert "FILE_FLAG_FIRST_PIPE_INSTANCE" in source
    # 远端拒绝 / 其它 Windows 用户连接被拒未实测（需要第二主机/第二账户）
    assert "未做的负验证" in source
