// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { NewJobForm } from './NewJobForm';

afterEach(cleanup);
describe('compact selected Session broadcast contract', () => {
  it.each(['once', 'interval', 'weekly'])('preserves the %s schedule and server identity contract', (kind) => {
    const create = vi.fn();
    const { rerender } = render(<NewJobForm broadcastTargets={['a', 'b']} onCreate={create} />);
    // A caller rerender cannot alter the click-time targets held by the form.
    rerender(<NewJobForm broadcastTargets={['c']} onCreate={create} />);
    fireEvent.change(screen.getByLabelText('Message text'), { target: { value: ' broadcast ' } });
    fireEvent.change(screen.getByLabelText('Message schedule type'), { target: { value: kind } });
    let schedule;
    if (kind === 'once') {
      fireEvent.change(screen.getByLabelText('Once schedule mode'), { target: { value: 'at' } });
      fireEvent.change(screen.getByLabelText('Run at'), { target: { value: '2030-01-02T12:34' } });
      schedule = { type: 'once', at: new Date('2030-01-02T12:34').toISOString() };
    } else if (kind === 'interval') {
      fireEvent.change(screen.getByLabelText('Message interval seconds'), { target: { value: '120' } });
      schedule = { type: 'interval', intervalSeconds: 120 };
    } else {
      fireEvent.change(screen.getByLabelText('Weekday'), { target: { value: '3' } });
      fireEvent.change(screen.getByLabelText('Weekly time'), { target: { value: '14:15' } });
      fireEvent.change(screen.getByLabelText('Message timezone'), { target: { value: 'Asia/Tokyo' } });
      schedule = { type: 'weekly', weekday: 3, time: '14:15', timezone: 'Asia/Tokyo' };
    }
    fireEvent.click(screen.getByRole('button', { name: 'Create job' }));
    expect(create).toHaveBeenCalledTimes(1);
    expect(create.mock.calls[0]?.[0]).toMatchObject({ kind: 'session-broadcast', target: { sessionId: 'a', sessionIds: ['a', 'b'] }, text: 'broadcast', schedule });
    expect(create.mock.calls[0]?.[0]).not.toHaveProperty('source');
    expect(screen.queryByLabelText('Createable Job kind')).toBeNull();
  });
});
