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
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';

// The convergence refresh fired when a recovery decision completes is the fix
// for the stale post-restart indicator: legal-state writes are silent on the
// WebSocket, so without it the sidebar keeps the pre-decision snapshot and a
// Session synchronized to its idle runtime keeps the mismatch red forever.
const { loadSessionsMock, workerRefreshMock } = vi.hoisted(() => ({
  loadSessionsMock: vi.fn(async () => {}),
  workerRefreshMock: vi.fn(async () => {}),
}));

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
  loadSessionsMock.mockClear();
  workerRefreshMock.mockClear();
  useSessionStore.setState({
    loadSessions: loadSessionsMock,
  } as unknown as Partial<ReturnType<typeof useSessionStore.getState>>);
  useWorkerStore.setState({
    refresh: workerRefreshMock,
  } as unknown as Partial<ReturnType<typeof useWorkerStore.getState>>);
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

  it('shows automatic startup failure details and exposes only the saved-choice retry', async () => {
    const failed = {
      ...record('failed', 'restart'),
      results: [{ sessionId: candidate.id, status: 'error', error: 'queue store unavailable' }],
    };
    fetchMock.mockResolvedValue(failed);
    claimMock.mockResolvedValue({
      ok: true,
      claimed: true,
      state: 'failed',
      decision: 'restart',
      attempts: 1,
      results: failed.results,
      candidates: [candidate],
    });
    decideMock.mockResolvedValue(record('completed', 'restart'));
    render(<StartupRecoveryPrompt />);

    expect(await screen.findByRole('dialog')).toBeTruthy();
    expect(screen.getByText('session-1: queue store unavailable')).toBeTruthy();
    expect((screen.getByRole('radio', { name: /Restart these Sessions/ }) as HTMLInputElement).checked)
      .toBe(true);
    expect((screen.getByRole('radio', { name: /Keep their legal state as running/ }) as HTMLInputElement).disabled)
      .toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Retry choice' }));
    await waitFor(() => expect(decideMock).toHaveBeenCalledWith(
      'generation-1', expect.any(String), 'restart',
    ));
  });

  it('does not render another prompt while another tab owns the claim', async () => {
    claimMock.mockResolvedValue({ ok: true, claimed: false, state: 'pending' });
    render(<StartupRecoveryPrompt />);
    await waitFor(() => expect(claimMock).toHaveBeenCalled());
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('converges the Session list after this tab submits a sync-actual decision', async () => {
    decideMock.mockResolvedValue(record('completed', 'sync-actual'));
    render(<StartupRecoveryPrompt />);

    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('radio', { name: /Update legal state to current Worker state/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    await waitFor(() => expect(decideMock).toHaveBeenCalled());
    await waitFor(() => expect(loadSessionsMock).toHaveBeenCalledTimes(1));
    expect(workerRefreshMock).toHaveBeenCalledTimes(1);
  });

  it('converges when the claim observes another tab already completed the decision', async () => {
    claimMock.mockResolvedValue({
      ok: true,
      claimed: false,
      state: 'completed',
      decision: 'sync-actual',
      candidates: [candidate],
    });
    render(<StartupRecoveryPrompt />);

    await waitFor(() => expect(claimMock).toHaveBeenCalled());
    await waitFor(() => expect(loadSessionsMock).toHaveBeenCalledTimes(1));
    expect(workerRefreshMock).toHaveBeenCalledTimes(1);
  });

  it('converges exactly once when the record is already completed on mount', async () => {
    fetchMock.mockResolvedValue(record('completed', 'preserve-running'));
    render(<StartupRecoveryPrompt />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await waitFor(() => expect(loadSessionsMock).toHaveBeenCalledTimes(1));
    expect(workerRefreshMock).toHaveBeenCalledTimes(1);
  });

  it('does not converge while the decision is still pending', async () => {
    render(<StartupRecoveryPrompt />);
    await waitFor(() => expect(claimMock).toHaveBeenCalled());
    // Give the effect's promise chain a tick to settle.
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeTruthy());
    expect(loadSessionsMock).not.toHaveBeenCalled();
    expect(workerRefreshMock).not.toHaveBeenCalled();
  });
});
