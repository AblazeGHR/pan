"""Read-only schema scan of REAL CodeBuddy (cbc) transcripts.

Emits key names / value types / id-field presence only — never message text.
Purpose: establish the real identity fields the Pan adapter/canonical parser
drop, instead of relying on hand-made Codex-shaped fixtures.
"""

from __future__ import annotations

import collections
import json
import os
from pathlib import Path

BASE = Path(os.path.expanduser("~/.codebuddy/projects"))

type_keys: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
block_keys: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
nested_msg_keys: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
id_fields: collections.Counter = collections.Counter()
files_scanned = 0
lines_scanned = 0
sample_shapes: dict[str, dict] = {}


def _record_shape(bucket: dict, key: str, value) -> None:
    """Record a compact structural signature for one JSON value."""
    if isinstance(value, dict):
        sig = "object{" + ",".join(sorted(value.keys())) + "}"
    elif isinstance(value, list):
        inner = value[0] if value else None
        sig = "array<" + (("object{" + ",".join(sorted(inner.keys())) + "}") if isinstance(inner, dict) else type(inner).__name__) + ">"
    else:
        sig = type(value).__name__
    bucket[key] = sig


for path in sorted(BASE.glob("*/*.jsonl")):
    if path.stem == "agent":
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    files_scanned += 1
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        lines_scanned += 1
        etype = str(event.get("type"))
        for key, value in event.items():
            type_keys[etype][key] += 1
            _record_shape(sample_shapes.setdefault("top:" + etype, {}), key, value)
        for idkey in ("id", "uuid", "messageId", "message_id", "parentUuid", "parent_uuid",
                      "sessionId", "session_id", "requestId", "request_id"):
            if idkey in event:
                id_fields[f"top:{etype}.{idkey}"] += 1

        # message payload identity
        msg = event.get("message")
        if isinstance(msg, dict):
            for key in msg:
                nested_msg_keys[etype][key] += 1
            for idkey in ("id", "uuid", "messageId", "role"):
                if idkey in msg:
                    id_fields[f"message:{etype}.{idkey}"] += 1

        # content blocks
        blocks = event.get("content")
        if not isinstance(blocks, list):
            m = event.get("message")
            blocks = m.get("content") if isinstance(m, dict) else None
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                btype = str(block.get("type"))
                for key in block:
                    block_keys[btype][key] += 1
                for idkey in ("id", "uuid", "tool_use_id", "toolUseId", "call_id", "callId"):
                    if idkey in block:
                        id_fields[f"block:{btype}.{idkey}"] += 1
                if btype == "tool_use":
                    _record_shape(sample_shapes.setdefault("block:tool_use", {}), "input", block.get("input"))

report = {
    "files_scanned": files_scanned,
    "lines_scanned": lines_scanned,
    "event_types": {t: c.most_common() for t, c in sorted(type_keys.items())},
    "block_types": {t: c.most_common() for t, c in sorted(block_keys.items())},
    "nested_message_keys": {t: c.most_common() for t, c in sorted(nested_msg_keys.items())},
    "identity_fields_present": dict(id_fields.most_common()),
    "sample_shapes": {k: {kk: vv for kk, vv in v.items()} for k, v in sorted(sample_shapes.items())},
}

target = Path(__file__).resolve().parent / "probe_real_cbc_schema.out.json"
target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {target}")
print(f"files={files_scanned} lines={lines_scanned}")
for t, c in sorted(type_keys.items()):
    print(f"  type={t!r}: keys={sorted(c)}")
for t, c in sorted(block_keys.items()):
    print(f"  block={t!r}: keys={sorted(c)}")
print("  identity fields:", dict(id_fields.most_common()))
