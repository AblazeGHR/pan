// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { NewJobForm } from './NewJobForm';

vi.mock('@/stores/sessionStore', () => ({ useSessionStore: (selector: (value: { sessions: never[] }) => unknown) => selector({ sessions: [] }) }));
afterEach(cleanup);

it('defaults shell Jobs to hidden windows and allows an explicit visible window', () => {
  const onCreate = vi.fn();
  render(<NewJobForm onCreate={onCreate} />);
  fireEvent.change(screen.getByLabelText('Execution mode'), { target: { value: 'shell' } });
  const hide = screen.getByRole('checkbox', { name: 'Hide command window (Windows)' }) as HTMLInputElement;
  expect(hide.checked).toBe(true);
  fireEvent.change(screen.getByLabelText('Shell command'), { target: { value: 'echo owned' } });
  fireEvent.change(screen.getByLabelText('Working directory'), { target: { value: 'D:/owned' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create job' }));
  expect(onCreate).toHaveBeenLastCalledWith(expect.objectContaining({ action: { api: 'shell', args: { command: 'echo owned', cwd: 'D:/owned', hideWindow: true } } }));
  expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ name: undefined }));
  fireEvent.click(hide);
  fireEvent.click(screen.getByRole('button', { name: 'Create job' }));
  expect(onCreate).toHaveBeenLastCalledWith(expect.objectContaining({ action: { api: 'shell', args: { command: 'echo owned', cwd: 'D:/owned', hideWindow: false } } }));
});
