"""DPAPI 用户作用域秘密存储（P1，Windows-only）。

一份秘密 = 一个终端的 IPC 凭据 + runner 身份事实 + 管道名：

```
data/terminals/secrets/<term_id>.secret      # DPAPI 密文（本模块）
data/terminals/secrets/<term_id>.hello       # runner 出生自证的 pid + raw FILETIME
```

不变量（实现与测试共同守护）：

1. **只在本机当前用户可解密**：``CryptProtectData`` /
   ``CryptUnprotectData`` **用户作用域**（不带 ``CRYPTPROTECT_LOCAL_MACHINE``，
   带 ``CRYPTPROTECT_UI_FORBIDDEN``——不在无人值守进程里弹 UI），并绑定
   ``terminal_id`` 派生的 optional entropy；解密失败 fail-closed。
2. **owner-only 从创建时生效**：``secrets/`` 目录与秘密文件都由
   ``win_pipe`` 的 SECURITY_ATTRIBUTES 创建（不是“先建后收紧”），DACL 只有
   一个当前用户 SID 的 ACE；读取时复核 ACL（有其它 SID 的 allow ACE 即拒绝）。
3. **原子持久化**：先写同目录 tmp（同样 owner-only）→ ``MoveFileExW`` 替换；
   任何异常路径清理 tmp；**绝不**截断写。
4. **禁止 reparse/越界名**：``terminal_id`` 必须匹配 ``term_`` 命名空间；
   秘密/hello 路径本身或所在目录是 reparse point（symlink/junction）一律拒绝。
5. **64 位身份精确**：pid 与 raw FILETIME 同时以**十进制字符串 + 0x 十六进制**
   保存；读到浮点即 ``SecretCorruptError``（禁止 Number 舍入），pid/filetime
   的十进制与十六进制表示不一致同样视为损坏。
6. **删除受约束**：只有 ``verified_exit=True``（调用方已用内核证据确认 runner
   已退出）或显式 ``caller_responsible=<责任说明>`` 才允许删除；
   **断连/失联不删秘密**（detach 的闭环依赖秘密跨 Pan 重启仍可解密）。
7. **bootstrap 闭环**：runner 出生先写 ``<path>.hello``（自身 pid + raw FILETIME
   来自 ``GetProcessTimes``），服务读 hello（不依赖 ``Popen.pid``，免受解释器
   包装层影响）→ 写 DPAPI 秘密；runner 等秘密出现（有界）→ 解密 → 与自身身份
   比对，不匹配即 fail-closed（不监听、退出非零）。
8. **argv 无 token**：runner 只接收 ``--secret-file <path>``；token 不进 argv、
   不进环境变量、不进 registry/日志/异常文本。

边界（如实声明）：DPAPI 用户作用域 + owner-only ACL 只隔离**其它 Windows
用户**。同一用户下的进程理论上都能解密/复制该文件——首版信任边界 = 同一 Pan
用户，不承诺“防同用户恶意软件”。**未做的负验证**：未创建其它账户、未改任何
账户权限，因此“其它用户确实被拒”只有结构性证据（DACL 无其它 SID ACE），
没有实测的跨用户拒绝。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from . import win_pipe as _win
from .contracts import BackendUnavailableError, ProcessIdentity, TERMINAL_ID_PREFIX

SECRET_SCHEMA_VERSION = 1
SECRET_FILE_SUFFIX = ".secret"
BOOTSTRAP_FILE_SUFFIX = ".hello"
SECRETS_DIRNAME = "secrets"
#: runner 等待秘密文件出现的默认上界（计划 §6.1：10s）。
DEFAULT_BOOTSTRAP_TIMEOUT = 10.0
#: 轮询间隔。
BOOTSTRAP_POLL_SECONDS = 0.05

_TERMINAL_ID_RE = re.compile(rf"^{TERMINAL_ID_PREFIX}[A-Za-z0-9_-]{{1,64}}$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_PIPE_RE = re.compile(r"^\\\\\.\\pipe\\[A-Za-z0-9_.-]{1,128}$")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SECRETS_ROOT = _PROJECT_ROOT / "data" / "terminals"


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------


class SecretStoreError(RuntimeError):
    """秘密存储错误基类（消息文本不含秘密）。"""


class SecretNotFoundError(SecretStoreError):
    """秘密/自证文件不存在。"""


class SecretCorruptError(SecretStoreError):
    """秘密文件存在但无法解析/字段非法（不静默丢弃、不猜测修复）。"""


class SecretIdentityMismatchError(SecretStoreError):
    """秘密中的 runner 身份与目标进程身份不匹配（fail-closed）。"""


class SecretProtectionError(SecretStoreError):
    """DPAPI 加/解密失败（fail-closed，不打印明文）。"""


class SecretSecurityError(SecretStoreError):
    """ACL 复核失败（存在其它 SID 的 allow ACE）或 reparse 路径。"""


class SecretDeletionRefused(SecretStoreError):
    """删除条件不满足：既非已验证退出，也没有显式调用者责任。"""


# --------------------------------------------------------------------------
# DPAPI
# --------------------------------------------------------------------------


class DpapiProtector:
    """``CryptProtectData`` / ``CryptUnprotectData``（用户作用域）。"""

    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def __init__(self, *, entropy: bytes | None = None) -> None:
        _win._require_windows()
        self.entropy = bytes(entropy) if entropy else None

    # -- 内部 ----------------------------------------------------------
    def protect(self, data: bytes, *, description: str = "pan-terminal-ipc-secret") -> bytes:
        return self._crypt(data, protect=True, description=description)

    def unprotect(self, blob: bytes) -> bytes:
        return self._crypt(blob, protect=False)

    def _crypt(self, payload: bytes, *, protect: bool, description: str = "") -> bytes:
        import ctypes

        _win._require_windows()  # noqa: SLF001
        crypto = _win._crypt32  # noqa: SLF001
        data_in = _win._CRYPTPROTECT_DATA_BLOB()  # noqa: SLF001
        data_out = _win._CRYPTPROTECT_DATA_BLOB()  # noqa: SLF001
        in_buffer = ctypes.create_string_buffer(bytes(payload), max(1, len(payload)))
        data_in.cbData = len(payload)
        data_in.pbData = ctypes.cast(in_buffer, ctypes.POINTER(_win.wintypes.BYTE))
        entropy_blob = None
        entropy_buffer = None
        if self.entropy:
            entropy_blob = _win._CRYPTPROTECT_DATA_BLOB()  # noqa: SLF001
            entropy_buffer = ctypes.create_string_buffer(self.entropy, len(self.entropy))
            entropy_blob.cbData = len(self.entropy)
            entropy_blob.pbData = ctypes.cast(entropy_buffer, ctypes.POINTER(_win.wintypes.BYTE))
        flags = self.CRYPTPROTECT_UI_FORBIDDEN if protect else 0
        if protect:
            ok = crypto.CryptProtectData(
                ctypes.byref(data_in),
                description or None,
                ctypes.byref(entropy_blob) if entropy_blob is not None else None,
                None,
                None,
                flags,
                ctypes.byref(data_out),
            )
        else:
            ok = crypto.CryptUnprotectData(
                ctypes.byref(data_in),
                None,
                ctypes.byref(entropy_blob) if entropy_blob is not None else None,
                None,
                None,
                flags,
                ctypes.byref(data_out),
            )
        if not ok:
            error = _win._last_error()  # noqa: SLF001
            raise SecretProtectionError(
                "DPAPI "
                + ("protect" if protect else "unprotect")
                + f" failed (error={error}: {_win._error_text(error)})"  # noqa: SLF001
            )
        try:
            return ctypes.string_at(data_out.pbData, int(data_out.cbData))
        finally:
            if data_out.pbData:
                _win._k32.LocalFree(ctypes.cast(data_out.pbData, _win.wintypes.HLOCAL))  # noqa: SLF001

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "dpapi",
            "scope": "user",
            "ui_forbidden": True,
            "entropy_bound": bool(self.entropy),
        }


# --------------------------------------------------------------------------
# 数据
# --------------------------------------------------------------------------


def _exact_int_fields(value: Any, *, name: str, minimum: int = 0, maximum: int = (1 << 63) - 1) -> tuple[int, int]:
    """解析 ``{"<name>": "<decimal>", "<name>_hex": "0x..."}``（缺 hex 即损坏）。

    浮点/Bool/负数/越界/十进制与十六进制不一致一律 ``SecretCorruptError``。
    """
    if not isinstance(value, Mapping):
        raise SecretCorruptError(f"{name} must be an object with decimal and hex forms")
    decimal_raw = value.get(name)
    hex_raw = value.get(f"{name}_hex")
    if decimal_raw is None or hex_raw is None:
        raise SecretCorruptError(f"{name} must carry both decimal and hex forms")
    parsed: list[int] = []
    for raw, label in ((decimal_raw, name), (hex_raw, f"{name}_hex")):
        if isinstance(raw, bool):
            raise SecretCorruptError(f"{label} must not be a boolean")
        if isinstance(raw, int):
            parsed.append(int(raw))
            continue
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            try:
                parsed.append(int(text, 16) if text.lower().startswith("0x") else int(text, 10))
            except ValueError as exc:
                raise SecretCorruptError(f"{label} is not a decimal/hex integer") from exc
            continue
        raise SecretCorruptError(
            f"{label} must be an integer or a decimal/hex string (floats would round 64-bit values)"
        )
    if parsed[0] != parsed[1]:
        raise SecretCorruptError(f"{name} decimal/hex representations disagree")
    result = parsed[0]
    if result < minimum or result > maximum:
        raise SecretCorruptError(f"{name} out of range")
    return result, result


def _exact_int_json(value: int) -> tuple[str, str]:
    number = int(value)
    return str(number), hex(number)


@dataclass(frozen=True)
class SecretPayload:
    """一个终端的 IPC 秘密（**只在内存中解密**）。

    ``as_dict()`` 刻意**不含 token**（日志/证据/异常出口）。
    """

    terminal_id: str
    pipe_name: str
    token: str
    runner_pid: int | None
    runner_filetime: int | None
    created_at: float
    updated_at: float
    schema_version: int = SECRET_SCHEMA_VERSION

    def runner_identity(self) -> ProcessIdentity | None:
        if self.runner_pid is None or self.runner_filetime is None:
            return None
        return ProcessIdentity(pid=int(self.runner_pid), created_at_filetime=int(self.runner_filetime))

    def matches_identity(self, identity: ProcessIdentity | None) -> bool:
        """与给出的进程身份精确比对（raw FILETIME；缺任一字段即 False）。"""
        expected = self.runner_identity()
        return expected is not None and identity is not None and expected.matches(identity)

    def as_dict(self) -> dict[str, Any]:
        """安全摘要：**不含 token**（只有 token 是否存在与长度）。"""
        return {
            "schema_version": self.schema_version,
            "terminal_id": self.terminal_id,
            "pipe_name": self.pipe_name,
            "runner_pid": self.runner_pid,
            "runner_filetime": self.runner_filetime,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "token_present": bool(self.token),
            "token_hex_chars": len(self.token),
        }

    def to_json(self) -> str:
        """明文 JSON（**只允许**用于立即加密写盘，不得落日志/进证据）。"""
        pid_decimal, pid_hex = _exact_int_json(self.runner_pid if self.runner_pid is not None else 0)
        ft_decimal, ft_hex = _exact_int_json(
            self.runner_filetime if self.runner_filetime is not None else 0
        )
        body = {
            "schema_version": self.schema_version,
            "terminal_id": self.terminal_id,
            "pipe_name": self.pipe_name,
            "token": self.token,
            "runner": {
                "pid": pid_decimal,
                "pid_hex": pid_hex,
                "created_at_filetime": ft_decimal,
                "created_at_filetime_hex": ft_hex,
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        return json.dumps(body, ensure_ascii=False, indent=2, sort_keys=False)

    @classmethod
    def from_json(cls, text: str, *, expect_terminal_id: str | None = None) -> "SecretPayload":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SecretCorruptError("secret payload is not valid json") from exc
        if not isinstance(data, Mapping):
            raise SecretCorruptError("secret payload must be a json object")
        version = data.get("schema_version")
        if version != SECRET_SCHEMA_VERSION:
            raise SecretCorruptError(f"unsupported secret schema_version: {version!r}")
        terminal_id = data.get("terminal_id")
        if not isinstance(terminal_id, str) or not _TERMINAL_ID_RE.match(terminal_id):
            raise SecretCorruptError("secret terminal_id is invalid")
        if expect_terminal_id is not None and terminal_id != expect_terminal_id:
            raise SecretCorruptError("secret terminal_id does not match its file name")
        pipe_name = data.get("pipe_name")
        if not isinstance(pipe_name, str) or not _PIPE_RE.match(pipe_name):
            raise SecretCorruptError("secret pipe_name is not a local named pipe")
        token = data.get("token")
        if not isinstance(token, str) or not _TOKEN_RE.match(token):
            raise SecretCorruptError("secret token must be 64 hex chars (256 bit)")
        runner = data.get("runner") or {}
        pid, _ = _exact_int_fields(runner, name="pid", minimum=1, maximum=(1 << 31) - 1)
        filetime, _ = _exact_int_fields(runner, name="created_at_filetime", minimum=1)
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        for label, value in (("created_at", created_at), ("updated_at", updated_at)):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SecretCorruptError(f"secret {label} must be a number")
        return cls(
            terminal_id=terminal_id,
            pipe_name=pipe_name,
            token=token,
            runner_pid=pid,
            runner_filetime=filetime,
            created_at=float(created_at),
            updated_at=float(updated_at),
            schema_version=int(version),
        )


@dataclass(frozen=True)
class BootstrapIdentity:
    """runner 自证（``<path>.hello``）：自身 pid + raw FILETIME。"""

    terminal_id: str
    pid: int
    filetime: int
    written_at: float
    schema_version: int = SECRET_SCHEMA_VERSION

    def identity(self) -> ProcessIdentity:
        return ProcessIdentity(pid=int(self.pid), created_at_filetime=int(self.filetime))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "terminal_id": self.terminal_id,
            "pid": self.pid,
            "filetime": self.filetime,
            "written_at": self.written_at,
        }

    def to_json(self) -> str:
        pid_decimal, pid_hex = _exact_int_json(self.pid)
        ft_decimal, ft_hex = _exact_int_json(self.filetime)
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "terminal_id": self.terminal_id,
                "pid": pid_decimal,
                "pid_hex": pid_hex,
                "created_at_filetime": ft_decimal,
                "created_at_filetime_hex": ft_hex,
                "written_at": self.written_at,
            },
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str, *, expect_terminal_id: str | None = None) -> "BootstrapIdentity":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SecretCorruptError("bootstrap payload is not valid json") from exc
        if not isinstance(data, Mapping):
            raise SecretCorruptError("bootstrap payload must be a json object")
        if data.get("schema_version") != SECRET_SCHEMA_VERSION:
            raise SecretCorruptError("unsupported bootstrap schema_version")
        terminal_id = data.get("terminal_id")
        if not isinstance(terminal_id, str) or not _TERMINAL_ID_RE.match(terminal_id):
            raise SecretCorruptError("bootstrap terminal_id is invalid")
        if expect_terminal_id is not None and terminal_id != expect_terminal_id:
            raise SecretCorruptError("bootstrap terminal_id does not match its file name")
        pid, _ = _exact_int_fields(data, name="pid", minimum=1, maximum=(1 << 31) - 1)
        filetime, _ = _exact_int_fields(data, name="created_at_filetime", minimum=1)
        written_at = data.get("written_at")
        if not isinstance(written_at, (int, float)) or isinstance(written_at, bool):
            raise SecretCorruptError("bootstrap written_at must be a number")
        return cls(
            terminal_id=terminal_id,
            pid=pid,
            filetime=filetime,
            written_at=float(written_at),
        )


# --------------------------------------------------------------------------
# 存储
# --------------------------------------------------------------------------


class SecretStore:
    """``<terminals_root>/secrets/`` 下的 DPAPI 秘密文件存取。"""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        protector: Any | None = None,
        clock: Callable[[], float] = time.time,
        require_owner_only_acl: bool = True,
    ) -> None:
        _win._require_windows()  # noqa: SLF001
        self.root = self._resolve_root(root)
        self.secrets_dir = self.root / SECRETS_DIRNAME
        self.clock = clock
        self.require_owner_only_acl = bool(require_owner_only_acl)
        self._protector = protector

    # -- 路径 ----------------------------------------------------------
    @staticmethod
    def _resolve_root(root: str | os.PathLike[str] | None) -> Path:
        if root is not None:
            return Path(root).expanduser()
        value = os.environ.get("PAN_TERMINALS_DIR")
        return Path(value).expanduser() if value else DEFAULT_SECRETS_ROOT

    @staticmethod
    def validate_terminal_id(terminal_id: str) -> str:
        if not isinstance(terminal_id, str) or not _TERMINAL_ID_RE.match(terminal_id):
            raise ValueError(f"invalid terminal id for secret store: {terminal_id!r}")
        return terminal_id

    def secret_path(self, terminal_id: str) -> Path:
        self.validate_terminal_id(terminal_id)
        return self.secrets_dir / f"{terminal_id}{SECRET_FILE_SUFFIX}"

    def bootstrap_path(self, terminal_id: str) -> Path:
        """``<secret_path>.hello``（runner 出生自证；argv 只传 secret 路径）。"""
        return Path(f"{self.secret_path(terminal_id)}{BOOTSTRAP_FILE_SUFFIX}")

    def ensure_secrets_dir(self) -> Path:
        """创建/收紧 ``secrets/``：owner-only DACL 从创建时生效，拒绝 reparse。"""
        target = self.secrets_dir
        if _win.is_reparse_point(str(target)):
            raise SecretSecurityError(f"secrets directory is a reparse point: {target.name}")
        _win.create_owner_only_directory(str(target))
        if self.require_owner_only_acl:
            self.verify_owner_only(target)
        return target

    def verify_owner_only(self, path: Path) -> None:
        """复核 ACL：只允许当前用户 SID 的 allow ACE（其它 SID 一律拒绝）。"""
        sid = _win.current_user_sid()
        if _win.owner_sid(str(path)) not in (None, sid):
            raise SecretSecurityError(f"object owner is not the current user: {path.name}")
        for entry in _win.dacl_entries(str(path)):
            if entry.get("ace_type") == "null-dacl":
                raise SecretSecurityError(f"object has a NULL DACL (everyone): {path.name}")
            if int(entry.get("ace_type", 0)) != _win.ACCESS_ALLOWED_ACE_TYPE:
                continue
            if entry.get("sid") != sid:
                raise SecretSecurityError(
                    f"object grants another principal ({entry.get('sid')!r}): {path.name}"
                )

    def _guard_path(self, path: Path) -> None:
        """拒绝 reparse（symlink/junction）与越界父目录。"""
        secrets_dir = self.secrets_dir
        if _win.is_reparse_point(str(secrets_dir)):
            raise SecretSecurityError("secrets directory is a reparse point")
        parent = os.path.normcase(str(Path(path).parent.resolve()))
        if parent != os.path.normcase(str(secrets_dir.resolve())):
            raise SecretSecurityError("secret path escapes the secrets directory")
        if _win.is_reparse_point(str(path)):
            raise SecretSecurityError(f"secret path is a reparse point: {path.name}")

    # -- 写 ------------------------------------------------------------
    def protector(self) -> Any:
        if self._protector is None:
            self._protector = DpapiProtector()
        return self._protector

    def _entropy_for(self, terminal_id: str) -> DpapiProtector:
        return DpapiProtector(entropy=f"pan-terminal-secret|{terminal_id}".encode("utf-8"))

    def write_secret(self, payload: SecretPayload, *, now: float | None = None) -> Path:
        """加密（DPAPI 用户作用域）并**原子**写入秘密文件。"""
        self.validate_terminal_id(payload.terminal_id)
        if not _TOKEN_RE.match(payload.token or ""):
            raise SecretStoreError("refusing to store a token that is not 256-bit hex")
        if payload.runner_pid is None or payload.runner_filetime is None:
            raise SecretStoreError("refusing to store a secret without a verifiable runner identity")
        self.ensure_secrets_dir()
        path = self.secret_path(payload.terminal_id)
        self._guard_path(path)
        protector = self._protector if self._protector is not None else self._entropy_for(payload.terminal_id)
        blob = protector.protect(payload.to_json().encode("utf-8"))
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{int(self.clock() * 1000) % 100000}.tmp")
        try:
            if os.path.exists(tmp):
                _win.delete_file(str(tmp))
            _win.write_file_owner_only(str(tmp), blob)
            if self.require_owner_only_acl:
                self.verify_owner_only(tmp)
            _win.replace_file_atomic(str(tmp), str(path))
        finally:
            if os.path.exists(tmp):
                try:
                    _win.delete_file(str(tmp))
                except OSError:
                    pass
        return path

    # -- 读 ------------------------------------------------------------
    def read_secret(self, terminal_id: str) -> SecretPayload:
        """解密并解析秘密；不存在/损坏/DPAPI 失败/ACL 不合格一律 fail-closed。"""
        self.validate_terminal_id(terminal_id)
        path = self.secret_path(terminal_id)
        self._guard_path(path)
        if not os.path.exists(path):
            raise SecretNotFoundError(f"secret file not found: {path.name}")
        if self.require_owner_only_acl:
            self.verify_owner_only(path)
        try:
            blob = path.read_bytes()
        except OSError as exc:
            raise SecretStoreError(f"cannot read secret file: {path.name}") from exc
        if not blob:
            raise SecretCorruptError("secret file is empty")
        protector = self._protector if self._protector is not None else self._entropy_for(terminal_id)
        try:
            plaintext = protector.unprotect(blob)
        except SecretProtectionError:
            raise
        except Exception as exc:  # noqa: BLE001 - 任何保护层异常都按 fail-closed 处理
            raise SecretProtectionError(f"DPAPI unprotect failed: {type(exc).__name__}") from exc
        try:
            text = plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretCorruptError("decrypted secret is not valid utf-8") from exc
        return SecretPayload.from_json(text, expect_terminal_id=terminal_id)

    def exists(self, terminal_id: str) -> bool:
        try:
            self.validate_terminal_id(terminal_id)
        except ValueError:
            return False
        return os.path.exists(self.secret_path(terminal_id))

    def wait_for_secret(
        self,
        terminal_id: str,
        *,
        timeout: float = DEFAULT_BOOTSTRAP_TIMEOUT,
        poll: float = BOOTSTRAP_POLL_SECONDS,
    ) -> SecretPayload:
        """有界等待秘密文件出现（runner 启动路径；超时 ``SecretNotFoundError``）。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                return self.read_secret(terminal_id)
            except (SecretNotFoundError, SecretCorruptError):
                if time.monotonic() >= deadline:
                    raise SecretNotFoundError(
                        f"secret file did not appear within {timeout}s"
                    ) from None
                time.sleep(max(0.01, float(poll)))

    def update_runner_identity(
        self,
        terminal_id: str,
        *,
        pid: int | None = None,
        filetime: int | None = None,
        identity: ProcessIdentity | None = None,
        now: float | None = None,
    ) -> SecretPayload:
        """更新秘密中的 runner 身份（bootstrap hello 之后/重新自证时使用）。"""
        if identity is not None:
            pid = identity.pid if pid is None else pid
            filetime = identity.created_at_filetime if filetime is None else filetime
        if pid is None or filetime is None:
            raise SecretStoreError("identity update requires pid and raw FILETIME")
        current = self.read_secret(terminal_id)
        updated = SecretPayload(
            terminal_id=current.terminal_id,
            pipe_name=current.pipe_name,
            token=current.token,
            runner_pid=int(pid),
            runner_filetime=int(filetime),
            created_at=current.created_at,
            updated_at=float(self.clock() if now is None else now),
        )
        self.write_secret(updated)
        return updated

    def verify_runner_identity(self, terminal_id: str, identity: ProcessIdentity) -> SecretPayload:
        """runner 自检：秘密中的身份必须与自身 pid + raw FILETIME 完全一致。"""
        payload = self.read_secret(terminal_id)
        if not payload.matches_identity(identity):
            raise SecretIdentityMismatchError(
                "secret runner identity mismatch (pid + raw FILETIME) — fail-closed"
            )
        return payload

    # -- bootstrap -----------------------------------------------------
    def write_bootstrap_identity(
        self,
        terminal_id: str,
        *,
        pid: int | None = None,
        filetime: int | None = None,
        identity: ProcessIdentity | None = None,
        now: float | None = None,
    ) -> Path:
        """runner 出生自证：写 ``<path>.hello``（默认取当前进程身份）。"""
        _win._require_windows()  # noqa: SLF001
        if identity is None and (pid is None or filetime is None):
            identity = _win.current_process_identity()
        if identity is not None:
            pid = identity.pid if pid is None else pid
            filetime = identity.created_at_filetime if filetime is None else filetime
        if pid is None or filetime is None:
            raise SecretStoreError("bootstrap identity requires pid and raw FILETIME")
        self.validate_terminal_id(terminal_id)
        self.ensure_secrets_dir()
        path = self.bootstrap_path(terminal_id)
        self._guard_path(path)
        record = BootstrapIdentity(
            terminal_id=terminal_id,
            pid=int(pid),
            filetime=int(filetime),
            written_at=float(self.clock() if now is None else now),
        )
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            _win.write_file_owner_only(str(tmp), record.to_json().encode("utf-8"))
            if self.require_owner_only_acl:
                self.verify_owner_only(tmp)
            _win.replace_file_atomic(str(tmp), str(path))
        finally:
            if os.path.exists(tmp):
                try:
                    _win.delete_file(str(tmp))
                except OSError:
                    pass
        return path

    def read_bootstrap_identity(self, terminal_id: str) -> BootstrapIdentity | None:
        """读 runner 自证；不存在返回 ``None``；损坏抛 ``SecretCorruptError``。"""
        path = self.bootstrap_path(terminal_id)
        self._guard_path(path)
        if not os.path.exists(path):
            return None
        if self.require_owner_only_acl:
            self.verify_owner_only(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SecretCorruptError("bootstrap file is not valid utf-8") from exc
        except OSError as exc:
            raise SecretStoreError(f"cannot read bootstrap file: {path.name}") from exc
        return BootstrapIdentity.from_json(text, expect_terminal_id=terminal_id)

    def wait_for_bootstrap_identity(
        self,
        terminal_id: str,
        *,
        timeout: float = DEFAULT_BOOTSTRAP_TIMEOUT,
        poll: float = BOOTSTRAP_POLL_SECONDS,
    ) -> BootstrapIdentity:
        """有界等待 runner 自证出现（服务侧 bootstrap 第 ③ 步）。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            record = self.read_bootstrap_identity(terminal_id)
            if record is not None:
                return record
            if time.monotonic() >= deadline:
                raise SecretNotFoundError(f"runner bootstrap did not appear within {timeout}s")
            time.sleep(max(0.01, float(poll)))

    def delete_bootstrap_identity(self, terminal_id: str) -> bool:
        path = self.bootstrap_path(terminal_id)
        return _win.delete_file(str(path))

    # -- 删除 ----------------------------------------------------------
    def delete_secret(
        self,
        terminal_id: str,
        *,
        reason: str,
        verified_exit: bool = False,
        caller_responsible: str | None = None,
    ) -> bool:
        """删除秘密 + 自证文件；**必须**满足下列之一，否则拒绝：

        - ``verified_exit=True``：调用方已用内核证据（同 handle ``Wait``/Job）
          确认 runner 已退出；
        - ``caller_responsible="<责任说明>"``：调用方显式承担“进程不会再回来用
          这份凭据”的责任（例如“显式关闭终端”路径）。

        **断连/失联不构成删除理由**——detach 的闭环正是靠秘密跨 Pan 重启仍可解密。
        """
        self.validate_terminal_id(terminal_id)
        if not isinstance(reason, str) or not reason:
            raise ValueError("delete_secret requires a non-empty reason")
        responsible = bool(caller_responsible and str(caller_responsible).strip())
        if not verified_exit and not responsible:
            raise SecretDeletionRefused(
                "refusing to delete secret: no verified process exit and no explicit caller "
                "responsibility (disconnects must not delete secrets)"
            )
        secret_path = self.secret_path(terminal_id)
        removed = _win.delete_file(str(secret_path))
        self.delete_bootstrap_identity(terminal_id)
        return removed

    # -- 诊断 ----------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "secrets_dir": str(self.secrets_dir),
            "schema_version": SECRET_SCHEMA_VERSION,
            "protection": (self._protector.describe() if hasattr(self._protector, "describe") else "dpapi-user-scope"),
            "require_owner_only_acl": self.require_owner_only_acl,
        }


__all__ = [
    "SECRET_SCHEMA_VERSION",
    "SECRET_FILE_SUFFIX",
    "BOOTSTRAP_FILE_SUFFIX",
    "SECRETS_DIRNAME",
    "DEFAULT_BOOTSTRAP_TIMEOUT",
    "DEFAULT_SECRETS_ROOT",
    "SecretStoreError",
    "SecretNotFoundError",
    "SecretCorruptError",
    "SecretIdentityMismatchError",
    "SecretProtectionError",
    "SecretSecurityError",
    "SecretDeletionRefused",
    "DpapiProtector",
    "SecretPayload",
    "BootstrapIdentity",
    "SecretStore",
]
