Isolated CBC protocol investigation begun at Pan 6b1d7ad9, followed by the
authorized production stream Steer integration on main 5fa64e2e.

- `python research/cbc_steer/inspect_protocol.py`: snapshot protocol excerpts and installed bundle hashes, no CLI execution.
- `node research/cbc_steer/protocol_fixture.mjs`: execute installed method bodies against deterministic managers, no model/network/authentication.
- `python research/cbc_steer/cli_probe.py`: one maximum hy3 task, private cwd/profile/cache/temp, bounded execution, suspended launch into a Windows kill-on-close Job, PID + kernel FILETIME evidence, whole Job cleanup. No credentials/history copying, no HOME/global configuration changes. Inline trust applies only to the private invocation.
- `python research/cbc_steer/verify_cli_result.py`: verify the retained headless trial against only its dedicated native history.
- `python research/cbc_steer/tui_probe.py`: one bounded hy3 native TUI task, observer-only hooks, private profile, ConPTY owned Job. The first run failed during close after the task completed; the script now persists evidence before cleanup and explicitly confirms root termination before close. This repair has syntax validation only and was not rerun with a model.
- `python research/cbc_steer/recover_tui_evidence.py`: recover the original TUI trial's history/run facts and read-only process absence evidence; records missing FILETIME/observer summary rather than reconstructing them.

Fixture output establishes method-level semantics only. CLI bootstrap or stdin acceptance alone cannot establish current-run consumption. Real evidence must correlate PID + creation FILETIME, CLI session ID, owner run ID, admission/history, final result, and terminal status. The probe retains synthetic CLI logs locally; inspect privately and do not publish authentication material.

Do not retry unknown-outcome input automatically. Duplicate wire request IDs and message IDs are not assumed to be provider idempotency keys.

Protocol fixtures target CBC 2.164.0 bundle boundaries and fail if those change. The global npm installation was externally replaced during this investigation; the source snapshots and completed native histories describe 2.164.0, not a later installation. Reinspect the installed version before a future CLI run.

`tools/probe_cbc_stream_steer.py` separately records the installed version/hash
for its full browser/HTTP/Worker proof. Private profiles, raw output, native
histories and screenshots remain local and are excluded from Git. Only
sanitized evidence summaries and reusable scripts belong in a commit.
