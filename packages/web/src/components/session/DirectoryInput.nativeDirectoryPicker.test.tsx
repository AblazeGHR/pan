// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { DirectoryInput } from './DirectoryInput';
import { usePlatformStore } from '@/stores/platformStore';

const apiMock = vi.hoisted(() => ({
  fetchDirectories: vi.fn(),
  pickNativeServerDirectory: vi.fn(),
}));
vi.mock('@/services/api', () => apiMock);

// JavaScript string literals: one backslash each, as the server spells paths.
const BROWSING = 'D:\\workspace\\';
const APP = 'D:\\workspace\\app';
const PICKED = 'D:\\workspace\\picked';
const TYPED = 'D:\\typed\\';
const CHILD = 'D:\\workspace\\child';

const listing = (current: string) => ({
  current,
  parent: null,
  entries: [{ name: 'child', path: `${current}\\child`, isDirectory: true }],
});

function Harness({ initial = '', nativePicker = true }: { initial?: string; nativePicker?: boolean }) {
  const [value, setValue] = useState(initial);
  return <DirectoryInput
    value={value}
    onChange={setValue}
    onSelect={setValue}
    selectDirectories
    nativeDirectoryPicker={nativePicker}
    inputTestId="workdir-input"
  />;
}

const button = () => screen.getByRole('button', { name: 'Browse server folders' });
const input = () => screen.getByTestId('workdir-input') as HTMLInputElement;

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.fetchDirectories.mockResolvedValue(listing('D:\\workspace'));
  usePlatformStore.setState({
    platform: 'windows', capabilities: { 'fileDialog.native': true }, loaded: true,
  });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

describe('New Session native folder picker', () => {
  it('writes the exact selected folder and keeps the server listing path validation', async () => {
    apiMock.pickNativeServerDirectory.mockResolvedValue({ status: 'selected', paths: [PICKED] });
    render(<Harness initial={BROWSING} />);

    fireEvent.click(button());

    await waitFor(() => expect(input().value).toBe(PICKED));
    // The dialog opens at the browsing directory the input already shows and
    // the raw path (no trailing separator) is what the workdir flow validates.
    expect(apiMock.pickNativeServerDirectory).toHaveBeenCalledWith('D:\\workspace');
    expect(apiMock.fetchDirectories).toHaveBeenCalledWith('D:\\workspace', false);
    expect(screen.getByTestId('native-picker-feedback').textContent)
      .toContain('Folder selected');
  });

  it('never offers the native button for inputs that did not ask for it', () => {
    render(<Harness nativePicker={false} />);
    expect(screen.queryByRole('button', { name: 'Browse server folders' })).toBeNull();
  });

  it('leaves the typed path untouched on cancel and keeps manual browsing usable', async () => {
    apiMock.pickNativeServerDirectory.mockResolvedValue({ status: 'cancelled', paths: [] });
    render(<Harness initial={APP} />);

    fireEvent.click(button());

    const feedback = await screen.findByTestId('native-picker-feedback');
    expect(feedback.textContent).toContain('cancelled');
    expect(input().value).toBe(APP);

    fireEvent.change(input(), { target: { value: TYPED } });
    expect(input().value).toBe(TYPED);
    expect(feedback.textContent).toContain('cancelled');
  });

  it.each([
    ['busy', 'already open'],
    ['unavailable', 'No interactive Windows desktop'],
    ['failed', 'failed'],
    ['timeout', 'timed out'],
  ] as const)('shows the %s status and keeps the manual directory field usable', async (status, copy) => {
    apiMock.pickNativeServerDirectory.mockResolvedValue({ status, paths: [] });
    render(<Harness initial={BROWSING} />);

    fireEvent.click(button());

    const feedback = await screen.findByTestId('native-picker-feedback');
    expect(feedback.textContent).toContain(copy);
    expect(input().disabled).toBe(false);
    fireEvent.change(input(), { target: { value: CHILD } });
    expect(input().value).toBe(CHILD);
  });

  it('reports an outdated backend without blocking manual browsing', async () => {
    apiMock.pickNativeServerDirectory.mockRejectedValue(new Error('HTTP 404: Not Found'));
    render(<Harness initial={BROWSING} />);

    fireEvent.click(button());

    expect((await screen.findByTestId('native-picker-feedback')).textContent)
      .toContain('server may need an update');
    fireEvent.change(input(), { target: { value: CHILD } });
    expect(input().value).toBe(CHILD);
  });

  it('disables the button with a visible reason when the platform has no native dialog', () => {
    usePlatformStore.setState({
      platform: 'linux', capabilities: { 'fileDialog.native': false }, loaded: true,
    });
    render(<Harness initial={BROWSING} />);

    expect((button() as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId('native-picker-unsupported').textContent)
      .toContain('interactive Windows desktop');
    expect(input().disabled).toBe(false);
    expect(apiMock.pickNativeServerDirectory).not.toHaveBeenCalled();
  });

  it('prevents a second request while the dialog is open', async () => {
    let resolve!: (value: { status: string; paths: string[] }) => void;
    apiMock.pickNativeServerDirectory.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness initial={BROWSING} />);

    fireEvent.click(button());
    fireEvent.click(button());
    expect(apiMock.pickNativeServerDirectory).toHaveBeenCalledTimes(1);
    expect((button() as HTMLButtonElement).disabled).toBe(true);

    await act(async () => resolve({ status: 'cancelled', paths: [] }));
    expect((button() as HTMLButtonElement).disabled).toBe(false);
  });

  it('drops a late result after a manual edit instead of overwriting the typed path', async () => {
    let resolve!: (value: { status: string; paths: string[] }) => void;
    apiMock.pickNativeServerDirectory.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness initial={BROWSING} />);

    fireEvent.click(button());
    fireEvent.change(input(), { target: { value: TYPED } });
    await act(async () => resolve({ status: 'selected', paths: ['D:\\late'] }));

    expect(input().value).toBe(TYPED);
    expect(screen.queryByTestId('native-picker-feedback')).toBeNull();
  });

  it('drops a late result after the dialog owner closed and reopened', async () => {
    let resolve!: (value: { status: string; paths: string[] }) => void;
    apiMock.pickNativeServerDirectory.mockReturnValue(new Promise((done) => { resolve = done; }));
    const first = render(<Harness initial={BROWSING} />);
    fireEvent.click(button());
    first.unmount();

    render(<Harness initial={BROWSING} />);
    await act(async () => resolve({ status: 'selected', paths: ['D:\\late'] }));

    expect(input().value).toBe(BROWSING);
    expect(screen.queryByTestId('native-picker-feedback')).toBeNull();
  });
});
