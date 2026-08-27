"""跨平台路径可移植性测试（mac 移植 feat/mac-port）。

在 Windows 上运行：native 分支是真实验证；POSIX 分支用 monkeypatch
(_IS_WINDOWS, os.path) 模拟——被测逻辑是纯字符串/文件构造，不含真 POSIX
系统调用，模拟是忠实的。需真 mac 实测的残余点见
docs/reports/portability-research-2026-08-27.md §2.2。
"""

import json
import os
import posixpath
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters.codex import sessions as codex_sessions
from packages.core.adapters.kimi import sessions as kimi_sessions
from packages.core.adapters.cbc import sessions as cbc_sessions


# ── codex _norm_path ──────────────────────────────────────────


def test_norm_path_windows_branch(monkeypatch):
    """Windows：casefold + 统一反斜杠 + 剥 \\?\\ 前缀（保持旧行为）。"""
    monkeypatch.setattr(codex_sessions, "_IS_WINDOWS", True)
    assert codex_sessions._norm_path("\\\\?\\C:\\Users\\x\\Temp\\w") == "c:\\users\\x\\temp\\w"
    assert codex_sessions._norm_path("c:/users/X/Temp/w") == "c:\\users\\x\\temp\\w"
    # 反斜杠与正斜杠等价
    assert codex_sessions._norm_path("C:/A/B") == codex_sessions._norm_path("C:\\A\\B")


def test_norm_path_posix_branch(monkeypatch):
    """POSIX：大小写保留、不做分隔符替换（修复点：旧实现 casefold+反斜杠
    会让 POSIX 下 cwd 过滤永远失配 → 静默找不到会话）。"""
    monkeypatch.setattr(codex_sessions, "_IS_WINDOWS", False)
    monkeypatch.setattr(os, "path", posixpath)  # normpath 按平台语义
    assert codex_sessions._norm_path("/home/User/Proj") == "/home/User/Proj"
    assert codex_sessions._norm_path("/home/User/Proj/") == "/home/User/Proj"
    # 大小写不同 → 归一结果不同（POSIX 敏感）
    assert codex_sessions._norm_path("/home/user/Proj") != codex_sessions._norm_path("/home/user/proj")
    # 反斜杠是合法文件名字符，不得当分隔符归一
    assert codex_sessions._norm_path("/home/a\\b") == "/home/a\\b"
    # 剥 \\?\ 前缀行为保留
    assert codex_sessions._norm_path("\\\\?\\/home/x") == "/home/x"


# ── kimi _same_path ───────────────────────────────────────────


def test_same_path_windows_branch(tmp_path, monkeypatch):
    """Windows：大小写/分隔符不敏感（保持旧行为）。"""
    monkeypatch.setattr(kimi_sessions, "_IS_WINDOWS", True)
    d = tmp_path / "WorkDir"
    d.mkdir()
    assert kimi_sessions._same_path(str(d), str(d).lower())
    assert kimi_sessions._same_path(str(d), str(d).replace("\\", "/"))
    assert not kimi_sessions._same_path(str(d), str(tmp_path / "Other"))


def test_same_path_posix_branch(monkeypatch):
    """POSIX：大小写敏感；反斜杠不当作分隔符等价物。

    注：pathlib.Path 的解析是宿主原生的（Windows 宿主上 / 锚到当前盘），
    但相等性断言对宿主不敏感——两侧走同一解析规则。
    """
    monkeypatch.setattr(kimi_sessions, "_IS_WINDOWS", False)
    monkeypatch.setattr(os, "path", posixpath)
    assert kimi_sessions._same_path("/home/u/Proj", "/home/u/Proj")
    assert kimi_sessions._same_path("/home/u//Proj", "/home/u/Proj")
    # 大小写不同 → 不同路径（旧实现 .lower() 会误判相等）
    assert not kimi_sessions._same_path("/home/u/Proj", "/home/u/proj")


# ── cbc 树浏览器 ──────────────────────────────────────────────


def _make_cbc_project(base: Path, dir_name: str, cwd: str, n_sessions: int = 1):
    """构造 fake cbc project 目录：含 cwd 字段的 JSONL 会话文件。"""
    proj = base / dir_name
    proj.mkdir(parents=True, exist_ok=True)
    for i in range(n_sessions):
        (proj / f"sess-{i}.jsonl").write_text(
            json.dumps({
                "id": f"e{i}", "timestamp": 1700000000000 + i,
                "type": "message", "role": "user",
                "content": [{"type": "text", "text": f"hello {dir_name} {i}"}],
                "cwd": cwd,
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


@pytest.fixture
def fake_cbc_home(monkeypatch, tmp_path):
    """把 ~/.codebuddy/projects 重定向到临时目录，并清理 _read_project_cwd 的 LRU。"""
    base = tmp_path / "projects"
    base.mkdir()
    real_expanduser = os.path.expanduser

    def fake_expanduser(p):
        if isinstance(p, str) and "~" in p:
            return str(base)
        return real_expanduser(p)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    cbc_sessions._read_project_cwd.cache_clear()
    yield base
    cbc_sessions._read_project_cwd.cache_clear()


def test_cbc_tree_posix_construction(fake_cbc_home, monkeypatch):
    """POSIX：无盘符、/ 分隔、大小写保留；根层按首段分组。"""
    monkeypatch.setattr(cbc_sessions, "_IS_WINDOWS", False)
    _make_cbc_project(fake_cbc_home, "home-u-alpha", "/home/u/alpha")
    _make_cbc_project(fake_cbc_home, "home-u-beta-deep", "/home/u/beta/deep")

    root = cbc_sessions.browse_cbc_tree()
    assert root["sessions"] == []
    assert [f["path"] for f in root["folders"]] == ["/home"]

    lvl1 = cbc_sessions.browse_cbc_tree("/home")
    assert [f["path"] for f in lvl1["folders"]] == ["/home/u"]

    lvl2 = cbc_sessions.browse_cbc_tree("/home/u")
    paths = [f["path"] for f in lvl2["folders"]]
    assert "/home/u/alpha" in paths
    assert "/home/u/beta" in paths
    beta = [f for f in lvl2["folders"] if f["path"] == "/home/u/beta"][0]
    assert beta["session_count"] == 1
    assert lvl2["breadcrumbs"] == [
        {"label": "home", "path": "/home"},
        {"label": "u", "path": "/home/u"},
    ]

    exact = cbc_sessions.browse_cbc_tree("/home/u/alpha")
    assert exact["total"] == 1
    assert exact["sessions"][0]["title"] == "hello home-u-alpha 0"

    # 前缀按路径段对齐：/home/u/al 不得误匹配 /home/u/alpha
    boundary = cbc_sessions.browse_cbc_tree("/home/u/al")
    assert boundary["total"] == 0 and boundary["folders"] == []


def test_cbc_tree_windows_native_unchanged(fake_cbc_home, monkeypatch):
    """Windows：盘符制行为不回归（大写段名、\\ 分隔、大小写不敏感匹配）。"""
    monkeypatch.setattr(cbc_sessions, "_IS_WINDOWS", True)
    _make_cbc_project(fake_cbc_home, "d-work-proja", "D:\\work\\projA")
    _make_cbc_project(fake_cbc_home, "d-work-deep-nested", "D:\\work\\deep\\nested")

    root = cbc_sessions.browse_cbc_tree()
    assert [f["path"] for f in root["folders"]] == ["D:"]

    lvl1 = cbc_sessions.browse_cbc_tree("D:")
    assert [f["path"] for f in lvl1["folders"]] == ["D:\\WORK"]

    lvl2 = cbc_sessions.browse_cbc_tree("D:\\WORK")
    paths = [f["path"] for f in lvl2["folders"]]
    assert "D:\\WORK\\PROJA" in paths
    assert "D:\\WORK\\DEEP" in paths

    exact = cbc_sessions.browse_cbc_tree("D:\\work\\proja")
    assert exact["total"] == 1
    assert exact["sessions"][0]["title"] == "hello d-work-proja 0"


def test_cbc_project_label_posix(fake_cbc_home, monkeypatch):
    """POSIX：无盘符 → drive 恒为空；cwd 真值优先，fallback 不臆造盘符。"""
    monkeypatch.setattr(cbc_sessions, "_IS_WINDOWS", False)
    _make_cbc_project(fake_cbc_home, "home-u-alpha", "/home/u/alpha")
    drive, label = cbc_sessions._parse_project_label("home-u-alpha")
    assert drive == ""
    assert label == "u/alpha"
    # 无 JSONL → fallback：原样返回目录名，不拆盘符
    drive2, label2 = cbc_sessions._parse_project_label("totally-unknown")
    assert drive2 == "" and label2 == "totally-unknown"
    # path 逆向 fallback：补回前导 /（有损，仅 fallback）
    assert cbc_sessions._project_dir_to_path("no-such-dir") == "/no/such/dir"
    # 有 JSONL 时 path_hint 用真值
    assert cbc_sessions._project_dir_to_path("home-u-alpha") == "/home/u/alpha"


def test_cbc_project_label_windows_unchanged(fake_cbc_home, monkeypatch):
    """Windows：盘符解析不回归。"""
    monkeypatch.setattr(cbc_sessions, "_IS_WINDOWS", True)
    _make_cbc_project(fake_cbc_home, "d-work-proja", "D:\\work\\projA")
    drive, label = cbc_sessions._parse_project_label("d-work-proja")
    assert drive == "D:"
    assert label == "work/projA"
    # fallback 仍产出盘符形态
    assert cbc_sessions._project_dir_to_path("d-work-x") == "D:\\WORK\\X"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
