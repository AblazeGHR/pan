"""平台层：平台名、能力注册表与路径策略基础函数（跨平台 Phase A）。

本模块是平台差异的**唯一入口骨架**：业务代码只通过 :func:`supports` 判断能力、
通过 :func:`path_policy` 取路径语义，不在业务模块里散布 ``sys.platform`` 判断。

导入纪律（根 ``AGENTS.md``）：

- **顶层零平台私有导入**：不导入 ``ctypes.wintypes`` / ``msvcrt`` / ``winreg`` /
  ``pywin32`` 等 Windows-only 模块，也不导入终端、进程、秘密存储等重模块；
- 能力表是**声明**而非探测：某项能力只有在对应实现落地并通过目标平台证据后
  才可把该平台加入 ``supported_on``；声明与实现不一致视为回归；
- 未登记的平台（既非 Windows 也非 macOS/Linux）按“未声明”处理：所有能力为
  ``False``，不臆测可用。

能力命名与 ``docs/plans&overviews/CROSS_PLATFORM_PLAN_20261025.md`` 的矩阵对齐；
新增/修改平台相关功能必须同步更新该矩阵。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Mapping

__all__ = [
    "CAPABILITIES",
    "Capability",
    "DARWIN",
    "LINUX",
    "PathPolicy",
    "UNKNOWN_PLATFORM",
    "UnknownCapability",
    "WINDOWS",
    "capabilities",
    "capability",
    "is_case_insensitive",
    "is_posix",
    "is_windows",
    "join_path",
    "normalize_separators",
    "path_policy",
    "path_separator",
    "platform_name",
    "strip_trailing_separator",
    "supports",
]

WINDOWS = "windows"
DARWIN = "darwin"
LINUX = "linux"

#: 未登记平台的规范化返回值（原样保留 ``sys.platform``，不伪装成三平台之一）。
UNKNOWN_PLATFORM = "unknown"


# --------------------------------------------------------------------------- 平台名


def platform_name(value: str | None = None) -> str:
    """把 ``sys.platform`` 归一为 ``windows`` / ``darwin`` / ``linux``。

    未知平台返回 :data:`UNKNOWN_PLATFORM`：宁可显式“未知”，也不把 FreeBSD 之类
    冒充成 ``linux``（能力表会因此全部为 ``False``）。
    """
    raw = (sys.platform if value is None else str(value)).strip().lower()
    if raw.startswith("win"):
        return WINDOWS
    if raw.startswith("darwin"):
        return DARWIN
    if raw.startswith("linux"):
        return LINUX
    return UNKNOWN_PLATFORM


def is_windows(value: str | None = None) -> bool:
    return platform_name(value) == WINDOWS


def is_posix(value: str | None = None) -> bool:
    """非 Windows 即按 POSIX 语义处理（含未登记平台）。"""
    return not is_windows(value)


# --------------------------------------------------------------------------- 能力注册表


@dataclass(frozen=True)
class Capability:
    """一项平台相关能力的声明。

    ``supported_on`` 为空表示“暂无任何平台可用”，用于占位与显式返回不支持。
    """

    name: str
    supported_on: frozenset[str]
    reason: str

    def supports(self, platform: str | None = None) -> bool:
        return platform_name(platform) in self.supported_on


#: 能力矩阵（顺序即 API 输出顺序，便于前端与测试稳定比较）。
CAPABILITIES: Mapping[str, Capability] = {
    "job.runtimeOwner": Capability(
        "job.runtimeOwner", frozenset({WINDOWS, DARWIN, LINUX}),
        "本地文件系统进程锁与 SQLite epoch CAS；不提供跨主机 owner 租约",
    ),
    # Windows ConPTY + Job Object + ConPTY 命名管道；POSIX PTY 见 Phase D。
    "terminal.native": Capability(
        "terminal.native",
        frozenset({WINDOWS}),
        "native terminal 依赖 ConPTY/Job Object；POSIX PTY backend 属 Phase D",
    ),
    # durable detach 依赖 Job Object 所有权与命名管道恢复。
    "terminal.detach": Capability(
        "terminal.detach",
        frozenset({WINDOWS}),
        "durable detach 依赖 Job Object 与命名管道持久所有权",
    ),
    # rewind 走 ConPTY 交互式菜单自动化（仅 cbc）。
    "rewind": Capability(
        "rewind",
        frozenset({WINDOWS}),
        "rewind 复用 ConPTY 交互式菜单自动化",
    ),
    # UI Exit/Restart 依赖 main_lifecycle supervisor（cmd.exe 脱离父进程树）。
    "service.lifecycle": Capability(
        "service.lifecycle",
        frozenset({WINDOWS}),
        "main lifecycle supervisor 依赖 Windows 脱离父进程树的启动方式",
    ),
    # 进程所有权：Windows Job Object 与 POSIX setsid/进程组实现均可由
    # typed executor 复用；各平台仍需通过契约测试证明实际运行语义。
    "processOwner": Capability(
        "processOwner",
        frozenset({WINDOWS, DARWIN, LINUX}),
        "Windows 使用 Job Object；POSIX 使用 setsid/进程组与持久身份核验",
    ),
    # DPAPI。
    "secretStore": Capability(
        "secretStore",
        frozenset({WINDOWS}),
        "终端秘密存储使用 DPAPI",
    ),
}


class UnknownCapability(KeyError):
    """查询了未登记的能力名：拼写错误必须显式失败，不能静默返回 False。"""


def capability(name: str) -> Capability:
    try:
        return CAPABILITIES[name]
    except KeyError:
        raise UnknownCapability(f"未登记的平台能力：{name!r}") from None


def supports(name: str, platform: str | None = None) -> bool:
    """当前（或指定）平台是否声明支持该能力。

    未登记的能力名抛 :class:`UnknownCapability`；未知平台一律 ``False``。
    """
    return capability(name).supports(platform)


def capabilities(platform: str | None = None) -> dict[str, bool]:
    """能力矩阵的布尔投影（保持声明顺序；只含能力名，不含路径/账号等信息）。"""
    resolved = platform_name(platform)
    return {
        name: resolved in item.supported_on
        for name, item in CAPABILITIES.items()
    }


# --------------------------------------------------------------------------- 路径策略


@dataclass(frozen=True)
class PathPolicy:
    """平台路径语义（纯数据，不触碰文件系统，可在任意平台构造与测试）。"""

    platform: str
    separator: str
    alt_separator: str | None
    case_insensitive: bool
    executable_suffixes: tuple[str, ...]

    @property
    def separators(self) -> tuple[str, ...]:
        return (self.separator,) if not self.alt_separator else (
            self.separator, self.alt_separator,
        )


_WINDOWS_POLICY = PathPolicy(
    WINDOWS, "\\", "/", True, (".exe", ".cmd", ".bat", ".com"),
)
_DARWIN_POLICY = PathPolicy(DARWIN, "/", None, True, ())
_LINUX_POLICY = PathPolicy(LINUX, "/", None, False, ())
#: 未知平台按保守 POSIX 语义（不做大小写折叠、不把反斜杠当分隔符）。
_UNKNOWN_POLICY = PathPolicy(UNKNOWN_PLATFORM, "/", None, False, ())

_POLICIES = {
    WINDOWS: _WINDOWS_POLICY,
    DARWIN: _DARWIN_POLICY,
    LINUX: _LINUX_POLICY,
}


def path_policy(platform: str | None = None) -> PathPolicy:
    return _POLICIES.get(platform_name(platform), _UNKNOWN_POLICY)


def path_separator(platform: str | None = None) -> str:
    return path_policy(platform).separator


def is_case_insensitive(platform: str | None = None) -> bool:
    """默认文件系统大小写语义（macOS 默认 APFS 不敏感，Linux 敏感）。"""
    return path_policy(platform).case_insensitive


def normalize_separators(path: str, platform: str | None = None) -> str:
    """把**备用**分隔符统一为平台分隔符。

    Windows 把 ``/`` 归一为 ``\\``；POSIX **原样返回**——反斜杠在 POSIX 上是
    合法文件名字符，替换会破坏真实路径（同 P2-13 的教训）。
    """
    policy = path_policy(platform)
    if policy.alt_separator is None:
        return str(path)
    return str(path).replace(policy.alt_separator, policy.separator)


def join_path(base: str, *parts: str, platform: str | None = None) -> str:
    """按平台分隔符拼接路径片段（纯字符串，不解析 ``..`` / 不访问文件系统）。"""
    policy = path_policy(platform)
    separator = policy.separator
    result = "" if base is None else str(base)
    for raw in parts:
        piece = "" if raw is None else str(raw)
        if not piece:
            continue
        if result and not result.endswith(policy.separators):
            result += separator
        result += piece.lstrip("".join(policy.separators))
    return result


def strip_trailing_separator(path: str, platform: str | None = None) -> str:
    """去掉尾部冗余分隔符；根路径（``/``、``C:\\``）保持原样。"""
    policy = path_policy(platform)
    text = "" if path is None else str(path)
    if not text:
        return text
    trimmed = text.rstrip("".join(policy.separators))
    if not trimmed:
        return policy.separator
    if policy.platform == WINDOWS and len(trimmed) == 2 and trimmed[1] == ":":
        return trimmed + policy.separator
    return trimmed
