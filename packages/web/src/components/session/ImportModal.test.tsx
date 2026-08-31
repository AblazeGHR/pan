// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ImportModal } from './ImportModal';
import { useAdapterStore } from '@/stores/adapterStore';
import type { ApiCliStatusResponse } from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchCbcProjects: vi.fn(async () => []),
  fetchCbcSessions: vi.fn(async () => []),
  importCbcSession: vi.fn(),
  fetchKimiWorkspaces: vi.fn(async () => []),
  fetchKimiSessions: vi.fn(async () => []),
  importKimiSession: vi.fn(),
  fetchOpencodeSessions: vi.fn(async () => []),
  importOpencodeSession: vi.fn(),
  fetchCodexSessions: vi.fn(async () => []),
  importCodexSession: vi.fn(),
}));

vi.mock('@/services/api', () => apiMock);

const cliStatus = (...entries: Array<[string, boolean]>): ApiCliStatusResponse => ({
  adapters: entries.map(([name, available]) => ({
    name,
    label: name,
    available,
    command: [name],
    missing: available ? [] : [name],
    hint: '',
  })),
  available: entries.filter(([, available]) => available).map(([name]) => name),
  hasAvailable: entries.some(([, available]) => available),
});

describe('ImportModal adapter availability', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAdapterStore.setState({
      cliStatus: cliStatus(
        ['cbc', true],
        ['kimi', false],
        ['opencode', false],
        ['codex', true],
      ),
      cliStatusLoading: false,
      cliStatusError: null,
      loadCliStatus: vi.fn(async () => {}),
    });
  });

  afterEach(cleanup);

  it('renders tabs only for available adapters', () => {
    render(<ImportModal open onClose={() => {}} />);

    expect(screen.getByRole('button', { name: 'cbc' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'codex' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'kimi' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'opencode' })).toBeNull();
  });

  it('switches the dynamically rendered tab and loads that adapter sessions', async () => {
    render(<ImportModal open onClose={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: 'codex' }));
    expect(screen.getByText('Working Directory')).toBeTruthy();
    await waitFor(() => expect(apiMock.fetchCodexSessions).toHaveBeenCalledWith(''));
  });

  it('shows the CLI request error instead of rendering import tabs', () => {
    useAdapterStore.setState({
      cliStatus: null,
      cliStatusLoading: false,
      cliStatusError: 'connection refused',
    });
    render(<ImportModal open onClose={() => {}} />);

    expect(screen.getByRole('alert').textContent).toContain('connection refused');
    expect(screen.queryByRole('button', { name: 'cbc' })).toBeNull();
  });

  it('shows a loading state before rendering adapter tabs', () => {
    useAdapterStore.setState({
      cliStatus: null,
      cliStatusLoading: true,
      cliStatusError: null,
    });
    render(<ImportModal open onClose={() => {}} />);

    expect(screen.getByText('正在检测 Agent CLI 可用性…')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'cbc' })).toBeNull();
  });

  it('explains an empty available adapter list', () => {
    useAdapterStore.setState({
      cliStatus: cliStatus(['cbc', false], ['kimi', false]),
      cliStatusLoading: false,
      cliStatusError: null,
    });
    render(<ImportModal open onClose={() => {}} />);

    expect(screen.getByRole('alert').textContent).toContain('没有可用的 Agent CLI');
    expect(screen.queryByRole('button', { name: 'cbc' })).toBeNull();
  });

  it('falls back from an unavailable initial adapter with an explanatory message', () => {
    render(<ImportModal open onClose={() => {}} initialAdapter="kimi" />);

    expect(screen.getByRole('button', { name: 'cbc' })).toBeTruthy();
    expect(screen.getByText(/请求的 adapter/).parentElement?.textContent)
      .toContain('kimi');
  });
});
