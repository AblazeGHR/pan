import { describe, expect, it } from 'vitest';
import type { Message, Session, TerminalIdentity } from '@/types';
import { findManualReportTargets } from './manualTaskReport';

const EPOCH = 'epoch-1';

function assign(taskId: string, messageId: string, content = `do ${taskId}`): Message {
  return {
    role: 'user',
    content,
    messageId,
    source: 'agent',
    taskIdSource: 'assign',
    taskId,
  };
}

function followUp(taskId: string, messageId: string): Message {
  return {
    role: 'user',
    content: `more for ${taskId}`,
    messageId,
    source: 'agent',
    taskIdSource: 'active',
    taskId,
  };
}

function browser(messageId: string, content = 'typed by the user'): Message {
  return { role: 'user', content, messageId };
}

function report(messageId: string): Message {
  return { role: 'user', content: '@@@@by agent : ses_x | name', messageId, source: 'report' };
}

function assistant(messageId: string | undefined, content = 'answer', extra: Partial<Message> = {}): Message {
  return { role: 'assistant', content, ...(messageId ? { messageId } : {}), ...extra };
}

function terminal(
  patch: Partial<TerminalIdentity> & { taskId: string; taskSeq: number },
): TerminalIdentity {
  return {
    resultCursor: patch.taskSeq,
    terminalKey: `terminal:${patch.taskId}:${patch.taskSeq}`,
    status: 'done',
    historyEpoch: EPOCH,
    historyRevision: 1,
    ...patch,
  };
}

function session(terminals: TerminalIdentity[], patch: Partial<Session> = {}): Session {
  return {
    id: 'ses_ta',
    name: 'TA',
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    managedBy: 'ses_mgr',
    historyEpoch: EPOCH,
    terminalResults: terminals,
    ...patch,
  } as Session;
}

describe('findManualReportTargets', () => {
  it('binds the tail text block of every completed task, not only the newest', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'answer 1'),
      assign('task-2', 'pan:u2'),
      assistant('pan:a2', 'answer 2'),
      assign('task-3', 'pan:u3'),
      assistant('pan:a3', 'answer 3'),
    ];
    const targets = findManualReportTargets(messages, session([
      terminal({ taskId: 'task-1', taskSeq: 1 }),
      terminal({ taskId: 'task-2', taskSeq: 2 }),
      terminal({ taskId: 'task-3', taskSeq: 3 }),
    ]));
    expect([...targets.keys()].sort()).toEqual(['pan:a1', 'pan:a2', 'pan:a3']);
    expect(targets.get('pan:a1')).toEqual({
      sessionId: 'ses_ta', taskId: 'task-1', taskSeq: 1, messageId: 'pan:a1',
      resultCursor: 1, terminalKey: 'terminal:task-1:1',
    });
    expect(targets.get('pan:a3')?.taskSeq).toBe(3);
  });

  it('keeps an older task bound while a newer task is still streaming', () => {
    const messages: Message[] = [
      assign('task-old', 'pan:u1'),
      assistant('pan:a1', 'old answer'),
      assign('task-new', 'pan:u2'),
      assistant('pan:a2', 'partial', { streaming: true }),
    ];
    const targets = findManualReportTargets(messages, session([
      terminal({ taskId: 'task-old', taskSeq: 1 }),
    ]));
    expect([...targets.keys()]).toEqual(['pan:a1']);
    expect(targets.get('pan:a1')?.taskId).toBe('task-old');
  });

  it('ignores tasks with no durable terminal in the index', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1'),
      assign('task-2', 'pan:u2'),
      assistant('pan:a2'),
    ];
    const targets = findManualReportTargets(messages, session([
      terminal({ taskId: 'task-2', taskSeq: 2 }),
    ]));
    expect([...targets.keys()]).toEqual(['pan:a2']);
  });

  // Red before the fix: the newest terminal of a repeated taskId was bound to
  // every segment of that task, so an older block could report a later run.
  it('refuses to bind when one taskId was dispatched twice', () => {
    const messages: Message[] = [
      assign('task-r', 'pan:u1'),
      assistant('pan:a1', 'first run'),
      assign('task-r', 'pan:u2'),
      assistant('pan:a2', 'second run'),
    ];
    const targets = findManualReportTargets(messages, session([
      terminal({ taskId: 'task-r', taskSeq: 1 }),
      terminal({ taskId: 'task-r', taskSeq: 2 }),
    ]));
    expect([...targets.keys()]).toEqual([]);
  });

  // Red before the fix: a window holding only the older segment was bound to the
  // newer run's terminal, reporting a result the block never produced.
  it('refuses to bind a segment when the window holds only one of several runs', () => {
    const messages: Message[] = [
      assign('task-w', 'pan:u1'),
      assistant('pan:a1', 'old run answer'),
    ];
    const targets = findManualReportTargets(messages, session([
      terminal({ taskId: 'task-w', taskSeq: 1 }),
      terminal({ taskId: 'task-w', taskSeq: 2 }),
    ]));
    expect([...targets.keys()]).toEqual([]);
  });

  it('refuses to bind when the run block boundary cannot be proven', () => {
    const messages: Message[] = [assign('task-1', 'pan:u1'), assistant('pan:a1')];
    // Legacy row: no history epoch/revision, so the tail relation is unprovable.
    expect(findManualReportTargets(messages, session([
      { taskId: 'task-1', taskSeq: 1, resultCursor: 1, terminalKey: 't', status: 'done' },
    ])).size).toBe(0);
    // Rewritten history epoch: the run's boundary no longer applies.
    expect(findManualReportTargets(messages, session([
      terminal({ taskId: 'task-1', taskSeq: 1, historyEpoch: 'older-epoch' }),
    ])).size).toBe(0);
    expect(findManualReportTargets(messages, session([
      terminal({ taskId: 'task-1', taskSeq: 1, historyRevision: 0 }),
    ])).size).toBe(0);
    expect(findManualReportTargets(messages, session([
      terminal({ taskId: 'task-1', taskSeq: 1, historyRevision: undefined }),
    ])).size).toBe(0);
  });

  it('does not bind a segment whose tail block is still streaming', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'partial', { streaming: true }),
    ];
    expect(findManualReportTargets(messages, session([terminal({ taskId: 'task-1', taskSeq: 1 })])).size).toBe(0);
  });

  it('does not bind a tail block without a stable identity', () => {
    const messages: Message[] = [assign('task-1', 'pan:u1'), assistant(undefined)];
    expect(findManualReportTargets(messages, session([terminal({ taskId: 'task-1', taskSeq: 1 })])).size).toBe(0);
  });

  it('ends a task segment at a non-follow-up user row', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'task 1 answer'),
      browser('pan:u2'),
      assistant('pan:a2', 'answer to a plain turn'),
    ];
    const targets = findManualReportTargets(messages, session([terminal({ taskId: 'task-1', taskSeq: 1 })]));
    expect([...targets.keys()]).toEqual(['pan:a1']);
  });

  it('ends a task segment at an injected report row', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'task 1 answer'),
      report('pan:r1'),
      assistant('pan:a2', 'answer to the report batch'),
    ];
    const targets = findManualReportTargets(messages, session([terminal({ taskId: 'task-1', taskSeq: 1 })]));
    expect([...targets.keys()]).toEqual(['pan:a1']);
  });

  it('extends the segment across an inherited same-task follow-up', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'first answer'),
      followUp('task-1', 'pan:u2'),
      assistant('pan:a2', 'follow-up answer'),
    ];
    const targets = findManualReportTargets(messages, session([
      terminal({ taskId: 'task-1', taskSeq: 2 }),
    ]));
    expect([...targets.keys()]).toEqual(['pan:a2']);
    expect(targets.get('pan:a2')?.taskSeq).toBe(2);
  });

  it('ignores rows before the first dispatch', () => {
    const messages: Message[] = [
      browser('pan:u0'),
      assistant('pan:a0', 'answer before any task'),
      assign('task-1', 'pan:u1'),
      assistant('pan:a1'),
    ];
    const targets = findManualReportTargets(messages, session([terminal({ taskId: 'task-1', taskSeq: 1 })]));
    expect([...targets.keys()]).toEqual(['pan:a1']);
  });

  it('binds only the final text block, ignoring tool/thinking/system rows after it', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'earlier block'),
      assistant('pan:a2', 'final block'),
      { role: 'tool', content: 'ls', messageId: 'pan:t1' },
      { role: 'thinking', content: 'hmm', messageId: 'pan:th1' },
      { role: 'system', content: '[DONE] Task completed', messageId: 'pan:d1' },
    ];
    const targets = findManualReportTargets(messages, session([terminal({ taskId: 'task-1', taskSeq: 1 })]));
    expect([...targets.keys()]).toEqual(['pan:a2']);
  });

  it('returns nothing without a manager or without terminals', () => {
    const messages: Message[] = [assign('task-1', 'pan:u1'), assistant('pan:a1')];
    expect(findManualReportTargets(messages, session([terminal({ taskId: 'task-1', taskSeq: 1 })], { managedBy: null })).size).toBe(0);
    expect(findManualReportTargets(messages, session([], { managedBy: 'ses_mgr' })).size).toBe(0);
    expect(findManualReportTargets(messages, undefined).size).toBe(0);
    expect(findManualReportTargets([], session([terminal({ taskId: 'task-1', taskSeq: 1 })])).size).toBe(0);
  });
});
