// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NewSessionModal } from './NewSessionModal';
import { useSessionStore } from '@/stores/sessionStore';
import { useAdapterStore } from '@/stores/adapterStore';
import type { ApiCliStatusResponse } from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchSessionTemplates: vi.fn(),
  fetchDirectories: vi.fn(),
}));

vi.mock('@/services/api', () => apiMock);

const layer = (current: string, entries: Array<{ name: string; path: string }>, parent: string | null = null) => ({
  current,
  parent,
  entries: entries.map((entry) => ({ ...entry, isDirectory: true })),
});

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

describe('NewSessionModal working directory browser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.fetchSessionTemplates.mockResolvedValue([]);
    apiMock.fetchDirectories.mockResolvedValue(layer('', [{ name: 'D:\\', path: 'D:\\' }]));
    useAdapterStore.setState({
      adapters: [{ name: 'cbc', defaultModel: '', supportsResume: false, supportsFork: false }],
      cliStatus: cliStatus(['cbc', true]),
      cliStatusLoading: false,
      cliStatusError: null,
      adapterConfigs: { cbc: { models: [], defaultModel: '', effortValues: [], permissionModes: [], defaultPermissionMode: '', supportedSettings: [], executionModes: ['stream'] } },
      loadAdapterList: vi.fn(async () => {}), loadCliStatus: vi.fn(async () => {}), loadConfig: vi.fn(async () => {}),
    });
    useSessionStore.setState({ sessions: [] });
  });

  afterEach(cleanup);

  it('opens and loads only the server root layer', async () => {
    render(<NewSessionModal open onClose={() => {}} />);
    fireEvent.click(screen.getByLabelText('Add folder'));
    await waitFor(() => expect(screen.getByText('D:\\')).toBeTruthy());
    expect(apiMock.fetchDirectories).toHaveBeenCalledWith(undefined);
    expect(apiMock.fetchDirectories).toHaveBeenCalledTimes(1);
  });

  it('loads the next layer on click and writes the selected current directory', async () => {
    apiMock.fetchDirectories
      .mockResolvedValueOnce(layer('', [{ name: 'D:\\', path: 'D:\\' }]))
      .mockResolvedValueOnce(layer('D:\\', [{ name: 'pan', path: 'D:\\pan' }], null))
      .mockResolvedValueOnce(layer('D:\\pan', [] , 'D:\\'));
    render(<NewSessionModal open onClose={() => {}} />);
    fireEvent.click(screen.getByLabelText('Add folder'));
    await waitFor(() => screen.getByText('D:\\'));
    fireEvent.click(screen.getByRole('button', { name: 'D:\\' }));
    await waitFor(() => expect(screen.getByText('pan')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'pan' }));
    await waitFor(() => expect(screen.getByText('选择当前目录')).toBeTruthy());
    fireEvent.click(screen.getByText('选择当前目录'));
    expect((screen.getByPlaceholderText('/path/to/project') as HTMLInputElement).value).toBe('D:\\pan');
  });

  it('cancel keeps the original value', async () => {
    render(<NewSessionModal open onClose={() => {}} />);
    const input = screen.getByPlaceholderText('/path/to/project');
    fireEvent.change(input, { target: { value: 'D:\\existing' } });
    fireEvent.click(screen.getByLabelText('Add folder'));
    await waitFor(() => expect(screen.getByTestId('directory-browser')).toBeTruthy());
    fireEvent.click(screen.getByText('取消'));
    expect((input as HTMLInputElement).value).toBe('D:\\existing');
  });

  it('shows a loading error without changing the working directory', async () => {
    apiMock.fetchDirectories.mockRejectedValueOnce(new Error('Permission denied'));
    render(<NewSessionModal open onClose={() => {}} />);
    fireEvent.click(screen.getByLabelText('Add folder'));
    await waitFor(() => expect(screen.getByText('加载失败：Permission denied')).toBeTruthy());
    expect((screen.getByPlaceholderText('/path/to/project') as HTMLInputElement).value).toBe('');
  });

  it('does not let an older response replace the newer directory', async () => {
    let resolveA!: (value: ReturnType<typeof layer>) => void;
    let resolveB!: (value: ReturnType<typeof layer>) => void;
    apiMock.fetchDirectories.mockImplementation((path?: string) => {
      if (path === 'D:\\a') return new Promise((resolve) => { resolveA = resolve; });
      if (path === 'D:\\b') return new Promise((resolve) => { resolveB = resolve; });
      return Promise.resolve(layer('', [{ name: 'a', path: 'D:\\a' }, { name: 'b', path: 'D:\\b' }]));
    });
    render(<NewSessionModal open onClose={() => {}} />);
    fireEvent.click(screen.getByLabelText('Add folder'));
    await waitFor(() => screen.getByText('a'));
    fireEvent.click(screen.getByRole('button', { name: 'a' }));
    fireEvent.click(screen.getByRole('button', { name: 'b' }));
    resolveB(layer('D:\\b', []));
    resolveA(layer('D:\\a', [{ name: 'stale', path: 'D:\\a\\stale' }]));
    await waitFor(() => expect(screen.getByText('D:\\b')).toBeTruthy());
    expect(screen.queryByText('stale')).toBeNull();
  });

  it('shows only adapters reported as available by CLI status', () => {
    useAdapterStore.setState({
      cliStatus: cliStatus(['cbc', true], ['kimi', false], ['codex', true]),
    });
    render(<NewSessionModal open onClose={() => {}} />);

    const options = Array.from(screen.getAllByRole('combobox')[0]!.querySelectorAll('option'))
      .map((option) => option.value);
    expect(options).toEqual(['cbc', 'codex']);
    expect(options).not.toContain('kimi');
  });

  it('does not invent cbc when CLI status fails', () => {
    useAdapterStore.setState({
      cliStatus: null,
      cliStatusLoading: false,
      cliStatusError: 'connection refused',
    });
    render(<NewSessionModal open onClose={() => {}} />);

    expect(screen.getByText(/无法检测当前可用 adapter：connection refused/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Create' }).hasAttribute('disabled')).toBe(true);
    expect(screen.getAllByRole('combobox')[0]!.querySelector('option')?.value).toBe('');
  });

  it('shows a loading state before enabling adapter selection', () => {
    useAdapterStore.setState({
      cliStatus: null,
      cliStatusLoading: true,
      cliStatusError: null,
    });
    render(<NewSessionModal open onClose={() => {}} />);

    expect(screen.getAllByRole('combobox')[0]!.textContent).toContain('检测 CLI 可用性中');
    expect(screen.getAllByRole('combobox')[0]!.hasAttribute('disabled')).toBe(true);
  });

  it('explains when no adapter is available', () => {
    useAdapterStore.setState({
      cliStatus: cliStatus(['cbc', false], ['kimi', false]),
      cliStatusLoading: false,
      cliStatusError: null,
    });
    render(<NewSessionModal open onClose={() => {}} />);

    expect(screen.getByText(/当前没有可用的 Agent CLI/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Create' }).hasAttribute('disabled')).toBe(true);
  });

  it('blocks a template whose adapter is unavailable', async () => {
    apiMock.fetchSessionTemplates.mockResolvedValueOnce([
      { name: 'kimi-template', adapter: 'kimi', model: '', mcpServers: [] } as never,
    ]);
    useAdapterStore.setState({
      cliStatus: cliStatus(['cbc', true], ['kimi', false]),
      cliStatusLoading: false,
      cliStatusError: null,
    });
    render(<NewSessionModal open onClose={() => {}} />);

    // The label is not associated with the select in this legacy modal, so
    // identify it by its option value when querying the rendered controls.
    const templateSelect = await waitFor(() => {
      const select = screen.getAllByRole('combobox').find((candidate) =>
        candidate.querySelector('option[value="kimi-template"]'),
      );
      expect(select).toBeTruthy();
      return select!;
    });
    fireEvent.change(templateSelect, { target: { value: 'kimi-template' } });

    await waitFor(() => expect(screen.getByText(/当前模板要求 adapter/).parentElement?.textContent)
      .toContain('kimi'));
    expect(screen.getByRole('button', { name: 'Create' }).hasAttribute('disabled')).toBe(true);
  });
});
