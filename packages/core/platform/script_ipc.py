"""Local-only Windows message pipe compatible with multiprocessing clients."""
import os

PIPE_REJECT_REMOTE_CLIENTS = 0x00000008


def listener(address):
    if os.name != 'nt':
        raise OSError('script IPC is unsupported on this platform')
    import _winapi
    from multiprocessing.connection import PipeListener
    from packages.core.terminal import win_pipe

    class LocalListener(PipeListener):
        def _new_handle(self, first=False):
            open_mode = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
            if first:
                open_mode |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            mode = (_winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE |
                    _winapi.PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS)
            security = win_pipe.OwnerOnlySecurity()
            try:
                handle = win_pipe._k32.CreateNamedPipeW(
                    self._address, open_mode, mode, _winapi.PIPE_UNLIMITED_INSTANCES,
                    8192, 8192, _winapi.NMPWAIT_WAIT_FOREVER, security.security_attributes)
                if win_pipe.is_invalid_handle(handle):
                    raise OSError('local script pipe creation failed')
                return int(handle)
            finally:
                security.close()

    return LocalListener(address)
