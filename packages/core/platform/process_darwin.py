"""Darwin native process identity, loaded only by the POSIX platform boundary.

ABI sources (Apple XNU): bsd/sys/proc_info.h, proc_info_private.h and
bsd/kern/proc_info.c. Flavor 18 returns BSD info and unique identifiers from the
same referenced proc; do not assemble birth/group fields from separate PID calls.
The interface is runtime-probed, not assumed available on every macOS release.
"""
from __future__ import annotations

import ctypes
import sys
import uuid

from packages.core.platform.process import PLATFORM_POSIX, ProcessIdentity

PROC_PIDT_BSDINFOWITHUNIQID = 18


class BsdInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid',
        'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in (
        'nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64),
                ('start_usec', ctypes.c_uint64)]


class UniqueInfo(ctypes.Structure):
    _fields_ = [('executable_uuid', ctypes.c_ubyte * 16),
                ('uniqueid', ctypes.c_uint64), ('parent_uniqueid', ctypes.c_uint64),
                ('idversion', ctypes.c_int32), ('original_parent_version', ctypes.c_int32),
                ('reserved2', ctypes.c_uint64), ('reserved3', ctypes.c_uint64)]


class BsdInfoWithUniqueId(ctypes.Structure):
    _fields_ = [('bsd', BsdInfo), ('unique', UniqueInfo)]


def abi_valid() -> bool:
    return (ctypes.sizeof(BsdInfo) == 136 and ctypes.alignment(BsdInfo) == 8
            and ctypes.sizeof(UniqueInfo) == 56 and ctypes.alignment(UniqueInfo) == 8
            and ctypes.sizeof(BsdInfoWithUniqueId) == 192
            and ctypes.alignment(BsdInfoWithUniqueId) == 8
            and BsdInfo.status.offset == 4 and BsdInfo.pid.offset == 12
            and BsdInfo.pgid.offset == 100 and BsdInfo.start_sec.offset == 120
            and BsdInfo.start_usec.offset == 128
            and BsdInfoWithUniqueId.unique.offset == 136
            and UniqueInfo.uniqueid.offset == 16 and UniqueInfo.idversion.offset == 32)


def _libraries():
    if sys.platform != 'darwin' or not abi_valid():
        raise OSError('Darwin native ABI unavailable')
    libproc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    system = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                    ctypes.c_void_p, ctypes.c_int]
    libproc.proc_pidinfo.restype = ctypes.c_int
    system.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p,
                                   ctypes.c_size_t]
    system.sysctlbyname.restype = ctypes.c_int
    return libproc, system


def _boot_uuid(system) -> str:
    buffer = ctypes.create_string_buffer(64)
    size = ctypes.c_size_t(len(buffer))
    if system.sysctlbyname(b'kern.bootsessionuuid', buffer, ctypes.byref(size), None, 0) != 0:
        raise OSError('boot session UUID unavailable')
    if size.value != 37 or buffer.raw[36:37] != b'\0':
        raise ValueError('invalid boot UUID size/termination')
    value = buffer.raw[:36].decode('ascii')
    parsed = uuid.UUID(value)
    if parsed.int == 0 or str(parsed) != value.lower():
        raise ValueError('invalid boot UUID')
    return str(parsed)


def read_darwin_identity(pid: int) -> tuple[ProcessIdentity | None, str | None]:
    """Fail closed for missing symbol/flavor/UUID, short copyout or malformed data.

    Boot UUID reads bracket the single proc query; no record crosses boot domains.
    arg=1 includes zombies so they remain visible resources, not absent processes.
    """
    try:
        libproc, system = _libraries()
        before = _boot_uuid(system)
        info = BsdInfoWithUniqueId()
        copied = libproc.proc_pidinfo(int(pid), PROC_PIDT_BSDINFOWITHUNIQID, 1,
                                      ctypes.byref(info), ctypes.sizeof(info))
        after = _boot_uuid(system)
        if copied != ctypes.sizeof(info) or before != after:
            return None, None
        bsd, unique = info.bsd, info.unique
        if (bsd.pid != int(pid) or bsd.pgid <= 0 or bsd.start_sec <= 0
                or bsd.start_usec >= 1_000_000 or unique.uniqueid == 0
                or bsd.status not in {1, 2, 3, 4, 5, 6}):
            return None, None
        # The executable UUID is deliberately excluded: exec is not a new proc.
        birth = f'{unique.uniqueid}:{unique.idversion}:{bsd.start_sec}:{bsd.start_usec}'
        return ProcessIdentity(pid=int(pid), kind='proc_uniqueid', birth=birth,
                               platform=PLATFORM_POSIX, boot_id=before,
                               pgid=int(bsd.pgid)), 'Z' if bsd.status == 5 else 'S'
    except (OSError, AttributeError, ValueError, TypeError, OverflowError, ctypes.ArgumentError):
        return None, None
