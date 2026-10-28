"""Short, private POSIX plugin socket endpoints, independent of profile paths."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile


class UnixEndpoint:
    """Own exactly one mkdtemp directory; never recursively delete caller data.

    macOS sockaddr_un is smaller than Linux's. Bound the encoded absolute path
    below 104 bytes, including room for the terminating NUL. TMPDIR on macOS
    can itself exceed that budget, so fall back to the OS shared /tmp directory.
    mkdtemp creates an unpredictable 0700 directory before Listener binds.
    """

    def __init__(self):
        self.directory = None
        self.identity = None
        for base in dict.fromkeys((tempfile.gettempdir(), '/tmp')):
            # Reserve suffix/name room without creating anything in a long root.
            if len(os.fsencode(str(Path(base).resolve()))) + 32 >= 104:
                continue
            directory = Path(tempfile.mkdtemp(prefix='pan-ipc-', dir=base))
            try:
                os.chmod(directory, 0o700)
                info = directory.lstat()
                address = str(directory / 'bridge.sock')
                if len(os.fsencode(address)) >= 104:
                    directory.rmdir()
                    continue
                self.directory = directory
                self.identity = (info.st_dev, info.st_ino)
                self.address = address
                return
            except BaseException:
                directory.rmdir()
                raise
        raise OSError('No short private Unix plugin socket path available')

    def close(self):
        directory = self.directory
        if directory is None:
            return
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self.identity:
            raise RuntimeError('Plugin IPC directory identity changed; cleanup refused')
        # Listener owns/unlinks bridge.sock. Unknown entries must never be deleted.
        directory.rmdir()
        self.directory = None
