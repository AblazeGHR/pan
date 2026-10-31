import { expect, it } from 'vitest';
import { partialJobNotificationMessage } from './partialJobNotification';

it('formats the existing terminal partial target identity for the visible consumer', () => {
  expect(partialJobNotificationMessage(['ses_b', 'ses_c'])).toBe('Job delivery incomplete: ses_b, ses_c');
});
