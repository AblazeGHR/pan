// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import { SessionInfoArea } from './SessionInfoArea';

/** Natural info-area widths (px) read by the fit decision's layout stub. */
const WIDTHS: Record<string, number> = {
  'session-job-badge': 14,
  'session-queue-badge': 14,
  'session-unread-done-badge': 14,
  'session-quick-action-adapter': 24,
};

function widthOf(el: Element): number {
  for (const className of Object.keys(WIDTHS)) {
    if (el.classList.contains(className)) return WIDTHS[className]!;
  }
  return 0;
}

/** Rail width seen by the component; mutated per case. */
let railWidth = 72;
const originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');

interface AreaProps {
  indicator?: 'running' | 'scheduled' | null;
  queuePendingCount?: number;
  unreadDoneCount?: number;
  adapter?: string;
}

function renderArea(props: AreaProps = {}) {
  const {
    indicator = 'running',
    queuePendingCount = 0,
    unreadDoneCount = 0,
    adapter = 'cbc',
  } = props;
  return render(
    <div className="session-quick-action-rail">
      <SessionInfoArea
        sessionId="session-1"
        indicator={indicator}
        queuePendingCount={queuePendingCount}
        queueAllLocked={false}
        unreadDoneCount={unreadDoneCount}
        unreadDoneGeneration={unreadDoneCount}
        adapter={adapter}
      />
    </div>,
  );
}

describe('SessionInfoArea Job badge width-driven visibility', () => {
  beforeEach(() => {
    railWidth = 72;
    WIDTHS['session-queue-badge'] = 14;
    WIDTHS['session-unread-done-badge'] = 14;
    WIDTHS['session-quick-action-adapter'] = 24;
    // jsdom performs no layout: stub the natural widths and the rail width the
    // fit decision reads (installed before render so the layout effect sees them).
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
      function (this: Element) {
        const width = widthOf(this);
        return { width, height: 14, top: 0, left: 0, right: width, bottom: 14, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
      },
    );
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('session-quick-action-rail') ? railWidth : 0;
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    if (originalClientWidth) {
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth);
    }
  });

  const badge = (container: HTMLElement) =>
    container.querySelector('[data-testid="session-job-badge"]');

  it('shows J when it is the only info-area item', () => {
    const { container } = renderArea({ adapter: undefined });
    expect(badge(container)).toBeTruthy();
    expect(badge(container)!.getAttribute('data-job-state')).toBe('running');
  });

  it('shows J beside a single numeric badge and the adapter (14 + 38 + 6 = 58)', () => {
    const { container } = renderArea({ unreadDoneCount: 3 });
    expect(badge(container)).toBeTruthy();
  });

  it('shows J beside multi-digit numbers and a short adapter (14 + 48 + 6 = 68)', () => {
    WIDTHS['session-unread-done-badge'] = 24; // "99+"
    const { container } = renderArea({ unreadDoneCount: 120 });
    expect(badge(container)).toBeTruthy();
  });

  it('hides J completely when two numbers plus the adapter do not fit (14 + 52 + 9 = 75)', () => {
    const { container } = renderArea({ queuePendingCount: 9, unreadDoneCount: 3 });
    // Unmounted entirely: no leftover pill, no reserved gap.
    expect(badge(container)).toBeNull();
  });

  it('hides J when a long adapter pushes the row past the rail (14 + 64 + 6 = 84)', () => {
    WIDTHS['session-unread-done-badge'] = 24; // "99+"
    WIDTHS['session-quick-action-adapter'] = 40; // "opencode"
    const { container } = renderArea({ unreadDoneCount: 120, adapter: 'opencode' });
    expect(badge(container)).toBeNull();
  });

  it('hides J when both numbers are multi-digit (14 + 72 + 9 = 95)', () => {
    WIDTHS['session-queue-badge'] = 24;
    WIDTHS['session-unread-done-badge'] = 24;
    const { container } = renderArea({ queuePendingCount: 120, unreadDoneCount: 120 });
    expect(badge(container)).toBeNull();
  });

  it('re-shows J when a sibling disappears (width-driven, not sticky)', () => {
    const { container, rerender } = renderArea({ queuePendingCount: 9, unreadDoneCount: 3 });
    expect(badge(container)).toBeNull();

    // The queue badge unmounts: 14 + 38 + 6 = 58 fits again.
    rerender(
      <div className="session-quick-action-rail">
        <SessionInfoArea
          sessionId="session-1"
          indicator="running"
          queuePendingCount={0}
          queueAllLocked={false}
          unreadDoneCount={3}
          unreadDoneGeneration={3}
          adapter="cbc"
        />
      </div>,
    );
    expect(badge(container)).toBeTruthy();
  });

  it('decides by the info-area width, not the viewport: a narrower rail hides J', () => {
    const { container } = renderArea({ unreadDoneCount: 3 });
    expect(badge(container)).toBeTruthy();

    // Same content, less available width (rail 40 < 58): J disappears although
    // nothing viewport-related changed.
    railWidth = 40;
    const { container: narrow } = renderArea({ unreadDoneCount: 3 });
    expect(badge(narrow)).toBeNull();
  });

  it('renders no J at all without a targeted Job', () => {
    const { container } = renderArea({ indicator: null, unreadDoneCount: 3 });
    expect(badge(container)).toBeNull();
    // The numeric badge and adapter keep rendering.
    expect(container.querySelector('[data-testid="session-unread-done-badge"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="session-adapter-badge"]')).toBeTruthy();
  });

  it('renders the scheduled (yellow) state when visible', () => {
    const { container } = renderArea({ indicator: 'scheduled', unreadDoneCount: 3 });
    expect(badge(container)!.getAttribute('data-job-state')).toBe('scheduled');
  });

  it('re-measures once after the webfont settles (display:swap flip window)', async () => {
    // Fonts still loading: the first measurement uses fallback-era widths.
    let resolveReady!: () => void;
    const ready = new Promise<void>((resolve) => { resolveReady = resolve; });
    const originalFonts = document.fonts;
    Object.defineProperty(document, 'fonts', {
      configurable: true,
      value: { status: 'loading', ready },
    });
    try {
      WIDTHS['session-unread-done-badge'] = 24; // "99+"
      WIDTHS['session-quick-action-adapter'] = 40; // long adapter: 84 > 71
      const { container } = renderArea({ unreadDoneCount: 120, adapter: 'opencode' });
      expect(badge(container)).toBeNull();

      // The webfont lands and the glyphs settle at Inter metrics.
      WIDTHS['session-quick-action-adapter'] = 24; // 14 + 48 + 6 = 68 <= 71
      resolveReady();
      await waitFor(() => expect(badge(container)).toBeTruthy());
    } finally {
      Object.defineProperty(document, 'fonts', {
        configurable: true,
        value: originalFonts,
      });
    }
  });

  it('keeps the numeric badges and adapter rendered while J is hidden', () => {
    const { container } = renderArea({ queuePendingCount: 120, unreadDoneCount: 120 });
    expect(badge(container)).toBeNull();
    expect(container.querySelector('[data-testid="session-queue-badge"]')!.textContent).toBe('99+');
    expect(container.querySelector('[data-testid="session-unread-done-badge"]')!.textContent).toBe('99+');
    expect(container.querySelector('[data-testid="session-adapter-badge"]')!.textContent).toBe('cbc');
  });
});
