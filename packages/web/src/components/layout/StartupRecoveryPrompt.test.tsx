// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const { fetchMock, claimMock, decideMock } = vi.hoisted(() => ({
  fetchMock: vi.fn(),
  claimMock: vi.fn(),
  decideMock: vi.fn(),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchStartupRecovery: fetchMock,
    claimStartupRecovery: claimMock,
    decideStartupRecovery: decideMock,
  };
});

import { StartupRecoveryPrompt } from './StartupRecoveryPrompt';

const candidate = {
  id: 'session-1',
  name: 'Prior Session',
  adapter: 'cbc',
  workdir: 'D:/project',
  lastLegalWorkerState: 'running' as const,
};

function record(
  state: 'pending' | 'failed' | 'completed' = 'pending',
  decision: 'restart' | 'preserve-running' | 'sync-actual' | null = null,
) {
  return {
    generation: 'generation-1',
    state,
    candidateSnapshot: [candidate],
    decision,
    attempts: state === 'failed' ? 1 : 0,
    results: [],
    error: state === 'failed' ? 'retry needed' : null,
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  claimMock.mockReset();
  decideMock.mockReset();
  fetchMock.mockResolvedValue(record());
  claimMock.mockResolvedValue({
    ok: true,
    claimed: true,
    state: 'pending',
    decision: null,
    candidates: [candidate],
  });
});

afterEach(() => cleanup());

describe('StartupRecoveryPrompt', () => {
  it('does not show a prompt when the process startup snapshot is empty', async () => {
    fetchMock.mockResolvedValue({ ...record(), state: 'no_candidates', candidateSnapshot: [] });
    render(<StartupRecoveryPrompt />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows all three choices and sends one durable choice', async () => {
    decideMock.mockResolvedValue(record('completed', 'restart'));
    render(<StartupRecoveryPrompt />);

    const dialog = await screen.findByRole('dialog');
    expect(dialog.className).toContain('max-w-[36rem]');
    expect(screen.getByText(/Restart these Sessions/)).toBeTruthy();
    expect(screen.getByText(/Keep their legal state as running/)).toBeTruthy();
    expect(screen.getByText(/Update legal state to current Worker state/)).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: /Restart these Sessions/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    await waitFor(() => expect(decideMock).toHaveBeenCalledTimes(1));
    expect(decideMock).toHaveBeenCalledWith('generation-1', expect.any(String), 'restart');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('keeps the load-error fallback at a readable width', async () => {
    fetchMock.mockRejectedValueOnce(new Error('API unavailable'));
    render(<StartupRecoveryPrompt />);

    const error = await screen.findByText('API unavailable');
    expect(error.closest('section')?.className).toContain('max-w-[32rem]');
  });

  it('locks a failed decision to the original choice for a retry', async () => {
    fetchMock.mockResolvedValue(record('failed', 'sync-actual'));
    claimMock.mockResolvedValue({
      ok: true,
      claimed: true,
      state: 'failed',
      decision: 'sync-actual',
      attempts: 1,
      error: 'retry needed',
      candidates: [candidate],
    });
    decideMock.mockResolvedValue(record('completed', 'sync-actual'));
    render(<StartupRecoveryPrompt />);

    expect(await screen.findByRole('dialog')).toBeTruthy();
    const restart = screen.getByRole('radio', { name: /Restart these Sessions/ });
    const sync = screen.getByRole('radio', { name: /Update legal state to current Worker state/ });
    expect((restart as HTMLInputElement).disabled).toBe(true);
    expect((sync as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Retry choice' }));
    await waitFor(() => expect(decideMock).toHaveBeenCalledWith(
      'generation-1', expect.any(String), 'sync-actual',
    ));
  });

  it('does not render another prompt while another tab owns the claim', async () => {
    claimMock.mockResolvedValue({ ok: true, claimed: false, state: 'pending' });
    render(<StartupRecoveryPrompt />);
    await waitFor(() => expect(claimMock).toHaveBeenCalled());
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
