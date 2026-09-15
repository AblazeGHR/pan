// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Modal } from './Modal';

// Modal renders through a portal to document.body — query there, not the
// render() container.
function cardEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.modal-card');
  expect(el).toBeTruthy();
  return el!;
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

describe('Modal', () => {
  it('renders the card with a full-width base and the requested max-width', () => {
    render(
      <Modal open onClose={() => {}} title="Test" size="lg">
        <div className="w-40">content</div>
      </Modal>,
    );

    const overlay = document.body.querySelector<HTMLElement>('.modal-overlay')!;
    const card = cardEl();

    // Overlay owns the horizontal inset (padding), so the card width is
    // deterministic (viewport − padding, capped by max-w) instead of relying
    // on flex-shrink-with-margins — the cause of the squeezed-slit modals.
    expect(overlay.className).toContain('p-4');
    expect(card.className).toContain('w-full');
    expect(card.className).toContain('max-w-[42rem]');
  });

  it('uses explicit width values for named sizes instead of theme spacing tokens', () => {
    render(
      <>
        <Modal open onClose={() => {}} title="Small" size="sm">
          <div>small</div>
        </Modal>
        <Modal open onClose={() => {}} title="Medium" size="md">
          <div>medium</div>
        </Modal>
        <Modal open onClose={() => {}} title="Large" size="lg">
          <div>large</div>
        </Modal>
        <Modal open onClose={() => {}} title="Extra Large" size="xl">
          <div>extra large</div>
        </Modal>
      </>,
    );

    const cards = Array.from(document.body.querySelectorAll<HTMLElement>('.modal-card'));
    expect(cards).toHaveLength(4);
    expect(cards.map((card) => card.className)).toEqual([
      expect.stringContaining('max-w-[24rem]'),
      expect.stringContaining('max-w-[32rem]'),
      expect.stringContaining('max-w-[42rem]'),
      expect.stringContaining('max-w-[56rem]'),
    ]);
    for (const card of cards) {
      expect(card.className).not.toMatch(/\bmax-w-(sm|md|lg|xl)\b/);
      expect(card.className).not.toContain('var(--spacing-lg)');
    }
  });

  it('leaves callers that do not opt in as centered dialogs', () => {
    render(
      <Modal open onClose={() => {}} title="Test" size="lg">
        <div>content</div>
      </Modal>,
    );

    const overlay = document.body.querySelector<HTMLElement>('.modal-overlay')!;
    const card = cardEl();

    expect(overlay.className).not.toContain('modal-overlay--mobile-fullscreen');
    expect(overlay.className).not.toContain('p-0');
    expect(card.className).not.toContain('modal-card--mobile-fullscreen');
    expect(card.className).not.toContain('max-md:h-[100dvh]');
    expect(card.className).not.toContain('max-md:rounded-none');
  });

  it('supports caller-scoped mobile fullscreen presentation', () => {
    render(
      <Modal open onClose={() => {}} title="Test" size="xl" mobileFullscreen>
        <div>content</div>
      </Modal>,
    );

    const overlay = document.body.querySelector<HTMLElement>('.modal-overlay')!;
    const card = cardEl();
    expect(overlay.className).toContain('p-0 md:p-4');
    expect(overlay.className).toContain('modal-overlay--mobile-fullscreen');
    expect(card.className).toContain('max-w-[56rem]');
    expect(card.className).toContain('max-md:h-[100dvh]');
    expect(card.className).toContain('modal-card--mobile-fullscreen');
    expect(card.className).toContain('max-md:rounded-none');
    // Safe-area insets keep the title row clear of the notch and the last
    // content line clear of the home indicator; both resolve to 0 outside a
    // notched standalone viewport.
    expect(card.className).toContain('max-md:pt-[var(--safe-top)]');
    expect(card.className).toContain('max-md:pb-[var(--safe-bottom)]');
  });

  it('scopes every fullscreen override to the mobile breakpoint', () => {
    render(
      <Modal open onClose={() => {}} title="Test" size="lg" mobileFullscreen>
        <div>content</div>
      </Modal>,
    );

    const card = cardEl();
    const cardClasses = card.className.split(/\s+/);

    // Desktop geometry comes from the untouched base + size classes.
    expect(cardClasses).toContain('rounded-lg');
    expect(cardClasses).toContain('border');
    expect(cardClasses).toContain('max-w-[42rem]');
    expect(cardClasses).toContain('max-h-[85vh]');
    // The fullscreen shell only adds max-md:-scoped overrides, so ≥ md is
    // byte-for-byte the pre-existing centered window.
    expect(cardClasses).not.toContain('rounded-none');
    expect(cardClasses).not.toContain('max-w-none');
    expect(cardClasses).not.toContain('h-[100dvh]');
    expect(cardClasses.filter((name) => /(100dvh|rounded-none|max-w-none)/.test(name)).every((name) => name.startsWith('max-md:'))).toBe(true);
  });
});
