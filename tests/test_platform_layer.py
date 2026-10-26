"""平台层（能力注册表 + 路径策略）单元测试。

纯函数测试，不导入 server、不触碰文件系统；所有平台值都显式注入，
因此在任意宿主平台结果一致。
"""

import sys

import pytest

from packages.core import platform as plat


# ── 平台名归一 ────────────────────────────────────────────────


def test_platform_name_maps_supported_platforms():
    assert plat.platform_name("win32") == plat.WINDOWS
    assert plat.platform_name("Windows") == plat.WINDOWS
    assert plat.platform_name("darwin") == plat.DARWIN
    assert plat.platform_name("linux") == plat.LINUX
    assert plat.platform_name("linux2") == plat.LINUX


def test_unknown_platform_is_explicit_not_disguised():
    # 不把 FreeBSD 冒充成 linux：能力表会因此全部为 False。
    assert plat.platform_name("freebsd14") == plat.UNKNOWN_PLATFORM
    assert plat.platform_name("") == plat.UNKNOWN_PLATFORM


def test_default_uses_current_interpreter_platform():
    assert plat.platform_name() == plat.platform_name(sys.platform)
    assert plat.is_windows() is (sys.platform == "win32")
    assert plat.is_posix() is (sys.platform != "win32")


# ── 能力注册表 ────────────────────────────────────────────────


EXPECTED_CAPABILITIES = (
    "job.runtimeOwner",
    "terminal.native",
    "terminal.detach",
    "rewind",
    "service.lifecycle",
    "processOwner",
    "process.hiddenDesktop",
    "secretStore",
)


def test_capability_matrix_shape_and_order():
    assert tuple(plat.capabilities("linux")) == EXPECTED_CAPABILITIES
    assert tuple(plat.CAPABILITIES) == EXPECTED_CAPABILITIES


def test_capabilities_match_implemented_platform_backends():
    # ProcessOwner now has both Windows Job Object and POSIX process-group
    # implementations. Other Phase A capabilities remain Windows-only.
    for name in EXPECTED_CAPABILITIES:
        assert plat.supports(name, "win32") is True, name
        expected = name in {"processOwner", "job.runtimeOwner"}
        assert plat.supports(name, "darwin") is expected, name
        assert plat.supports(name, "linux") is expected, name


def test_unknown_platform_declares_nothing():
    table = plat.capabilities("freebsd14")
    assert set(table) == set(EXPECTED_CAPABILITIES)
    assert not any(table.values())


def test_capabilities_only_carry_booleans():
    for platform_name in ("win32", "darwin", "linux"):
        table = plat.capabilities(platform_name)
        assert all(isinstance(value, bool) for value in table.values())


def test_unknown_capability_fails_loudly():
    with pytest.raises(plat.UnknownCapability):
        plat.supports("terminal.nativ")  # 拼写错误不得静默为 False
    with pytest.raises(plat.UnknownCapability):
        plat.capability("nope")


def test_capability_entries_explain_why():
    for name, entry in plat.CAPABILITIES.items():
        assert entry.name == name
        assert entry.reason, name
        assert entry.supported_on, name


# ── 路径策略 ──────────────────────────────────────────────────


def test_separator_and_case_policy_per_platform():
    assert plat.path_separator("win32") == "\\"
    assert plat.path_separator("darwin") == "/"
    assert plat.path_separator("linux") == "/"
    assert plat.is_case_insensitive("win32") is True
    assert plat.is_case_insensitive("darwin") is True
    assert plat.is_case_insensitive("linux") is False
    assert plat.is_case_insensitive("freebsd14") is False


def test_windows_executable_suffixes():
    assert ".exe" in plat.path_policy("win32").executable_suffixes
    assert plat.path_policy("linux").executable_suffixes == ()


def test_join_path_uses_platform_separator():
    assert plat.join_path("C:\\work", "repo", platform="win32") == "C:\\work\\repo"
    assert plat.join_path("C:\\work\\", "repo", platform="win32") == "C:\\work\\repo"
    assert plat.join_path("/home/pan", "repo", platform="linux") == "/home/pan/repo"
    assert plat.join_path("C:/work", "repo", platform="win32") == "C:/work\\repo"


def test_join_path_tolerates_empty_parts():
    assert plat.join_path("/home/pan", "", None, "repo", platform="darwin") == "/home/pan/repo"


def test_normalize_separators_keeps_posix_backslash_literal():
    # 反斜杠在 POSIX 上是合法文件名字符；替换会破坏真实路径。
    assert plat.normalize_separators("/home/a\\b", platform="linux") == "/home/a\\b"
    assert plat.normalize_separators("C:/work/repo", platform="win32") == "C:\\work\\repo"


def test_strip_trailing_separator_keeps_roots():
    assert plat.strip_trailing_separator("/home/pan/", platform="linux") == "/home/pan"
    assert plat.strip_trailing_separator("/", platform="linux") == "/"
    assert plat.strip_trailing_separator("C:\\", platform="win32") == "C:\\"
    assert plat.strip_trailing_separator("C:\\work\\", platform="win32") == "C:\\work"
    assert plat.strip_trailing_separator("", platform="win32") == ""


def test_path_policy_is_pure_data():
    policy = plat.path_policy("win32")
    assert policy.separators == ("\\", "/")
    assert plat.path_policy("linux").separators == ("/",)
