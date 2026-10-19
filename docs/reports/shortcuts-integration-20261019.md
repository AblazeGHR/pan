# Shortcuts integration acceptance

Integrated 7d05bd9055426819066efbb5f128a9df896b9834 onto main d913012392d885c2580e07bd09c144e752491e5d in an isolated worktree. Preserved Lifecycle/Behavior/Remote categories, LoginStartupSettings, and inserted Shortcuts between Notifications and Adapters. Updated the obsolete reserved-slot test to require the real page. No backend product code changes.

MA verification: 8 frontend suites / 116 tests passed; isolated settings and real queue HTTP / 18 tests passed (one existing pydantic warning). TypeScript, Vite build and 72 compressed assets passed; commit hook passed. TA evidence records 11 Chromium keyboard cases, baseline-identical two old pytest failures, and real xterm DOM focus avoidance. Terminal engine did not start in that fixture; no claim of PTY key delivery. No local full CI performed.

Requires final combined SHA GitHub CI before release. Auto-report remains excluded until release completes. Production service was not restarted.
