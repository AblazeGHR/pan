# Query UI and Action Panel coexistence: MA acceptance

Source f328a8d247d5e3f2deb443146c1c8a117631a7cf independently reviewed.
Five-file Vitest: 79 passed, 0 skipped/failed, exit 0, 3.77 seconds:
ActionsView, AutomationView, sessionQueryModel, actionRegistryStore, automation fields.
Backend/query and plugin implementation remain accepted input versions;
shared registry cache is byte-identical to requested main fd15054c.
Reviewed typed structured query, advanced invalid raw buffer, disabled template,
Run frozen selection diagnostics and current full-panel Action editors.
TA production HTTP/Chromium 10 coexistence/query and 5 draft scenarios retained
in artifacts/eta-action-panel-integration; not claimed as new MA browser runs.

Isolation UI merge c886564; current main 4ae71124 merged as bbb38bff.
Only documentation merge conflict; retained implemented query plus plugin rules.
Original commit hook initially failed from missing frontend dependency setup;
configured this tree's web node_modules junction, then original hook passed.
No skip-hook workaround. Whole-feature design/combined reliability audit is
assigned to core TA T-ETA-FINAL-DESIGN-COEXISTENCE-20261022-1.
No main/practical advancement for ETA; no service operations.
