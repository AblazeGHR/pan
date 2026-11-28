# Conditional Session queue wake

Within a single Pan service process, an authorized caller may read
`GET /api/sessions/{id}/queue/state-token`. The response includes a version,
adapter, terminal identity and status, history and queue revisions, pause and Illegal
flags, and pending queue item identifiers, sources, delivery states and lock
flags. It excludes message bodies. The caller decides whether that state is
acceptable; Pan does not infer an application-specific recovery policy.

`POST /api/sessions/{id}/queue/conditional-wake` takes `source`, a stable
`clientMessageId`, and `expectedVersion`. It inserts the configured Wake at
the queue head only if the whole projected version still matches at the
metadata replacement commit. A Session with unpersisted history is rejected until its history has committed. A `conflict` did not enqueue anything. `queued`
confirms queue admission, not consumption. `GET /api/sessions/{id}/queue/wake-receipt?clientMessageId=...`
queries the original durable queue receipt; `unknown` is not permission to
replay. A repeated request with an existing original Wake receipt returns that
receipt without changing its position. Existing ordinary Wake is unchanged.

The version covers only the returned fields. This contract uses the existing
per-process Session writer, queue lock and history summary lock; independent
Pan processes sharing the same Session data directory are not supported by
this contract. It is not a cross-process compare-and-swap.

Local script profiles need explicit new rules for each operation; old profiles
receive no additional permission. A bound plugin uses the same core primitive
through its existing Session scope. No account, quota, provider, or recovery
policy is encoded in this interface.


## Isolated rotation state and evidence map

All examples below use synthetic identities and a disposable data root. The
full-chain test owns the Pan automation service, real event store and local
script IPC, calls the watcher `main` entry point, and uses a simulated provider
synthetic in-process Worker, and deterministic native RPC messages through the
real Codex wrapper and adapter parser. It does not run a native Codex process.

| Input and authority | Persisted state and decision | Effect, recovery point | Test |
| --- | --- | --- | --- |
| Worker structured quota, generation 0, manager dispatches target | Real terminal plus event; target is authorized Codex Session; source checked and not projected | Event is matched to its terminal once | `test_owned_runtime_issue_to_conditional_wake_end_to_end`, `test_delegated_event_target_scope_and_source_consistency` |
| Conflicting source/target, wrong structure or numeric identity | No authorized matching event or terminal | No batch or cross-scope effects | `test_ma_dispatched_ta_generation_zero_survives_projection_and_terminal`, `test_issue_zero_generation_does_not_weaken_other_numeric_identity`, `test_refuses_scope_bypasses` |
| Filtered event pages and old/new ledger | Cursor advances independently of visible rows; first scan starts at tip | Process restart uses persisted cursor and dedupe | `test_filtered_ipc_pagination_existing_and_new_ledger`, `test_owned_server_watcher_first_scan_without_browser_login`, `test_duplicate_terminal_is_checked_once` |
| Current quota fresh and available | Current provider confirmed | No switch; original target gets conditional Wake | `test_owned_runtime_issue_to_conditional_wake_end_to_end[10]`, `test_session_continuation_when_worker_offline_and_no_restart_receipt` |
| Current quota exhausted, other candidate fresh | Gated source only after exhaustion; disabled target and unknown quota excluded | CAS switch and readback precede delayed restart and Wake | `test_owned_runtime_issue_to_conditional_wake_end_to_end[100]`, `test_disabled_source_can_switch_only_after_own_exhaustion_is_observed`, `test_quota_admission_rejects_changed_or_conflicting_identity` |
| All candidates exhausted, stale/unknown or concurrent manual switch | Wait until reset plus five minutes and resample, or remain unknown | No blind switch replay after unknown CAS | `test_all_exhausted_waits_then_refreshes_before_restarts`, `test_unknown_other_candidate_does_not_prove_all_exhausted`, `test_manual_provider_change_at_switch_cas_is_unknown_and_never_replayed` |
| Online idle/running Sessions, only one trigger | Restart receipts are independent for online batch; original target is sole continuation target | Stable request ID records queue admission, not consumption | `test_owned_runtime_issue_to_conditional_wake_end_to_end`, `test_local_ipc_watcher_simulated_rotation_once`, `test_http_recovery_switch_then_restart_and_enqueue_once` |
| New input, pause, Illegal, history or terminal changes | Version conflict checked at metadata replacement; unpersisted history rejected | No partial queue commit; lost response queries original receipt | `test_pending_or_racing_session_change_must_not_enqueue_wake`, `test_conditional_commit_rechecks_history_at_actual_replace`, `test_send_response_loss_is_never_replayed` |
| Legacy settled/unknown or previously matched ledger | Existing cursor and identity retained | No historical automatic replay | `test_settled_legacy_batch_is_not_replayed`, `test_existing_unknown_batch_still_deduplicates`, `test_legacy_match_without_batch_stays_unreplayed_and_reported` |
| Independent plugin, same local Session data across processes | Scoped plugin uses the same primitive; process locks are independent | Ordinary Wake unchanged; cross-process shared-directory CAS unsupported | `test_generic_plugin_ipc_scopes_same_queue_primitive`, `test_real_isolated_offline_wake_queue_head_and_original_receipt`, `test_conditional_snapshot_cross_process_shared_directory_is_not_supported` |

The full-chain test exercises the existing Worker terminal producer and event
admission, but the Worker object and provider are controlled fixtures; it does
not prove native adapter process behavior, provider binding, actual downstream
consumption, production data migration, or a second-host remote-pipe attempt.

For migration, the operator must explicitly add the three rules above to the
existing script profile and install compatible service and watcher versions.
Then, following independent acceptance, run the existing launcher with
`--script-profile NAME --script-data-dir OWNED_ROOT --ledger LEDGER
--bridge-data BRIDGE_ROOT`; add `--execute` only for authorized effects. The
first enabled run starts from the current event tip when there is no ledger;
existing ledgers must be retained and unknown historical effects reconciled
without cursor rollback or replay. Do not place credentials in arguments.
