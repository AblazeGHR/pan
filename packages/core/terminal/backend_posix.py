"""Non-durable Linux/macOS PTY adapter for the existing PtyRuntime contract.

openpty supplies terminal semantics; ProcessOwner supplies birth/group authority.
No registry, recovery ledger, authentication substitute or durable detach host.
Group absence is scoped evidence, not arbitrary descendant containment. Dead
leaders never grant stale-PGID signal authority. Close retains resources on unknown.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import select
import socket
import struct
import sys
import threading
import time

from packages.core.platform.process import OwnershipUnknownError, UnsupportedPlatformError
from packages.core.platform.process_posix import PosixProcessOwner
from .contracts import ProcessIdentity, ProcessProbe, ProcessStatus, ProcessOwnershipEvidence
from .ownership import UnverifiedOwnershipGate


class PosixPtyError(RuntimeError):
    pass


class PosixPtyClosed(PosixPtyError):
    pass


def _identity(value):
    return ProcessIdentity(pid=value.pid, image=value.image,
        native_identity=(value.platform, value.kind, value.birth, value.boot_id or ""))


def _dimensions(rows, cols):
    if (type(rows) is not int or type(cols) is not int
            or not (1 <= rows <= 65535 and 1 <= cols <= 65535)):
        raise ValueError("PTY rows/cols must be 1..65535")
    return struct.pack("HHHH", rows, cols, 0, 0)


class PosixPtyGuard:
    """Adapt the original retained ProcessOwner to TreeGuard, without extra authority."""
    def __init__(self, backend):
        self.backend = backend

    def describe(self):
        return {"kind": "posix-process-group", "os_level_guard": True,
            "containment": False, "durable_detach": False,
            "scope": "original group and observed strong-identity members"}

    def owned_pids(self, root_pid):
        b = self.backend
        if root_pid != b.pid:
            raise OwnershipUnknownError("foreign PTY root")
        with b._process_lock:
            b.owner._observe_members(b.owned, strict=True)
            return sorted(b.owned.members)

    def remaining(self, pids):
        b = self.backend
        with b._process_lock:
            # Unknown supplied identities are not silently treated as absent.
            if any(pid not in b.owned.members for pid in pids):
                raise OwnershipUnknownError("unobserved PTY member")
            settled = b.owner._exited_tree(b.owned)
            if settled is not None:
                return list(settled.remaining)
            b.owner._observe_members(b.owned, strict=True)
            return list(b.owner._member_remaining(tuple(b.owned.members.values()),
                b.owner._remaining(b.owned, b.owned.pgid)))

    def terminate_tree(self, root_pid, *, timeout):
        b = self.backend
        if root_pid != b.pid:
            raise OwnershipUnknownError("foreign PTY root")
        with b._process_lock:
            result = b.owner.kill_tree(b.owned, timeout=max(0., min(timeout, 30.)))
            return list(result.owned), list(result.remaining)


class PosixPtyBackend:
    def __init__(self, master, owner, owned):
        self._master = master
        self.owner, self.owned = owner, owned
        self.pid = owned.pid
        self.identity = _identity(owned.identity)
        self._io_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._process_lock = threading.RLock()
        self._closed = False
        self.guard = PosixPtyGuard(self)
        self.gate = UnverifiedOwnershipGate(ProcessOwnershipEvidence(
            assigned=True, atomic_with_spawn=True, identity=self.identity,
            handle_bound_for_cleanup=True, guard="posix-process-group",
            detail="setsid before fresh bootstrap; retained Popen/birth before exec admission; no containment"))
        # Match the Windows backend: drain kernel output immediately, even
        # before PtyRuntime starts. In particular, unread canonical echo must
        # not hold Darwin's terminal exit path behind an idle caller.
        self._cv = threading.Condition()
        self._buffer = bytearray()
        self._buffer_limit = 4 * 1024 * 1024
        self._pump_stop = threading.Event()
        self._pump_eof = False
        self._pump_error = None
        self._pump = threading.Thread(target=self._pump_output,
            name=f"posix-pty-pump-{self.pid}", daemon=True)
        self._pump.start()

    @classmethod
    def spawn(cls, argv, *, cwd=None, env=None, rows=24, cols=80, startup_timeout=5.):
        if sys.platform not in {"linux", "darwin"}:
            raise UnsupportedPlatformError("POSIX PTY requires Linux/macOS")
        import fcntl
        import termios
        size = _dimensions(rows, cols)
        if not argv:
            raise ValueError("PTY argv required")
        owner = PosixProcessOwner()
        master, slave = os.openpty()
        parent = child = owned = None
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, size)
            os.set_blocking(master, False)
            parent, child = socket.socketpair()
            parent.settimeout(max(.05, min(float(startup_timeout), 30.)))
            command = [sys.executable, str(Path(__file__).with_name("pty_bootstrap_posix.py")),
                       str(child.fileno()), *[str(x) for x in argv]]
            owned = owner.spawn(command, cwd=cwd, env=env, stdin=slave,
                stdout=slave, stderr=slave, pass_fds=(child.fileno(),))
            child.close()
            os.close(slave)
            slave = -1
            ready = b""
            while not ready.endswith(b"\n") and len(ready) < 128:
                chunk = parent.recv(128)
                if not chunk:
                    break
                ready += chunk
            if ready != b"ready\n":
                raise PosixPtyError("controlling PTY bootstrap failed")
            if not owner.check(owned.identity).verified_alive:
                raise OwnershipUnknownError("PTY birth unavailable before exec admission")
            parent.sendall(b"G")
            outcome = parent.recv(128)  # CLOEXEC EOF proves bootstrap exec, errors explicit.
            if outcome:
                if outcome == b"error:FileNotFoundError\n":
                    raise FileNotFoundError("PTY executable unavailable")
                raise PosixPtyError("PTY exec failed: " + outcome.decode("ascii", "replace").strip())
            return cls(master, owner, owned)
        except BaseException:
            if owned is not None:
                # Retained original ProcessOwner handles spawn-abort cleanup; never
                # replace uncertain cleanup with a success or discard owner state.
                try:
                    # An exec failure normally self-exits. Reap through the
                    # retained owner before deciding whether any signal is needed.
                    owner.wait(owned, timeout=.25)
                    result = owner.kill_tree(owned, timeout=2.)
                    if not result.confirmed:
                        raise OwnershipUnknownError("PTY failed startup cleanup unconfirmed")
                except BaseException as cleanup_error:
                    # Explicit resource owner is recoverable by the caller; no
                    # closed/success result and no forgotten master on failure.
                    cleanup_error.pty_backend = cls(master, owner, owned)
                    raise
                owned.close()
            os.close(master)
            raise
        finally:
            if slave >= 0:
                os.close(slave)
            if parent is not None:
                parent.close()
            if child is not None:
                child.close()

    def _fd(self):
        if self._closed:
            raise PosixPtyClosed("PTY handles closed")
        return self._master

    def _pump_output(self):
        try:
            while not self._pump_stop.is_set():
                with self._cv:
                    while len(self._buffer) >= self._buffer_limit and not self._pump_stop.is_set():
                        self._cv.wait(.05)
                if self._pump_stop.is_set():
                    return
                data = self._read_master(65536)
                if data:
                    with self._cv:
                        self._buffer.extend(data)
                        self._cv.notify_all()
        except EOFError:
            with self._cv:
                self._pump_eof = True
                self._cv.notify_all()
        except BaseException as exc:
            with self._cv:
                self._pump_error = exc
                self._cv.notify_all()

    def read(self, size):
        if size <= 0:
            raise ValueError("PTY read size must be positive")
        with self._cv:
            if not self._buffer and not self._pump_eof and self._pump_error is None and not self._closed:
                self._cv.wait(.05)
            if self._closed:
                raise PosixPtyClosed("PTY handles closed; cancellation is not peer EOF")
            if self._buffer:
                data = bytes(self._buffer[:size])
                del self._buffer[:size]
                self._cv.notify_all()
                return data
            if self._pump_error is not None:
                raise PosixPtyError("PTY output pump failed") from self._pump_error
            if self._pump_eof:
                raise EOFError("PTY slave EOF after buffered tail")
            return b""

    def _read_master(self, size):
        if size <= 0:
            raise ValueError("PTY read size must be positive")
        with self._io_lock:
            fd = self._fd()
        try:
            readable, _, _ = select.select([fd], [], [], .05)
        except (OSError, ValueError):
            with self._io_lock:
                self._fd()
            raise
        with self._io_lock:
            self._fd()
            if not readable:
                return b""
            try:
                data = os.read(fd, size)
            except BlockingIOError:
                return b""
            except OSError as exc:
                # Linux PTY master reports EIO when its last slave closes.
                if sys.platform.startswith("linux") and exc.errno == errno.EIO:
                    probe = select.poll()
                    probe.register(fd, select.POLLHUP)
                    if any(events & select.POLLHUP for _, events in probe.poll(0)):
                        raise EOFError("PTY slave hangup") from exc
                raise
            if not data:
                raise EOFError("PTY slave EOF")
            return data

    def write(self, data):
        deadline = time.monotonic() + 1.5
        if not self._write_lock.acquire(timeout=1.5):
            raise PosixPtyError("PTY writer busy")
        try:
            sent = 0
            while sent < len(data) and time.monotonic() < deadline:
                with self._io_lock:
                    fd = self._fd()
                    try:
                        sent += os.write(fd, data[sent:sent+65536])
                        continue
                    except BlockingIOError:
                        pass
                select.select([], [fd], [], min(.05, max(0., deadline-time.monotonic())))
            return sent
        finally:
            self._write_lock.release()

    def resize(self, rows, cols):
        import fcntl
        import termios
        size = _dimensions(rows, cols)
        with self._io_lock:
            fcntl.ioctl(self._fd(), termios.TIOCSWINSZ, size)

    def probe(self, pid):
        if pid != self.pid:
            return ProcessProbe(ProcessStatus.UNKNOWN, detail="foreign PID")
        with self._process_lock:
            if self.owned.popen.poll() is not None:
                return ProcessProbe(ProcessStatus.DEAD, self.identity, "retained Popen exit")
            check = self.owner.check(self.owned.identity)
            if check.verified_alive:
                return ProcessProbe(ProcessStatus.ALIVE, _identity(check.observed))
            return ProcessProbe(ProcessStatus.UNKNOWN, detail="PTY birth unavailable or mismatched")

    def alive(self):
        probe = self.probe(self.pid)
        if probe.status is ProcessStatus.UNKNOWN:
            raise OwnershipUnknownError(probe.detail)
        return probe.status is ProcessStatus.ALIVE

    def exit_code(self):
        with self._process_lock:
            return self.owned.popen.poll()

    def terminate(self, force):
        # A completed close already proved original root/group/member absence.
        # Match the retained-dead idempotence of the Windows backend.
        with self._process_lock:
            if self._closed:
                return
        if not force:
            self.write(b"\x03")
            return
        with self._process_lock:
            try:
                result = self.owner.terminate(self.owned, grace=.25)
            except (OSError, OwnershipUnknownError) as exc:
                # The leader may exit between birth check and signal (Darwin can
                # return EPERM for a group containing only an exiting zombie).
                # No second signal: wait/reap the original handle, then require
                # the original owner's read-only group AND member absence proof.
                self.owner.wait(self.owned, timeout=.25)
                settled = self.owner._exited_tree(self.owned)
                if settled is None or not settled.confirmed:
                    from packages.core.platform.process_posix import read_posix_identity
                    root, state = read_posix_identity(self.pid)
                    exc.add_note(str({"pty_root_code": self.owned.popen.returncode,
                        "root_observed": root is not None, "root_state": state,
                        "original_pgid": self.owned.pgid,
                        "known_members": sorted(self.owned.members),
                        "remaining": None if settled is None else settled.remaining,
                        "membership_unknown": self.owned.membership_unknown}))
                    exc.add_note("PTY signal failed; original read-only clearance unconfirmed; owner retained")
                    raise
                return
            if not result.exited or result.remaining:
                raise OwnershipUnknownError("PTY group/member cleanup unconfirmed")

    def close(self):
        with self._process_lock:
            if self._closed:
                return
            settled = self.owner._exited_tree(self.owned)
            if settled is None or not settled.confirmed:
                raise OwnershipUnknownError("PTY close refuses live/unknown original group")
            self._pump_stop.set()
            with self._cv:
                self._cv.notify_all()
            # The pump's select has a short deadline and holds no I/O lock.
            # Converge it before releasing the FD, preventing FD reuse races.
            self._pump.join(timeout=.5)
            if self._pump.is_alive():
                raise OwnershipUnknownError("PTY output pump did not converge; FD retained")
            with self._io_lock:
                os.close(self._master)
                self._closed = True
            with self._cv:
                self._cv.notify_all()
            self.owned.close()

    def describe(self):
        return {"backend": "PosixPtyBackend", "platform": sys.platform,
            "identity": self.identity.as_dict(), "guard": self.guard.describe(),
            "durable_detach": False, "closed": self._closed,
            "pump_buffer_bytes": len(self._buffer), "pump_eof": self._pump_eof,
            "pump_alive": self._pump.is_alive()}
