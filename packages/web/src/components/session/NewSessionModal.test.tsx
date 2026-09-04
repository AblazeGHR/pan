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

function setupCommonStores() {
  useAdapterStore.setState({
    adapters: [{ name: 'cbc', defaultModel: '', supportsResume: false, supportsFork: false }],
    cliStatus: cliStatus(['cbc', true]),
    cliStatusLoading: false,
    cliStatusError: null,
    adapterConfigs: { cbc: { models: [], defaultModel: '', effortValues: [], permissionModes: [], defaultPermissionMode: '', supportedSettings: [], executionModes: ['stream'] } },
    loadAdapterList: vi.fn(async () => {}), loadCliStatus: vi.fn(async () => {}), loadConfig: vi.fn(async () => {}),
  });
  useSessionStore.setState({ sessions: [] });
}

/** jsdom's matchMedia never matches; stub it to simulate the mobile breakpoint. */
function mockMatchMedia(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

describe('NewSessionModal working directory browser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.fetchSessionTemplates.mockResolvedValue([]);
    apiMock.fetchDirectories.mockResolvedValue(layer('', [{ name: 'D:\\', path: 'D:\\' }]));
    setupCommonStores();
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

describe('NewSessionModal mobile full-screen page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.fetchSessionTemplates.mockResolvedValue([]);
    apiMock.fetchDirectories.mockResolvedValue(layer('', [{ name: 'D:\\', path: 'D:\\' }]));
    setupCommonStores();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('desktop keeps the centered dialog and does not render the full-screen page', () => {
    render(<NewSessionModal open onClose={() => {}} />);

    expect(document.querySelector('.modal-overlay')).not.toBeNull();
    expect(screen.queryByTestId('new-session-fullscreen')).toBeNull();
  });

  it('mobile renders a full-screen page with back button, scroll body and footer actions', () => {
    mockMatchMedia(true);
    const onClose = vi.fn();
    render(<NewSessionModal open onClose={onClose} />);

    const page = screen.getByTestId('new-session-fullscreen');
    expect(page.getAttribute('role')).toBe('dialog');
    expect(document.querySelector('.modal-overlay')).toBeNull();

    // Back (close) in the header.
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    cleanup();

    // Re-open: scrollable middle section and fixed footer with safe-area
    // padding, holding Cancel + the form-associated Create button.
    render(<NewSessionModal open onClose={onClose} />);
    const scrollBody = page.querySelector('.overflow-y-auto');
    expect(scrollBody).not.toBeNull();
    expect(scrollBody!.className).toContain('min-h-0');
    const footer = page.querySelector('footer')!;
    expect(footer.className).toContain('safe-area-inset-bottom');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();
    const create = screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement;
    expect(create.getAttribute('form')).toBe('new-session-form');
    expect(create.hasAttribute('disabled')).toBe(false);
  });

  it('mobile full-screen page also closes on Escape', () => {
    mockMatchMedia(true);
    const onClose = vi.fn();
    render(<NewSessionModal open onClose={onClose} />);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('NewSessionModal directory browser window and hierarchy', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Drop leftover mockResolvedValueOnce queues from earlier tests —
    // clearAllMocks only clears call history, so an unconsumed "Once" layer
    // (e.g. a cache-hit path that never fetched) would leak into here and
    // desynchronize the layer sequence.
    apiMock.fetchSessionTemplates.mockReset().mockResolvedValue([]);
    apiMock.fetchDirectories.mockReset();
    setupCommonStores();
  });

  afterEach(cleanup);

  async function openBrowser() {
    render(<NewSessionModal open onClose={() => {}} />);
    fireEvent.click(screen.getByLabelText('Add folder'));
    await waitFor(() => expect(screen.getByTestId('directory-browser')).toBeTruthy());
  }

  it('renders entries in a fixed-height scrollable window with an explicit scrollbar', async () => {
    apiMock.fetchDirectories.mockResolvedValue(
      layer('', Array.from({ length: 40 }, (_, i) => ({ name: `vol${i}`, path: `X${i}:\\` }))),
    );
    await openBrowser();

    const entries = screen.getByTestId('directory-entries');
    expect(entries.className).toContain('overflow-y-auto');
    expect(entries.className).toContain('dir-scroll');
    expect(entries.className).toContain('h-64');
  });

  it('up from a Windows drive root returns to the drive-less roots level', async () => {
    // The backend reports parent=null for a drive root (D:\'s parent is itself).
    apiMock.fetchDirectories
      .mockResolvedValueOnce(layer('', [{ name: 'D:\\', path: 'D:\\' }]))
      .mockResolvedValueOnce(layer('D:\\', [{ name: 'pan', path: 'D:\\pan' }], null))
      .mockResolvedValueOnce(layer('', [{ name: 'D:\\', path: 'D:\\' }]));

    await openBrowser();
    await waitFor(() => screen.getByRole('button', { name: 'D:\\' }));

    // Roots level: 上一级 disabled, no breadcrumb.
    expect((screen.getByRole('button', { name: '上一级' }) as HTMLButtonElement).hasAttribute('disabled')).toBe(true);
    expect(screen.queryByTestId('directory-breadcrumb')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'D:\\' }));
    await waitFor(() => screen.getByRole('button', { name: 'pan' }));

    // Drive root: 上一级 enabled again and the breadcrumb offers the way back.
    const up = screen.getByRole('button', { name: '上一级' }) as HTMLButtonElement;
    expect(up.hasAttribute('disabled')).toBe(false);
    expect(screen.getByTestId('directory-breadcrumb').textContent).toContain('盘符列表');

    // Back up: parent=null at the drive root, so the browser must land on the
    // roots level (drive list) — the level where no drive is selected yet.
    // (Already-visited levels are served from the component cache, so the
    // observable outcome is the restored DOM, not a new fetch.)
    fireEvent.click(up);
    await waitFor(() => expect(screen.queryByTestId('directory-breadcrumb')).toBeNull());
    expect(screen.getByRole('button', { name: 'D:\\' })).toBeTruthy();
  });

  it('up from a normal subdirectory goes to its parent level', async () => {
    apiMock.fetchDirectories
      .mockResolvedValueOnce(layer('', [{ name: 'D:\\', path: 'D:\\' }]))
      .mockResolvedValueOnce(layer('D:\\', [{ name: 'pan', path: 'D:\\pan' }], null))
      .mockResolvedValueOnce(layer('D:\\pan', [{ name: 'app', path: 'D:\\pan\\app' }], 'D:\\'));

    await openBrowser();
    await waitFor(() => screen.getByRole('button', { name: 'D:\\' }));
    fireEvent.click(screen.getByRole('button', { name: 'D:\\' }));
    await waitFor(() => screen.getByRole('button', { name: 'pan' }));
    fireEvent.click(screen.getByRole('button', { name: 'pan' }));
    await waitFor(() => screen.getByRole('button', { name: 'app' }));

    fireEvent.click(screen.getByRole('button', { name: '上一级' }));
    await waitFor(() => screen.getByRole('button', { name: 'pan' }));
    expect(screen.queryByRole('button', { name: 'app' })).toBeNull();
  });

  it('breadcrumb 盘符列表 restores the no-drive-selected roots level', async () => {
    apiMock.fetchDirectories
      .mockResolvedValueOnce(layer('', [{ name: 'D:\\', path: 'D:\\' }]))
      .mockResolvedValueOnce(layer('D:\\', [{ name: 'pan', path: 'D:\\pan' }], null));

    await openBrowser();
    await waitFor(() => screen.getByRole('button', { name: 'D:\\' }));
    fireEvent.click(screen.getByRole('button', { name: 'D:\\' }));
    await waitFor(() => screen.getByRole('button', { name: 'pan' }));

    fireEvent.click(screen.getByText('盘符列表'));
    await waitFor(() => expect(screen.queryByTestId('directory-breadcrumb')).toBeNull());
    expect(screen.getByRole('button', { name: 'D:\\' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'pan' })).toBeNull();
  });

  it('cancel from a drive level keeps the working directory unchanged', async () => {
    apiMock.fetchDirectories.mockResolvedValue(layer('', [{ name: 'D:\\', path: 'D:\\' }]));

    await openBrowser();
    const input = screen.getByPlaceholderText('/path/to/project') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'D:\\keep' } });
    await waitFor(() => screen.getByRole('button', { name: 'D:\\' }));
    fireEvent.click(screen.getByRole('button', { name: 'D:\\' }));
    await waitFor(() => expect(screen.getByTestId('directory-breadcrumb')).toBeTruthy());
    fireEvent.click(screen.getByText('取消'));

    expect((screen.getByPlaceholderText('/path/to/project') as HTMLInputElement).value).toBe('D:\\keep');
  });
});
