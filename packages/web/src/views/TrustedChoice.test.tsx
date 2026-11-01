// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { TrustedChoice } from './TrustedChoice';
import type { TrustedBinding } from './lockerModel';

afterEach(cleanup);
const choice = { id: 'go', label: 'Go' };
const common = { summary: 'Server supplied summary', triggerRevision: 1, targetIds: ['ses_target'] };

describe('trusted Card proof presentation', () => {
  it('preserves the v1 Action summary and digest', () => {
    const binding: TrustedBinding = { ...common, version: 1, actionId: 'old_action', actionDigest: 'a'.repeat(64), arguments: {} };
    render(<TrustedChoice choice={choice} binding={binding} />);
    expect(screen.getByText('Action: old_action')).toBeTruthy();
    expect(screen.getByText(binding.actionDigest)).toBeTruthy();
    expect(screen.getByText('Target: ses_target')).toBeTruthy();
  });

  it('shows actual v2 Job config revision and immutable Action references', () => {
    const binding: TrustedBinding = { ...common, version: 2, triggerId: 'trigger', triggerDigest: 'b'.repeat(64),
      jobId: 'job_original', jobRevision: 7, jobDigest: 'c'.repeat(64), authorityDigest: 'd'.repeat(64),
      actions: [{ id: 'product_assign_v2', revision: 1, digest: 'e'.repeat(64) }] };
    render(<TrustedChoice choice={choice} binding={binding} />);
    expect(screen.getByText('Job: job_original · configRevision 7')).toBeTruthy();
    expect(screen.getByText(binding.jobDigest)).toBeTruthy();
    expect(screen.getByText('Action: product_assign_v2 · revision 1')).toBeTruthy();
    expect(screen.getByText('e'.repeat(64))).toBeTruthy();
    expect(screen.getByText(common.summary)).toBeTruthy();
  });
});
