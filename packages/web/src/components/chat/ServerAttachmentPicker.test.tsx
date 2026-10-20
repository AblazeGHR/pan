// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ServerAttachmentPicker } from './ServerAttachmentPicker';
import { fetchDirectories, pickNativeServerFiles } from '@/services/api';

vi.mock('@/services/api', () => ({
  pickNativeServerFiles: vi.fn(),
  fetchDirectories: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(fetchDirectories).mockResolvedValue({ current: 'C:\\files', parent: 'C:\\', entries: [
    { name: 'manual.txt', path: 'C:\\files\\manual.txt', isDirectory: false },
  ] });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const nativeButton = () => screen.getByRole('button', { name: '使用原生文件选择器' });
function setup() {
  const onChange = vi.fn(), onSelect = vi.fn();
  const props = { sessionId: 's1', value: 'C:\\files\\', onChange, onSelect };
  return { ...render(<ServerAttachmentPicker {...props} />), props, onSelect, onChange };
}

it('fills multiple paths without registration; confirms through existing selection callback', async () => {
  vi.mocked(pickNativeServerFiles).mockResolvedValue({ status: 'selected', paths: ['C:\\one.txt', 'C:\\two.txt'] });
  const { onSelect } = setup();
  fireEvent.click(nativeButton());
  await screen.findByText('C:\\one.txt');
  expect(onSelect).not.toHaveBeenCalled();
  expect(pickNativeServerFiles).toHaveBeenCalledWith('s1', 'C:\\files');
  fireEvent.click(screen.getByText('确认添加所选附件'));
  expect(onSelect.mock.calls).toEqual([['C:\\one.txt'], ['C:\\two.txt']]);
});

it.each(['cancelled', 'failed', 'timeout', 'busy', 'unavailable'] as const)('reports %s while fallback remains usable', async (status) => {
  vi.mocked(pickNativeServerFiles).mockResolvedValue({ status, paths: [] });
  const { onSelect } = setup();
  fireEvent.click(nativeButton());
  await act(async () => {});
  expect(screen.getByRole('status').textContent).toBeTruthy();
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByText('manual.txt'));
  expect(onSelect).toHaveBeenCalledWith('C:\\files\\manual.txt');
});

function deferred() {
  let resolve!: (result: Awaited<ReturnType<typeof pickNativeServerFiles>>) => void;
  vi.mocked(pickNativeServerFiles).mockReturnValue(new Promise((done) => { resolve = done; }));
  return async () => { await act(async () => resolve({ status: 'selected', paths: ['C:\\late.txt'] })); };
}

it('keeps fallback enabled during pending dialog, prevents repeat clicks and ignores results after manual selection', async () => {
  const finish = deferred();
  const { onSelect } = setup();
  fireEvent.click(nativeButton());
  fireEvent.click(nativeButton());
  expect(pickNativeServerFiles).toHaveBeenCalledTimes(1);
  fireEvent.click(await screen.findByText('manual.txt'));
  expect(onSelect).toHaveBeenCalledTimes(1);
  await finish();
  expect(screen.queryByText('C:\\late.txt')).toBeNull();
  expect(onSelect).toHaveBeenCalledTimes(1);
});

it('ignores results after manual directory change', async () => {
  const finish = deferred();
  const { onChange } = setup();
  fireEvent.click(nativeButton());
  fireEvent.change(screen.getByLabelText('Directory path'), { target: { value: 'C:\\other\\' } });
  expect(onChange).toHaveBeenCalledWith('C:\\other\\');
  await finish();
  expect(screen.queryByText('C:\\late.txt')).toBeNull();
});

it('does not contaminate a new window after close', async () => {
  const finish = deferred();
  const first = setup();
  fireEvent.click(nativeButton());
  first.unmount();
  const second = setup();
  await finish();
  expect(screen.queryByText('C:\\late.txt')).toBeNull();
  expect(second.onSelect).not.toHaveBeenCalled();
});

it('ignores a result after switching Session', async () => {
  const finish = deferred();
  const { rerender, props, onSelect } = setup();
  fireEvent.click(nativeButton());
  rerender(<ServerAttachmentPicker {...props} sessionId="s2" />);
  await finish();
  expect(screen.queryByText('C:\\late.txt')).toBeNull();
  expect(onSelect).not.toHaveBeenCalled();
});

it('cancels staged selection without registration', async () => {
  vi.mocked(pickNativeServerFiles).mockResolvedValue({ status: 'selected', paths: ['C:\\one.txt'] });
  const { onSelect } = setup();
  fireEvent.click(nativeButton());
  await screen.findByText('C:\\one.txt');
  fireEvent.click(screen.getByText('取消选择'));
  expect(onSelect).not.toHaveBeenCalled();
  expect(screen.queryByText('确认添加所选附件')).toBeNull();
});
