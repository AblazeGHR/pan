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
    expect(card.className).toContain('max-w-2xl');
  });

  it('applies the requested size class', () => {
    render(
      <Modal open onClose={() => {}} title="Test" size="sm">
        <div>content</div>
      </Modal>,
    );
    expect(cardEl().className).toContain('max-w-sm');
  });
});
