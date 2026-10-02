"""Pan Terminal IPC 帧协议、双向认证与有界调度（平台无关，P1）。

配套模块：``win_pipe.py``（Windows 命名管道传输 + 身份核验原语）、
``secret_store.py``（DPAPI 秘密存储）。本模块**不依赖任何平台 API**，
可在任意平台导入与测试（见接口文档 §3 的纯逻辑测试分层）。

硬约束（实现与测试共同守护）：

1. **传输与协议分离**：本模块只处理字节/帧/消息，不 import ``win_pipe``；
   ``win_pipe.PipeConnection`` 通过 ``FrameTransport`` 协议接入（鸭子类型）。
2. **认证前不碰业务**：``IpcSession`` 在握手成功前拒绝收发任何业务帧；
   ``serve()`` 先握手，握手失败/超时**不调用业务 handler**（handler 只接收
   已通过认证、且**未过期**的请求）。
3. **凭据不落日志**：token 只出现在 ``SecretStore`` 的 DPAPI 密文文件与内存中；
   verify 走 ``hmac.compare_digest``；``redact``/``describe_message`` 是日志/
   异常文本的唯一出口（``data_b64``、``mac``、token 永不进入描述文本）。
4. **凭据分层**：IPC token 只用于 Pan↔runner 身份认证，**不是**浏览器
   attachment lease；lease 的 ``revocation_id``/``generation`` 与本模块无关，
   用 lease 值冒充 IPC 凭据必定认证失败（见测试与接口文档 §5）。
5. **全部有界**：单帧上界、解码后负载上界、排队操作上界、待响应请求上界、
   诊断错误条数与长度上界；超界一律**拒绝**（不截断、不静默丢弃）。
6. **精确整数**：``cursor``/``seq``/``size``/``total_bytes`` 等可能超过 2**53 的
   字段一律以**十进制字符串**上线（消费用 :func:`payload_int`），拒绝浮点，
   避免任何 Number 舍入（与 64 位身份口径一致）。
7. **不做迟到执行**：请求携带 ``deadline``（epoch 秒）与 ``timeout_ms``，
   服务端在**分发前**用 ``min(sender_deadline, now + timeout_ms)`` 复核；
   过期请求回 ``expired`` 错误而**不执行**任何 handler；客户端对超时后到达的
   响应一律丢弃并计入 ``late_responses``；超时的 mutating 请求被标记为
   **结果未知**（调用方必须重新取快照，禁止静默重试）。
7. **威胁模型如实**：管道 DACL + DPAPI 只隔离其它 Windows 用户；同一用户下
   的进程理论上都能解密/复制 token。首版信任边界 = 同一 Pan 用户，不作
   “防同用户恶意软件”的承诺（见接口文档 §7）。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .contracts import ProcessIdentity, ProcessProbe, ProcessStatus

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

#: 线协议版本。不匹配（缺失/更高/更低）一律拒绝——不做“宽容解析”。
IPC_PROTOCOL_VERSION = 1
#: MAC 域分离前缀：同一 token 在不同用途下派生的 MAC 不可互换。
IPC_MAC_DOMAIN = "pan-terminal-ipc"
#: 单帧（含换行）上界。base64 负载帧也受此限制。
DEFAULT_MAX_FRAME_BYTES = 256 * 1024
#: 解码后二进制负载上界（input 帧的原始字节）。
DEFAULT_MAX_PAYLOAD_BYTES = 128 * 1024
#: 待响应请求上界（客户端内存中有界）。
DEFAULT_MAX_PENDING_REQUESTS = 32
#: 有界操作队列上界。
DEFAULT_MAX_OPERATIONS = 32
#: 握手（challenge→hello→ack）总预算。
DEFAULT_HANDSHAKE_TIMEOUT = 5.0
#: 单次帧收发的默认预算。
DEFAULT_IO_TIMEOUT = 10.0
#: 请求默认超时（毫秒）。
DEFAULT_REQUEST_TIMEOUT_MS = 10_000
#: 请求超时上界（毫秒）——防止“永不超时”的请求把队列占满。
MAX_REQUEST_TIMEOUT_MS = 60_000
#: 诊断错误列表的条数上界。
DEFAULT_MAX_DIAGNOSTIC_ERRORS = 8
#: 单条诊断错误的字符上界。
DEFAULT_DIAGNOSTIC_ERROR_CHARS = 160

#: token 字节数（256 bit）。
TOKEN_BYTES = 32
#: nonce 字节数（challenge 新鲜度，256 bit）。
NONCE_BYTES = 32

#: 终端尺寸合法区间（行/列）。
MIN_ROWS = 1
MAX_ROWS = 512
MIN_COLS = 1
MAX_COLS = 512
#: 序号/尺寸字段的精确整数上界（2**63-1，十进制字符串传输）。
MAX_EXACT_INT = (1 << 63) - 1

REQUEST_ID_RE = re.compile(r"^r-[0-9a-f]{16}$")
_IDENT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_REDACTED = "***redacted***"


class MessageType(str, Enum):
    """帧类型。未知类型一律拒绝（``MalformedFrameError``）。"""

    CHALLENGE = "challenge"
    HELLO = "hello"
    HELLO_ACK = "hello_ack"
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"


#: 控制端点操作名（与实施计划 §2 对齐：input/read/snapshot/resize/lease/stop）。
OP_READ = "read"
OP_INPUT = "input"
OP_SNAPSHOT = "snapshot"
OP_RESIZE = "resize"
OP_LEASE = "lease"
OP_STOP = "stop"
ALL_OPS = (OP_READ, OP_INPUT, OP_SNAPSHOT, OP_RESIZE, OP_LEASE, OP_STOP)
#: 会改变终端状态的操作：超时后结果未知，禁止静默重试。
MUTATING_OPS = frozenset({OP_INPUT, OP_RESIZE, OP_LEASE, OP_STOP})


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------


class IpcError(RuntimeError):
    """IPC 层错误基类（消息文本必须已脱敏）。"""


class ProtocolError(IpcError):
    """协议违例（帧结构/版本/字段）。"""


class MalformedFrameError(ProtocolError):
    """畸形帧：非 JSON、非对象、字段缺失/越界、未知类型/操作。"""


class FrameTooLargeError(ProtocolError):
    """帧或解码后负载超过上界：拒绝（不截断、不静默丢弃）。"""


class FrameTruncatedError(ProtocolError):
    """断帧：通道在帧未完成时结束。"""


class ProtocolVersionError(ProtocolError):
    """协议版本不匹配。"""


class AuthenticationError(IpcError):
    """认证失败/拒绝（含“缺少身份核验能力”时的 fail-closed）。"""


class HandshakeTimeout(AuthenticationError):
    """握手在有界预算内未完成。"""


class TransportClosedError(IpcError):
    """传输已关闭/对端关闭。"""


class TransportTimeout(IpcError):
    """传输操作超时（不是协议错误）。"""


class RequestTimeout(IpcError):
    """请求在本地预算内没有拿到响应。"""


class OperationQueueFull(IpcError):
    """有界队列已满：拒绝而不是阻塞或静默丢弃。"""


# --------------------------------------------------------------------------
# token / MAC
# --------------------------------------------------------------------------


def generate_token() -> str:
    """生成 256 bit 随机 token（64 位十六进制字符串）。"""
    return secrets.token_hex(TOKEN_BYTES)


def generate_nonce() -> str:
    """生成 256 bit 随机 nonce（challenge 新鲜度）。"""
    return secrets.token_hex(NONCE_BYTES)


def token_bytes(token: str | bytes) -> bytes:
    """token 的字节形式（十六进制字符串或原字节）。

    非法形式抛 ``ValueError``（调用方按配置错误处理，绝不猜测）。
    """
    if isinstance(token, bytes):
        raw = bytes(token)
    elif isinstance(token, str):
        text = token.strip()
        if len(text) != TOKEN_BYTES * 2:
            raise ValueError("token must be 64 hex chars")
        try:
            raw = bytes.fromhex(text)
        except ValueError as exc:  # pragma: no cover - 长度已校验
            raise ValueError("token must be hexadecimal") from exc
    else:
        raise ValueError("token must be str or bytes")
    if len(raw) != TOKEN_BYTES:
        raise ValueError("token must be 256 bit")
    return raw


def verify_token(expected: str | bytes, presented: str | bytes | None) -> bool:
    """定长比较（``hmac.compare_digest``）。

    只用于“明文 token 比对”的降级/测试路径（loopback 原型，见计划 §6.1.7）；
    命名管道路径使用 :func:`compute_mac` / :func:`verify_mac` 的
    challenge-response，token 本身**不上线**。
    """
    if presented is None:
        return False
    try:
        expected_raw = token_bytes(expected)
        presented_raw = token_bytes(presented)
    except ValueError:
        return False
    return hmac.compare_digest(expected_raw, presented_raw)


@dataclass(frozen=True)
class MacContext:
    """认证 MAC 的绑定上下文。

    把 ``role`` / ``terminal_id`` / ``nonce`` / 对端身份（pid + raw FILETIME）
    一起绑定进 MAC：任一字段被替换（重放到新 nonce、换成别的终端、换成别的
    PID/FILETIME）都会导致校验失败。
    """

    role: str
    terminal_id: str
    nonce: str
    actor: str
    bind_pid: int | None = None
    bind_filetime: int | None = None
    extra: str = ""

    def to_bytes(self) -> bytes:
        parts = [
            IPC_MAC_DOMAIN,
            str(IPC_PROTOCOL_VERSION),
            self.role,
            self.terminal_id,
            self.nonce,
            self.actor,
            "" if self.bind_pid is None else str(int(self.bind_pid)),
            "" if self.bind_filetime is None else str(int(self.bind_filetime)),
            self.extra,
        ]
        return "|".join(parts).encode("utf-8")


def compute_mac(token: str | bytes, context: MacContext) -> str:
    """HMAC-SHA256(token, 上下文) 的十六进制字符串。"""
    return hmac.new(token_bytes(token), context.to_bytes(), hashlib.sha256).hexdigest()


def verify_mac(token: str | bytes, mac: str | None, context: MacContext) -> bool:
    """定长比较 MAC（``hmac.compare_digest``）；缺失/格式非法即 False。"""
    if not mac or not isinstance(mac, str) or len(mac) != 64:
        return False
    try:
        expected = compute_mac(token, context)
    except ValueError:
        return False
    return hmac.compare_digest(expected, mac)


# --------------------------------------------------------------------------
# 字段解析（精确整数 / base64）
# --------------------------------------------------------------------------


def parse_exact_int(
    value: Any, *, name: str, minimum: int = 0, maximum: int = MAX_EXACT_INT
) -> int:
    """解析可能超过 2**53 的整数。

    只接受 ``int`` 或十进制/十六进制**字符串**；``bool``/``float`` 一律拒绝
    （浮点会静默舍入 64 位身份值，见接口文档 §4.3）。越界同样拒绝。
    """
    if isinstance(value, bool):
        raise MalformedFrameError(f"{name} must be an integer, not bool")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise MalformedFrameError(f"{name} must not be empty")
        try:
            result = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
        except ValueError as exc:
            raise MalformedFrameError(f"{name} is not a decimal/hex integer") from exc
    else:
        raise MalformedFrameError(
            f"{name} must be an integer or decimal string (no floats: 64-bit values must stay exact)"
        )
    if result < minimum or result > maximum:
        raise MalformedFrameError(f"{name} out of range [{minimum}, {maximum}]")
    return result


def encode_exact_int(value: int) -> str:
    """精确整数的线上形式：十进制字符串（避免 JS/浮点风格的 Number 舍入）。"""
    number = int(value)
    if number < 0 or number > MAX_EXACT_INT:
        raise ValueError("value out of exact-int range")
    return str(number)


def parse_b64_bytes(value: Any, *, name: str = "data_b64", max_bytes: int | None = None) -> bytes:
    """解析并校验 base64 负载（严格 base64 + 尺寸上界）。"""
    if not isinstance(value, str):
        raise MalformedFrameError(f"{name} must be a base64 string")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise MalformedFrameError(f"{name} is not valid base64") from exc
    limit = DEFAULT_MAX_PAYLOAD_BYTES if max_bytes is None else int(max_bytes)
    if len(raw) > limit:
        raise FrameTooLargeError(f"{name} decodes to {len(raw)} bytes > limit {limit}")
    return raw


def encode_payload_bytes(data: bytes) -> str:
    """负载字节的线上形式（base64）。"""
    return base64.b64encode(bytes(data)).decode("ascii")


def payload_int(payload: Mapping[str, Any], key: str, *, default: int | None = None) -> int:
    """读取负载中的精确整数字段（线上为十进制字符串，也可能是 int）。

    消费 ``cursor``/``seq``/``size`` 等字段时用本函数，避免出现浮点中间值。
    """
    if key not in payload:
        if default is None:
            raise MalformedFrameError(f"{key} is required")
        return int(default)
    return parse_exact_int(payload.get(key), name=key)


# --------------------------------------------------------------------------
# 脱敏助手
# --------------------------------------------------------------------------


def redact(text: str, secrets_: Iterable[str | bytes] = ()) -> str:
    """把已知秘密替换成占位符（日志/异常文本出口）。"""
    result = "" if text is None else str(text)
    for secret in secrets_:
        if secret is None:
            continue
        candidate = secret.decode("utf-8", "ignore") if isinstance(secret, bytes) else str(secret)
        if candidate:
            result = result.replace(candidate, _REDACTED)
    return result


def _safe_error_text(exc: BaseException, secrets_: Iterable[str | bytes] = ()) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return redact(text, secrets_)[:DEFAULT_DIAGNOSTIC_ERROR_CHARS]


def describe_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """帧的**安全摘要**：结构元数据，绝不含负载内容/MAC/token。

    日志只允许打印本函数的返回值（``data_b64`` 只留长度，MAC 只留是否存在）。
    """
    if not isinstance(message, Mapping):
        return {"type": type(message).__name__}
    summary: dict[str, Any] = {
        "type": message.get("type"),
        "v": message.get("v"),
        "request_id": message.get("request_id"),
        "terminal_id": message.get("terminal_id"),
    }
    payload = message.get("payload")
    if isinstance(payload, Mapping):
        summary["op"] = payload.get("op")
        if "data_b64" in payload:
            raw = payload.get("data_b64")
            summary["data_b64_len"] = len(raw) if isinstance(raw, str) else None
        if "seq" in payload:
            summary["seq"] = payload.get("seq")
        if "rows" in payload:
            summary["rows"] = payload.get("rows")
            summary["cols"] = payload.get("cols")
        if "size" in payload:
            summary["size"] = payload.get("size")
    if "mac" in message:
        summary["mac_present"] = bool(message.get("mac"))
    if "nonce" in message:
        summary["nonce_present"] = bool(message.get("nonce"))
    if message.get("error") is not None:
        summary["error"] = redact(str(message.get("error")))[:DEFAULT_DIAGNOSTIC_ERROR_CHARS]
    return summary


# --------------------------------------------------------------------------
# 帧编解码
# --------------------------------------------------------------------------


def new_request_id() -> str:
    """请求 id（``r-`` + 16 位十六进制）。"""
    return f"r-{secrets.token_hex(8)}"


def decode_frame_line(line: bytes) -> dict[str, Any]:
    """单帧（不含换行）→ **已校验**的消息对象。

    解析与 schema 校验一次完成：未知类型/版本不符/字段越界/浮点序号都会在
    传输边界被拒绝（``MalformedFrameError`` / ``ProtocolVersionError``）。
    """
    if not line:
        raise MalformedFrameError("empty frame")
    if len(line) > DEFAULT_MAX_FRAME_BYTES:  # 兜底（解码器已提前拒绝）
        raise FrameTooLargeError("frame exceeds limit")
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MalformedFrameError("frame is not valid utf-8") from exc
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MalformedFrameError("frame is not valid json") from exc
    if not isinstance(obj, dict):
        raise MalformedFrameError("frame must be a json object")
    return validate_message(obj)


def encode_frame(message: Mapping[str, Any], *, validate: bool = True) -> bytes:
    """消息 → 单帧字节（紧凑 JSON + ``\\n``）。"""
    if not isinstance(message, Mapping):
        raise TypeError("message must be a mapping")
    obj: Mapping[str, Any] = validate_message(message) if validate else message
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    data = text.encode("utf-8") + b"\n"
    if len(data) > DEFAULT_MAX_FRAME_BYTES:
        raise FrameTooLargeError(f"encoded frame {len(data)} bytes exceeds limit")
    return data


class FrameDecoder:
    """有界帧解码器：``feed(bytes) -> list[dict]``。

    - 单帧（含未终止的累积）超过 ``max_frame_bytes`` 立即拒绝——缓冲不会
      随对端输入无界增长；
    - 非 UTF-8 / 非 JSON 对象 / 空帧 → ``MalformedFrameError``；
    - 首次违例后进入**粘滞失败**状态：后续 ``feed`` 直接抛同一异常（连接必须
      关闭，不做“跳过坏帧继续解析”）。
    """

    def __init__(self, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> None:
        if int(max_frame_bytes) < 16:
            raise ValueError("max_frame_bytes too small")
        self.max_frame_bytes = int(max_frame_bytes)
        self._buffer = bytearray()
        self._failure: ProtocolError | None = None

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    @property
    def failed(self) -> bool:
        return self._failure is not None

    def take_partial(self) -> bytes:
        """取走未完成的残帧（EOF 时用于判定“断帧”）。"""
        data = bytes(self._buffer)
        self._buffer.clear()
        return data

    def reset(self) -> None:
        self._buffer.clear()
        self._failure = None

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        if self._failure is not None:
            raise self._failure
        if not data:
            return []
        self._buffer.extend(data)
        frames: list[dict[str, Any]] = []
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                if len(self._buffer) > self.max_frame_bytes:
                    self._fail(FrameTooLargeError(
                        f"frame exceeds {self.max_frame_bytes} bytes without terminator"
                    ))
                break
            line = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            if len(line) > self.max_frame_bytes:
                self._fail(FrameTooLargeError(f"frame exceeds {self.max_frame_bytes} bytes"))
            if line.endswith(b"\r"):
                line = line[:-1]
            try:
                frames.append(decode_frame_line(line))
            except ProtocolError as exc:
                self._fail(exc)
        return frames

    def _fail(self, error: ProtocolError) -> None:
        self._buffer.clear()
        self._failure = error
        raise error


# --------------------------------------------------------------------------
# 消息构建与校验
# --------------------------------------------------------------------------

_ENVELOPE_KEYS: dict[MessageType, frozenset[str]] = {
    MessageType.CHALLENGE: frozenset({"v", "type", "nonce"}),
    MessageType.HELLO: frozenset({"v", "type", "mac", "client_id", "pid"}),
    MessageType.HELLO_ACK: frozenset({"v", "type", "mac", "server_id", "pid"}),
    MessageType.REQUEST: frozenset(
        {"v", "type", "request_id", "terminal_id", "payload", "timeout_ms", "deadline"}
    ),
    MessageType.RESPONSE: frozenset({"v", "type", "request_id", "ok", "payload", "error"}),
    MessageType.EVENT: frozenset({"v", "type", "payload"}),
    MessageType.ERROR: frozenset({"v", "type", "request_id", "error"}),
    MessageType.PING: frozenset({"v", "type"}),
    MessageType.PONG: frozenset({"v", "type"}),
}

_PAYLOAD_KEYS: dict[str, frozenset[str]] = {
    OP_READ: frozenset({"op", "cursor", "max_bytes"}),
    OP_INPUT: frozenset({"op", "data_b64", "seq"}),
    OP_SNAPSHOT: frozenset({"op", "timeout_ms"}),
    OP_RESIZE: frozenset({"op", "rows", "cols"}),
    OP_LEASE: frozenset({"op", "client_id", "role", "generation"}),
    OP_STOP: frozenset({"op", "reason"}),
}


def _check_ident(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not _IDENT_RE.match(value):
        raise MalformedFrameError(f"{name} must match [A-Za-z0-9_.:-]{{1,64}}")
    return value


def _validate_payload(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise MalformedFrameError("payload must be an object")
    op = payload.get("op")
    if not isinstance(op, str) or op not in _PAYLOAD_KEYS:
        raise MalformedFrameError(f"unknown op: {op!r}")
    allowed = _PAYLOAD_KEYS[op]
    unknown = set(payload) - allowed
    if unknown:
        raise MalformedFrameError(f"unknown payload fields for {op}: {sorted(unknown)}")
    normalized: dict[str, Any] = {"op": op}
    if op == OP_READ:
        normalized["cursor"] = encode_exact_int(
            parse_exact_int(payload.get("cursor", 0), name="cursor")
        )
        normalized["max_bytes"] = parse_exact_int(
            payload.get("max_bytes", DEFAULT_MAX_PAYLOAD_BYTES),
            name="max_bytes",
            minimum=1,
            maximum=DEFAULT_MAX_PAYLOAD_BYTES,
        )
    elif op == OP_INPUT:
        normalized["data_b64"] = payload.get("data_b64")
        parse_b64_bytes(normalized["data_b64"])
        if "seq" in payload:
            normalized["seq"] = encode_exact_int(
                parse_exact_int(payload.get("seq"), name="seq")
            )
    elif op == OP_SNAPSHOT:
        normalized["timeout_ms"] = parse_exact_int(
            payload.get("timeout_ms", DEFAULT_REQUEST_TIMEOUT_MS),
            name="timeout_ms",
            minimum=1,
            maximum=MAX_REQUEST_TIMEOUT_MS,
        )
    elif op == OP_RESIZE:
        normalized["rows"] = parse_exact_int(
            payload.get("rows"), name="rows", minimum=MIN_ROWS, maximum=MAX_ROWS
        )
        normalized["cols"] = parse_exact_int(
            payload.get("cols"), name="cols", minimum=MIN_COLS, maximum=MAX_COLS
        )
    elif op == OP_LEASE:
        normalized["client_id"] = _check_ident(payload.get("client_id"), name="client_id")
        role = payload.get("role")
        if role not in ("control", "observer"):
            raise MalformedFrameError("role must be 'control' or 'observer'")
        normalized["role"] = role
        if "generation" in payload:
            normalized["generation"] = parse_exact_int(
                payload.get("generation"), name="generation", maximum=(1 << 31) - 1
            )
    elif op == OP_STOP:
        reason = payload.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 64:
            raise MalformedFrameError("reason must be a string <= 64 chars")
        normalized["reason"] = reason
    return normalized


def validate_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """校验并规范化一帧；任何未知/畸形/越界都抛 ``ProtocolError`` 子类。"""
    if not isinstance(message, Mapping):
        raise MalformedFrameError("frame must be an object")
    raw_type = message.get("type")
    if not isinstance(raw_type, str):
        raise MalformedFrameError("missing message type")
    try:
        message_type = MessageType(raw_type)
    except ValueError as exc:
        raise MalformedFrameError(f"unknown message type: {raw_type!r}") from exc
    if "v" not in message:
        raise ProtocolVersionError("missing protocol version")
    version = message.get("v")
    if isinstance(version, bool) or not isinstance(version, int) or version != IPC_PROTOCOL_VERSION:
        raise ProtocolVersionError(
            f"unsupported protocol version {version!r} (expected {IPC_PROTOCOL_VERSION})"
        )
    allowed = _ENVELOPE_KEYS[message_type]
    unknown = set(message) - allowed
    if unknown:
        raise MalformedFrameError(f"unknown fields for {raw_type}: {sorted(unknown)}")

    out: dict[str, Any] = {"v": IPC_PROTOCOL_VERSION, "type": message_type.value}
    if message_type is MessageType.CHALLENGE:
        nonce = message.get("nonce")
        if not isinstance(nonce, str) or len(nonce) != NONCE_BYTES * 2:
            raise MalformedFrameError("challenge nonce must be 64 hex chars")
        bytes.fromhex(nonce)
        out["nonce"] = nonce.lower()
    elif message_type is MessageType.HELLO:
        mac = message.get("mac")
        if not isinstance(mac, str) or len(mac) != 64:
            raise MalformedFrameError("hello mac must be 64 hex chars")
        bytes.fromhex(mac)
        out["mac"] = mac.lower()
        out["client_id"] = _check_ident(message.get("client_id"), name="client_id")
        if "pid" in message:
            out["pid"] = parse_exact_int(
                message.get("pid"), name="pid", minimum=1, maximum=(1 << 31) - 1
            )
    elif message_type is MessageType.HELLO_ACK:
        mac = message.get("mac")
        if not isinstance(mac, str) or len(mac) != 64:
            raise MalformedFrameError("hello_ack mac must be 64 hex chars")
        bytes.fromhex(mac)
        out["mac"] = mac.lower()
        out["server_id"] = _check_ident(message.get("server_id"), name="server_id")
        if "pid" in message:
            out["pid"] = parse_exact_int(
                message.get("pid"), name="pid", minimum=1, maximum=(1 << 31) - 1
            )
    elif message_type is MessageType.REQUEST:
        request_id = message.get("request_id")
        if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
            raise MalformedFrameError("request_id must match r-<16 hex>")
        out["request_id"] = request_id
        out["terminal_id"] = _check_ident(message.get("terminal_id"), name="terminal_id")
        if "payload" in message:
            out["payload"] = _validate_payload(message.get("payload"))
        else:
            out["payload"] = {}
        timeout_ms = parse_exact_int(
            message.get("timeout_ms", DEFAULT_REQUEST_TIMEOUT_MS),
            name="timeout_ms",
            minimum=1,
            maximum=MAX_REQUEST_TIMEOUT_MS,
        )
        out["timeout_ms"] = timeout_ms
        if "deadline" in message:
            deadline = message.get("deadline")
            if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
                raise MalformedFrameError("deadline must be a number (epoch seconds)")
            if deadline <= 0:
                raise MalformedFrameError("deadline must be positive")
            out["deadline"] = float(deadline)
    elif message_type is MessageType.RESPONSE:
        request_id = message.get("request_id")
        if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
            raise MalformedFrameError("responses must echo a valid request_id")
        out["request_id"] = request_id
        ok = message.get("ok")
        if not isinstance(ok, bool):
            raise MalformedFrameError("response ok must be a boolean")
        out["ok"] = ok
        payload = message.get("payload")
        if payload is not None:
            if not isinstance(payload, Mapping):
                raise MalformedFrameError("response payload must be an object")
            out["payload"] = _validate_response_payload(payload)
        if message.get("error") is not None:
            out["error"] = redact(str(message.get("error")))[:DEFAULT_DIAGNOSTIC_ERROR_CHARS]
    elif message_type is MessageType.EVENT:
        payload = message.get("payload")
        if payload is not None:
            if not isinstance(payload, Mapping):
                raise MalformedFrameError("event payload must be an object")
            out["payload"] = _validate_response_payload(payload)
    elif message_type is MessageType.ERROR:
        if "request_id" in message:
            request_id = message.get("request_id")
            if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
                raise MalformedFrameError("error.request_id must match r-<16 hex>")
            out["request_id"] = request_id
        error = message.get("error")
        if not isinstance(error, str) or not error:
            raise MalformedFrameError("error message must be a non-empty string")
        out["error"] = redact(error)[:DEFAULT_DIAGNOSTIC_ERROR_CHARS]
    return out


def _validate_response_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """响应/事件负载白名单：``data_b64`` / ``seq`` / ``size`` / 尺寸与状态字段。

    负载只允许“数据 + 序号 + 尺寸 + 状态”形态，不接受任意嵌套结构（有界且可
    审计）；未知字段一律拒绝。
    """
    allowed = {
        "data_b64",
        "seq",
        "size",
        "cursor",
        "next_cursor",
        "gap",
        "truncated",
        "rows",
        "cols",
        "status",
        "detail",
        "snapshot",
        "reason",
        "total_bytes",
        "first_retained_seq",
        "ok",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise MalformedFrameError(f"unknown response fields: {sorted(unknown)}")
    out: dict[str, Any] = {}
    if "data_b64" in payload:
        out["data_b64"] = payload.get("data_b64")
        parse_b64_bytes(out["data_b64"])
    for key in ("seq", "size", "cursor", "next_cursor", "total_bytes", "first_retained_seq"):
        if key in payload:
            out[key] = encode_exact_int(parse_exact_int(payload.get(key), name=key))
    if "gap" in payload:
        gap = payload.get("gap")
        if gap is not None:
            if not isinstance(gap, Sequence) or isinstance(gap, (str, bytes)) or len(gap) != 2:
                raise MalformedFrameError("gap must be [from, to] or null")
            out["gap"] = [
                encode_exact_int(parse_exact_int(gap[0], name="gap[0]")),
                encode_exact_int(parse_exact_int(gap[1], name="gap[1]")),
            ]
        else:
            out["gap"] = None
    if "truncated" in payload:
        out["truncated"] = bool(payload.get("truncated"))
    if "ok" in payload:
        out["ok"] = bool(payload.get("ok"))
    for key in ("rows", "cols"):
        if key in payload:
            out[key] = parse_exact_int(
                payload.get(key), name=key, minimum=MIN_ROWS, maximum=MAX_ROWS
            )
    for key in ("status", "detail", "snapshot", "reason"):
        if key in payload:
            value = payload.get(key)
            if not isinstance(value, str) or len(value) > 4096:
                raise MalformedFrameError(f"{key} must be a string <= 4096 chars")
            out[key] = value
    return out


def build_challenge(nonce: str | None = None) -> dict[str, Any]:
    """服务器 → 客户端：challenge（新鲜度 nonce，不含任何秘密）。"""
    return {"v": IPC_PROTOCOL_VERSION, "type": MessageType.CHALLENGE.value, "nonce": nonce or generate_nonce()}


def build_hello(
    *, client_id: str, mac: str, pid: int | None = None
) -> dict[str, Any]:
    """客户端 → 服务器：hello（MAC 证明 token，token 本身不上线）。"""
    message: dict[str, Any] = {
        "v": IPC_PROTOCOL_VERSION,
        "type": MessageType.HELLO.value,
        "client_id": client_id,
        "mac": mac,
    }
    if pid is not None:
        message["pid"] = int(pid)
    return message


def build_hello_ack(*, server_id: str, mac: str, pid: int | None = None) -> dict[str, Any]:
    """服务器 → 客户端：ack（证明服务器同样持有 token = 双向认证）。"""
    message: dict[str, Any] = {
        "v": IPC_PROTOCOL_VERSION,
        "type": MessageType.HELLO_ACK.value,
        "server_id": server_id,
        "mac": mac,
    }
    if pid is not None:
        message["pid"] = int(pid)
    return message


def build_request(
    op: str,
    payload: Mapping[str, Any] | None = None,
    *,
    terminal_id: str,
    request_id: str | None = None,
    timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
    deadline: float | None = None,
) -> dict[str, Any]:
    """构建（并校验）一个请求帧。``deadline`` 缺省 = now + timeout_ms。"""
    body: dict[str, Any] = dict(payload or {})
    body["op"] = op
    timeout_ms = int(timeout_ms)
    message = {
        "v": IPC_PROTOCOL_VERSION,
        "type": MessageType.REQUEST.value,
        "request_id": request_id or new_request_id(),
        "terminal_id": terminal_id,
        "payload": body,
        "timeout_ms": timeout_ms,
        "deadline": float(deadline if deadline is not None else time.time() + timeout_ms / 1000.0),
    }
    return validate_message(message)


def build_response(
    request_id: str,
    payload: Mapping[str, Any] | None = None,
    *,
    ok: bool = True,
    error: str | None = None,
    secrets_: Iterable[str | bytes] = (),
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "v": IPC_PROTOCOL_VERSION,
        "type": MessageType.RESPONSE.value,
        "request_id": request_id,
        "ok": bool(ok),
    }
    if payload is not None:
        message["payload"] = dict(payload)
    if error is not None:
        message["error"] = redact(error, secrets_)[:DEFAULT_DIAGNOSTIC_ERROR_CHARS]
    return validate_message(message)


def build_error(
    error: str, *, request_id: str | None = None, secrets_: Iterable[str | bytes] = ()
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "v": IPC_PROTOCOL_VERSION,
        "type": MessageType.ERROR.value,
        "error": redact(error, secrets_)[:DEFAULT_DIAGNOSTIC_ERROR_CHARS],
    }
    if request_id is not None:
        message["request_id"] = request_id
    return validate_message(message)


def build_pong() -> dict[str, Any]:
    return {"v": IPC_PROTOCOL_VERSION, "type": MessageType.PONG.value}


# --------------------------------------------------------------------------
# 有界诊断
# --------------------------------------------------------------------------


@dataclass
class IpcDiagnostics:
    """有界诊断计数器 + 有界错误列表（线程安全）。

    错误文本一律经 :func:`redact` 截断（≤ ``DEFAULT_DIAGNOSTIC_ERROR_CHARS``），
    条目数 ≤ ``DEFAULT_MAX_DIAGNOSTIC_ERRORS``。
    """

    max_errors: int = DEFAULT_MAX_DIAGNOSTIC_ERRORS
    label: str = ""
    frames_in: int = 0
    frames_out: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    rejected_frames: int = 0
    rejected_oversize: int = 0
    rejected_unknown_type: int = 0
    auth_failures: int = 0
    handshake_timeouts: int = 0
    requests_dispatched: int = 0
    expired_dropped: int = 0
    late_responses: int = 0
    queue_rejected: int = 0
    handler_errors: int = 0
    connections_accepted: int = 0
    connections_rejected_capacity: int = 0
    errors: deque[str] = field(default_factory=lambda: deque(maxlen=DEFAULT_MAX_DIAGNOSTIC_ERRORS))

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.errors = deque(self.errors, maxlen=int(self.max_errors))

    def bump(self, field_name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, field_name, int(getattr(self, field_name)) + int(amount))

    def record_error(self, text: str, secrets_: Iterable[str | bytes] = ()) -> None:
        with self._lock:
            self.errors.append(redact(text, secrets_)[:DEFAULT_DIAGNOSTIC_ERROR_CHARS])

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            snapshot = {
                key: value
                for key, value in self.__dict__.items()
                if not key.startswith("_") and key not in ("errors",)
            }
            snapshot["errors"] = list(self.errors)
        return snapshot


# --------------------------------------------------------------------------
# 客户端待响应请求（有界；超时响应不投递）
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingRequest:
    request_id: str
    op: str
    deadline: float
    mutating: bool


class PendingRequests:
    """客户端在飞请求表：有界、可过期、拒绝投递迟到响应。"""

    def __init__(
        self,
        *,
        max_pending: int = DEFAULT_MAX_PENDING_REQUESTS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_pending = int(max_pending)
        self._clock = clock
        self._pending: dict[str, PendingRequest] = {}
        self._expired: set[str] = set()
        self._lock = threading.Lock()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def expired_count(self) -> int:
        with self._lock:
            return len(self._expired)

    def register(
        self,
        op: str,
        *,
        timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        request_id: str | None = None,
        mutating: bool | None = None,
        now: float | None = None,
    ) -> PendingRequest:
        current = self._clock() if now is None else float(now)
        entry = PendingRequest(
            request_id=request_id or new_request_id(),
            op=op,
            deadline=current + int(timeout_ms) / 1000.0,
            mutating=MUTATING_OPS.__contains__(op) if mutating is None else bool(mutating),
        )
        with self._lock:
            if len(self._pending) >= self.max_pending:
                raise OperationQueueFull(
                    f"pending request limit {self.max_pending} reached (bounded, no blocking)"
                )
            self._pending[entry.request_id] = entry
        return entry

    def expire(self, *, now: float | None = None) -> list[PendingRequest]:
        """把已超时的在飞请求移入“已放弃”集合（迟到响应用于丢弃）。"""
        current = self._clock() if now is None else float(now)
        timed_out: list[PendingRequest] = []
        with self._lock:
            for request_id, entry in list(self._pending.items()):
                if entry.deadline <= current:
                    timed_out.append(entry)
                    del self._pending[request_id]
                    self._expired.add(request_id)
        return timed_out

    def accept_response(
        self, message: Mapping[str, Any], *, now: float | None = None
    ) -> tuple[bool, str]:
        """尝试接收一个响应：``(投递?, 原因)``。

        迟到（超时后到达）与未知 request_id 一律丢弃——不对已放弃的 mutating
        请求静默执行“迟到结果”。
        """
        request_id = message.get("request_id") if isinstance(message, Mapping) else None
        if not isinstance(request_id, str):
            return False, "missing-request-id"
        current = self._clock() if now is None else float(now)
        with self._lock:
            entry = self._pending.pop(request_id, None)
            if entry is None:
                if request_id in self._expired:
                    return False, "late-response-after-timeout"
                return False, "unknown-request-id"
            if entry.deadline <= current:
                self._expired.add(request_id)
                return False, "late-response-after-timeout"
        return True, "delivered"


# --------------------------------------------------------------------------
# 服务端有界操作队列（过期请求不执行）
# --------------------------------------------------------------------------


class SubmitOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED_QUEUE_FULL = "rejected-queue-full"
    REJECTED_EXPIRED = "rejected-expired"


class RequestScheduler:
    """有界操作队列：入队校验 + 分发前过期复核（不允许迟到执行）。

    - 入队即过期 → ``REJECTED_EXPIRED``（不执行）；
    - 队列满 → ``REJECTED_QUEUE_FULL``（拒绝，不阻塞、不无界增长）；
    - ``claim`` 只返回**未过期**请求；排队期间过期的项被丢弃并计入
      ``dropped_expired``。
    """

    def __init__(
        self,
        *,
        max_operations: int = DEFAULT_MAX_OPERATIONS,
        clock: Callable[[], float] = time.time,
        timeout_ms_cap: int = MAX_REQUEST_TIMEOUT_MS,
    ) -> None:
        self.max_operations = int(max_operations)
        self._clock = clock
        self.timeout_ms_cap = int(timeout_ms_cap)
        #: 队列元素 = (入队时确定的生效 deadline, 原始请求)；deadline **不随
        #: 排队时间滑动**（否则等待越久预算越宽，等于允许迟到执行）。
        self._queue: deque[tuple[float, dict[str, Any]]] = deque()
        self.dropped_expired = 0
        self.rejected_queue_full = 0
        self.accepted = 0
        self._lock = threading.Lock()

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def effective_deadline(self, request: Mapping[str, Any], *, now: float | None = None) -> float:
        """``min(sender_deadline, now + timeout_ms)``：不信任远未来 deadline。"""
        current = self._clock() if now is None else float(now)
        timeout_ms = int(request.get("timeout_ms", DEFAULT_REQUEST_TIMEOUT_MS))
        timeout_ms = max(1, min(timeout_ms, self.timeout_ms_cap))
        local_deadline = current + timeout_ms / 1000.0
        sender_deadline = request.get("deadline")
        if isinstance(sender_deadline, (int, float)) and not isinstance(sender_deadline, bool):
            return min(float(sender_deadline), local_deadline)
        return local_deadline

    def submit(self, request: Mapping[str, Any], *, now: float | None = None) -> SubmitOutcome:
        current = self._clock() if now is None else float(now)
        deadline = self.effective_deadline(request, now=current)
        if deadline <= current:
            with self._lock:
                self.dropped_expired += 1
            return SubmitOutcome.REJECTED_EXPIRED
        with self._lock:
            if len(self._queue) >= self.max_operations:
                self.rejected_queue_full += 1
                return SubmitOutcome.REJECTED_QUEUE_FULL
            self._queue.append((deadline, dict(request)))
            self.accepted += 1
        return SubmitOutcome.ACCEPTED

    def claim(self, *, now: float | None = None) -> dict[str, Any] | None:
        """取出下一个未过期请求；过期项丢弃（handler 不会被调用）。"""
        current = self._clock() if now is None else float(now)
        with self._lock:
            while self._queue:
                deadline, request = self._queue.popleft()
                if deadline <= current:
                    self.dropped_expired += 1
                    continue
                return request
        return None

    def drain(self) -> int:
        with self._lock:
            count = len(self._queue)
            self._queue.clear()
        return count


# --------------------------------------------------------------------------
# 传输协议与握手
# --------------------------------------------------------------------------


class FrameTransport(Protocol):
    """``win_pipe.PipeConnection`` 满足的传输面（鸭子类型，本模块不 import）。"""

    def send_frame(self, message: Mapping[str, Any]) -> None: ...

    def recv_frame(self, *, timeout: float | None = None) -> dict[str, Any] | None: ...

    def peer_server_pid(self) -> int | None: ...

    def peer_client_pid(self) -> int | None: ...

    def close(self, *, timeout: float | None = None) -> Any: ...


@dataclass(frozen=True)
class PeerIdentity:
    """已验证的对端身份（raw FILETIME 口径，与 ``contracts.ProcessIdentity`` 一致）。"""

    pid: int | None
    filetime: int | None
    verified: bool
    via: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "filetime": self.filetime,
            "verified": self.verified,
            "via": self.via,
        }


@dataclass(frozen=True)
class AuthResult:
    """握手结果（``ok=False`` 时调用方不得进入业务循环）。"""

    ok: bool
    role: str
    peer: PeerIdentity
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "role": self.role,
            "peer": self.peer.as_dict(),
            "detail": self.detail,
        }


def normalize_probe(pid: int, probe: Callable[[int], Any] | None) -> ProcessProbe:
    """把 ``contracts.IdentityProbe`` 形态的返回值归一为三态 ``ProcessProbe``。

    - 裸 ``None`` / 无法归一 → ``UNKNOWN``（**不得**当作已退出/已认证）；
    - 裸 ``ProcessIdentity`` 按 ``ALIVE`` + 该身份处理；
    - 裸 ``ProcessStatus`` 按对应状态处理（无身份）。
    """
    if probe is None:
        return ProcessProbe(status=ProcessStatus.UNKNOWN, detail="no identity probe injected")
    try:
        raw = probe(int(pid))
    except Exception as exc:  # noqa: BLE001 - 探针异常按 UNKNOWN 处理（fail-closed）
        return ProcessProbe(
            status=ProcessStatus.UNKNOWN,
            detail=f"probe raised {type(exc).__name__}",
        )
    if isinstance(raw, ProcessProbe):
        return raw
    if isinstance(raw, ProcessIdentity):
        return ProcessProbe(status=ProcessStatus.ALIVE, identity=raw, detail="bare ProcessIdentity")
    if isinstance(raw, ProcessStatus):
        return ProcessProbe(status=raw, detail="bare ProcessStatus")
    return ProcessProbe(status=ProcessStatus.UNKNOWN, detail=f"unsupported probe result {type(raw).__name__}")


def _expected_identity_ready(identity: ProcessIdentity | None) -> bool:
    """身份核验需要 pid + **raw FILETIME**（只有 ``created_at`` 浮点不算）。"""
    return (
        isinstance(identity, ProcessIdentity)
        and identity.pid is not None
        and identity.created_at_filetime is not None
    )


def verify_peer_identity(
    *,
    pid: int | None,
    expected: ProcessIdentity | None,
    probe: Callable[[int], Any] | None,
    via: str,
) -> PeerIdentity:
    """同 handle 口径的对端身份核验（不含 I/O）。

    - 缺 ``expected`` / 缺 raw FILETIME / 缺探针 → 抛
      ``AuthenticationError``（fail-closed：**宁可不认证，也不向未核验对端
      发出凭据**）；
    - 探针必须给出 ``ALIVE`` + 与 ``expected`` 精确匹配（raw FILETIME 相等）的
      身份；``DEAD``/``UNKNOWN``/``MISMATCH`` 一律拒绝。
    """
    if not _expected_identity_ready(expected):
        raise AuthenticationError(
            "refusing to authenticate without an expected peer identity "
            "(pid + raw creation FILETIME)"
        )
    assert expected is not None and expected.pid is not None
    if pid is None:
        raise AuthenticationError(f"peer pid unavailable ({via})")
    if int(pid) != int(expected.pid):
        raise AuthenticationError(
            f"peer pid mismatch ({via}): pipe={int(pid)} expected={int(expected.pid)}"
        )
    if probe is None:
        raise AuthenticationError("refusing to authenticate without an identity probe")
    result = normalize_probe(int(pid), probe)
    if result.status is not ProcessStatus.ALIVE:
        raise AuthenticationError(f"peer identity probe status={result.status.value} ({via})")
    if result.identity is None:
        raise AuthenticationError(f"peer identity unavailable from probe ({via})")
    if not expected.matches(result.identity):
        raise AuthenticationError(
            "peer identity mismatch (pid + raw FILETIME) — refusing to authenticate"
        )
    return PeerIdentity(
        pid=int(pid),
        filetime=result.identity.created_at_filetime,
        verified=True,
        via=via,
    )


class IpcSession:
    """一条已连接传输上的认证会话（客户端或服务器侧）。

    关键不变量：

    - ``handshake()`` 之前，``recv_request``/``send_request``/``serve`` 全部拒绝
      （``AuthenticationError``）；
    - 客户端在**核验服务器身份之后**才发送 hello MAC；服务器在**核验 token**之后
      才回 ack 并进入业务循环；
    - 服务器侧 ``serve()`` 只把未过期请求交给 handler。
    """

    def __init__(
        self,
        transport: FrameTransport,
        *,
        role: str,
        token: str,
        terminal_id: str,
        expected_peer_identity: ProcessIdentity | None = None,
        identity_probe: Callable[[int], Any] | None = None,
        local_identity: ProcessIdentity | None = None,
        local_pid: int | None = None,
        client_id: str = "",
        server_id: str = "runner",
        timeout: float = DEFAULT_HANDSHAKE_TIMEOUT,
        diagnostics: IpcDiagnostics | None = None,
        secrets_: Iterable[str | bytes] = (),
    ) -> None:
        if role not in ("client", "server"):
            raise ValueError("role must be 'client' or 'server'")
        self.transport = transport
        self.role = role
        self.terminal_id = terminal_id
        self.expected_peer_identity = expected_peer_identity
        self.local_identity = local_identity
        self.local_pid = int(local_pid) if local_pid is not None else os.getpid()
        self.identity_probe = identity_probe
        self.client_id = client_id or (f"client-{secrets.token_hex(4)}" if role == "client" else "")
        self.server_id = server_id
        self.handshake_timeout = float(timeout)
        self.diagnostics = diagnostics if diagnostics is not None else IpcDiagnostics(label=terminal_id)
        self.pending = PendingRequests()
        # token 也进入脱敏词表：任何异常文本都不会把它带进日志。
        self._secrets = tuple(secrets_) + (token,)
        self._token = token
        token_bytes(token)  # 提前校验 token 形态（非法配置立即暴露）
        self._authenticated = False
        self._auth_failure: AuthenticationError | None = None
        self._peer = PeerIdentity(pid=None, filetime=None, verified=False)
        self.ambiguous_mutations = 0
        self._last_ambiguous_op: str | None = None
        self._lock = threading.Lock()

    # -- 状态 ----------------------------------------------------------
    @property
    def authenticated(self) -> bool:
        return self._authenticated

    @property
    def peer(self) -> PeerIdentity:
        return self._peer

    @property
    def last_ambiguous_op(self) -> str | None:
        return self._last_ambiguous_op

    def _require_authenticated(self) -> None:
        if self._auth_failure is not None:
            raise self._auth_failure
        if not self._authenticated:
            raise AuthenticationError("session is not authenticated: business frames are refused")

    # -- 握手 ----------------------------------------------------------
    def handshake(self) -> AuthResult:
        """执行握手；失败抛 ``AuthenticationError`` 子类并**永久**标记本会话不可用。"""
        with self._lock:
            if self._authenticated:
                return AuthResult(ok=True, role=self.role, peer=self._peer, detail="already authenticated")
            if self._auth_failure is not None:
                raise self._auth_failure
            try:
                result = self._client_handshake() if self.role == "client" else self._server_handshake()
            except AuthenticationError as exc:
                self._auth_failure = exc
                reason = type(exc).__name__
                if isinstance(exc, HandshakeTimeout):
                    self.diagnostics.bump("handshake_timeouts")
                self.diagnostics.bump("auth_failures")
                self.diagnostics.record_error(f"handshake rejected: {reason}: {exc}", self._secrets)
                raise
            self._authenticated = True
            self._peer = result.peer
            return result

    def _recv(self, deadline: float, *, expect: str | None = None) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HandshakeTimeout("handshake budget exhausted")
        try:
            message = self.transport.recv_frame(timeout=remaining)
        except (TransportClosedError, EOFError, OSError) as exc:
            raise AuthenticationError(f"transport closed during handshake: {type(exc).__name__}") from exc
        if message is None:
            raise HandshakeTimeout("handshake timed out waiting for peer frame")
        self.diagnostics.bump("frames_in")
        try:
            message = validate_message(message)
        except ProtocolError as exc:
            raise AuthenticationError(f"invalid handshake frame: {type(exc).__name__}") from exc
        if expect is not None and message.get("type") != expect:
            raise AuthenticationError(
                f"unexpected handshake frame: {describe_message(message).get('type')!r} (expected {expect!r})"
            )
        return message

    def _client_handshake(self) -> AuthResult:
        deadline = time.monotonic() + self.handshake_timeout
        # 1) 先核验服务器身份（同 handle：PID + raw FILETIME + Wait）——fail-fast，
        #    冒充者连 challenge 都没机会换到我们的凭据。管道连接期间服务器进程被
        #    内核引用，PID 不会被复用，故该核验无 TOCTOU 窗口。
        peer = verify_peer_identity(
            pid=self.transport.peer_server_pid(),
            expected=self.expected_peer_identity,
            probe=self.identity_probe,
            via="server-pid-probe",
        )
        challenge = self._recv(deadline, expect=MessageType.CHALLENGE.value)
        nonce = challenge["nonce"]

        server_pid = int(peer.pid) if peer.pid is not None else 0
        filetime = peer.filetime
        # 2) 身份核验通过后才交换认证：MAC 绑定 nonce + 终端 + 服务器身份。
        context = MacContext(
            role="hello",
            terminal_id=self.terminal_id,
            nonce=nonce,
            actor=self.client_id,
            bind_pid=server_pid,
            bind_filetime=filetime,
        )
        self.transport.send_frame(
            build_hello(client_id=self.client_id, mac=compute_mac(self._token, context), pid=self.local_pid)
        )
        self.diagnostics.bump("frames_out")

        ack = self._recv(deadline, expect=MessageType.HELLO_ACK.value)
        if int(ack.get("pid", -1)) != int(self.local_pid):
            raise AuthenticationError(
                "server acknowledgement is bound to another client pid — refusing"
            )
        ack_context = MacContext(
            role="ack",
            terminal_id=self.terminal_id,
            nonce=nonce,
            actor=self.client_id,
            bind_pid=int(ack["pid"]),
            bind_filetime=None,
            extra=f"server={server_pid}:{filetime}",
        )
        if not verify_mac(self._token, ack.get("mac"), ack_context):
            raise AuthenticationError("server acknowledgement MAC mismatch — not authenticated")
        return AuthResult(
            ok=True,
            role="client",
            peer=peer,
            detail=f"server verified via {peer.via}; ack mac ok",
        )

    def _server_handshake(self) -> AuthResult:
        deadline = time.monotonic() + self.handshake_timeout
        if not _expected_identity_ready(self.local_identity):
            # 服务器必须声明自身身份（pid + raw FILETIME）：否则无法与客户端
            # 核验出的身份绑定，宁可拒绝也不发出无绑定的 ack。
            raise AuthenticationError(
                "server must declare local_identity (pid + raw FILETIME) to authenticate"
            )
        assert self.local_identity is not None and self.local_identity.pid is not None
        local_pid = int(self.local_identity.pid)
        local_filetime = self.local_identity.created_at_filetime
        nonce = generate_nonce()
        self.transport.send_frame(build_challenge(nonce))
        self.diagnostics.bump("frames_out")

        hello = self._recv(deadline, expect=MessageType.HELLO.value)
        client_pid = self.transport.peer_client_pid()
        if client_pid is None:
            raise AuthenticationError("client pid unavailable from pipe — refusing to authenticate")
        context = MacContext(
            role="hello",
            terminal_id=self.terminal_id,
            nonce=nonce,
            actor=hello["client_id"],
            bind_pid=local_pid,
            bind_filetime=local_filetime,
        )
        if not verify_mac(self._token, hello.get("mac"), context):
            raise AuthenticationError("client token proof rejected")

        # 双向：本端也核验对端身份（配置了 expected 时 fail-closed）。
        if self.expected_peer_identity is not None:
            peer = verify_peer_identity(
                pid=client_pid,
                expected=self.expected_peer_identity,
                probe=self.identity_probe,
                via="client-pid-probe",
            )
        else:
            peer = PeerIdentity(
                pid=int(client_pid),
                filetime=None,
                verified=False,
                via="token-only (no expected client identity configured)",
            )
        ack_context = MacContext(
            role="ack",
            terminal_id=self.terminal_id,
            nonce=nonce,
            actor=hello["client_id"],
            bind_pid=int(client_pid),
            bind_filetime=None,
            extra=f"server={local_pid}:{local_filetime}",
        )
        self.transport.send_frame(
            build_hello_ack(
                server_id=self.server_id,
                mac=compute_mac(self._token, ack_context),
                pid=int(client_pid),
            )
        )
        self.diagnostics.bump("frames_out")
        self.client_id = hello["client_id"]
        return AuthResult(
            ok=True,
            role="server",
            peer=peer,
            detail="client token proof verified"
            + (f"; client identity verified via {peer.via}" if peer.verified else ""),
        )

    # -- 业务帧 --------------------------------------------------------
    def send_request(
        self,
        op: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        now: float | None = None,
    ) -> PendingRequest:
        """发送请求并登记到飞表（有界；超时后响应一律丢弃）。"""
        self._require_authenticated()
        entry = self.pending.register(op, timeout_ms=timeout_ms, now=now)
        message = build_request(
            op,
            payload,
            terminal_id=self.terminal_id,
            request_id=entry.request_id,
            timeout_ms=timeout_ms,
            deadline=entry.deadline,
        )
        self.transport.send_frame(message)
        self.diagnostics.bump("frames_out")
        return entry

    def recv_message(self, *, timeout: float | None = None) -> dict[str, Any] | None:
        self._require_authenticated()
        message = self.transport.recv_frame(timeout=timeout)
        if message is None:
            return None
        self.diagnostics.bump("frames_in")
        try:
            return validate_message(message)
        except ProtocolError as exc:
            if isinstance(exc, FrameTooLargeError):
                self.diagnostics.bump("rejected_oversize")
            else:
                self.diagnostics.bump("rejected_frames")
            self.diagnostics.record_error(
                f"rejected frame: {describe_message(message)}", self._secrets
            )
            raise

    def call(
        self,
        op: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        io_slack: float = 1.0,
    ) -> dict[str, Any]:
        """请求-响应：返回响应帧；超时抛 ``RequestTimeout``（mutating 记未知）。

        超时后到达的响应一律丢弃（``late_responses``），mutating 操作的结果被
        标记为**未知**（``ambiguous_mutations``）——调用方必须重新取快照，
        禁止静默重试。
        """
        entry = self.send_request(op, payload, timeout_ms=timeout_ms)
        deadline = entry.deadline + max(0.0, float(io_slack))
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                self.diagnostics.bump("expired_dropped")
                if entry.mutating:
                    self.ambiguous_mutations += 1
                    self._last_ambiguous_op = entry.op
                self.pending.expire()
                raise RequestTimeout(
                    f"request {op!r} timed out; outcome unknown for mutating ops (resync required)"
                )
            message = self.recv_message(timeout=remaining)
            if message is None:
                continue
            delivered, reason = self.pending.accept_response(message)
            if not delivered:
                self.diagnostics.bump("late_responses")
                self.diagnostics.record_error(f"dropped response for {op!r}: {reason}", self._secrets)
                continue
            return message

    def run_handler(self, request: Mapping[str, Any], handler: Callable[[Mapping[str, Any]], Any]) -> dict[str, Any]:
        """**唯一**的 handler 调用点（先复核过期，再执行）。

        - 过期（``min(sender_deadline, now + timeout_ms) <= now``）→ 返回
          ``expired`` 错误响应，handler 不被调用；
        - handler 异常/非法返回值 → 脱敏错误响应（不返回到业务循环外）。
        """
        request_id = str(request.get("request_id"))
        now = time.time()
        timeout_ms = int(request.get("timeout_ms", DEFAULT_REQUEST_TIMEOUT_MS))
        deadline = now + max(1, min(timeout_ms, MAX_REQUEST_TIMEOUT_MS)) / 1000.0
        sender_deadline = request.get("deadline")
        if isinstance(sender_deadline, (int, float)) and not isinstance(sender_deadline, bool):
            deadline = min(float(sender_deadline), deadline)
        if deadline <= now:
            self.diagnostics.bump("expired_dropped")
            return build_error("expired", request_id=request_id, secrets_=self._secrets)
        try:
            payload = handler(request)
        except Exception as exc:  # noqa: BLE001 - handler 失败不拖死连接
            self.diagnostics.bump("handler_errors")
            self.diagnostics.record_error(f"handler error: {_safe_error_text(exc, self._secrets)}", self._secrets)
            return build_error("handler-error", request_id=request_id, secrets_=self._secrets)
        self.diagnostics.bump("requests_dispatched")
        if payload is None:
            return build_response(request_id, None, ok=True)
        if not isinstance(payload, Mapping):
            self.diagnostics.bump("handler_errors")
            self.diagnostics.record_error("handler returned a non-mapping payload", self._secrets)
            return build_error("handler-invalid-payload", request_id=request_id, secrets_=self._secrets)
        return build_response(request_id, payload, ok=True, secrets_=self._secrets)

    def serve(
        self,
        handler: Callable[[Mapping[str, Any]], Any],
        *,
        max_requests: int | None = None,
        idle_timeout: float = DEFAULT_IO_TIMEOUT,
    ) -> dict[str, Any]:
        """服务器侧业务循环：先认证，再逐请求分发（内联 = 天然背压）。

        认证失败/超时 → 抛 ``AuthenticationError``，**handler 一次都不会被调用**。
        """
        self.handshake()
        handled = 0
        reason = "idle-timeout"
        while True:
            if max_requests is not None and handled >= int(max_requests):
                reason = "max-requests"
                break
            try:
                message = self.recv_message(timeout=idle_timeout)
            except (TransportClosedError, EOFError) as exc:
                self.diagnostics.record_error(f"transport closed: {type(exc).__name__}", self._secrets)
                reason = "closed"
                break
            except ProtocolError as exc:
                # 协议违例（畸形/未知/过大/断帧）：拒绝并结束该连接，不“跳过坏帧继续”。
                self.diagnostics.record_error(
                    f"protocol violation: {type(exc).__name__}", self._secrets
                )
                reason = "protocol-error"
                break
            if message is None:
                reason = "idle-timeout"
                break
            message_type = message.get("type")
            if message_type == MessageType.PING.value:
                self.transport.send_frame(build_pong())
                self.diagnostics.bump("frames_out")
                continue
            if message_type != MessageType.REQUEST.value:
                self.diagnostics.bump("rejected_unknown_type")
                self.diagnostics.record_error(
                    f"unsupported message in serve loop: {describe_message(message).get('type')!r}",
                    self._secrets,
                )
                continue
            response = self.run_handler(message, handler)
            self.transport.send_frame(response)
            self.diagnostics.bump("frames_out")
            handled += 1
        return {
            "handled": handled,
            "reason": reason,
            "authenticated": self._authenticated,
            "diagnostics": self.diagnostics.as_dict(),
        }

    def close(self, *, timeout: float | None = None) -> Any:
        return self.transport.close(timeout=timeout)


__all__ = [
    "IPC_PROTOCOL_VERSION",
    "IPC_MAC_DOMAIN",
    "DEFAULT_MAX_FRAME_BYTES",
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "DEFAULT_MAX_PENDING_REQUESTS",
    "DEFAULT_MAX_OPERATIONS",
    "DEFAULT_HANDSHAKE_TIMEOUT",
    "DEFAULT_IO_TIMEOUT",
    "DEFAULT_REQUEST_TIMEOUT_MS",
    "MAX_REQUEST_TIMEOUT_MS",
    "DEFAULT_MAX_DIAGNOSTIC_ERRORS",
    "DEFAULT_DIAGNOSTIC_ERROR_CHARS",
    "TOKEN_BYTES",
    "NONCE_BYTES",
    "MIN_ROWS",
    "MAX_ROWS",
    "MIN_COLS",
    "MAX_COLS",
    "MAX_EXACT_INT",
    "REQUEST_ID_RE",
    "MessageType",
    "OP_READ",
    "OP_INPUT",
    "OP_SNAPSHOT",
    "OP_RESIZE",
    "OP_LEASE",
    "OP_STOP",
    "ALL_OPS",
    "MUTATING_OPS",
    "IpcError",
    "ProtocolError",
    "MalformedFrameError",
    "FrameTooLargeError",
    "FrameTruncatedError",
    "ProtocolVersionError",
    "AuthenticationError",
    "HandshakeTimeout",
    "TransportClosedError",
    "TransportTimeout",
    "RequestTimeout",
    "OperationQueueFull",
    "generate_token",
    "generate_nonce",
    "token_bytes",
    "verify_token",
    "MacContext",
    "compute_mac",
    "verify_mac",
    "parse_exact_int",
    "encode_exact_int",
    "parse_b64_bytes",
    "encode_payload_bytes",
    "payload_int",
    "redact",
    "describe_message",
    "new_request_id",
    "decode_frame_line",
    "encode_frame",
    "FrameDecoder",
    "validate_message",
    "build_challenge",
    "build_hello",
    "build_hello_ack",
    "build_request",
    "build_response",
    "build_error",
    "build_pong",
    "IpcDiagnostics",
    "PendingRequest",
    "PendingRequests",
    "SubmitOutcome",
    "RequestScheduler",
    "FrameTransport",
    "PeerIdentity",
    "AuthResult",
    "normalize_probe",
    "verify_peer_identity",
    "IpcSession",
]
