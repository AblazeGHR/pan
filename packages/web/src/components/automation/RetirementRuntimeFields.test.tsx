// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { CanonicalDefinitionFields } from './CanonicalDefinitionFields';
import { canonicalJobs } from '@/services/canonicalJobs';
import type { JsonObject } from '@/types/canonicalJobs';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('displays concrete runtime inputs and declares pending-only Notification Manager ownership', async () => {
  vi.spyOn(canonicalJobs, 'actions').mockResolvedValue({ items: [], references: {}, nextCursor: null });
  const runtime = { owner: 'delivery-manager', targetMode: 'runtime', inputFields: ['target', 'text'],
    allowedStates: ['pending'], concurrency: 'job-config-cas+event-receipt', phase: 'before-admission',
    audit: 'final_operations', unknownPolicy: 'query-original' };
  function Owned() {
    const [value, setValue] = useState<JsonObject>({ steps: [], retention: { policyRef: 'test' },
      deliveryRuntime: runtime, notification: { on: ['completed'], recipients: [
        { recipientId: 'owned-notice', channelId: 'session', targetRef: 'ses_original' }] } });
    return <><CanonicalDefinitionFields kind="jobs" value={value} onChange={setValue} />
      <output data-testid="runtime-value">{JSON.stringify(value)}</output></>;
  }
  render(<Owned />);
  await waitFor(() => expect(canonicalJobs.actions).toHaveBeenCalled());
  expect(screen.getByLabelText('Runtime Job input declaration').textContent).toContain('target');
  fireEvent.click(screen.getByLabelText('Runtime notification target while pending'));
  const doc = JSON.parse(screen.getByTestId('runtime-value').textContent ?? '{}');
  expect(doc.notification.runtime).toEqual({ owner: 'notification-manager', fields: ['targetRef', 'payload'],
    allowedStates: ['pending'], effectiveStage: 'before-enqueue', concurrency: 'outbox-reservation+job-config-cas',
    audit: 'final_operations+outbox-receipt', unknownPolicy: 'query-original', acceptedPolicy: 'frozen' });
  fireEvent.change(screen.getByLabelText('targetRef'), { target: { value: '' } });
  const cleared = JSON.parse(screen.getByTestId('runtime-value').textContent ?? '{}');
  expect(cleared.notification.recipients[0]).toEqual({ recipientId: 'owned-notice', channelId: 'session', targetRef: null });
  expect(cleared.deliveryRuntime).toEqual(runtime);
  expect(screen.getByLabelText('Notification Manager contract').textContent).toContain('unknown');
});
