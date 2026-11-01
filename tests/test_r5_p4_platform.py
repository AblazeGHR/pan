"""Platform boundary contracts for the retained executor primitives."""
import ast
from pathlib import Path

import pytest

from packages.core import platform
from packages.core.platform.process import UnsupportedPlatformError


def test_hidden_desktop_capability_is_explicit_on_each_platform():
    assert platform.supports("process.hiddenDesktop", "win32") is True
    for value in ("linux", "darwin", "unknown"):
        assert platform.supports("process.hiddenDesktop", value) is False


def test_native_facades_refuse_unsupported_before_opening_handles(monkeypatch):
    from packages.jobs.desktop import HiddenDesktop, DesktopCommand
    from packages.jobs.restricted_token import HiddenJobToken
    monkeypatch.setattr(platform, "supports", lambda _: False)
    for cls, args in ((HiddenDesktop, ()), (DesktopCommand, ([], ".", None)), (HiddenJobToken, ())):
        with pytest.raises(UnsupportedPlatformError, match="process.hiddenDesktop"):
            cls(*args)


@pytest.mark.parametrize("relative", [
    "packages/jobs/desktop.py", "packages/jobs/restricted_token.py",
    "packages/jobs/owned_process.py", "packages/jobs/independent_runner.py",
    "packages/jobs/independent_service.py", "packages/jobs/independent_execution_runner.py",
    "packages/jobs/independent_executor.py", "packages/core/main_lifecycle.py",
])
def test_shared_executor_imports_have_no_top_level_windows_dependencies(relative):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / relative).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"ctypes.wintypes", "msvcrt", "winreg"}
            assert not (node.module == "ctypes" and any(name.name == "wintypes" for name in node.names))
        if isinstance(node, ast.Import):
            assert not any(name.name in {"ctypes.wintypes", "msvcrt", "winreg"} for name in node.names)


def test_native_consumers_do_not_import_retired_runner_for_abi():
    root = Path(__file__).resolve().parents[1]
    for name in ("desktop_win.py", "restricted_token_win.py"):
        source = (root / "packages/core/platform" / name).read_text(encoding="utf-8")
        assert "packages.jobs.independent_runner" not in source
        assert "packages.core.platform.process_structs_win" in source


def test_action_registry_validation_does_not_import_retired_dispatcher(monkeypatch):
    import builtins
    from packages.actions.store import system_definition, validate_action
    original = builtins.__import__
    def guard(name, *args, **kwargs):
        assert name != "packages.jobs.system_actions", "registry depends on retired executor"
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guard)
    assert validate_action(system_definition("main-lifecycle"))["effect"] == "high_risk"


@pytest.mark.skipif(not platform.supports("process.hiddenDesktop"), reason="Windows CreateProcess ABI")
def test_create_process_structs_preserve_native_abi():
    import ctypes
    from packages.core.platform.process_structs_win import _STARTUPINFOW, _PROCESS_INFORMATION
    wide = ctypes.sizeof(ctypes.c_void_p) == 8
    assert ctypes.sizeof(_STARTUPINFOW) == (104 if wide else 68)
    assert ctypes.sizeof(_PROCESS_INFORMATION) == (24 if wide else 16)
