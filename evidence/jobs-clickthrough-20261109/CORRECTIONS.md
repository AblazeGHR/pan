# Evidence status correction — 2026-11-09

The first committed run of `jobs-clickthrough.e2e.mjs` reported
`"status": "passed"` while only two of the four Job kinds were created through
the UI and no real Run/log was asserted. That label overstated the scope.

- Original file preserved unchanged: `evidence.2026-11-09-claimed-passed.json`.
- Corrected `evidence.json` now reports:
  - `"status": "partial"`
  - `"scope": "Jobs designed UI click chain"`
  - `"blocked": [broadcast create (harness provider), scheduled-task create
    (harness scheduler provider), run and logs (owned process provider not
    aligned yet)]`
- What the partial run does prove: single unified list, one New Job entry,
  background-process and session-message creation by real clicks, edit from the
  detail drawer, Pin and Delete from the row menu (closed through its backdrop),
  and the Cancel control being reachable on an active row.
- What it does not prove: broadcast/scheduled-task creation and any Run/log
  behaviour. Those stay red until the owned provider fixture covers the
  production Action refs.

## Second correction — 2026-11-09 (immediate start)

After `ecd62e32`, a legacy background-process Job starts on creation and the
designed detail shows the real Run reaching `completed`; the "run and logs"
item was removed from the blocked list because the run is now proven in the UI
(`processStatus: completed`). Logs were read from the canonical log route in the
separate process probe (attempt + logRef), not yet asserted inside the drawer.
Still blocked: broadcast and scheduled-task creation (harness provider).
