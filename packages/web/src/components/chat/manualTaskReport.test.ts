import { describe, expect, it } from 'vitest';
import type { Message, Session } from '@/types';
import { findManualReportTarget } from './manualTaskReport';

function assistant(messageId: string | undefined, extra: Partial<Message> = {}): Message {
  return { role: 'assistant', content: `answer ${messageId ?? 'none'}`, ...(messageId ? { messageId } : {}), ...extra };
}

function session(patch: Partial<Session> = {}): Session {
  return {
    id: 'ses_ta',
    name: 'TA',
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    ...patch,
  } as Session;
}

const withManager = (patch: Partial<Session> = {}) => session({
  managedBy: 'ses_mgr',
  workerStatus: 'idle',
  // The real list summary exposes terminal identity only — never a result body.
  lastResult: { status: 'done', taskId: 'task-2', taskSeq: 2, workerId: 'worker-1', terminalKey: 'terminal:task-2' },
  ...patch,
});

describe('findManualReportTarget', () => {
  it('binds the last assistant text block of the transcript', () => {
    const messages: Message[] = [
      { role: 'user', content: 'do task 1', messageId: 'pan:u1', source: 'agent', taskIdSource: 'assign' },
      assistant('pan:a1'),
      { role: 'user', content: 'do task 2', messageId: 'pan:u2', source: 'agent', taskIdSource: 'assign' },
      assistant('pan:a2'),
      { role: 'user', content: '@@@@by agent report', messageId: 'pan:r1' },
    ];
    expect(findManualReportTarget(messages, withManager())).toEqual({
      sessionId: 'ses_ta',
      taskId: 'task-2',
      messageId: 'pan:a2',
    });
  });

  it('binds only one block across multiple tasks (the latest)', () => {
    const messages: Message[] = [
      assistant('pan:a1'),
      assistant('pan:a2'),
      assistant('pan:a3'),
    ];
    const target = findManualReportTarget(messages, withManager());
    expect(target?.messageId).toBe('pan:a3');
    // Exactly one row can match: identity equality, never a role/content guess.
    expect(messages.filter((m) => m.messageId === target?.messageId)).toHaveLength(1);
  });

  it('ignores tool/thinking rows after the final text block', () => {
    const messages: Message[] = [
      assistant('pan:a1'),
      { role: 'tool', content: 'ls output', messageId: 'pan:t1' },
      { role: 'thinking', content: 'hmm', messageId: 'pan:th1' },
    ];
    expect(findManualReportTarget(messages, withManager())?.messageId).toBe('pan:a1');
  });

  it('ignores system terminal markers after the final text block', () => {
    const messages: Message[] = [
      assistant('pan:a1'),
      { role: 'system', content: '[DONE] Task completed', messageId: 'pan:d1' },
    ];
    expect(findManualReportTarget(messages, withManager())?.messageId).toBe('pan:a1');
  });

  it('returns null without a manager (no report target)', () => {
    expect(findManualReportTarget([assistant('pan:a1')], session({ workerStatus: 'idle' }))).toBeNull();
    expect(findManualReportTarget([assistant('pan:a1')], session({ managedBy: null }))).toBeNull();
  });

  it('returns null while a worker is running or queued', () => {
    const messages = [assistant('pan:a1')];
    expect(findManualReportTarget(messages, withManager({ workerStatus: 'running' }))).toBeNull();
    expect(findManualReportTarget(messages, withManager({ workerStatus: 'queued' }))).toBeNull();
  });

  it('returns null without a durable terminal result', () => {
    expect(findManualReportTarget([assistant('pan:a1')], withManager({ lastResult: null }))).toBeNull();
    expect(findManualReportTarget([assistant('pan:a1')], withManager({ lastResult: undefined }))).toBeNull();
  });

  it('returns null when the tail assistant row is still streaming', () => {
    const messages = [assistant('pan:a1'), assistant('pan:a2', { streaming: true })];
    expect(findManualReportTarget(messages, withManager())).toBeNull();
  });

  it('returns null when the tail assistant row has no stable identity', () => {
    const messages = [assistant('pan:a1'), assistant(undefined)];
    expect(findManualReportTarget(messages, withManager())).toBeNull();
  });

  it('returns null without any assistant text block', () => {
    const messages: Message[] = [{ role: 'user', content: 'hello', messageId: 'pan:u1' }];
    expect(findManualReportTarget(messages, withManager())).toBeNull();
    expect(findManualReportTarget([], withManager())).toBeNull();
  });

  it('binds a null taskId for a plain turn', () => {
    const messages = [assistant('pan:a1')];
    expect(findManualReportTarget(messages, withManager({
      lastResult: { status: 'done', taskId: null },
    }))).toEqual({ sessionId: 'ses_ta', taskId: null, messageId: 'pan:a1' });
  });

  it('binds without a result body in the terminal projection', () => {
    // The list summary deliberately omits the result text; the binding must not
    // depend on it (the report body comes from the server's durable terminal).
    const messages = [assistant('pan:a1')];
    const target = findManualReportTarget(messages, withManager({
      lastResult: { status: 'done', taskId: 'task-2' },
    }));
    expect(target).toEqual({ sessionId: 'ses_ta', taskId: 'task-2', messageId: 'pan:a1' });
  });

  it('treats a non-string lastResult taskId as null', () => {
    const messages = [assistant('pan:a1')];
    expect(findManualReportTarget(messages, withManager({
      lastResult: { status: 'done', result: 'plain', taskId: 42 },
    }))?.taskId).toBeNull();
  });

  it('tolerates a missing session object', () => {
    expect(findManualReportTarget([assistant('pan:a1')], undefined)).toBeNull();
  });
});
