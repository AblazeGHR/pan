"""Bounded, re-openable console stdout/stderr logs for managed Pan launches.

``launcher._start_main`` no longer hands the console log files to the managed
child as stdout/stderr: a Windows venv ``python.exe`` is a redirector stub that
keeps its own copy of every inherited handle for the child's whole lifetime, so
the real interpreter could never rename the file while Pan ran.  The launcher
passes the destination paths and starts ``python -c <shim> main.py``; the shim
(``console_bootstrap``) opens the files itself for bootstrap crash evidence and
then calls :func:`install` here to take them over.

``install()`` re-points the process's own stdout/stderr at in-process pipes and
forwards every byte to a writer that opens, appends and closes the file.
Rotation is then a real rename-aside plus a fresh file, and output of child
processes that inherit the same streams (QQ/WeChat bots, provider children) is
captured by the same relay.

Design boundaries:

* The relay thread never logs and never raises: a broken log file must not stop
  Pan, and a warning written into the pipe it is draining could deadlock it.
  Degradation is observable through :func:`status` (``rotationFailures``,
  ``degraded``, ``overLimit``), surfaced by the persistence diagnostics API.
* No process holds a long-lived handle between writes: each chunk is appended
  with open/append/close.  That is what lets another process rotate the file at
  any time without truncating it, and it keeps a rotation from failing just
  because a second Pan instance kept its descriptor open.
* Append and rotation share one cross-process lock.  A writer that held an
  append handle while another process used that file as a rename destination
  would lose its bytes when Windows unlinks the replaced file.
* A failed rotation never truncates the active file.  Writes continue to the
  current file until a later attempt succeeds, so the size bound is soft while
  another process holds the file: it may exceed ``max_bytes`` by the bytes
  written during the degraded window.
"""

from __future__ import annotations

import atexit
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .console_bootstrap import ENV_ERR, ENV_OUT
from .data_retention import cross_process_file_lock
from .logging_setup import bounded_rotation_settings, rotate_whole_file

OUT_NAME = "pan-console.out.log"
ERR_NAME = "pan-console.err.log"

_CHUNK = 16 * 1024
#: One relay read is the only amount a file may exceed ``max_bytes`` by.
RELAY_CHUNK_BYTES = _CHUNK
_ROTATE_RETRY_MIN_SEC = 0.02
_ROTATE_RETRY_MAX_SEC = 5.0
_MAX_CONSECUTIVE_READ_ERRORS = 100

_state: dict | None = None
_state_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RotatingByteLog:
    """Append bytes to one file and rename it aside at ``max_bytes``.

    Each chunk is appended with a short-lived ``O_APPEND`` handle, so any
    process may rename the active file between appends.  When the rename fails
    (another handle without delete sharing) the failure is recorded and the
    next write keeps appending to the current file instead of truncating it.
    """

    def __init__(self, path: str | os.PathLike[str], *, max_bytes: int,
                 backup_count: int, label: str = ""):
        self.path = Path(path)
        self.max_bytes = max(1, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self.label = label or self.path.name
        self._lock = threading.RLock()
        self._rotations = 0
        self._failures = 0
        self._degraded = False
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._last_rotation_at: str | None = None
        self._last_write_at = time.monotonic()
        self._retry_after = 0.0
        self._retry_delay = _ROTATE_RETRY_MIN_SEC
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # -- public API -----------------------------------------------------
    def last_write_at(self) -> float:
        return self._last_write_at

    def write(self, data: bytes) -> None:
        """Append one chunk.  Never raises; failures are recorded in status.

        The append itself runs under the same cross-process lock as rotation.
        Without it a second writer could hold an ``O_APPEND`` handle on a file
        that another process is about to use as a rename destination: Windows
        then unlinks that file while the handle still writes to it, and those
        bytes are lost.  Serializing the (short) append is what makes the
        multi-process promise "no lost, no duplicated lines" true.
        """
        if not data:
            return
        with self._lock:
            self._last_write_at = time.monotonic()
            try:
                with cross_process_file_lock(self._lock_path()):
                    self._rotate_locked()
                    if not self._append(data):
                        return
                    # Rotate right after the chunk that crossed the limit: the
                    # next writer must not keep growing an oversized file.
                    self._rotate_locked()
            except OSError as exc:
                # An unusable lock file must not cost the log line; rotation is
                # skipped for this chunk.  The body above never raises, so this
                # handler cannot double-append.
                self._fail(exc)
                self._append(data)

    def _lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    def _append(self, data: bytes) -> bool:
        """Append with bounded open retries; another process may be renaming."""
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        last: BaseException | None = None
        fd = None
        for attempt in range(5):
            try:
                fd = os.open(str(self.path), flags, 0o644)
                break
            except OSError as exc:
                last = exc
                time.sleep(0.005 * (attempt + 1))
        if fd is None:
            if last is not None:
                self._fail(last)
            return False
        try:
            # ``os.write`` may be short; loop so a line can never be cut in
            # half by a partial write (this file is the only record of it).
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short write to console log")
                view = view[written:]
            return True
        except OSError as exc:
            self._fail(exc)
            return False
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def status(self) -> dict:
        with self._lock:
            try:
                size = os.stat(self.path).st_size
            except OSError:
                size = 0
            return {
                "path": str(self.path),
                "maxBytes": self.max_bytes,
                "backupCount": self.backup_count,
                "sizeBytes": size,
                "overLimit": size >= self.max_bytes,
                "rotations": self._rotations,
                "rotationFailures": self._failures,
                "degraded": self._degraded,
                "lastError": self._last_error,
                "lastErrorAt": self._last_error_at,
                "lastRotationAt": self._last_rotation_at,
            }

    # -- internals ------------------------------------------------------
    def _rotate_locked(self) -> None:
        """Rotate the active file if it reached ``max_bytes``.  Caller locks."""
        if time.monotonic() < self._retry_after:
            return
        try:
            size = os.stat(self.path).st_size
        except OSError:
            return
        if size < self.max_bytes:
            return
        if not rotate_whole_file(self.path, self.backup_count):
            self._fail(OSError(
                f"{self.path.name} could not be renamed (still open elsewhere)"))
            return
        self._rotations += 1
        self._last_rotation_at = _utc_now()
        self._degraded = False
        self._retry_after = 0.0
        self._retry_delay = _ROTATE_RETRY_MIN_SEC

    def _fail(self, exc: BaseException) -> None:
        self._failures += 1
        self._degraded = True
        self._last_error = f"{type(exc).__name__}: {exc}"[:200]
        self._last_error_at = _utc_now()
        # Another writer's short-lived append handle makes a rename fail
        # transiently; back off exponentially so a long-held handle degrades
        # quietly while a one-off collision is retried almost immediately.
        self._retry_after = time.monotonic() + self._retry_delay
        self._retry_delay = min(_ROTATE_RETRY_MAX_SEC, self._retry_delay * 2)


def _make_pipe() -> tuple[int, int]:
    read_fd, write_fd = os.pipe()
    if os.name == "nt":
        try:
            import msvcrt
            msvcrt.setmode(read_fd, os.O_BINARY)
            msvcrt.setmode(write_fd, os.O_BINARY)
        except (OSError, ImportError):
            pass
    return read_fd, write_fd


def _close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _relay(read_fd: int, writer: RotatingByteLog) -> None:
    """Drain one pipe into its writer.  No logging, no propagation."""
    errors = 0
    while True:
        try:
            chunk = os.read(read_fd, _CHUNK)
        except OSError:
            errors += 1
            if errors >= _MAX_CONSECUTIVE_READ_ERRORS:
                return
            time.sleep(0.05)
            continue
        errors = 0
        if not chunk:
            return
        writer.write(chunk)


def _bind_native_std_handle(fd: int) -> None:
    """Point the Win32 standard handle at the same pipe as the CRT fd."""
    if os.name != "nt":
        return
    constant = {1: -11, 2: -12}.get(fd)  # STD_OUTPUT_HANDLE / STD_ERROR_HANDLE
    if constant is None:
        return
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(fd)
        if handle == -1:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetStdHandle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
        kernel32.SetStdHandle.restype = ctypes.c_int
        kernel32.SetStdHandle(constant & 0xffffffff, handle)
    except (OSError, ImportError, AttributeError):
        pass


def install(*, environ=None, config: dict | None = None) -> bool:
    """Forward this process's stdout/stderr into bounded console logs.

    Returns ``True`` when at least one stream is relayed.  A missing
    ``PAN_CONSOLE_OUT_LOG``/``PAN_CONSOLE_ERR_LOG`` means the launcher did not
    redirect (foreground ``python main.py``) and makes this a no-op.  Any
    failure leaves the process with its original streams.
    """
    global _state
    with _state_lock:
        if _state is not None:
            return True
        env = os.environ if environ is None else environ
        try:
            cfg = (load_config() if config is None else config).get("logging", {})
        except Exception:
            cfg = {}
        max_bytes, backups = bounded_rotation_settings(
            cfg, max_key="console_max_bytes", backup_key="console_backup_count")
        plan = []
        for env_name, stream_name in ((ENV_OUT, "stdout"), (ENV_ERR, "stderr")):
            raw = str(env.get(env_name) or "").strip()
            stream = getattr(sys, stream_name, None)
            if not raw or stream is None:
                continue
            try:
                fd = int(stream.fileno())
            except (AttributeError, OSError, ValueError):
                continue
            plan.append((raw, stream, fd))
        if not plan:
            return False

        entries: list[dict] = []
        try:
            for raw, stream, fd in plan:
                writer = RotatingByteLog(
                    raw, max_bytes=max_bytes, backup_count=backups, label=Path(raw).name)
                read_fd, write_fd = _make_pipe()
                entries.append({"writer": writer, "read_fd": read_fd,
                                "write_fd": write_fd, "stream": stream, "fd": fd})
        except OSError:
            for entry in entries:
                _close_fd(entry["read_fd"])
                _close_fd(entry["write_fd"])
            return False

        threads: list[threading.Thread] = []
        try:
            for entry in entries:
                thread = threading.Thread(
                    target=_relay, args=(entry["read_fd"], entry["writer"]),
                    name=f"pan-console-log:{entry['writer'].label}", daemon=True)
                thread.start()
                threads.append(thread)
        except RuntimeError:
            for entry in entries:
                _close_fd(entry["read_fd"])
                _close_fd(entry["write_fd"])
            return False

        installed: list[dict] = []
        for entry in entries:
            try:
                entry["stream"].flush()
            except Exception:
                pass
            try:
                os.dup2(entry["write_fd"], entry["fd"])
            except OSError:
                _close_fd(entry["write_fd"])
                continue
            _close_fd(entry["write_fd"])
            _bind_native_std_handle(entry["fd"])
            installed.append(entry)
        if not installed:
            return False
        _state = {
            "writers": {entry["writer"].label: entry["writer"] for entry in installed},
            "threads": threads,
        }
        atexit.register(shutdown)
        return True


def shutdown() -> None:
    """Flush the streams and let the relays drain before the process exits."""
    global _state
    with _state_lock:
        state, _state = _state, None
    if state is None:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        idle = now - max(writer.last_write_at() for writer in state["writers"].values())
        if idle >= 0.25:
            return
        time.sleep(0.01)


def status() -> dict:
    """Observable relay/rotation state for the diagnostics API."""
    state = _state
    if state is None:
        return {"active": False, "logs": {}}
    return {
        "active": True,
        "logs": {key: writer.status() for key, writer in state["writers"].items()},
    }
