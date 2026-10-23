"""Reviewable current-user CLI registration; no service operations or admin rights.

Manual register without --user-path only modifies the explicit bin directory.
The auto operation registers current-user PATH from setup/start entry points.
No Git metadata or branch name is required. Uninstall
requires exact owned bytes and never removes a pre-existing PATH entry.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

MANIFEST = "pan-cli-registration.json"


def psquote(value):
    return "'" + str(value).replace("'", "''") + "'"


def render(root, python):
    script = (f"& {psquote(python)} {psquote(root / 'scripts/pan_cli.py')} --root {psquote(root)} @args\r\n"
              "exit $LASTEXITCODE\r\n").encode("utf-8-sig")
    wrapper = ("@echo off\r\nrem Pan owned CLI shim; do not edit\r\n"
               'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0pan.ps1" %*\r\n'
               "exit /b %ERRORLEVEL%\r\n").encode("ascii")
    return {"pan.cmd": wrapper, "pan.ps1": script}


def path_conflicts(directory, search_path):
    result = []
    for item in search_path.split(os.pathsep):
        if not item:
            continue
        folder = Path(os.path.expandvars(item.strip('"'))).resolve()
        if folder == directory:
            continue
        for suffix in ("", ".exe", ".com", ".bat", ".cmd", ".ps1"):
            candidate = folder / ("pan" + suffix)
            if candidate.exists():
                result.append(str(candidate))
    return result


def user_path(value=None):
    import winreg
    access = winreg.KEY_READ | (winreg.KEY_WRITE if value is not None else 0)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, access) as key:
        if value is not None:
            try:
                _, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                kind = winreg.REG_EXPAND_SZ
            winreg.SetValueEx(key, "Path", 0, kind, value)
        try:
            return winreg.QueryValueEx(key, "Path")[0]
        except FileNotFoundError:
            return ""


def owned(directory, manifest):
    if manifest.get("schema") != 1 or set(manifest.get("files", {})) != {"pan.cmd", "pan.ps1"}:
        raise ValueError("Unrecognized registration manifest")
    for name, encoded in manifest["files"].items():
        target = directory / name
        if target.is_symlink() or target.read_bytes() != base64.b64decode(encoded):
            raise ValueError(f"Registration conflict: {target} is not unchanged owned content")


def register(directory, root, python, *, change_path=False, search_path=None):
    if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
        raise ValueError("Registration directory cannot be a link/junction")
    directory, root = directory.resolve(), root.resolve()
    if not (root / "scripts/pan_cli.py").is_file() or not Path(python).is_file():
        raise ValueError("Stable CLI entry point and absolute Python executable must exist")
    if change_path:
        if os.name != "nt":
            raise ValueError("User PATH registration is Windows-only")
    conflicts = path_conflicts(directory, os.environ.get("PATH", "") if search_path is None else search_path)
    if conflicts:
        raise ValueError(f"Unrelated pan command(s) already on PATH; refusing registration: {conflicts}")
    if change_path:
        conflicts = path_conflicts(directory, user_path())
        if conflicts:
            raise ValueError(f"Unrelated pan command(s) on user PATH: {conflicts}")
    files = render(root, python)
    manifest_path = directory / MANIFEST
    if directory.is_symlink() or (directory.exists() and getattr(directory, "is_junction", lambda: False)()):
        raise ValueError("Registration directory cannot be a link/junction")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        owned(directory, manifest)
        if manifest.get("root") != str(root) or any(
                base64.b64decode(manifest["files"][name]) != data for name, data in files.items()):
            raise ValueError("Existing registration targets another checkout/interpreter; uninstall explicitly first")
        if bool(manifest.get("userPath")) != change_path:
            raise ValueError("Existing registration has a different PATH mode")
        if change_path and not any(Path(os.path.expandvars(x)).resolve() == directory
                                   for x in user_path().split(os.pathsep) if x):
            raise ValueError("Owned shims exist but user PATH entry is missing; registration is not active")
        return manifest
    directory.mkdir(parents=True, exist_ok=True)
    if any((directory / name).exists() for name in files):
        raise ValueError("Registration files already exist without ownership; refusing overwrite")
    old_path = user_path() if change_path else None
    entries = old_path.split(os.pathsep) if old_path else []
    added = change_path and not any(Path(os.path.expandvars(x)).resolve() == directory for x in entries if x)
    manifest = {"schema": 1, "root": str(root), "python": str(python), "userPath": change_path,
                "pathAdded": added, "files": {name: base64.b64encode(data).decode() for name, data in files.items()}}
    created = []
    manifest_created = False
    path_attempted = False
    try:
        for name, data in files.items():
            with (directory / name).open("xb") as handle:
                handle.write(data)
            created.append(name)
        with manifest_path.open("x", encoding="utf-8") as handle:
            manifest_created = True
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        if added:
            value = (old_path + os.pathsep if old_path else "") + str(directory)
            # Recheck before writing; never overwrite concurrent user edits.
            if user_path() != old_path:
                raise ValueError("User PATH changed during registration; retry explicitly")
            path_attempted = True
            if user_path(value) != value:
                raise ValueError("User PATH readback failed")
    except Exception:
        if path_attempted:
            try:
                if user_path() == value:
                    user_path(old_path)
            except OSError:
                # Retain ownership evidence when rollback cannot be verified.
                raise
        # Only clean files just created by this operation, never existing commands.
        for name in created:
            target = directory / name
            if not target.is_symlink() and target.read_bytes() == files[name]:
                target.unlink()
        if manifest_created and not any((directory / name).exists() for name in created):
            manifest_path.unlink(missing_ok=True)
        raise
    return manifest


def unregister(directory, *, change_path=False):
    if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
        raise ValueError("Registration directory cannot be a link/junction")
    directory = directory.resolve()
    manifest_path = directory / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    owned(directory, manifest)
    if bool(manifest.get("userPath")) != change_path:
        raise ValueError("Pass the original --user-path mode for uninstall")
    if manifest.get("pathAdded"):
        value = os.pathsep.join(x for x in user_path().split(os.pathsep)
                               if not x or Path(os.path.expandvars(x)).resolve() != directory)
        if user_path(value) != value:
            raise ValueError("User PATH removal readback failed; shims retained")
    for name in manifest["files"]:
        (directory / name).unlink()
    manifest_path.unlink()


def notify_environment():
    import ctypes
    from ctypes import wintypes
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    send(0xffff, 0x1a, 0, "Environment", 2, 1000, None)


def auto_register(root, python, *, directory=None, search_path=None):
    """Explicit Windows install/start hook. Never called by imports or Workers.

    Failure is diagnostic only: registration must not prevent service startup.
    Tests inject a temporary directory and replace user_path with memory storage.
    """
    if os.name != "nt":
        return 0
    try:
        if directory is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local or not Path(local).is_absolute():
                raise ValueError("LOCALAPPDATA must be an absolute directory")
            directory = Path(local) / "Pan" / "bin"
        register(directory, root, python, change_path=True, search_path=search_path)
        notify_environment()
        print(f"Pan CLI registered in {directory}; open a new terminal to use pan.")
    except Exception as exc:
        print(f"[WARN] Pan CLI automatic registration failed: {exc}. "
              "Startup continues; resolve the conflict/permissions and rerun setup or start. "
              "See docs/PAN_CLI.md.", file=sys.stderr)
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=("register", "unregister", "auto"))
    p.add_argument("--root", type=Path, help="checkout containing the CLI entry point (required for register/auto)")
    p.add_argument("--python", type=Path, default=Path(sys.executable))
    p.add_argument("--bin-dir", type=Path, help="owned shim directory; auto defaults to LOCALAPPDATA/Pan/bin")
    p.add_argument("--user-path", action="store_true", help="update current-user PATH; no Git or branch requirement")
    args = p.parse_args()
    if args.operation == "auto":
        if args.root is None:
            p.error("--root is required for auto")
        return auto_register(args.root, args.python.resolve(), directory=args.bin_dir)
    if args.bin_dir is None:
        p.error("--bin-dir is required for register/unregister")
    try:
        if args.operation == "register":
            if args.root is None:
                p.error("--root is required for register")
            result = register(args.bin_dir, args.root, args.python.resolve(), change_path=args.user_path)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            unregister(args.bin_dir, change_path=args.user_path)
            print("Owned Pan CLI registration removed")
        if args.user_path:
            # Notify newly launched shells; existing process environments remain unchanged.
            notify_environment()
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"pan registration: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
