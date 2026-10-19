"""Read-only takeover admission from native resume storage, never Pan history."""
from .adapters import get_sessions_provider


def readiness(s) -> dict:
    if not s.cli_session_id or not s.cli_session_id.strip():
        return {"ready": False, "reason": "No resumable CLI session yet. Send a message and wait for the CLI session to be established."}
    try:
        provider = get_sessions_provider(s.adapter)
        if s.adapter == "kimi":
            # Pan can run Kimi with an isolated home; the global index is not
            # evidence for that profile's resume command.
            home = s.adapter_config.get("kimi_home_dir")
            directory = provider._find_session_dir(s.cli_session_id, s.workdir or None, home)
            exists = directory is not None and bool(provider._read_state(directory))
        else:
            exists = bool(provider and provider.session_exists(s.cli_session_id, s.workdir or None))
            if exists and s.adapter == "codex" and hasattr(provider, "takeover_resume_available"):
                exists = provider.takeover_resume_available(s.cli_session_id)
            elif exists and s.adapter in {"codex", "cbc", "claude"}:
                # A minted thread ID / metadata-only transcript is not yet a
                # native resumable conversation. A native user turn suffices;
                # imported sessions do not need a Pan assistant message.
                exists = bool(provider.parse_history(s.cli_session_id, s.workdir or None))
        if exists:
            return {"ready": True, "reason": None}
        return {"ready": False, "reason": "CLI resume data is not available yet (or session creation failed). Complete the first CLI turn, then retry takeover."}
    except Exception:
        return {"ready": False, "reason": "Unable to verify CLI resume data. Check the CLI session/profile and retry takeover."}
