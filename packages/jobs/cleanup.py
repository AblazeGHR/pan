"""Attempt every owned cleanup and preserve all failures for diagnosis."""
from contextlib import ExitStack
import sys
import traceback


class CleanupError(RuntimeError):
    def __init__(self, original, failures):
        self.original = original
        self.failures = tuple(failures)
        parts = ['Owned resource cleanup failed; completion is unconfirmed.']
        if original is not None:
            parts.append('Original error:\n' + ''.join(traceback.format_exception(original)))
        for index, error in enumerate(failures, 1):
            parts.append(f'Cleanup error {index}:\n' + ''.join(traceback.format_exception(error)))
        super().__init__('\n'.join(parts))


class CleanupStack:
    """ExitStack callbacks isolated so a failing callback cannot erase another."""
    def __init__(self):
        self.stack = ExitStack()
        self.failures = []

    def callback(self, action):
        def attempt():
            try:
                action()
            except BaseException as error:
                self.failures.append(error)
        self.stack.callback(attempt)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, error, tb):
        self.stack.close()
        if self.failures:
            raise CleanupError(error, self.failures) from error
        return False


def stop_window_guard(guard):
    if guard.stop() is False:
        raise RuntimeError('owned window guard stop unconfirmed: thread still alive')


def close_resources(*actions):
    """Actions execute in listed order, including during an active exception."""
    stack = CleanupStack()
    for action in reversed(actions):
        stack.callback(action)
    stack.__exit__(*sys.exc_info())
