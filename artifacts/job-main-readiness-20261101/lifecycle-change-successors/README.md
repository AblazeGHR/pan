# Canonical lifecycle publication and permission-filtered live refresh

Original `tests/test_background_jobs.py::test_agent_message_job_registry_commits_publish_full_lifecycle[single/broadcast]` source and full red identities remain in full-terminal-5a7 and cohortfd1c-terminal. They require creation/edit, original immutable snapshots, published running before send, terminal and cancel updates. A REST readback alone did not preserve the live capability. New explicit canonical tests initially fail at empty post-commit observations; that red source/JUnit is retained here.

Minimal product repair reuses the existing canonical/Creator SQLite transaction and original web broadcast:

- FinalService records copied definition/Run projections only when an installed observer exists and the saved public state actually changed. No observer is an execution, admission, receipt or retry authority.
- CreatorStore.after_commit joins the existing outermost transaction. Borrowed composition writes notify only after the actual outer connection commits; rollback never publishes. No second ledger/store/poller or startup payload scanner is added.
- Production lifespan binds a local observer to the current event loop. It coalesces observations into a content-free `canonical.changed` event through the original broadcast/WS queues. The wire has no object ID, name, arguments, Run or history payload. UI refetches current data using existing authenticated/permission-filtered canonical endpoints; stale signal order cannot overwrite a newer authoritative revision.
- CanonicalCollection and useAutomationWorkspace subscribe through the existing singleton wsClient and unsubscribe normally. An in-progress form mutation does not start another write. Draft editor state remains independent; list refresh does not submit or rewrite it.
- Observer publication failure records `change_publication_failed` diagnostics and cannot roll back an already committed operation. Ephemeral live invalidation is not a durable receipt/user-read guarantee; operation/history APIs remain authoritative. No unknown event/result is resent.

Exact coverage:

1. Both original single/broadcast nodes now run the actual canonical product provider. Creation/admission/edit observations retain old copies; rollback negative produces none; original real Worker enqueue is instrumented only to assert the committed running observation already exists before calling the unchanged provider. Completion stays on the same Run. A second queued Run is explicitly cancelled before execution, and the corresponding cancelled observation appears; Job retirement is visible. Previous snapshots remain byte-equivalent.
2. Real owned HTTP/WS for both operations proves external API updates produce native refresh frames, private payload fields absent, current authenticated list reflects committed revisions, invalid token still401, completion and retirement signal original identity. The sink uses the original native WS outbound implementation, not an in-memory fake broadcast.
3. Joined outer Creator transaction positive/rollback negative proves no early publication. Observer-failure diagnostic negative preserves committed Job/queued Run and zero effects.
4. Production build two-page Chromium test: external HTTP creates and edits a Job; both pages update automatically without refresh clicks. Logout page remains empty while the other page updates again; native WS frame contains no sensitive object fields, private data read only through authenticated API. No Run/provider execution occurs.

Formal focused evidence: lifecycle-complete-green7passed includes the2original nodes; lifecycle-observer-error1passed; real production browser1passed; UI CanonicalJobsView21passed; pnpm build exit0,88compressedassets. Earlier4/5node combinations retained as intermediate source-bound observations, not aggregated as extra distinct coverage. Final frozen/full precise-SHA CI remains required.

Previous46d6 frozen result preserved in cohort46d6-terminal: browser-host15passed39.470s, notification-WS28passed63.145s, zero failure/error/skip/source drift. Eight self-generated browser artifacts were privately hash-backed before restoring only those tracked files.

Current original failure map262/263 has passing successor observations; the remaining `test_terminal_archive_scope_ma::test_real_external_kill_is_exited_and_archivable` is the original platform skip and is not converted to pass. All red evidence remains, including the original13Unknown-tool versus approved capability_unsupported conflict mapped earlier. This is not a final full8472+ gate, remote CI, real-data migration or MA acceptance. Source practical8unknownleases/authority and main dirty remain untouched; MA owns main advancement.
