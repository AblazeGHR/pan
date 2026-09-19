// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastContainer } from './Toast';
import { useUIStore } from '@/stores/uiStore';

const writeText = vi.fn<(text: string) => Promise<void>>();

function setToasts(...messages: string[]) {
  useUIStore.setState({
    toastQueue: messages.map((message, index) => ({
      id: `toast-test-${index}`,
      message,
      type: index % 2 === 0 ? 'info' : 'error',
    })),
  });
}

describe('ToastContainer', () => {
  beforeEach(() => {
    writeText.mockReset();
    writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    setToasts();
  });

  afterEach(() => {
    cleanup();
    useUIStore.setState({ toastQueue: [] });
  });

  it('copies the displayed message and still dismisses after clicking a toast', async () => {
    setToasts('Saved successfully');
    render(<ToastContainer />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy toast message: Saved successfully' }));
    await act(async () => {});
    expect(writeText).toHaveBeenCalledWith('Saved successfully');

    fireEvent.animationEnd(screen.getByRole('alert'));
    expect(useUIStore.getState().toastQueue).toEqual([]);
  });

  it('keeps toast interactions independent', async () => {
    setToasts('First toast', 'Second toast');
    render(<ToastContainer />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy toast message: First toast' }));
    await act(async () => {});
    expect(writeText).toHaveBeenCalledWith('First toast');
    expect(writeText).not.toHaveBeenCalledWith('Second toast');

    fireEvent.animationEnd(screen.getAllByRole('alert')[0]!);
    expect(useUIStore.getState().toastQueue.map((toast) => toast.message)).toEqual(['Second toast']);
  });

  it('swallows clipboard rejection and still dismisses', async () => {
    writeText.mockRejectedValueOnce(new Error('clipboard unavailable'));
    setToasts('Cannot copy');
    render(<ToastContainer />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy toast message: Cannot copy' }));
    await act(async () => {});
    fireEvent.animationEnd(screen.getByRole('alert'));

    expect(useUIStore.getState().toastQueue).toEqual([]);
  });

  it.each(['Enter', ' '])('copies and dismisses with %s', async (key) => {
    setToasts('Keyboard toast');
    render(<ToastContainer />);

    fireEvent.keyDown(screen.getByRole('button', { name: 'Copy toast message: Keyboard toast' }), { key });
    await act(async () => {});
    expect(writeText).toHaveBeenCalledWith('Keyboard toast');

    fireEvent.animationEnd(screen.getByRole('alert'));
    expect(useUIStore.getState().toastQueue).toEqual([]);
  });

  it('keeps the dismiss button independent from copying', async () => {
    setToasts('Close without copying');
    render(<ToastContainer />);

    expect(screen.getAllByRole('button')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss toast' }));
    await act(async () => {});

    expect(writeText).not.toHaveBeenCalled();
    fireEvent.animationEnd(screen.getByRole('alert'));
    expect(useUIStore.getState().toastQueue).toEqual([]);
  });
});
