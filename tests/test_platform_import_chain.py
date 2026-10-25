"""平台导入链门禁：POSIX 导入不得触发 Windows-only 模块。

三层证据（严格区分，不互相冒充）：

1. **模拟 POSIX**（子进程）：把 ``sys.platform`` 钉成 ``linux`` 后导入 P0 链与
   ``packages.web.server``，并用 import hook 断言没有任何 ``packages.*`` 模块在
   导入期拉 Windows-only 模块（``ctypes.wintypes`` / ``msvcrt`` / ``winreg`` /
   ``pywinpty`` / ``win32*``）。
2. **非 Windows 行为**：``JobObjectGuard`` / ``kernel32()`` 明确抛
   ``WindowsApiUnavailable``，不是 ``AttributeError``/``NameError``。
3. **静态 AST 门禁**：共享模块的模块级导入（平台 ``if`` 门之外的）不得包含
   Windows-only 模块——这是根 ``AGENTS.md`` 的硬规则。

边界：Windows 上无法用 ``sys.platform`` 伪造完整 POSIX 运行环境（psutil 等第三方
按 ``sys.platform`` 选择平台实现），因此第 1 项在翻转平台前先按真实平台预载第三
方依赖；真实 POSIX 证据由 ubuntu/macOS CI 的
``python -c "import packages.web.server"`` 提供，本文件不伪造。
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_SIMULATED_POSIX_IMPORT = r"""
import builtins
import importlib
import sys

# 第三方依赖先按真实平台加载：psutil 等在 import 期按 sys.platform 选实现，
# 翻转平台后再加载会测到模拟器本身而不是 Pan 的导入链。
for name in ("psutil", "fastapi", "uvicorn", "websockets", "httpx", "mcp",
             "jsonschema", "jwt", "pydantic", "starlette"):
    try:
        importlib.import_module(name)
    except Exception:
        pass

sys.platform = "linux"

WIN_ONLY = {"msvcrt", "winreg", "_winreg", "winpty", "pywinpty"}


def win_only(name, fromlist):
    if name in WIN_ONLY:
        return name
    if name == "ctypes" and fromlist and "wintypes" in fromlist:
        return "ctypes.wintypes"
    if name.startswith("win32") or name.startswith("pywin"):
        return name
    return None


original = builtins.__import__
violations = []


def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    hit = win_only(name, fromlist)
    if hit:
        caller = (globals or {}).get("__name__") or "?"
        if caller.startswith("packages") or caller in ("main", "__main__"):
            violations.append(caller + " -> " + hit)
    return original(name, globals, locals, fromlist, level)


builtins.__import__ = guarded
import packages.web.server  # noqa: F401
import packages.core.terminal.backend  # noqa: F401
import packages.core.terminal.guard  # noqa: F401
import packages.core.terminal.identity  # noqa: F401
import packages.core.terminal.spawn_win  # noqa: F401
import packages.core.rewind.driver  # noqa: F401
import packages.core.rewind.pty_session  # noqa: F401
builtins.__import__ = original

if violations:
    raise SystemExit(
        "project modules imported Windows-only modules: " + "; ".join(sorted(set(violations)))
    )
from packages.core import platform as platform_layer
from packages.core.terminal import guard
assert platform_layer.platform_name() == "linux", platform_layer.platform_name()
assert guard._WINDOWS is False
print("ok")
"""

_GUARD_ON_POSIX = r"""
import sys

sys.platform = "linux"
assert "ctypes.wintypes" not in sys.modules

import packages.core.terminal.guard as guard
import packages.core.terminal.identity as identity
import packages.core.terminal.spawn_win as spawn_win

assert "ctypes.wintypes" not in sys.modules, "guard/identity/spawn_win loaded ctypes.wintypes"
assert guard._WINDOWS is False and identity._WINDOWS is False and spawn_win._WINDOWS is False

for label, call in (
    ("JobObjectGuard", lambda: guard.JobObjectGuard()),
    ("kernel32", identity.kernel32),
    ("spawn _api", spawn_win._api),
):
    try:
        call()
    except identity.WindowsApiUnavailable:
        pass
    else:
        raise SystemExit(label + " did not raise WindowsApiUnavailable on POSIX")
print("ok")
"""


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_simulated_posix_import_chain_loads_no_windows_modules():
    result = _run(_SIMULATED_POSIX_IMPORT)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.strip().endswith("ok")


def test_guard_and_windows_apis_fail_closed_on_posix():
    result = _run(_GUARD_ON_POSIX)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.strip().endswith("ok")


# ── 静态 AST 门禁 ─────────────────────────────────────────────

_WIN_ONLY_MODULES = {"msvcrt", "winreg", "_winreg", "winpty", "pywinpty"}

_SHARED_MODULES = (
    "packages/web/server.py",
    "packages/web/terminal_api.py",
    "packages/web/terminal_ws.py",
    "packages/web/terminal_tools_api.py",
    "packages/web/native_control_api.py",
    "packages/core/platform/__init__.py",
)

_SHARED_DIRECTORIES = (
    "packages/core/terminal",
    "packages/core/rewind",
)


def _shared_files() -> list[Path]:
    files = [ROOT / rel for rel in _SHARED_MODULES]
    for directory in _SHARED_DIRECTORIES:
        files.extend(sorted((ROOT / directory).glob("*.py")))
    return files


def _module_level_imports(body):
    """模块级导入：平台 ``if`` 门内的豁免；``try`` 体内仍算导入期。"""
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.Try):
            yield from _module_level_imports(node.body)
            for handler in node.handlers:
                yield from _module_level_imports(handler.body)


def _win_only_target(node) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.name.split(".")[0]
            if name in _WIN_ONLY_MODULES or name.startswith("win32") or name.startswith("pywin"):
                return alias.name
        return None
    module = node.module or ""
    if module == "ctypes":
        for alias in node.names:
            if alias.name == "wintypes":
                return "ctypes.wintypes"
        return None
    root = module.split(".")[0]
    if root in _WIN_ONLY_MODULES or root.startswith("win32") or root.startswith("pywin"):
        return module
    return None


def test_shared_modules_have_no_unguarded_windows_imports():
    files = _shared_files()
    assert len(files) >= 20, "shared module list shrank unexpectedly"
    violations: list[str] = []
    for path in files:
        assert path.is_file(), path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _module_level_imports(tree.body):
            target = _win_only_target(node)
            if target:
                rel = path.relative_to(ROOT).as_posix()
                violations.append(f"{rel}:{node.lineno} imports {target}")
    assert not violations, "unguarded Windows-only imports: " + "; ".join(violations)


def test_platform_layer_imports_only_stdlib():
    tree = ast.parse((ROOT / "packages/core/platform/__init__.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in _module_level_imports(tree.body):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        else:
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "sys", "dataclasses", "typing"}, imported
