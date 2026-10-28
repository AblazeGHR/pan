# P23 process logging capability matrix

The capture uses the existing `processOwner` capability. It adds no process
manager, daemon, outbox, receipt store, platform selector or retention service.

| Boundary | Windows system Python 3.14.5 | Windows Pan-main venv Python 3.14.5 | Linux/macOS |
|---|---|---|---|
| Default canonical detached business stdout+stderr | Real owned process, merged append file, Unicode and output larger than pipe capacity | Same targeted suite | Not executed here |
| Cold reopen and original historical reference | New interpreter queries original Run/Attempt; file survives supplier exit and archival | Same | Not executed here |
| Cancel / terminal / late terminal | Original P23 tree+supervisor proof gates remain; files are retained | Same | Not executed here |
| Controlled path | Reuses plain direct log policy, rejects hardlinks, actual directory junction, forged reference; pins directories during use | Same | Uses existing no-follow directory descriptors; not target-executed here |
| Unknown/capture failure | Unknown stays queryable; no new effect or retry to obtain logs | Same | Not executed here |
| Auth and bounds | Existing job read grant, recheck after external await, bounded byte pages | Same | No platform dependency; not target-executed here |

The new `process_logs.py` uses the existing `_controlled_logs_dir` /
`_path_has_reparse_component` policy, `_is_plain_file_entry`, and
`pinned_directories`. There are no new Windows imports or `/proc` assumptions.
Bootstrap/supervisor diagnostic `DEVNULL` and `TypedExecutor._process` remain
unchanged. The actual business sink is supplied by production RecordingOwner.

Existing platform skips remain explicit in the system-final JUnit; their exact
node/reason list is in validation.json. No three-platform execution/CI claim is
made. `product_process_logs_v1` publication belongs to the separate Session TA;
its approved delta is documented in SHARED_ENTRY_AND_PUBLICATION.md and is not
implemented in this candidate.
