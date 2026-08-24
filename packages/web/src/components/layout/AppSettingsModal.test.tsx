// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/react';
import { AppSettingsModal } from './AppSettingsModal';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';

// AppSettingsModal renders through a portal to document.body — query there,
// not the render() container.
function overlayEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.app-settings-overlay');
  expect(el).toBeTruthy();
  return el!;
}

function cardEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.app-settings-card');
  expect(el).toBeTruthy();
  return el!;
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

describe('AppSettingsModal', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
  });

  it('renders nothing when closed', () => {
    render(<AppSettingsModal open={false} onClose={() => {}} />);
    expect(document.body.querySelector('.app-settings-overlay')).toBeNull();
  });

  it('renders the 4 settings items plus Reset', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const card = cardEl();
    expect(card.textContent).toContain('Default group by');
    expect(card.querySelectorAll('[role="switch"]')).toHaveLength(3);
    expect(card.textContent).toContain('Reset to defaults');
  });

  it('is full-screen on mobile and ~75% of the viewport on desktop', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const card = cardEl();
    // Mobile (base): edge-to-edge, no rounded corners.
    expect(card.className).toContain('w-full');
    expect(card.className).toContain('h-full');
    // Desktop (md+): centered card covering ~3/4 of the window.
    expect(card.className).toContain('md:w-[75vw]');
    expect(card.className).toContain('md:h-[75vh]');
  });

  it('closes when the backdrop is clicked', () => {
    const onClose = vi.fn();
    render(<AppSettingsModal open onClose={onClose} />);
    fireEvent.click(overlayEl());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close when clicking inside the card', () => {
    const onClose = vi.fn();
    render(<AppSettingsModal open onClose={onClose} />);
    fireEvent.click(cardEl());
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(<AppSettingsModal open onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('toggles a setting through a switch and writes the store', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const switches = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="switch"]'),
    );
    expect(switches).toHaveLength(3);
    // meta-agent is on by default; toggle it off.
    expect(switches[0]!.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(switches[0]!);
    expect(useAppSettingsStore.getState().showMetaAgent).toBe(false);
    expect(switches[0]!.getAttribute('aria-checked')).toBe('false');
  });

  it('changes default group by via the select', () => {
    render(<AppSettingsModal open onClose={() => {}} />);
    const select = document.body.querySelector<HTMLSelectElement>('select')!;
    fireEvent.change(select, { target: { value: 'workdir' } });
    expect(useAppSettingsStore.getState().defaultGroupBy).toBe('workdir');
  });

  it('resets all settings to defaults', () => {
    useAppSettingsStore.setState({
      ...DEFAULT_SETTINGS,
      defaultGroupBy: 'manager',
      showMetaAgent: false,
      showTaskAgent: false,
      showQQ: false,
    });
    render(<AppSettingsModal open onClose={() => {}} />);
    const resetBtn = Array.from(
      document.body.querySelectorAll<HTMLElement>('button'),
    ).find((b) => b.textContent?.includes('Reset to defaults'))!;
    fireEvent.click(resetBtn);
    const s = useAppSettingsStore.getState();
    expect(s.defaultGroupBy).toBe(DEFAULT_SETTINGS.defaultGroupBy);
    expect(s.showMetaAgent).toBe(true);
    expect(s.showTaskAgent).toBe(true);
    expect(s.showQQ).toBe(true);
  });
});
