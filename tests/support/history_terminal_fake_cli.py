"""Deterministic long provider transcript through the real Worker parser."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support import session_error_fake_cli as cli

original = cli._handle
def handle(text, mode):
    if text == "history-terminal-long":
        cli._append({"kind": "received", "text": text, "mode": mode})
        for index in range(130):
            cli._assistant(f"Long history body {index:03d}")
        cli._emit({"type": "result", "is_error": False, "result": "Long history body 129"})
    else:
        original(text, mode)
cli._handle = handle
cli.main()
