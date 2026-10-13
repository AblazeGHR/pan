"""Per-Worker CBC native transport; the ordinary CBC adapter stays unchanged."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import uuid

from .adapter import CbcAdapter


class CbcNativeAdapter(CbcAdapter):
    execution_modes = ["stream"]
    supports_native_interrupt = True
    # Typing while a turn is running queues another CLI prompt. It is not a
    # provider-confirmed Steer and must never be advertised as one.
    supports_native_steer = False

    def build_spawn_args(self, s, extra_args=None):
        sid = s.cli_session_id or ("pan-" + uuid.uuid4().hex)
        cli = self._resolve_cbc_argv()
        cli.extend(self.model_args(s))
        cli.extend(self.permission_mode_args(s))
        cli.extend(self.effort_args(s))
        cli.extend(self.thinking_args(s))
        cli.extend(self.resume_args(s) if s.cli_session_id else ["--session-id", sid])
        cli.extend(self.mcp_args(s))
        if s.system_prompt and not s.cli_session_id and not extra_args:
            cli.extend(["--system-prompt", s.system_prompt])
        if extra_args:
            cli.extend(extra_args)
        return [sys.executable, str(Path(__file__).with_name("native_bridge.py")),
                "--cwd", s.workdir, "--session-id", sid,
                "--cli-argv", json.dumps(cli, ensure_ascii=True)]

    def encode_control_message(self, control):
        return json.dumps(control, ensure_ascii=True).encode("utf-8")

    def extract_assistant_blocks(self, event):
        if event.get("delta"):
            return []
        blocks = super().extract_assistant_blocks(event)
        for block in blocks:
            if event.get("item_id") is not None:
                block["nativeItemId"] = str(event["item_id"])
        return blocks

    def enrich_after_result(self, s):
        # Native hooks are authoritative for task lifetime. Existing JSONL
        # enrichment remains usage-only and must not decide turn completion.
        return super().enrich_after_result(s)
