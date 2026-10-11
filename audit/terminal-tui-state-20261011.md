# Terminal TUI entry acceptance — 2026-10-11

Scope: TopBar entry presentation and index.css only; control ownership, upgrade API and worker lifecycle unchanged. Available/enabled use translucent success theme tokens. Upgrade retains gray with terminal + ArrowUp and desktop label Upgrades. Mobile hides text in every state, retains Terminal TUI aria-label and state-specific title, and reserves 44px target.

Actual isolated Chromium: e2e/terminalTuiStates.mjs passed 30 combinations (1440/390/320px × dark/light × upgrade/available/enabled/upgrading/error). Real ChatView/TopBar, intercepted HTTP. Asserted mobile no text, >=44×44 target, within viewport/no horizontal overflow, desktop labels, icon, title/aria, disabled pending, theme color equality and contrast. Screenshots and six traces: packages/web/test-results/terminal-tui-states/. Initial color assertion sampled during CSS transition; driver now waits actual element animations to finish without changing result assertions.

Existing native-control.e2e.mjs passed real xterm typing, generation write gate, queue visibility, refresh ownership, return to Pan, pending idle upgrade and unknown control gate. Its Vite server now configFile:false, isolated cache and no proxy, preserving assertions. HTTP is intercepted; this is not a real provider/PTY/backend acceptance. Native provider, OS terminal and human theme acceptance remain unverified. Mobile touch target geometry is measured; tap delivery on a physical device is not verified.

19 unit tests passed (TopBar 12 + nativeControlStore 7). Targeted eslint passed. Production build passed, 62 compressed assets verified. Logs: unit.log, lint.log, build.log, native-browser.log; raw browser state evidence.json and screenshots/traces under terminal-tui-states.

No protected ports, native data, shared dependency install, push or main/practical changes. Junction retained per user instruction.
