# Automation UI stage independent acceptance — 2026-10-22

Accepted source: e57bda29713f991530fe3448df24e6fb167571f3.
Integration merge: 8f1ab77b (isolated integration/event-trigger-v01-20261022 only).

MA reviewed TriggerEditor leave/busy gates, immutable condition row identities,
LiteralInput emitted value/raw-buffer separation, conflict rebase/server reload,
and production 320px evidence. Re-ran fields, AutomationView, ActionsView and
ViewEntryGroups Vitest: 32 passed, 0 skipped, exit 0. One existing router fixture
warning (No routes matched location /) remains. Integrated frontend source is
byte-identical to accepted candidate (git diff e57bda29 -- packages/web/src empty).

TA production HTTP/Chromium evidence covers five repair scenarios; MA reviewed
that evidence, did not claim a new independent browser run. Overall human and
combined external-plugin/query-selector acceptance remain pending. No main or
practical movement, service operation, paid model call, push or release.
