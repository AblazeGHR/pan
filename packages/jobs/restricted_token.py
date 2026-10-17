"""Per-job token that keeps an ordinary console off the default terminal.

An owned hidden command that allocates a console is normally handed to the
per-user default terminal, whose window belongs to the user and is neither
attributable nor safe to touch. The default-terminal handoff opens the console
client with ``PROCESS_SET_INFORMATION``, so a child token whose DefaultDacl
denies the current user that one right makes the console fall back to a local
host on the child's own (private) desktop.

This module builds such a token per job and launches the child with
``CreateProcessAsUserW``:

- the token is a duplicate of the runner's own token, so the child keeps the
  same user, cwd, environment and stdio;
- the only change is the DefaultDacl deny ACE, and ``0x200`` is a process-only
  access right. DefaultDacl also seeds other object types: compatibility must
  be tested, and ordinary process SET_INFORMATION access is deliberately
  unavailable. An opener with enabled SeDebugPrivilege can bypass this DACL;
  this is console routing policy, not a boundary against elevated debuggers;
- the token is closed by the caller (``close()``) and is never written to any
  user or machine configuration;
- if the token cannot be built or the process cannot be created, the error is
  raised. There is deliberately no silent fallback to a launch path whose
  console could become visible.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w

TOKEN_QUERY = 0x0008
TOKEN_DUPLICATE = 0x0002
TOKEN_ASSIGN_PRIMARY = 0x0001
TOKEN_ADJUST_DEFAULT = 0x0080
TOKEN_ALL_ACCESS = 0x000F01FF
TOKEN_USER_CLASS = 1
TOKEN_DEFAULT_DACL_CLASS = 6
ACL_REVISION = 2
PROCESS_SET_INFORMATION = 0x200
MAXDWORD = 0xFFFFFFFF


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", w.DWORD)]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


class _TOKEN_DEFAULT_DACL(ctypes.Structure):
    _fields_ = [("DefaultDacl", ctypes.c_void_p)]


class _ACL(ctypes.Structure):
    _fields_ = [("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte),
                ("AclSize", w.WORD), ("AceCount", w.WORD), ("Sbz2", w.WORD)]


class _ACE_HEADER(ctypes.Structure):
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", w.WORD)]


def _api():
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi32.OpenProcessToken.restype = w.BOOL
    advapi32.CreateRestrictedToken.argtypes = [w.HANDLE, w.DWORD, w.DWORD, ctypes.c_void_p,
                                               w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.c_void_p,
                                               ctypes.POINTER(w.HANDLE)]
    advapi32.CreateRestrictedToken.restype = w.BOOL
    advapi32.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             w.DWORD, ctypes.POINTER(w.DWORD)]
    advapi32.GetTokenInformation.restype = w.BOOL
    advapi32.SetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    advapi32.SetTokenInformation.restype = w.BOOL
    advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD]
    advapi32.InitializeAcl.restype = w.BOOL
    advapi32.AddAccessDeniedAce.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p]
    advapi32.AddAccessDeniedAce.restype = w.BOOL
    advapi32.GetAce.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = w.BOOL
    advapi32.AddAce.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD]
    advapi32.AddAce.restype = w.BOOL
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = w.DWORD
    advapi32.CreateProcessAsUserW.argtypes = [
        w.HANDLE, w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
        ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
    advapi32.CreateProcessAsUserW.restype = w.BOOL
    kernel32.GetCurrentProcess.restype = w.HANDLE
    kernel32.CloseHandle.argtypes = [w.HANDLE]
    kernel32.CloseHandle.restype = w.BOOL
    return advapi32, kernel32


class HiddenJobToken:
    """Duplicate of the runner token with one DefaultDacl deny ACE."""

    def __init__(self):
        self.advapi32, self.kernel32 = _api()
        self.handle = 0
        self.user_sid = None
        self._sid_buffer = None
        self._acl_buffer = None
        self._build()

    def _user_sid(self, token):
        needed = w.DWORD()
        self.advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(needed.value)
        if not self.advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, buffer, needed.value,
                                                 ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER)).contents.User
        self._sid_buffer = buffer
        return user.Sid

    def _default_acl(self, token):
        needed = w.DWORD()
        self.advapi32.GetTokenInformation(token, TOKEN_DEFAULT_DACL_CLASS, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(needed.value)
        if not self.advapi32.GetTokenInformation(token, TOKEN_DEFAULT_DACL_CLASS, buffer, needed.value,
                                                 ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        acl = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_DEFAULT_DACL)).contents.DefaultDacl
        if not acl:
            raise OSError("token has no DefaultDacl to restrict")
        return buffer, acl

    def _build(self):
        token = w.HANDLE()
        # The duplicate inherits this handle's granted access, so it must include
        # TOKEN_ASSIGN_PRIMARY or CreateProcessAsUser fails with access denied.
        if not self.advapi32.OpenProcessToken(self.kernel32.GetCurrentProcess(),
                                              TOKEN_ALL_ACCESS, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.user_sid = self._user_sid(token)
            restricted = w.HANDLE()
            if not self.advapi32.CreateRestrictedToken(token, 0, 0, None, 0, None, 0, None,
                                                       ctypes.byref(restricted)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = int(restricted.value or 0)
        finally:
            self.kernel32.CloseHandle(token)
        try:
            buffer, acl = self._default_acl(w.HANDLE(self.handle))
            self._old_acl_buffer = buffer
            original = ctypes.cast(acl, ctypes.POINTER(_ACL)).contents
            ace_size = ctypes.sizeof(_ACE_HEADER) + ctypes.sizeof(w.DWORD) + \
                self.advapi32.GetLengthSid(self.user_sid)
            total = original.AclSize + ace_size
            combined = ctypes.create_string_buffer(total)
            if not self.advapi32.InitializeAcl(combined, total, ACL_REVISION):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.advapi32.AddAccessDeniedAce(combined, ACL_REVISION,
                                                    PROCESS_SET_INFORMATION, self.user_sid):
                raise ctypes.WinError(ctypes.get_last_error())
            for index in range(original.AceCount):
                pointer = ctypes.c_void_p()
                if not self.advapi32.GetAce(acl, index, ctypes.byref(pointer)):
                    raise ctypes.WinError(ctypes.get_last_error())
                size = ctypes.cast(pointer, ctypes.POINTER(_ACE_HEADER)).contents.AceSize
                if not self.advapi32.AddAce(combined, ACL_REVISION, MAXDWORD, pointer, size):
                    raise ctypes.WinError(ctypes.get_last_error())
            self._acl_buffer = combined
            info = _TOKEN_DEFAULT_DACL(ctypes.cast(combined, ctypes.c_void_p))
            if not self.advapi32.SetTokenInformation(w.HANDLE(self.handle), TOKEN_DEFAULT_DACL_CLASS,
                                                     ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def create_process(self, command, cwd, env_block, startup, creation_flags):
        """CreateProcessAsUserW with this token; returns (hProcess, hThread, pid)."""
        from packages.jobs.independent_runner import _PROCESS_INFORMATION
        structure = _PROCESS_INFORMATION()
        environment = ctypes.cast(env_block, ctypes.c_void_p) if env_block is not None else None
        created = self.advapi32.CreateProcessAsUserW(
            w.HANDLE(self.handle), None, ctypes.create_unicode_buffer(command), None, None, True,
            creation_flags, environment, cwd, ctypes.byref(startup), ctypes.byref(structure))
        if not created:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(structure.hProcess), int(structure.hThread), int(structure.dwProcessId)

    def close(self):
        if self.handle:
            self.kernel32.CloseHandle(w.HANDLE(self.handle))
            self.handle = 0
