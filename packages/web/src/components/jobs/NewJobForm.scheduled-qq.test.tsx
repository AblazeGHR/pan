// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { NewJobForm } from './NewJobForm';
import { fetchQqChannels, fetchQqContacts } from '@/services/api';
import type { Job } from '@/types/jobs';

vi.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: { sessions: never[] }) => unknown) =>
    selector({ sessions: [] }),
}));

vi.mock('@/services/api', () => ({
  fetchQqChannels: vi.fn(),
  fetchQqContacts: vi.fn(),
}));

const channels = [
  { name: 'bot-one', bot_uin: '111111', connected: true },
  { name: 'bot-two', bot_uin: '222222', connected: true },
];

describe('NewJobForm scheduled QQ message template', () => {
  beforeEach(() => {
    vi.mocked(fetchQqChannels).mockResolvedValue(channels);
    vi.mocked(fetchQqContacts).mockImplementation(async (botUin) => botUin === '222222'
      ? [
        { peerName: 'Alice', peerUin: '123456', chatType: 1 },
        { peerName: 'Study group', peerUin: '654321', chatType: 2 },
        { peerName: 'Unknown', peerUin: '0', chatType: 0 },
      ]
      : [{ peerName: 'Alice', peerUin: '123456', chatType: 1 }]);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('loads each bot contact list and creates a targetless scheduled QQ Job', async () => {
    const onCreate = vi.fn();
    render(<NewJobForm onCreate={onCreate} />);

    fireEvent.click(screen.getByRole('button', { name: 'Scheduled QQ message' }));
    const aliceRows = await screen.findAllByRole('button', { name: /Alice/ });
    expect(screen.queryByRole('button', { name: /Unknown/ })).toBeNull();
    expect(fetchQqChannels).toHaveBeenCalledTimes(1);
    expect(fetchQqContacts).toHaveBeenNthCalledWith(1, '111111');
    expect(fetchQqContacts).toHaveBeenNthCalledWith(2, '222222');
    fireEvent.click(aliceRows[1]!);
    fireEvent.change(screen.getByLabelText('QQ message text'), {
      target: { value: 'Hello Alice' },
    });
    fireEvent.change(screen.getByLabelText('Max runs'), { target: { value: '4' } });

    expect(screen.getByLabelText('Missed fire')).toBeTruthy();
    expect(screen.getByLabelText('Misfire grace seconds')).toBeTruthy();
    expect(screen.getByRole('switch', { name: 'Job paused' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Create job' }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'scheduled-task',
      target: { sessionId: null },
      action: { api: 'send_qq', args: {
        targetType: 'user', targetId: '123456', botUin: '222222',
      } },
      text: 'Hello Alice',
      maxRuns: 4,
      schedule: [expect.objectContaining({ kind: 'cron', misfirePolicy: 'fire_now' })],
    }));
  });

  it('edits an existing QQ Job with a picker-selected bot and shared schedule controls', async () => {
    const onSave = vi.fn();
    const job: Job = {
      jobId: 'job_qq',
      taskId: 'sch_qq',
      kind: 'scheduled-task',
      status: 'scheduled',
      name: 'Alice reminder',
      description: 'Daily note',
      source: { type: 'system' },
      target: { sessionId: null },
      action: { api: 'send_qq', args: {
        targetType: 'user', targetId: '123456', botUin: '111111',
      } },
      text: 'Old message',
      paused: false,
      enabled: true,
      maxRuns: 8,
      misfirePolicy: 'skip',
      schedule: [{
        id: 'entry_1', kind: 'interval', intervalSec: 3600,
        enabled: true, misfirePolicy: 'skip', graceSec: 30, nextFireAt: null,
      }],
      runCount: 0,
      createdAt: 1,
      updatedAt: 1,
    };
    render(<NewJobForm mode="edit" initialJob={job} onSave={onSave} />);

    await screen.findAllByRole('button', { name: /Alice/ });
    fireEvent.change(screen.getByLabelText('Search QQ contacts'), {
      target: { value: 'Study' },
    });
    fireEvent.click(await screen.findByRole('button', { name: /Study group/ }));
    fireEvent.change(screen.getByLabelText('QQ message text'), {
      target: { value: 'New message' },
    });
    fireEvent.change(screen.getByLabelText('Interval seconds'), {
      target: { value: '7200' },
    });
    fireEvent.change(screen.getByLabelText('Max runs'), { target: { value: '9' } });
    fireEvent.click(screen.getByRole('switch', { name: 'Job paused' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      action: { api: 'send_qq', args: {
        targetType: 'group', targetId: '654321', botUin: '222222',
      } },
      target: { sessionId: null },
      text: 'New message',
      maxRuns: 9,
      paused: true,
      misfirePolicy: 'skip',
      schedule: [expect.objectContaining({
        kind: 'interval', intervalSec: 7200, misfirePolicy: 'skip', graceSec: 30,
      })],
    }));
  });

  it('keeps the legacy single default channel bot identity omitted', async () => {
    vi.mocked(fetchQqChannels).mockResolvedValue([
      { name: 'default-channel', bot_uin: '', connected: true },
    ]);
    vi.mocked(fetchQqContacts).mockResolvedValue([
      { peerName: 'Legacy friend', peerUin: '345678', chatType: 1 },
    ]);
    const onCreate = vi.fn();
    render(<NewJobForm onCreate={onCreate} />);

    fireEvent.click(screen.getByRole('button', { name: 'Scheduled QQ message' }));
    fireEvent.click(await screen.findByRole('button', { name: /Legacy friend/ }));
    fireEvent.change(screen.getByLabelText('QQ message text'), {
      target: { value: 'Legacy default send' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create job' }));

    expect(fetchQqContacts).toHaveBeenCalledWith(undefined);
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      target: { sessionId: null },
      action: { api: 'send_qq', args: {
        targetType: 'user', targetId: '345678',
      } },
      text: 'Legacy default send',
    }));
  });
});
