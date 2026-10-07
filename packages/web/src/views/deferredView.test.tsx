// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { deferredView } from './deferredView';

afterEach(cleanup);

it('leaves an invisible route unloaded and reuses the module after leaving during its download', async () => {
  let resolve!: (module: { default: () => React.ReactNode }) => void;
  const load = vi.fn(() => new Promise<{ default: () => React.ReactNode }>(done => { resolve = done; }));
  const Chat = deferredView(load);
  render(<MemoryRouter initialEntries={['/jobs']}>
    <Link to="/">Chat</Link><Link to="/jobs">Jobs</Link>
    <Routes><Route path="/" element={<Chat />} /><Route path="/jobs" element={<p>Visible jobs</p>} /></Routes>
  </MemoryRouter>);
  expect(screen.getByText('Visible jobs')).toBeTruthy();
  expect(load).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('link', { name: 'Chat' }));
  expect(load).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Loading...')).toBeTruthy();
  fireEvent.click(screen.getByRole('link', { name: 'Jobs' }));
  await act(async () => { resolve({ default: () => <p>Ready chat</p> }); });
  expect(screen.queryByText('Ready chat')).toBeNull();
  expect(screen.getByText('Visible jobs')).toBeTruthy();
  fireEvent.click(screen.getByRole('link', { name: 'Chat' }));
  expect(screen.getByText('Ready chat')).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(1);
});
