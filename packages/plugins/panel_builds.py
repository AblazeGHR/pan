"""Host-owned build store for ``web-app/v1`` plugin panels.

A panel build runs the argv declared by the plugin manifest inside the plugin
directory and copies the declared output into a Pan-owned directory. Serving
reads only that copy, so a rebuild cannot expose the rest of the disk, and a
failed build never replaces the last usable artifact.

This is path fencing for a local trusted build command, not a sandbox.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace

STATE_PROTOCOL = "pan-panel-state/v1"
BUILD_PROTOCOL = "pan-panel-build/v1"
DEFAULT_TIMEOUT = 300
LOG_LIMIT = 200_000
MAX_BUILD_FILES = 20_000
MAX_BUILD_BYTES = 512 * 1024 * 1024
RETAINED_BUILDS = 3
TERMINATE_TREE_TIMEOUT = 5.0
READER_DRAIN_TIMEOUT = 3.0
SPAWN_REAP_TIMEOUT = 2.0
SPAWN_TIMEOUT = 30.0
_CREATE_SUSPENDED = 0x00000004
_SAFE_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_MEDIA = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".cjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


class PanelNotReady(ValueError):
    """The panel has no enabled, successful build to serve."""


def _blank(plugin_id: str, panel_id: str) -> dict:
    return {"protocol": STATE_PROTOCOL, "pluginId": plugin_id, "panelId": panel_id,
            "enabled": False, "activeBuild": None, "status": "unbuilt",
            "lastError": None, "startedAt": None, "finishedAt": None,
            "builtAt": None, "log": ""}


def _milliseconds(value):
    return None if value is None else int(value * 1000)


def _entry_parts(entry: str) -> tuple[str, ...]:
    if not isinstance(entry, str) or not entry:
        raise ValueError("Panel entry is missing")
    path = PurePosixPath(entry.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(
            part in {"", ".", ".."} or ":" in part for part in path.parts):
        raise ValueError("Panel entry is not a relative path inside the build output")
    return path.parts


def normalize_asset_path(raw: str) -> list[str]:
    from urllib.parse import unquote
    text = unquote(raw or "").replace("\\", "/")
    if "\x00" in text:
        raise ValueError("Invalid panel asset path")
    parts = [part for part in text.strip("/").split("/") if part]
    if any(part in {".", ".."} or ":" in part for part in parts):
        raise ValueError("Invalid panel asset path")
    return parts


def media_type(path: Path) -> str:
    return _MEDIA.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")


def _resume_process(process_handle: int) -> None:
    """Resume a CREATE_SUSPENDED child; same primitive as terminal.emulator."""
    from ctypes import wintypes
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = int(ntdll.NtResumeProcess(wintypes.HANDLE(int(process_handle))))
    if status != 0:
        raise OSError(f"NtResumeProcess failed: 0x{status & 0xFFFFFFFF:08x}")


class _BuildProcess:
    """One build command in a host-owned tree guard.

    Windows: create suspended, assign to a KILL_ON_JOB_CLOSE Job, prove the
    membership, then resume. Every timeout/cancel/close path terminates the
    whole Job and verifies that no member remains (never a PID scan).
    POSIX: the child gets its own session and cleanup uses that process group.
    """

    def __init__(self, argv, cwd, append):
        self.argv = list(argv)
        self.cwd = Path(cwd)
        self.append = append
        self.proc = None
        self.guard = None
        self.pgid = None
        self.reader = None
        self.reader_done = threading.Event()
        self.reader_ready = threading.Event()
        self.reader_tid = None
        self.wake_read = None
        self.wake_write = None
        self.exit_event = None
        self.code = None
        self.terminated = 0
        self._spawn_thread = None
        self._spawn_event = None
        self._spawn_done = threading.Event()
        self._spawn_lock = threading.Lock()
        self._spawn_abandoned = False
        self._spawn_cleaned = False
        self._spawn_cleanup_done = threading.Event()
        self._spawn_receipt_cb = None
        self.spawn_error = None

    def spawn(self) -> None:
        from packages.jobs.window import command_window_options
        options = command_window_options()
        if sys.platform == "win32":
            from packages.core.terminal.guard import JobObjectGuard
            guard = JobObjectGuard(kill_on_close=True)
            flags = int(options.get("creationflags", 0)) | _CREATE_SUSPENDED
            try:
                proc = subprocess.Popen(
                    self.argv, cwd=str(self.cwd), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    creationflags=flags, startupinfo=options.get("startupinfo"))
            except Exception:
                guard.close()
                raise
            try:
                guard.assign(int(proc._handle))
                if not guard.is_member(int(proc._handle)):
                    raise OSError("build process did not join its own Job")
                active = guard.active_processes()
                if active is None or int(active) < 1:
                    raise OSError("build process was not counted in its own Job")
                _resume_process(int(proc._handle))
            except Exception:
                # This child never ran. It is our own suspended handle.
                proc.kill()
                try:
                    proc.wait(timeout=TERMINATE_TREE_TIMEOUT)
                except subprocess.TimeoutExpired:
                    pass
                guard.close()
                raise
            self.guard = guard
        else:
            proc = subprocess.Popen(
                self.argv, cwd=str(self.cwd), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True)
            # The session leader ID is the process group ID. Save it now:
            # after the root exits, os.getpgid(pid) no longer resolves, while
            # children can still be alive in that group.
            self.pgid = int(proc.pid)
            read_fd, write_fd = os.pipe()
            os.set_blocking(write_fd, False)
            self.wake_read, self.wake_write = read_fd, write_fd
        self.proc = proc
        self.reader = threading.Thread(target=self._read, name="panel-build-output", daemon=True)
        self.reader.start()

    def start_spawn(self, loop, receipt_callback=None) -> None:
        """Start process creation in an owned thread with a completion event."""
        if self._spawn_thread is not None:
            raise RuntimeError("spawn already started")
        self._spawn_receipt_cb = receipt_callback
        self._spawn_event = asyncio.Event()
        self._spawn_thread = threading.Thread(
            target=self._spawn_worker, args=(loop,), name="panel-build-spawn", daemon=True)
        self._spawn_thread.start()

    @property
    def spawn_event(self):
        return self._spawn_event

    def _spawn_worker(self, loop) -> None:
        error = None
        try:
            self.spawn()
        except BaseException as exc:
            error = exc
        with self._spawn_lock:
            self.spawn_error = error
            self._spawn_done.set()
            abandoned = self._spawn_abandoned
            has_process = self.proc is not None
            claim = abandoned and has_process and not self._spawn_cleaned
            if claim:
                self._spawn_cleaned = True
        try:
            if abandoned:
                if claim:
                    self._late_cleanup()
                elif not has_process:
                    self._emit_spawn_receipt(
                        "spawn_failed", str(error) if error else "spawn ended without a process")
        finally:
            try:
                loop.call_soon_threadsafe(self._spawn_event.set)
            except RuntimeError:
                pass  # the host loop is already closed; cleanup receipt stands.

    def abandon_spawn(self) -> None:
        with self._spawn_lock:
            self._spawn_abandoned = True

    def spawn_pending(self) -> bool:
        return self._spawn_thread is not None and not self._spawn_done.is_set()

    async def reap_spawn_after_cancel(self, timeout: float = SPAWN_REAP_TIMEOUT) -> str | None:
        """Take ownership of a late spawn result after task cancellation.

        The worker keeps the process until the spawn call returns. If it is
        still blocked, a receipt-backed reaper will clean it when it appears.
        """
        self.abandon_spawn()
        if self._spawn_event is None:
            return "spawn transaction is unavailable"
        try:
            await asyncio.wait_for(self._spawn_event.wait(), timeout)
        except TimeoutError:
            return "spawn still running; late creation is owned by the cleanup reaper"
        if self.proc is None:
            return None
        with self._spawn_lock:
            if self._spawn_cleaned:
                return None  # the spawn worker already reaped it
            self._spawn_cleaned = True
        errors = []
        try:
            errors = await asyncio.to_thread(self._abort_sync)
            if not await self._wait_reader(READER_DRAIN_TIMEOUT):
                cancel_error = await asyncio.to_thread(self._cancel_reader)
                if not await self._wait_reader(READER_DRAIN_TIMEOUT):
                    errors.append("build output reader did not stop")
                    if cancel_error:
                        errors.append(cancel_error)
                else:
                    await self._close_reader_if_done()
            else:
                await self._close_reader_if_done()
            dispose_error = await asyncio.to_thread(self._dispose_impl)
            if dispose_error:
                errors.append(dispose_error)
        except Exception as exc:
            errors.append(f"late spawn cleanup failed: {exc}")
        finally:
            self._spawn_cleanup_done.set()
        self._emit_spawn_receipt(
            "cleaned" if not errors else "unconfirmed", "; ".join(errors))
        return "; ".join(errors) or None

    def _late_cleanup(self) -> None:
        errors = []
        try:
            errors = self._abort_sync()
            cancel_error = self._cancel_reader()
            if cancel_error:
                errors.append(cancel_error)
            if self.reader_done.wait(READER_DRAIN_TIMEOUT):
                self._close_reader_sync()
            else:
                errors.append("build output reader did not stop")
            dispose_error = self._dispose_impl()
            if dispose_error:
                errors.append(dispose_error)
        except Exception as exc:
            errors.append(f"late spawn cleanup failed: {exc}")
        finally:
            self._spawn_cleanup_done.set()
        self._emit_spawn_receipt(
            "cleaned" if not errors else "unconfirmed", "; ".join(errors))

    def _emit_spawn_receipt(self, outcome: str, detail: str) -> None:
        if self._spawn_receipt_cb is None:
            return
        pid = getattr(self.proc, "pid", None)
        try:
            self._spawn_receipt_cb({"pid": pid, "outcome": outcome,
                                    "detail": detail[:500]})
        except Exception:
            pass  # A receipt callback must not keep a late process alive.

    def _read(self) -> None:
        try:
            if sys.platform == "win32":
                self._read_windows()
            else:
                self._read_posix()
        finally:
            self.reader_done.set()

    def _read_windows(self) -> None:
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        self.reader_tid = int(kernel.GetCurrentThreadId())
        self.reader_ready.set()
        try:
            while True:
                chunk = self.proc.stdout.read(4096)
                if not chunk:
                    break
                self.append(chunk.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass

    def _read_posix(self) -> None:
        import select
        fd = self.proc.stdout.fileno()
        self.reader_ready.set()
        try:
            while True:
                ready, _, _ = select.select([fd, self.wake_read], [], [], 0.2)
                if self.wake_read in ready:
                    try:
                        os.read(self.wake_read, 4096)
                    except OSError:
                        pass
                    break
                if fd in ready:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break
                    self.append(chunk.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass

    def _cancel_reader(self) -> str | None:
        """Interrupt a blocked reader without closing its BufferedReader.

        Windows uses CancelSynchronousIo on the reader's own thread, so the
        BufferedReader lock stays valid. POSIX wakes the reader through its
        own pipe. Never call BufferedReader.close() before reader_done.
        """
        if self.reader_done.is_set() or self.reader is None:
            return None
        if sys.platform == "win32":
            if not self.reader_ready.wait(1.0):
                return "build reader did not start"
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenThread.restype = wintypes.HANDLE
            kernel.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
            kernel.CancelSynchronousIo.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenThread(0x0001, False, int(self.reader_tid or 0))  # THREAD_TERMINATE
            if not handle:
                return f"OpenThread failed ({ctypes.get_last_error()})"
            try:
                ok = kernel.CancelSynchronousIo(handle)
                error = int(ctypes.get_last_error())
            finally:
                kernel.CloseHandle(handle)
            if not ok and not self.reader_done.is_set():
                if error == 1168:  # ERROR_NOT_FOUND: the read already ended
                    return None
                return f"CancelSynchronousIo failed ({error})"
            return None
        try:
            os.write(self.wake_write, b"x")
            return None
        except OSError as exc:
            return f"could not wake build reader ({exc})"

    async def _wait_reader(self, timeout: float) -> bool:
        return await asyncio.to_thread(self.reader_done.wait, timeout)

    def _close_reader_sync(self) -> None:
        if not self.reader_done.is_set():
            return
        try:
            self.proc.stdout.close()
        except (OSError, ValueError):
            pass
        for fd in (self.wake_read, self.wake_write):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.wake_read = self.wake_write = None

    async def _close_reader_if_done(self) -> str | None:
        if not self.reader_done.is_set():
            return "build output reader is still active"
        await asyncio.to_thread(self._close_reader_sync)
        return None

    def start_monitor(self, loop) -> None:
        self.exit_event = asyncio.Event()

        def monitor():
            self.code = self.proc.wait()
            loop.call_soon_threadsafe(self.exit_event.set)

        threading.Thread(target=monitor, name="panel-build-wait", daemon=True).start()

    async def wait_exit(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self.exit_event.wait(), timeout)
            return True
        except TimeoutError:
            return False

    def _terminate_tree(self) -> list[int]:
        if self.proc is None:
            return []
        if self.guard is not None:
            _owned, remaining = self.guard.terminate_tree(
                self.proc.pid, timeout=TERMINATE_TREE_TIMEOUT)
            return remaining
        if self.pgid is None:
            raise RuntimeError("build process group identity is unavailable")
        import signal
        sig = getattr(signal, "SIGKILL", signal.SIGTERM)
        try:
            os.killpg(self.pgid, sig)
        except ProcessLookupError:
            return []  # the whole saved group is already gone
        except PermissionError as exc:
            raise RuntimeError(f"permission denied terminating build group {self.pgid}") from exc
        except OSError as exc:
            raise RuntimeError(f"could not terminate build group {self.pgid}: {exc}") from exc
        deadline = time.monotonic() + TERMINATE_TREE_TIMEOUT
        while time.monotonic() < deadline:
            try:
                os.killpg(self.pgid, 0)
            except ProcessLookupError:
                return []
            except PermissionError as exc:
                raise RuntimeError(f"permission denied verifying build group {self.pgid}") from exc
            except OSError as exc:
                raise RuntimeError(f"could not verify build group {self.pgid}: {exc}") from exc
            time.sleep(0.02)
        return [self.pgid]

    def _confirm_empty(self) -> None:
        if self.guard is None:
            if self.pgid is None:
                raise RuntimeError("build process group identity is unavailable")
            try:
                os.killpg(self.pgid, 0)
            except ProcessLookupError:
                return
            except PermissionError as exc:
                raise RuntimeError(f"permission denied verifying build group {self.pgid}") from exc
            except OSError as exc:
                raise RuntimeError(f"could not verify build group {self.pgid}: {exc}") from exc
            raise RuntimeError(f"build process group {self.pgid} still exists")
        members = self.guard.member_pids()
        if members:
            raise RuntimeError(f"build process tree still has members {members}")

    def _terminate_and_confirm(self) -> None:
        remaining = self._terminate_tree()
        if remaining:
            raise RuntimeError(f"build process tree still has members {remaining}")
        self._confirm_empty()

    def _root_gone(self, timeout: float = TERMINATE_TREE_TIMEOUT) -> bool:
        if self.proc is None:
            return True
        try:
            self.proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    async def _drain_reader(self) -> str | None:
        if await self._wait_reader(READER_DRAIN_TIMEOUT):
            return await self._close_reader_if_done()
        try:
            await asyncio.to_thread(self._terminate_and_confirm)
        except Exception as exc:
            cancel_error = await asyncio.to_thread(self._cancel_reader)
            if await self._wait_reader(READER_DRAIN_TIMEOUT):
                await self._close_reader_if_done()
            return f"could not confirm build descendants exited: {exc}" + (
                f"; {cancel_error}" if cancel_error else "")
        if await self._wait_reader(READER_DRAIN_TIMEOUT):
            return await self._close_reader_if_done()
        cancel_error = await asyncio.to_thread(self._cancel_reader)
        if await self._wait_reader(READER_DRAIN_TIMEOUT):
            return await self._close_reader_if_done()
        return ("build output pipe did not close after the process tree ended"
                + (f"; {cancel_error}" if cancel_error else ""))

    def _live_members(self) -> list[int]:
        if self.guard is not None:
            return self.guard.member_pids()
        if self.pgid is None:
            raise RuntimeError("build process group identity is unavailable")
        try:
            os.killpg(self.pgid, 0)
        except ProcessLookupError:
            return []
        except PermissionError as exc:
            raise RuntimeError(f"permission denied verifying build group {self.pgid}") from exc
        except OSError as exc:
            raise RuntimeError(f"could not verify build group {self.pgid}: {exc}") from exc
        return [self.pgid]

    async def finish_root_exit(self) -> str | None:
        try:
            members = len(self._live_members())
        except Exception as exc:
            return f"could not confirm build descendants exited: {exc}"
        if members:
            try:
                await asyncio.to_thread(self._terminate_and_confirm)
            except Exception as exc:
                return f"could not confirm build descendants exited: {exc}"
            self.terminated = members
            self.append(f"\nTerminated {members} remaining build process(es).\n")
        return await self._drain_reader()

    def _abort_sync(self) -> list[str]:
        errors = []
        try:
            self._terminate_and_confirm()
        except Exception as exc:
            errors.append(f"build process tree not confirmed: {exc}")
        if not self._root_gone():
            try:
                self.proc.kill()
            except (OSError, ProcessLookupError):
                pass
            if not self._root_gone(2.0):
                errors.append("build root did not exit")
        return errors

    async def abort(self) -> str | None:
        if self.proc is None:
            return None
        if self.spawn_pending():
            return "spawn still running; cleanup is owned by the spawn transaction"
        claimed = False
        with self._spawn_lock:
            if self._spawn_cleaned:
                return None
            if self._spawn_thread is not None:
                self._spawn_cleaned = True
                claimed = True
        errors = []
        try:
            errors = await asyncio.to_thread(self._abort_sync)
            if not await self._wait_reader(READER_DRAIN_TIMEOUT):
                cancel_error = await asyncio.to_thread(self._cancel_reader)
                if not await self._wait_reader(READER_DRAIN_TIMEOUT):
                    errors.append("build output reader did not stop")
                    if cancel_error:
                        errors.append(cancel_error)
                else:
                    await self._close_reader_if_done()
            else:
                await self._close_reader_if_done()
        except Exception as exc:
            errors.append(f"build cleanup failed: {exc}")
        finally:
            if claimed:
                self._spawn_cleanup_done.set()
        if claimed and self._spawn_abandoned:
            self._emit_spawn_receipt(
                "cleaned" if not errors else "unconfirmed", "; ".join(errors))
        return "; ".join(errors) or None

    def dispose(self) -> str | None:
        if self.spawn_pending():
            return "spawn still in progress; cleanup is owned by the spawn reaper"
        if self._spawn_cleaned and not self._spawn_cleanup_done.is_set():
            return "late spawn cleanup is in progress"
        return self._dispose_impl()

    def _dispose_impl(self) -> str | None:
        error = None
        if self.guard is not None:
            if not self.guard.close():
                error = "could not close the build Job handle"
            self.guard = None
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.kill()
            except (OSError, ProcessLookupError):
                pass
        return error


class PanelBuildStore:
    """Persistent panel enable/build state plus immutable served artifacts."""

    def __init__(self, data_root):
        self.root = Path(data_root).resolve() / "plugin-panels"
        self._states: dict[tuple[str, str], dict] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._guard = asyncio.Lock()
        self._closed = False
        self._spawn_receipts: list[dict] = []
        self._receipt_lock = threading.Lock()

    # ── paths ──
    def _owned(self, plugin_id: str, panel_id: str) -> Path:
        if not _SAFE_ID.fullmatch(plugin_id or "") or not _SAFE_ID.fullmatch(panel_id or ""):
            raise ValueError("Invalid plugin panel identity")
        return self.root / plugin_id / panel_id

    def _state_path(self, plugin_id: str, panel_id: str) -> Path:
        return self._owned(plugin_id, panel_id) / "state.json"

    def _builds_root(self, plugin_id: str, panel_id: str) -> Path:
        return self._owned(plugin_id, panel_id) / "builds"

    def _build_dir(self, plugin_id: str, panel_id: str, build_id: str) -> Path:
        if not re.fullmatch(r"[0-9A-Za-z_-]{8,80}", build_id or ""):
            raise ValueError("Invalid panel build identity")
        return self._builds_root(plugin_id, panel_id) / build_id

    # ── state ──
    def _state(self, plugin_id: str, panel_id: str) -> dict:
        key = (plugin_id, panel_id)
        if key in self._states:
            return self._states[key]
        path = self._state_path(plugin_id, panel_id)
        state = _blank(plugin_id, panel_id)
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = None
            if isinstance(saved, dict) and saved.get("protocol") == STATE_PROTOCOL \
                    and saved.get("pluginId") == plugin_id and saved.get("panelId") == panel_id:
                state.update({k: saved[k] for k in state if k in saved})
                state["protocol"] = STATE_PROTOCOL
        if state["activeBuild"] and not (self._build_dir(plugin_id, panel_id, state["activeBuild"]) / ".pan-panel-build.json").is_file():
            state.update(activeBuild=None, enabled=False,
                         status="unbuilt" if state["status"] != "failed" else state["status"])
        if state["status"] == "building":
            # A build cannot survive a host restart; report it instead of
            # leaving the GUI waiting forever.
            state.update(status="failed", lastError="Build was interrupted by a Pan restart.",
                         finishedAt=time.time(),
                         log=(state.get("log") or "") + "\nBuild interrupted by a Pan restart.")
            self._save(state)
        self._states[key] = state
        return state

    def _save(self, state: dict) -> None:
        path = self._state_path(state["pluginId"], state["panelId"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def status(self, plugin_id: str, panel_id: str) -> dict:
        state = self._state(plugin_id, panel_id)
        return {"pluginId": plugin_id, "panelId": panel_id,
                "enabled": bool(state["enabled"]),
                "status": state["status"],
                "available": bool(state["activeBuild"]),
                "activeBuild": state["activeBuild"],
                "builtAt": _milliseconds(state["builtAt"]),
                "startedAt": _milliseconds(state["startedAt"]),
                "finishedAt": _milliseconds(state["finishedAt"]),
                "lastError": state["lastError"],
                "log": state["log"]}

    def is_enabled(self, plugin_id: str, panel_id: str) -> bool:
        return bool(self._state(plugin_id, panel_id)["enabled"] and self._state(plugin_id, panel_id)["activeBuild"])

    def set_enabled(self, plugin_id: str, panel_id: str, enabled: bool) -> dict:
        state = self._state(plugin_id, panel_id)
        if enabled and not state["activeBuild"]:
            raise ValueError("Panel has no successful build; build it before enabling")
        if enabled and not (self._build_dir(plugin_id, panel_id, state["activeBuild"]) / ".pan-panel-build.json").is_file():
            raise ValueError("Panel build artifact is missing; rebuild it before enabling")
        state["enabled"] = bool(enabled)
        self._save(state)
        return self.status(plugin_id, panel_id)

    def unbuild(self, plugin_id: str, panel_id: str) -> dict:
        running = self._tasks.get((plugin_id, panel_id))
        if running is not None and not running.done():
            raise ValueError("Cannot unbuild while a build is running")
        state = self._state(plugin_id, panel_id)
        builds = self._builds_root(plugin_id, panel_id)
        if builds.exists():
            shutil.rmtree(builds)
        state.update(enabled=False, activeBuild=None, status="unbuilt", lastError=None,
                     startedAt=None, finishedAt=None, builtAt=None, log="")
        self._save(state)
        return self.status(plugin_id, panel_id)

    def _record_spawn_receipt(self, plugin_id: str, panel_id: str, receipt: dict) -> None:
        with self._receipt_lock:
            self._spawn_receipts.append({
                "pluginId": plugin_id, "panelId": panel_id,
                "pid": receipt.get("pid"), "outcome": receipt.get("outcome"),
                "detail": receipt.get("detail", "")})

    def spawn_receipts(self) -> list[dict]:
        with self._receipt_lock:
            return [dict(item) for item in self._spawn_receipts]

    # ── build ──
    @staticmethod
    def _spec(panel: dict) -> dict:
        build = panel.get("build") or {}
        return {"command": list(build.get("command") or []),
                "cwd": Path(build.get("cwd") or "."),
                "output": Path(build.get("output") or ""),
                "entry": panel.get("entry"),
                "timeout": int(build.get("timeout_seconds", DEFAULT_TIMEOUT))}

    async def start(self, plugin_id: str, panel: dict) -> dict:
        if self._closed:
            raise ValueError("Panel build store is closed")
        spec = self._spec(panel)
        if not spec["command"]:
            raise ValueError("Panel has no build command")
        key = (plugin_id, panel["id"])
        async with self._guard:
            if self._closed:
                raise ValueError("Panel build store is closed")
            running = self._tasks.get(key)
            if running is not None and not running.done():
                return self.status(plugin_id, panel["id"])
            state = self._state(plugin_id, panel["id"])
            previous_status = state["status"]
            state.update(status="building", startedAt=time.time(), finishedAt=None,
                         lastError=None, log="")
            try:
                self._save(state)
            except Exception as exc:
                state.update(status=previous_status, lastError=f"Could not start the build: {exc}")
                raise ValueError(f"Could not persist build state: {exc}") from exc
            try:
                task = asyncio.create_task(self._run(key, plugin_id, panel["id"], spec),
                                           name=f"panel-build-{plugin_id}-{panel['id']}")
            except Exception as exc:
                state.update(status=previous_status, lastError=f"Could not start the build: {exc}")
                raise ValueError(f"Could not start the build: {exc}") from exc
            self._tasks[key] = task
            task.add_done_callback(lambda finished, k=key: self._retire(k, finished))
        return self.status(plugin_id, panel["id"])

    def _retire(self, key, task) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def wait(self, plugin_id: str, panel_id: str) -> dict:
        task = self._tasks.get((plugin_id, panel_id))
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self.status(plugin_id, panel_id)

    async def _run(self, key, plugin_id: str, panel_id: str, spec: dict) -> None:
        state = self._states[key]
        log: list[str] = []
        size = 0

        def append(text: str) -> None:
            nonlocal size
            log.append(text)
            size += len(text)
            while size > LOG_LIMIT and log:
                size -= len(log[0])
                log.pop(0)
            state["log"] = "".join(log)

        failure = None
        process = None
        staging = None
        published = False
        try:
            if not spec["output"].is_absolute() or not spec["cwd"].is_absolute():
                raise ValueError("Panel build paths must be resolved by the manifest loader")
            if not spec["cwd"].is_dir():
                raise ValueError("Panel build cwd does not exist")
            process = _BuildProcess(spec["command"], spec["cwd"], append)
            loop = asyncio.get_running_loop()
            process.start_spawn(
                loop, lambda receipt: self._record_spawn_receipt(plugin_id, panel_id, receipt))
            try:
                await asyncio.wait_for(process.spawn_event.wait(), SPAWN_TIMEOUT)
            except asyncio.CancelledError:
                receipt = await process.reap_spawn_after_cancel()
                failure = "Build was cancelled."
                if receipt:
                    failure += f" Spawn cleanup: {receipt}."
                raise
            except TimeoutError:
                receipt = await process.reap_spawn_after_cancel()
                failure = f"Build process creation timed out after {SPAWN_TIMEOUT} seconds."
                if receipt:
                    failure += f" Spawn cleanup: {receipt}."
                raise ValueError(failure)
            if process.spawn_error is not None:
                raise process.spawn_error
            process.start_monitor(loop)
            exited = await process.wait_exit(spec["timeout"])
            if not exited:
                failure = f"Build timed out after {spec['timeout']} seconds."
                cleanup = await process.abort()
                if cleanup:
                    failure += f" Cleanup: {cleanup}."
            else:
                cleanup = await process.finish_root_exit()
                if cleanup:
                    failure = f"Build process tree cleanup could not be confirmed: {cleanup}."
                    abort_error = await process.abort()
                    if abort_error:
                        failure += f" Cleanup: {abort_error}."
                elif process.code != 0:
                    failure = f"Build command exited with code {process.code}."
            if failure is None:
                build_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
                final = self._build_dir(plugin_id, panel_id, build_id)
                staging = self._builds_root(plugin_id, panel_id) / f".tmp-{build_id}"
                self._copy_output(spec["output"], staging, spec["entry"])
                metadata = {"protocol": BUILD_PROTOCOL, "pluginId": plugin_id,
                            "panelId": panel_id, "entry": spec["entry"],
                            "createdAt": time.time(), "command": spec["command"]}
                (staging / ".pan-panel-build.json").write_text(
                    json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, final)
                staging = None
                previous = state["activeBuild"]
                previous_status = state["status"]
                state.update(activeBuild=build_id, status="built", builtAt=metadata["createdAt"],
                             lastError=None)
                try:
                    # Publish the pointer before deleting anything: a failed
                    # write leaves the previous build and pointer intact.
                    self._save(state)
                    published = True
                except Exception as exc:
                    state.update(activeBuild=previous,
                                 status=previous_status if previous else "unbuilt",
                                 lastError=f"Could not publish the new build: {exc}")
                    shutil.rmtree(final, ignore_errors=True)
                    failure = f"Could not publish the new build: {exc}"
                if published:
                    try:
                        self._prune(plugin_id, panel_id, keep=build_id, previous=previous)
                    except OSError as exc:
                        append(f"\nCould not remove an older build: {exc}\n")
        except asyncio.CancelledError:
            if failure is None:
                failure = "Build was cancelled."
            if process is not None:
                cleanup = await process.abort()
                if cleanup:
                    failure += f" Cleanup: {cleanup}."
            raise
        except Exception as exc:  # build tooling failure stays in the panel state
            failure = str(exc) or exc.__class__.__name__
            if process is not None:
                cleanup = await process.abort()
                if cleanup:
                    failure += f" Cleanup: {cleanup}."
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            if process is not None:
                dispose_error = await asyncio.to_thread(process.dispose)
                if dispose_error:
                    failure = f"{failure or 'Build stopped.'} Cleanup: {dispose_error}."
            if failure is not None:
                state["status"] = "failed"
                state["lastError"] = failure
                append(("\n" if log else "") + failure + "\n")
            state["finishedAt"] = time.time()
            try:
                self._save(state)
            except Exception as exc:
                # Keep the in-memory terminal state visible; never leave
                # "building" merely because the state file could not be written.
                state["lastError"] = f"{state['lastError'] or failure or 'Build stopped.'} (state not saved: {exc})"
                if not published:
                    state["status"] = "failed"

    def _copy_output(self, source: Path, target: Path, entry: str) -> None:
        parts = _entry_parts(entry)
        root = source.resolve()
        if not root.is_dir():
            raise ValueError("Build output directory is missing")
        target.mkdir(parents=True, exist_ok=True)
        files = 0
        total = 0
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if resolved != root and root not in resolved.parents:
                continue
            relative = resolved.relative_to(root)
            destination = target / relative
            if resolved.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if not resolved.is_file():
                continue
            files += 1
            total += resolved.stat().st_size
            if files > MAX_BUILD_FILES or total > MAX_BUILD_BYTES:
                raise ValueError("Build output is too large to publish")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, destination)
        if not (target / Path(*parts)).is_file():
            raise ValueError("Build output does not contain the declared entry file")

    def _prune(self, plugin_id: str, panel_id: str, keep: str, previous: str | None) -> None:
        """Retain recent immutable builds for pages that are still open.

        Called only after the new activeBuild has been persisted. The active
        and previous successful builds are always kept in addition to the
        newest RETAINED_BUILDS; a failure here must never remove the active one.
        """
        root = self._builds_root(plugin_id, panel_id)
        if not root.exists():
            return
        protected = {keep} | ({previous} if previous else set())
        candidates = []
        for item in root.iterdir():
            if not item.is_dir() or item.name.startswith("."):
                continue
            metadata = item / ".pan-panel-build.json"
            try:
                created = json.loads(metadata.read_text(encoding="utf-8")).get("createdAt", 0)
            except (OSError, ValueError):
                created = 0
            candidates.append((float(created or 0), item))
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        retained_names = {item.name for _, item in candidates[:RETAINED_BUILDS]}
        for _, item in candidates:
            if item.name in protected or item.name in retained_names:
                continue
            shutil.rmtree(item, ignore_errors=True)
        for item in root.iterdir():
            if item.name.startswith(".tmp-"):
                shutil.rmtree(item, ignore_errors=True)

    # ── serving ──
    @staticmethod
    def asset_path(plugin_id: str, panel_id: str, build_id: str) -> str:
        return f"/panel-assets/{plugin_id}/{panel_id}/{build_id}/"

    def asset(self, plugin_id: str, panel_id: str, build_id: str, raw_path: str):
        state = self._state(plugin_id, panel_id)
        if not state["enabled"]:
            raise PanelNotReady("Panel is disabled")
        build = self._build_dir(plugin_id, panel_id, build_id)
        metadata_path = build / ".pan-panel-build.json"
        if not metadata_path.is_file():
            raise PanelNotReady("Panel build is no longer available")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        entry = Path(*_entry_parts(metadata.get("entry")))
        parts = normalize_asset_path(raw_path)
        if any(part.startswith(".pan-") for part in parts):
            return None  # Host build metadata is not part of the panel site.
        target = build.joinpath(*parts) if parts else build
        if target.is_dir():
            target = target / entry
        elif not target.exists() and parts and "." not in parts[-1]:
            target = build / entry  # SPA route without a file extension
        resolved = target.resolve()
        if resolved != build.resolve() and build.resolve() not in resolved.parents:
            raise ValueError("Panel asset path escapes the build output")
        if not resolved.is_file():
            return None
        # Immutable build URLs still revalidate the entry after a rebuild.
        return SimpleNamespace(path=resolved, media_type=media_type(resolved), cache="no-cache")

    async def close(self) -> None:
        async with self._guard:
            self._closed = True
            tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 20)
            except TimeoutError:
                for state in self._states.values():
                    if state["status"] == "building":
                        state.update(status="failed",
                                     lastError="Build did not stop during Pan shutdown.",
                                     finishedAt=time.time())
                        try:
                            self._save(state)
                        except OSError:
                            pass
                for task in tasks:
                    task.cancel()
        self._tasks.clear()
