"""Per-Worker Kimi native transport; the ordinary Kimi adapter stays unchanged.

The bridge owns one PTY with the real ``kimi`` TUI. Structured facts come from
kimi's own wire.jsonl (turn.prompt / agent.turn.started|ended / turn.ended),
never from the screen. Verified limits (kimi 2.3.1, 2026-10-24):

- **steer**: mid-turn input is queued as the next turn, so Pan declares
  ``supports_native_steer = False`` (same honesty rule as CBC's native TUI).
- **interrupt**: Ctrl+C cancels the running turn and the wire reports
  ``turn.ended.reason == "cancelled"`` — a real receipt, so the worker can use
  its native-control interrupt path instead of killing the process.
- **single writer**: kimi refuses a second CLI instance on an occupied session
  (web 40931 / ACP "in use"), so this bridge must be the only owner.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .adapter import KimiAdapter


class KimiNativeAdapter(KimiAdapter):
    execution_modes = ["stream"]
    supports_native_control = True
    # Ctrl+C + wire turn.ended(reason=cancelled) is a real interrupt receipt.
    supports_native_interrupt = True
    interrupt_via_native_control = True
    # PTY input is queued by the TUI as the next turn; there is no mid-turn
    # admission or steer receipt, so Pan must not advertise steer.
    supports_native_steer = False
    supports_steer_receipt = False
    steer_requires_run_id = False

    # The TUI has no equivalent of the wrapper's first-turn --agent-file path
    # for resumed sessions (kimi forbids --agent-file with -S). build_spawn_args
    # handles the fresh-session case itself, so the worker must not append a
    # --system-prompt flag the bridge would reject.
    supports_spawn_system_prompt = False
    supports_spawn_system_prompt_file = False

    def build_spawn_args(self, s, extra_args=None):
        args = [
            sys.executable, "-u", str(Path(__file__).with_name("native_bridge.py")),
            "--kimi-path", self._KIMI_PATH,
            "--cwd", s.workdir,
            "--session-id", s.cli_session_id or "",
            "--model", s.model or self.default_model,
        ]
        home = self._prepare_kimi_home(s)
        if home is not None:
            args.extend(["--kimi-home", str(home)])
        # Fresh session (no native id yet): kimi accepts --agent-file only here.
        if s.system_prompt and not s.cli_session_id:
            from .wrapper import _write_agent_file

            agent_file = _write_agent_file(s.system_prompt, str(home) if home else None)
            args.extend(["--agent-file", agent_file])
        if extra_args:
            args.extend(extra_args)
        return args

    def encode_control_message(self, control):
        return json.dumps(control, ensure_ascii=True).encode("utf-8")

    def encode_user_message(self, text):
        # Bridge protocol (same shape cbc's PTY bridge accepts): a queued Pan
        # task becomes one TUI prompt via bracketed paste.
        return json.dumps(
            {"type": "user", "message": {"content": [{"type": "text", "text": text}]}},
            ensure_ascii=True,
        ).encode("utf-8")

    def takeover_command(self, s):
        """Interactive takeover reuses the same native profile as the bridge."""
        return super().takeover_command(s)
