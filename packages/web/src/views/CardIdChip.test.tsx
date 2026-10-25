// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CardIdChip } from './CardIdChip';
import { useUIStore } from '@/stores/uiStore';

const clipboard = vi.hoisted(() => ({ copyText: vi.fn() }));
vi.mock('@/utils/clipboard', () => clipboard);

const ID = `card_${'a'.repeat(64)}`;

describe('Locker card id chip', () => {
  beforeEach(() => {
    clipboard.copyText.mockReset().mockResolvedValue(undefined);
    useUIStore.setState({ toastQueue: [] });
  });
  afterEach(cleanup);

  it('shows the readable id with the full value in the tooltip and accessible name', () => {
    render(<CardIdChip id={ID} testId="locker-card-id" kind="interaction" />);
    const chip = screen.getByTestId('locker-card-id');
    expect(chip.textContent).toContain(ID);
    expect(chip.getAttribute('title')).toContain(ID);
    expect(chip.getAttribute('aria-label')).toContain(ID);
    expect(chip.getAttribute('data-card-id-value')).toBe(ID);
  });

  it('copies the exact id and reports success', async () => {
    render(<CardIdChip id={ID} testId="locker-card-id" kind="interaction" />);
    fireEvent.click(screen.getByTestId('locker-card-id'));
    await waitFor(() => expect(clipboard.copyText).toHaveBeenCalledWith(ID));
    await waitFor(() =>
      expect(useUIStore.getState().toastQueue.map((toast) => toast.message)).toContain(
        '卡片 ID 已复制',
      ),
    );
  });

  it('reports a clipboard failure instead of claiming success', async () => {
    clipboard.copyText.mockRejectedValueOnce(new Error('denied'));
    render(<CardIdChip id={ID} testId="reminding-card-id" kind="reminding" />);
    fireEvent.click(screen.getByTestId('reminding-card-id'));
    await waitFor(() =>
      expect(useUIStore.getState().toastQueue.map((toast) => toast.type)).toContain('error'),
    );
    expect(useUIStore.getState().toastQueue.map((toast) => toast.message)).not.toContain(
      '卡片 ID 已复制',
    );
  });
});
