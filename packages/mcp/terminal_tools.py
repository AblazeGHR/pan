"""Small registration layer; Pan server supplies verified local invocation."""


def register_terminal_tools(mcp, invoke):
    @mcp.tool()
    def terminal_create(rows: int = 24, cols: int = 80, cwd: str | None = None,
                        workspace_id: str | None = None, session_id: str | None = None) -> dict:
        """Create an independent terminal. Default scope is the calling Session.

        No provider worker is started. Closing Pan normally stops managed
        terminals; durable detach remains environment-dependent.
        """
        return invoke("create", {"rows": rows, "cols": cols, "cwd": cwd, "workspace_id": workspace_id, "session_id": session_id})

    @mcp.tool()
    def terminal_list(workspace_id: str | None = None, session_id: str | None = None) -> dict:
        """List public terminal records within the caller's permitted scope."""
        return invoke("list", {"workspace_id": workspace_id, "session_id": session_id})

    @mcp.tool()
    def terminal_get(terminal_id: str) -> dict:
        """Get public terminal state without secrets or channel handles."""
        return invoke("get", {"terminal_id": terminal_id})

    @mcp.tool()
    def terminal_read(terminal_id: str, cursor: str = "0", max_bytes: int = 32768) -> dict:
        """Read absolute raw output. Cursor is decimal text; gaps are explicit."""
        return invoke("read", {"terminal_id": terminal_id, "cursor": cursor, "max_bytes": max_bytes})

    @mcp.tool()
    def terminal_snapshot(terminal_id: str, timeout_ms: int = 1000) -> dict:
        """Get serialized screen plus applied cursor. Partial is not full fidelity."""
        return invoke("snapshot", {"terminal_id": terminal_id, "timeout_ms": timeout_ms})

    @mcp.tool()
    def terminal_input(terminal_id: str, data_b64: str, take_control: bool = False,
                       seq: str | None = None) -> dict:
        """Send bytes only with explicit take_control=True.

        Takes control away from any browser controller for this command, then
        releases its actual lease. Does not promise OS Ctrl-C semantics.
        """
        return invoke("input", {"terminal_id": terminal_id, "data_b64": data_b64, "take_control": take_control, "seq": seq})

    @mcp.tool()
    def terminal_close(terminal_id: str) -> dict:
        """Stop an independent terminal; unconfirmed cleanup remains retryable."""
        return invoke("close", {"terminal_id": terminal_id})

    @mcp.tool()
    def terminal_detach(terminal_id: str) -> dict:
        """Request durable detach; refusal leaves the session unchanged."""
        return invoke("detach", {"terminal_id": terminal_id})
