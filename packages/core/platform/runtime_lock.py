"""Process lifetime exclusion for a local runtime authority.

The lock file is never deleted: unlinking a live lock would create two owners.
"""
import os


class RuntimeLock:
    def __init__(self, path):
        self.path = path
        self.file = None

    def acquire(self):
        from packages.core.platform import supports
        if not supports("job.runtimeOwner"):
            raise NotImplementedError("job.runtimeOwner unsupported on this platform")
        if self.file is not None:
            raise RuntimeError("runtime lock already held")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if not self.path.stat().st_size:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            elif os.name == "posix":
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                raise RuntimeError("runtime ownership unsupported on this platform")
        except BaseException:
            stream.close()
            raise
        self.file = stream

    def release(self):
        if self.file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None
