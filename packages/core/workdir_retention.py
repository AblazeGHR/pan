"""Bounded orphan workdir retention; never accepts a client supplied path."""
from __future__ import annotations

from functools import wraps
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading
import time

from .data_retention import _safe_root, _iso_now

# Shared by actual metadata commits, not just HTTP handlers. Do not acquire
# Session store/ticket locks while holding this lock in the collector.
REFERENCE_LOCK = threading.RLock()
_retired: set[Path] = set()
MAX_ENTRIES = 100_000
MAX_SECONDS = 20
MAX_DIRECTORIES = 2048


@contextmanager
def pinned_directories(root: Path, path: Path, snapshot):
    """Windows: deny directory rename/reparse replacement while deleting children.

    POSIX uses directory descriptors and no-follow traversal. Ordinary file
    unlink never follows a leaf symlink; pinned ancestors prevent escape.
    """
    if os.name != 'nt':
        handles = {}
        expected = {path / row[0]: row[1:4] for row in snapshot if stat.S_ISDIR(row[3])}
        info = root.lstat()
        expected[root] = (info.st_dev, info.st_ino, info.st_mode)
        try:
            for directory in sorted(expected, key=lambda p: len(p.parts)):
                parent = handles.get(directory.parent)
                descriptor = os.open(str(directory) if parent is None else directory.name,
                                     os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=parent)
                handles[directory] = descriptor
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino, info.st_mode) != expected[directory]:
                    raise ValueError('directory_identity_changed')
            def release(directory):
                descriptor = handles.pop(directory, None)
                if descriptor is not None:
                    os.close(descriptor)
            yield release, handles
        finally:
            for descriptor in handles.values():
                os.close(descriptor)
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handles = {}
    expected = {path / row[0]: row[1:4] for row in snapshot if stat.S_ISDIR(row[3])}
    root_info = root.lstat()
    expected[root] = (root_info.st_dev, root_info.st_ino, root_info.st_mode)
    try:
        for directory in sorted(expected, key=lambda p: len(p.parts)):
            # GENERIC_READ (attributes-only handles do not block rename on Windows);
            # share READ|WRITE, deliberately no DELETE.
            handle = create(str(directory), 0x80000000, 3, None, 3, 0x02200000, None)
            if handle == wintypes.HANDLE(-1).value:
                raise OSError(ctypes.get_last_error(), 'Cannot pin workdir directory')
            handles[directory] = handle
            info = directory.lstat()
            if ((info.st_dev, info.st_ino, info.st_mode) != expected[directory]
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError('directory_identity_changed')
        def release(directory):
            handle = handles.pop(directory, None)
            if handle is not None:
                close(handle)
        yield release, None
    finally:
        for handle in handles.values():
            close(handle)


def reference_commit(function):
    @wraps(function)
    def guarded(owner, *args, **kwargs):
        with REFERENCE_LOCK:
            paths = [owner.workdir] if hasattr(owner, 'workdir') else owner.dirs
            validate_reference_paths(paths)
            return function(owner, *args, **kwargs)
    return guarded


def validate_reference_paths(paths):
    """Called under REFERENCE_LOCK before publishing a reference or commit."""
    for raw in paths if _retired else []:
        if not raw:
            continue
        path = Path(raw).resolve()
        if any(path == old or old in path.parents or path in old.parents
               for old in _retired) and not path.is_dir():
            raise ValueError('Workdir was removed by retention; choose an existing directory')


def tree_snapshot(path: Path, deadline: float, budget: list[int]) -> tuple:
    """lstat every entry; reject links, hardlinks, special files and aliases."""
    rows = []
    pending = [path]
    directories = 0
    while pending:
        item = pending.pop()
        budget[0] -= 1
        if budget[0] < 0 or time.monotonic() > deadline:
            raise ValueError('scan_limit')
        info = item.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400
                or item.resolve(strict=True) != item.absolute()):
            raise ValueError('unsafe_tree')
        if stat.S_ISDIR(info.st_mode):
            directories += 1
            if directories > MAX_DIRECTORIES:
                raise ValueError("directory_handle_limit")
            with os.scandir(item) as entries:
                for entry in entries:
                    pending.append(Path(entry.path))
                    if len(pending) > budget[0]:
                        raise ValueError('scan_limit')
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('unsafe_tree')
        rows.append((str(item.relative_to(path)), info.st_dev, info.st_ino,
                     info.st_mode, info.st_mtime_ns, info.st_size))
    return tuple(sorted(rows))


class WorkdirRetention:
    def __init__(self, root: Path, reference_state):
        self.root = Path(root)
        self.reference_state = reference_state

    def _candidate(self, name, days, now, deadline, budget):
        if (not isinstance(name, str) or not name or name in {'.', '..'}
                or any(c in name for c in '/\\:\0') or Path(name).name != name):
            raise ValueError('invalid_name')
        root = _safe_root(self.root)
        if root is None:
            raise ValueError('unsafe_root')
        path = root / name
        snapshot = tree_snapshot(path, deadline, budget)
        if not stat.S_ISDIR(next(row[3] for row in snapshot if row[0] == '.')):
            raise ValueError('not_directory')
        newest = max(row[4] for row in snapshot) / 1e9
        if newest > now - days * 86400:
            raise ValueError('recently_modified')
        referenced, reason = self.reference_state(path)
        if referenced is not False:
            raise ValueError(reason or 'references_unclear')
        return path, snapshot, newest

    def run(self, days: int, names: list[str] | None = None, *, delete=False, now=None):
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 36500:
            raise ValueError('days must be an integer from 1 to 36500')
        now = time.time() if now is None else now
        result = {'candidates': [], 'results': [], 'scanned': 0, 'deleted': 0,
                  'skipped': 0, 'skipReasons': {}, 'error': None, 'completedAt': _iso_now(now)}
        deadline, budget = time.monotonic() + MAX_SECONDS, [MAX_ENTRIES]
        try:
            if _safe_root(self.root) is None:
                raise ValueError('unsafe_root')
            if names is None:
                names = []
                with os.scandir(self.root) as entries:
                    for entry in entries:
                        names.append(entry.name)
                        if len(names) > 2000:
                            raise ValueError('directory_limit')
            if len(names) > 2000:
                raise ValueError('directory_limit')
            for name in dict.fromkeys(names):
                result['scanned'] += 1
                try:
                    with REFERENCE_LOCK:
                        path, snapshot, newest = self._candidate(name, days, now, deadline, budget)
                        if delete:
                            # Preview is never authority. Re-read identity, times,
                            # tree and references immediately before removing bytes.
                            fresh, again, _ = self._candidate(name, days, time.time(), deadline, budget)
                            if fresh != path or again != snapshot:
                                raise ValueError('changed_during_scan')
                            # Remove only validated plain entries, checking identity
                            # again for each operation; never recursively follow links.
                            _retired.add(path)
                            identities = {str(path / entry[0]): entry[1:4] for entry in snapshot}
                            with pinned_directories(path.parent, path, snapshot) as pinned:
                                if tree_snapshot(path, deadline, budget) != snapshot:
                                    raise ValueError('changed_before_delete')
                                self._remove_tree(path, snapshot, identities, deadline, *pinned)
                            result['deleted'] += 1
                            result['results'].append({'name': name, 'deleted': True})
                        else:
                            result['candidates'].append({'name': name, 'path': str(path),
                                'modifiedAt': _iso_now(newest), 'entries': len(snapshot)})
                except (OSError, ValueError) as exc:
                    reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    result['skipped'] += 1
                    result['skipReasons'][reason] = result['skipReasons'].get(reason, 0) + 1
                    result['results'].append({'name': name, 'deleted': False, 'reason': reason})
        except (OSError, ValueError) as exc:
            result['error'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return result

    def _remove_tree(self, path, snapshot, identities, deadline, release, descriptors):
        for row in sorted(snapshot, key=lambda r: len(Path(r[0]).parts), reverse=True):
            if time.monotonic() > deadline:
                raise ValueError("delete_time_limit")
            item = path / row[0]
            if _safe_root(self.root) != path.parent:
                raise ValueError('root_changed')
            ancestors = [path, *item.parents]
            for ancestor in ancestors:
                if str(ancestor) not in identities:
                    continue
                ancestor_info = ancestor.lstat()
                if (ancestor_info.st_dev, ancestor_info.st_ino,
                        ancestor_info.st_mode) != identities[str(ancestor)]:
                    raise ValueError('ancestor_changed')
            parent_fd = descriptors[item.parent] if descriptors is not None else None
            info = os.stat(item.name, dir_fd=parent_fd, follow_symlinks=False) if parent_fd is not None else item.lstat()
            if (info.st_dev, info.st_ino, info.st_mode) != row[1:4]:
                raise ValueError('identity_changed')
            if stat.S_ISDIR(info.st_mode):
                release(item)
                if parent_fd is not None:
                    os.rmdir(item.name, dir_fd=parent_fd)
                else:
                    item.rmdir()
            else:
                if info.st_mtime_ns != row[4] or info.st_size != row[5]:
                    raise ValueError('changed_during_delete')
                if parent_fd is not None:
                    os.unlink(item.name, dir_fd=parent_fd)
                else:
                    item.unlink()
