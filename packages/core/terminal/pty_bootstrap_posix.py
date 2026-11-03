"""Fresh interpreter only: acquire controlling tty, await owner admission, exec same PID.

No preexec_fn or forkpty inside the threaded Pan host. The socket is a short-lived
spawn control channel, never an attach/detach endpoint or a persisted credential.
"""
from __future__ import annotations
import os
import socket
import sys


def main():
    import fcntl
    import termios
    control = socket.socket(fileno=int(sys.argv[1]))
    control.set_inheritable(False)
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        os.tcsetpgrp(0, os.getpgrp())
        os.dup2(0, 2)
        control.sendall(b"ready\n")
        if control.recv(1) != b"G":
            return 125
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    except BaseException as exc:
        # Fixed exception category, no argv, paths, terminal content or secrets.
        control.sendall(("error:" + type(exc).__name__ + "\n").encode("ascii"))
        return 127
    finally:
        control.close()


if __name__ == "__main__":
    raise SystemExit(main())
