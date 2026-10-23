# MA Locker and CI integration acceptance

Accepted inputs: Locker `25b0a5d25cb78820851d291e29acfb4746860ff0` and CI diagnostics `c8871886a9c792c7a5d2290b6b8aa92eb42b5173`, based on main `818f6b849d31cac98f03766ad1a6eb7788f5ea58`.

Both histories merged without conflict. Tested merge source: `3ad01ccc9671f2bf0f16554a6ec699b6267bdee1`. The subsequent review commit only preserves this review, live evidence, and removes a trailing blank line from the fake browser harness.

MA reviewed ordinary-message schema/storage, additive initialization and tombstones, recipient/source/attachment authorization, mixed pagination and ordering, auth-response fencing, read revisions, Card lifecycle and shared Markdown behavior. The User Locker panel remains independent of Chat; Agent Inbox has no added GUI. Public producer API/MCP remains deferred as requested.

Independent checks:

- Locker candidate: 40 backend tests and 20 frontend tests passed, exit 0.
- Combined source: 65 backend tests covering Locker/Card, runtime issues, system fields and metadata-read repair passed, exit 0.
- Combined production build and TypeScript passed; 94 compressed assets verified.
- Combined real HTTP, production Chromium, fakeCLI and cold-restart test passed, exit 0. No core API interception; source notifications consumed once, original Queue IDs retained, actual choice-to-Trigger-to-Pause and protected attachment checks passed. Owned processes ended and port released; see `live-evidence.json`.
- Product/test/document whitespace check passed after removing the harness's trailing blank line. Historical raw audit logs retain their original bytes.

This accepts main integration, not practical deployment. Remote CI has not verified this complete SHA. Prior intermittent settings PUT 500 and Locker shutdown failures remain unclosed pending the exact-SHA matrix and diagnostics; passing local tests is not proof of their root cause.

Next: push the complete main candidate; collect Python 3.12/3.14 logs/JUnit and repair failures. In parallel, repeat the practical read-only private-copy compatibility gate against the final source, including additive Locker initialization and unknown/non-replay. Follow `docs/plans&overviews/main-practical-integration-gate.md`; practical, services and real data remain unchanged until all gates close.
