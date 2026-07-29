"""File-system watcher for memory index updates.

Uses watchdog to monitor a directory for .md file changes and triggers
re-indexing via a callback.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DEBOUNCE = 1.5  # seconds


class MemoryWatcher:
    """Watches a directory for Markdown file changes with debounce.

    Usage::

        watcher = MemoryWatcher("/path/to/memory", on_change=my_handler)
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(
        self,
        watch_dir: str,
        on_change: Callable[[str], None],
        debounce: float = DEFAULT_DEBOUNCE,
    ) -> None:
        self._watch_dir = Path(watch_dir).resolve()
        self._on_change = on_change
        self._debounce = debounce
        self._observer: object | None = None
        self._timer: threading.Timer | None = None
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Start / stop
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Begin watching the directory for changes."""
        if self._observer is not None:
            return

        if not self._watch_dir.exists():
            log.warning(
                "Watch directory does not exist, skipping watcher: %s",
                self._watch_dir,
            )
            return

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.warning(
                "watchdog is not installed. File monitoring disabled. "
                "Install with: pip install watchdog"
            )
            return

        handler = _Handler(self._on_file_event)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watch_dir), recursive=True)  # type: ignore[union-attr]
        self._observer.start()  # type: ignore[union-attr]
        log.info("MemoryWatcher started for %s", self._watch_dir)

    def stop(self) -> None:
        """Stop watching and cancel any pending debounce timer."""
        if self._observer is not None:
            self._observer.stop()  # type: ignore[union-attr]
            self._observer.join()  # type: ignore[union-attr]
            self._observer = None
        self._cancel_timer()
        log.info("MemoryWatcher stopped")

    # ------------------------------------------------------------------ #
    #  Event handling
    # ------------------------------------------------------------------ #

    def _on_file_event(self, file_path: str):
        """Handler for file-system events, debounced."""
        if not file_path.endswith(".md"):
            return

        with self._lock:
            self._pending.add(file_path)
            self._cancel_timer()
            self._timer = threading.Timer(self._debounce, self._flush)
            self._timer.start()

    def _flush(self):
        """Deliver all accumulated file paths to the callback."""
        with self._lock:
            if not self._pending:
                return
            paths = sorted(self._pending)
            self._pending = set()

        for path in paths:
            try:
                self._on_change(path)
            except Exception:
                log.exception("Error handling file change for %s", path)

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


# ------------------------------------------------------------------ #
#  Watchdog event handler
# ------------------------------------------------------------------ #


class _Handler:
    """Minimal watchdog handler that forwards events."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        from watchdog.events import FileSystemEventHandler

        self._callback = callback

    def on_modified(self, event):
        # watchdog may pass event; skip guard to avoid import issues
        path = getattr(event, "src_path", None)
        if path:
            self._callback(path)

    def on_created(self, event):
        path = getattr(event, "src_path", None)
        if path:
            self._callback(path)

    def dispatch(self, event):
        """Route event to the correct handler."""
        from watchdog.events import (
            FileCreatedEvent,
            FileModifiedEvent,
        )

        if isinstance(event, (FileModifiedEvent, FileCreatedEvent)):
            path = event.src_path
            if path:
                self._callback(path)
