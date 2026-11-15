"""Deterministic long provider transcript through the real Worker parser."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support import session_error_fake_cli as cli

CBC_EVENT = json.loads((Path(__file__).resolve().parents[1] /
                        'fixtures/cbc_quota_error_result.json').read_text(encoding='utf-8'))['event']
CBC_ERROR_TEXT = CBC_EVENT['errors'][0]
# Shares the provider prefix but is a different full text: must stay visible.
PREFIX_PROSE = "429 您的使用量已超出频率限制（普通说明，不是诊断）。"
UNKNOWN_PROSE = "429 频率限制说明（本次没有结构化原因）。"

original = cli._handle
def handle(text, mode):
    if text == "history-terminal-long":
        cli._append({"kind": "received", "text": text, "mode": mode})
        for index in range(130):
            cli._assistant(f"Long history body {index:03d}")
        cli._emit({"type": "result", "is_error": False, "result": "Long history body 129"})
    elif text == "cbc-quota-result":
        cli._append({"kind": "received", "text": text, "mode": mode})
        # Ordinary assistant prose that mentions 429 but is NOT the diagnosis.
        cli._assistant(PREFIX_PROSE)
        # Native failed result: no "result" field, the reason is in
        # errors/errors_info (status 429, code 6004, category quota).
        cli._emit(CBC_EVENT)
    elif text == "cbc-quota-echo":
        cli._append({"kind": "received", "text": text, "mode": mode})
        # Real shape: the assistant body is byte-identical to errors[0].
        cli._assistant(CBC_ERROR_TEXT)
        cli._emit(CBC_EVENT)
    elif text == "same-429-done":
        cli._append({"kind": "received", "text": text, "mode": mode})
        # Previous/completed run with the identical body: never folded.
        cli._assistant(CBC_ERROR_TEXT)
        cli._emit({"type": "result", "is_error": False, "result": CBC_ERROR_TEXT})
    elif text == "cbc-unknown-error":
        cli._append({"kind": "received", "text": text, "mode": mode})
        # Failed result without native detail: prose stays, card stays generic.
        cli._assistant(UNKNOWN_PROSE)
        cli._emit({"type": "result", "subtype": "error_during_execution",
                   "is_error": True})
    else:
        original(text, mode)
cli._handle = handle
cli.main()
