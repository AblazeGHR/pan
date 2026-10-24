"""Stdlib-only entry shim for launcher-managed Pan launches.

The launcher must not hand its log files to the managed child as stdout/stderr:
a Windows venv ``python.exe`` is a redirector stub that keeps its own copy of
every inherited handle for the child's whole lifetime, so the real interpreter
can never rename ``pan-console.out.log`` / ``pan-console.err.log`` while Pan is
running.  Instead the launcher passes the destination paths in the environment
and starts ``python -c <shim> main.py ...``.

This module is the shim's payload.  It must stay importable **without any other
Pan module** (only the standard library at module level) so that a failure while
importing Pan is still written to ``data/logs``:

* stage 1 (:func:`open_console_logs`) redirects fd 1/2 to the destination files
  with a plain append handle, so import-time and ``main.py`` compile-time
  failures land there;
* stage 2 (:func:`run`) installs the rotation relay from ``console_logs``, which
  replaces those handles with in-process pipes, then executes ``main.py``.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ENV_OUT = "PAN_CONSOLE_OUT_LOG"
ENV_ERR = "PAN_CONSOLE_ERR_LOG"

#: ``python -c`` payload used by the launcher.  It must stay stdlib-only, and it
#: must be able to write the destinations even when importing Pan itself fails.
SHIM = r"""
import os, sys

_entry = sys.argv[1] if len(sys.argv) > 1 else ""
_root = os.path.dirname(os.path.abspath(_entry))
if _root and _root not in sys.path:
    sys.path.insert(0, _root)


def _raw_redirect():
    for _name, _fd in (("PAN_CONSOLE_OUT_LOG", 1), ("PAN_CONSOLE_ERR_LOG", 2)):
        _target = os.environ.get(_name)
        if not _target:
            continue
        try:
            _parent = os.path.dirname(os.path.abspath(_target))
            if _parent:
                os.makedirs(_parent, exist_ok=True)
            _handle = open(_target, "ab", buffering=0)
            try:
                os.dup2(_handle.fileno(), _fd)
            finally:
                _handle.close()
        except OSError:
            pass


try:
    from packages.core.console_bootstrap import run as _run
except BaseException:
    _raw_redirect()
    raise

_run(sys.argv[1:])
"""


def open_console_logs(environ=None) -> bool:
    """Point this process's stdout/stderr at the launcher-provided files.

    Only stdlib calls: this runs before Pan's own modules are trusted.  Returns
    ``True`` when at least one stream was redirected; failures are silent so the
    launch can continue to the relay (which retries the directory itself).
    """
    env = os.environ if environ is None else environ
    redirected = False
    for name, fd in ((ENV_OUT, 1), (ENV_ERR, 2)):
        target = str(env.get(name) or "").strip()
        if not target:
            continue
        try:
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path, "ab", buffering=0)
        except OSError:
            continue
        try:
            os.dup2(handle.fileno(), fd)
            redirected = True
        except OSError:
            pass
        finally:
            try:
                handle.close()
            except OSError:
                pass
    return redirected


def run(argv: list[str]) -> None:
    """Install the relay and execute ``main.py`` as ``__main__``."""
    entry = os.path.abspath(argv[0])
    root = os.path.dirname(entry)
    if root and root not in sys.path:
        sys.path.insert(0, root)
    open_console_logs()
    from .console_logs import install

    install()
    sys.argv = [entry, *argv[1:]]
    runpy.run_path(entry, run_name="__main__")
