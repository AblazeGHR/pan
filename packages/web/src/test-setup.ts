// Global Vitest setup.
//
// jsdom does not implement window.matchMedia. Components using the
// useMediaQuery hook (mobile breakpoint detection) would crash in tests.
// Provide a non-matching (desktop) stub; individual tests can override it
// with vi.stubGlobal('matchMedia', ...) to simulate the mobile breakpoint.
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// jsdom has no layout observer. Real popup/scroll geometry is verified in
// Chromium; unit fixtures may replace this with an observer that emits sizes.
if (typeof window !== 'undefined' && !globalThis.ResizeObserver) {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    writable: true,
    configurable: true,
    value: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
}
