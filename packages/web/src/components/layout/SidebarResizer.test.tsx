// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { SidebarResizer } from './SidebarResizer';
import { useUIStore } from '@/stores/uiStore';

afterEach(() => {
  cleanup();
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
});

describe('SidebarResizer', () => {
  it('keeps a visible resize target and applies pointer drag width changes', () => {
    useUIStore.setState({ sidebarWidth: 280 });
    const { container } = render(<SidebarResizer />);
    const resizer = container.firstElementChild as HTMLElement;

    expect(resizer.className).toContain('cursor-col-resize');
    expect(resizer.querySelector('div')?.className).toContain('bg-border-default/70');

    fireEvent.mouseDown(resizer, { clientX: 280 });
    fireEvent.mouseMove(document, { clientX: 320 });
    fireEvent.mouseUp(document);

    expect(useUIStore.getState().sidebarWidth).toBe(320);
  });
});
