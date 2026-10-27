// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { CanonicalSchedulerFields } from './CanonicalSchedulerFields';
import { CanonicalDefinitionFields } from './CanonicalDefinitionFields';
import type { JsonObject } from '@/types/canonicalJobs';
import { canonicalJobs } from '@/services/canonicalJobs';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const full: JsonObject = {
  rules: [
    {
      ruleId: 'fraction',
      type: 'interval',
      seconds: '0.000000123456789',
      anchor: '2027-11-01T00:00:12.123456789+08:00',
      advanceMode: 'afterPublication',
    },
    { ruleId: 'once', type: 'once', at: '2027-11-01T00:00:13.123456789+05:30' },
    {
      ruleId: 'weekly',
      type: 'weekly',
      weekday: 6,
      time: '01:30:12',
      timezone: 'America/New_York',
      dstPolicy: 'gap-skip-fold-second',
    },
    {
      ruleId: 'cron',
      type: 'cron',
      expression: '15 9 * * 1',
      timezone: '+08:00',
      dstPolicy: 'gap-shift-fold-first',
    },
  ],
  misfirePolicy: 'skip',
  graceSeconds: '0.00000125',
  pausePolicy: 'advance',
  driveLimit: { maxEvents: 100, initialProducedCount: 17 },
};
function Scheduler({ initial = full }: { initial?: JsonObject }) {
  const [value, set] = useState(initial);
  return (
    <>
      <CanonicalSchedulerFields value={value} onChange={set} />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}
const data = () => JSON.parse(screen.getByTestId('value').textContent!) as JsonObject;

it('edits one rule without converting or truncating other rule IDs, precision, offsets, timezone, policies or counts', () => {
  render(<Scheduler />);
  expect(data()).toEqual(full);
  fireEvent.change(
    within(screen.getByRole('group', { name: 'Time rule 4' })).getByLabelText('Cron expression'),
    { target: { value: '16 9 * * 1' } },
  );
  const expected = structuredClone(full);
  (expected.rules as JsonObject[])[3]!.expression = '16 9 * * 1';
  expect(data()).toEqual(expected);
  expect((screen.getByLabelText('Interval seconds') as HTMLInputElement).value).toBe(
    '0.000000123456789',
  );
  expect((screen.getByLabelText('Scheduled time (RFC3339)') as HTMLInputElement).value).toBe(
    '2027-11-01T00:00:13.123456789+05:30',
  );
});
it('adds and removes multiple rules and changes type with stable IDs, including the surviving row', () => {
  render(<Scheduler />);
  fireEvent.click(screen.getByRole('button', { name: 'Add time rule' }));
  const id = (data().rules as JsonObject[])[4]!.ruleId;
  fireEvent.change(
    within(screen.getByRole('group', { name: 'Time rule 5' })).getByLabelText('Time rule type'),
    { target: { value: 'weekly' } },
  );
  expect((data().rules as JsonObject[])[4]!.ruleId).toBe(id);
  fireEvent.click(screen.getByRole('button', { name: 'Remove time rule 2' }));
  expect((data().rules as JsonObject[]).map((r) => r.ruleId)).toEqual([
    'fraction',
    'weekly',
    'cron',
    id,
  ]);
  expect((screen.getByLabelText('Anchor (RFC3339)') as HTMLInputElement).value).toBe(
    (full.rules as JsonObject[])[0]!.anchor,
  );
});
it('edits decimal grace/interval strings, grid, misfire and driveLimit without floating point conversion', () => {
  render(<Scheduler />);
  fireEvent.change(screen.getByLabelText('Interval seconds'), {
    target: { value: '1.000000000000000001' },
  });
  fireEvent.change(screen.getByLabelText('Grace period (seconds)'), {
    target: { value: '0.000000000000000001' },
  });
  fireEvent.change(screen.getByLabelText('Interval advance mode'), { target: { value: 'grid' } });
  fireEvent.change(screen.getByLabelText('Missed trigger policy'), {
    target: { value: 'fire_now' },
  });
  fireEvent.change(screen.getByLabelText('Maximum events'), { target: { value: '101' } });
  expect(data()).toMatchObject({
    rules: [
      { seconds: '1.000000000000000001', advanceMode: 'grid' },
      ...(full.rules as JsonObject[]).slice(1),
    ],
    graceSeconds: '0.000000000000000001',
    driveLimit: { maxEvents: 101, initialProducedCount: 17 },
    misfirePolicy: 'fire_now',
  });
});
it('keeps extension fields and unknown existing policy tokens while changing a supported field', () => {
  render(<Scheduler initial={{ ...full, future: { keep: true }, pausePolicy: 'future-pause' }} />);
  fireEvent.change(screen.getByLabelText('Grace period (seconds)'), { target: { value: '4.25' } });
  expect(data()).toMatchObject({
    future: { keep: true },
    pausePolicy: 'future-pause',
    graceSeconds: '4.25',
  });
});

const ref = { id: 'action_message', revision: 7, digest: 'a'.repeat(64) };
const action = {
  id: ref.id,
  name: 'Send message',
  revision: 7,
  state: 'published',
  enabled: true,
  inputSchema: {
    type: 'object',
    properties: {
      text: { type: 'string' },
      count: { type: 'integer' },
      enabled: { type: 'boolean' },
    },
    required: ['text'],
  },
  defaultCompletion: 'durable-queue-accepted',
  completionContracts: [{ id: 'durable-queue-accepted' }],
};
function Job({
  initial = { steps: [], retention: { policyRef: 'kept' } },
}: {
  initial?: JsonObject;
}) {
  const [value, set] = useState<JsonObject>(initial);
  return (
    <>
      <CanonicalDefinitionFields kind="jobs" value={value} onChange={set} />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}
async function chooseAction() {
  await screen.findByRole('option', { name: 'Send message · Revision 7' });
  fireEvent.change(screen.getByLabelText('Action for step 1'), { target: { value: ref.id } });
}
function catalog() {
  vi.spyOn(canonicalJobs, 'actions').mockResolvedValue({
    items: [action],
    references: { [ref.id]: ref },
    nextCursor: null,
  });
}
it('selects server-issued Action revision/digest and completion and edits ordinary string/number/boolean parameters without definition JSON', async () => {
  catalog();
  render(<Job />);
  fireEvent.click(screen.getByRole('button', { name: 'Add step' }));
  await chooseAction();
  fireEvent.change(screen.getByLabelText('text value'), {
    target: { value: 'hello, no JSON quotes' },
  });
  fireEvent.change(screen.getByLabelText('Add parameter for step 1'), {
    target: { value: 'count' },
  });
  fireEvent.change(screen.getByLabelText('count value'), { target: { value: '42' } });
  fireEvent.change(screen.getByLabelText('Add parameter for step 1'), {
    target: { value: 'enabled' },
  });
  fireEvent.change(screen.getByLabelText('enabled value'), { target: { value: 'true' } });
  expect((data().steps as JsonObject[])[0]).toMatchObject({
    actionRef: ref,
    arguments: {
      text: { from: 'literal', value: 'hello, no JSON quotes' },
      count: { from: 'literal', value: 42 },
      enabled: { from: 'literal', value: true },
    },
    completion: { contract: 'durable-queue-accepted' },
  });
});
it('moves stable steps, preserves bindings, flags forward output references and keeps the surviving row on removal', async () => {
  catalog();
  const a = {
    stepId: 'one',
    actionRef: ref,
    arguments: { text: { from: 'literal', value: 'first' } },
    completion: { contract: 'durable-queue-accepted' },
  };
  const b = {
    stepId: 'two',
    actionRef: ref,
    arguments: { text: { from: 'step', stepId: 'one', path: 'messageId' } },
    completion: { contract: 'durable-queue-accepted' },
  };
  render(
    <Job
      initial={{ steps: [a, b], retention: { policyRef: 'kept' }, recovery: { automatic: false } }}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Move step 2 up' }));
  expect(screen.getByLabelText('Chain validation').textContent).toContain('preceding step');
  expect((data().steps as JsonObject[]).map((s) => s.stepId)).toEqual(['two', 'one']);
  fireEvent.click(screen.getByRole('button', { name: 'Move step 1 down' }));
  expect(data()).toMatchObject({ steps: [a, b], recovery: { automatic: false } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove step 1' }));
  expect((data().steps as JsonObject[])[0]).toEqual(b);
});
it('defines Job input fields and uses input or Event paths without flattening typed bindings', async () => {
  catalog();
  render(<Job />);
  fireEvent.change(screen.getByLabelText('New input name'), { target: { value: 'message' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add Job input' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add step' }));
  await chooseAction();
  fireEvent.change(screen.getByLabelText('text source'), { target: { value: 'input' } });
  fireEvent.change(screen.getByLabelText('text path'), { target: { value: 'message' } });
  expect(data()).toMatchObject({
    inputSchema: { type: 'object', properties: { message: { type: 'string' } } },
    steps: [{ arguments: { text: { from: 'input', path: 'message' } } }],
  });
  fireEvent.change(screen.getByLabelText('text source'), { target: { value: 'event' } });
  fireEvent.change(screen.getByLabelText('text path'), { target: { value: 'payload.text' } });
  expect((data().steps as JsonObject[])[0]).toMatchObject({
    arguments: { text: { from: 'event', path: 'payload.text' } },
  });
});
it('edits policies without dropping exact recovery values, notification recipient identity or existing retention metadata', () => {
  catalog();
  const initial: JsonObject = {
    steps: [],
    retention: { policyRef: 'kept', pinned: true },
    overlap: { mode: 'queue', maxActive: 1, maxPending: 4 },
    recovery: {
      automatic: false,
      maxAttempts: 3,
      backoffSeconds: ['0.000000000000000001', '15'],
      stableResetSeconds: '300.000000000000001',
    },
    notification: {
      on: ['failed', 'uncertain'],
      recipients: [{ recipientId: 'kept-recipient', channelId: 'channel', targetRef: 'target' }],
    },
    runLimit: { maxRuns: 17, counterDomain: 'run-admission' },
  };
  render(<Job initial={initial} />);
  fireEvent.change(screen.getByLabelText('Maximum pending Runs'), { target: { value: '5' } });
  fireEvent.change(screen.getByLabelText('targetRef'), { target: { value: 'new-target' } });
  fireEvent.change(screen.getByLabelText('Maximum Runs'), { target: { value: '18' } });
  const expected = structuredClone(initial);
  (expected.overlap as JsonObject).maxPending = 5;
  ((expected.notification as JsonObject).recipients as JsonObject[])[0]!.targetRef = 'new-target';
  (expected.runLimit as JsonObject).maxRuns = 18;
  expect(data()).toEqual(expected);
  fireEvent.click(screen.getByLabelText('Pin Job retention'));
  expect(data().retention).toEqual({ policyRef: 'kept', pinned: false });
});
it('retains a previously fixed Action revision and all bindings when a newer catalog revision arrives', async () => {
  const oldRef = { ...ref, revision: 6, digest: 'b'.repeat(64) };
  const initial: JsonObject = {
    steps: [
      {
        stepId: 'kept',
        actionRef: oldRef,
        arguments: { text: { from: 'literal', value: 'old action text' } },
        completion: { contract: 'durable-queue-accepted' },
      },
    ],
    retention: { policyRef: 'kept' },
  };
  catalog();
  render(<Job initial={initial} />);
  await screen.findByRole('option', { name: 'Send message · Revision 7' });
  expect(data()).toEqual(initial);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh Actions' }));
  await waitFor(() => expect(canonicalJobs.actions).toHaveBeenCalledTimes(2));
  expect(data()).toEqual(initial);
  fireEvent.change(screen.getByLabelText('Description'), {
    target: { value: 'only description changed' },
  });
  expect(data()).toEqual({ ...initial, description: 'only description changed' });
});
it('edits OR event types and nested AND/OR/NOT conditions with structured controls while keeping existing filters', async () => {
  function Trigger() {
    const [value, set] = useState<JsonObject>({
      eventTypes: ['scheduler.time.v2'],
      creatorSelector: { mode: 'ids', ids: ['kept'] },
      eventFilter: { 'payload.scope': 'owned' },
    });
    return (
      <>
        <CanonicalDefinitionFields kind="triggers" value={value} onChange={set} />
        <output data-testid="value">{JSON.stringify(value)}</output>
      </>
    );
  }
  render(<Trigger />);
  fireEvent.change(screen.getByLabelText('Event type'), { target: { value: 'workers.all_idle' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add Event' }));
  fireEvent.click(screen.getByLabelText('Check State after Event'));
  fireEvent.change(screen.getByLabelText('Compare value type'), { target: { value: 'string' } });
  fireEvent.change(screen.getByLabelText('Compare value'), {
    target: { value: 'plain text, no quotes' },
  });
  expect(data().condition).toEqual({
    field: 'workers.runningCount',
    op: 'eq',
    value: 'plain text, no quotes',
  });
  expect(screen.queryByLabelText('Compare value (JSON)')).toBeNull();
  fireEvent.change(screen.getByLabelText('Condition type'), { target: { value: 'or' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add condition' }));
  const types = screen.getAllByLabelText('Condition type');
  fireEvent.change(types[2]!, { target: { value: 'not' } });
  expect(data()).toMatchObject({
    eventTypes: ['scheduler.time.v2', 'workers.all_idle'],
    condition: {
      or: [
        { field: 'workers.runningCount', op: 'eq', value: 0 },
        { not: { field: 'workers.runningCount', op: 'eq', value: 0 } },
      ],
    },
    eventFilter: { 'payload.scope': 'owned' },
    creatorSelector: { mode: 'ids', ids: ['kept'] },
  });
  await waitFor(() => expect(screen.getAllByLabelText('State field')).toHaveLength(2));
});
