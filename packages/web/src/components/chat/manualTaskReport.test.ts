import { describe, expect, it } from 'vitest';
import type { Message, Session, TerminalIdentity } from '@/types';
import {
  findManualReportTargets,
  type HistoryBoundaryProof,
} from './manualTaskReport';

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

/**
 * Mirror the production ordering: the terminal's history revision is recorded
 * AFTER the run's final row was appended, so the run's tail row sits at
 * (recorded revision - 1) and the boundary recovers exactly that offset.
 */
function proofFor(messages: Message[]): HistoryBoundaryProof {
  const offsets = new Map<string, number>();
  messages.forEach((message, index) => {
    if (message.messageId) offsets.set(message.messageId, index);
  });
  return {
    epoch: EPOCH,
    revision: messages.length,
    total: messages.length,
    offsetOf: (message) => offsets.get(message.messageId ?? ''),
  };
}

function terminal(
  taskId: string,
  taskSeq: number,
  tailRow: Message,
  messages: Message[],
): TerminalIdentity {
  const index = messages.findIndex((message) => message.messageId === tailRow.messageId);
  return {
    taskId,
    taskSeq,
    resultCursor: taskSeq,
    terminalKey: `terminal:${taskId}:${taskSeq}`,
    status: 'done',
    historyEpoch: EPOCH,
    historyRevision: index + 1,
  };
}

describe('findManualReportTargets', () => {
  it('binds the tail text block of every completed run at its own boundary', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'answer 1'),
      assign('task-2', 'pan:u2'),
      assistant('pan:a2', 'answer 2'),
      assign('task-3', 'pan:u3'),
      assistant('pan:a3', 'answer 3'),
    ];
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-1', 1, messages[1]!, messages),
      terminal('task-2', 2, messages[3]!, messages),
      terminal('task-3', 3, messages[5]!, messages),
    ]), proof);
    expect([...targets.keys()].sort()).toEqual(['pan:a1', 'pan:a2', 'pan:a3']);
    expect(targets.get('pan:a1')).toEqual({
      sessionId: 'ses_ta', taskId: 'task-1', taskSeq: 1, messageId: 'pan:a1',
      resultCursor: 1, terminalKey: 'terminal:task-1:1',
    });
    expect(targets.get('pan:a3')?.taskSeq).toBe(3);
  });

  it('keeps an older run bound while a newer task is still streaming', () => {
    const messages: Message[] = [
      assign('task-old', 'pan:u1'),
      assistant('pan:a1', 'old answer'),
      assign('task-new', 'pan:u2'),
      assistant('pan:a2', 'partial', { streaming: true }),
    ];
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-old', 1, messages[1]!, messages),
    ]), proof);
    expect([...targets.keys()]).toEqual(['pan:a1']);
    expect(targets.get('pan:a1')?.taskId).toBe('task-old');
  });

  it('ignores runs with no durable terminal in the index', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1'),
      assign('task-2', 'pan:u2'),
      assistant('pan:a2'),
    ];
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-2', 2, messages[3]!, messages),
    ]), proof);
    expect([...targets.keys()]).toEqual(['pan:a2']);
  });

  // Red before this fix: both segments of one taskId were bound to the newest
  // terminal, so the older block reported the newer run's result.
  it('binds each run of a repeated taskId to its own block and result', () => {
    const messages: Message[] = [
      assign('task-r', 'pan:u1'),
      assistant('pan:a1', 'first run'),
      assign('task-r', 'pan:u2'),
      assistant('pan:a2', 'second run'),
    ];
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-r', 1, messages[1]!, messages),
      terminal('task-r', 2, messages[3]!, messages),
    ]), proof);
    expect(targets.get('pan:a1')?.taskSeq).toBe(1);
    expect(targets.get('pan:a2')?.taskSeq).toBe(2);
  });

  // Red before this fix: with only the newest terminal indexed, the older
  // segment was bound to it and reported a result the block never produced.
  it('binds only the indexed run block when an older run is evicted', () => {
    const messages: Message[] = [
      assign('task-w', 'pan:u1'),
      assistant('pan:a1', 'old run answer'),
      assign('task-w', 'pan:u2'),
      assistant('pan:a2', 'newer run answer'),
    ];
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-w', 2, messages[3]!, messages),
    ]), proof);
    // a2 is provably the indexed run's own block; the evicted run's block (a1)
    // is never bound to it.
    expect([...targets.keys()]).toEqual(['pan:a2']);
    expect(targets.get('pan:a2')?.taskSeq).toBe(2);
  });

  it('binds nothing when two runs claim the same boundary', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'answer'),
    ];
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-1', 1, messages[1]!, messages),
      terminal('task-2', 2, messages[1]!, messages),
    ]), proof);
    expect([...targets.keys()]).toEqual([]);
  });

  it('binds nothing when the run boundary cannot be proven', () => {
    const messages: Message[] = [assign('task-1', 'pan:u1'), assistant('pan:a1')];
    const proof = proofFor(messages);
    // Legacy row: no history epoch/revision at all.
    expect(findManualReportTargets(messages, session([
      { taskId: 'task-1', taskSeq: 1, resultCursor: 1, terminalKey: 't', status: 'done' },
    ]), proof).size).toBe(0);
    // Rewritten history epoch.
    expect(findManualReportTargets(messages, session([
      { ...terminal('task-1', 1, messages[1]!, messages), historyEpoch: 'older-epoch' },
    ]), proof).size).toBe(0);
    // Revision beyond the loaded window: the boundary is not recoverable here.
    expect(findManualReportTargets(messages, session([
      { ...terminal('task-1', 1, messages[1]!, messages), historyRevision: messages.length + 5 },
    ]), proof).size).toBe(0);
    expect(findManualReportTargets(messages, session([
      { ...terminal('task-1', 1, messages[1]!, messages), historyRevision: 0 },
    ]), proof).size).toBe(0);
  });

  it('leaves runtime-only rows unbound until their canonical rows converge', () => {
    const messages: Message[] = [assign('task-1', 'pan:u1'), assistant('pan:a1')];
    const proof = proofFor(messages);
    // The proof cannot resolve the block's canonical offset.
    const noOffsets: HistoryBoundaryProof = { ...proof, offsetOf: () => undefined };
    expect(findManualReportTargets(messages, session([
      terminal('task-1', 1, messages[1]!, messages),
    ]), noOffsets).size).toBe(0);
  });

  it('does not bind a segment whose tail block is still streaming', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'partial', { streaming: true }),
    ];
    const proof = proofFor(messages);
    expect(findManualReportTargets(messages, session([
      terminal('task-1', 1, messages[1]!, messages),
    ]), proof).size).toBe(0);
  });

  it('does not bind a tail block without a stable identity', () => {
    const messages: Message[] = [assign('task-1', 'pan:u1'), assistant(undefined)];
    const proof = proofFor(messages);
    expect(findManualReportTargets(messages, session([
      terminal('task-1', 1, messages[1]!, messages),
    ]), proof).size).toBe(0);
  });

  it('ends a task segment at a non-follow-up user row', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'task 1 answer'),
      browser('pan:u2'),
      assistant('pan:a2', 'answer to a plain turn'),
    ];
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-1', 1, messages[1]!, messages),
    ]), proof);
    expect([...targets.keys()]).toEqual(['pan:a1']);
  });

  it('ends a task segment at an injected report row', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'task 1 answer'),
      report('pan:r1'),
      assistant('pan:a2', 'answer to the report batch'),
    ];
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-1', 1, messages[1]!, messages),
    ]), proof);
    expect([...targets.keys()]).toEqual(['pan:a1']);
  });

  it('extends the segment across an inherited same-task follow-up', () => {
    const messages: Message[] = [
      assign('task-1', 'pan:u1'),
      assistant('pan:a1', 'first answer'),
      followUp('task-1', 'pan:u2'),
      assistant('pan:a2', 'follow-up answer'),
    ];
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-1', 2, messages[3]!, messages),
    ]), proof);
    expect([...targets.keys()]).toEqual(['pan:a2']);
    expect(targets.get('pan:a2')?.taskSeq).toBe(2);
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
    const proof = proofFor(messages);
    const targets = findManualReportTargets(messages, session([
      terminal('task-1', 1, messages[2]!, messages),
    ]), proof);
    expect([...targets.keys()]).toEqual(['pan:a2']);
  });

  it('returns nothing without a manager, terminals, a window or rows', () => {
    const messages: Message[] = [assign('task-1', 'pan:u1'), assistant('pan:a1')];
    const proof = proofFor(messages);
    expect(findManualReportTargets(messages, session([terminal('task-1', 1, messages[1]!, messages)], { managedBy: null }), proof).size).toBe(0);
    expect(findManualReportTargets(messages, session([], { managedBy: 'ses_mgr' }), proof).size).toBe(0);
    expect(findManualReportTargets(messages, undefined, proof).size).toBe(0);
    expect(findManualReportTargets([], session([terminal('task-1', 1, messages[1]!, messages)]), proof).size).toBe(0);
    expect(findManualReportTargets(messages, session([terminal('task-1', 1, messages[1]!, messages)]), undefined).size).toBe(0);
  });
});
