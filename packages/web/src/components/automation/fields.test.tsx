// @vitest-environment jsdom
import { useState } from 'react';
import { afterEach, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ConditionEditor } from './fields';
import type { Condition } from '@/types/automation';

afterEach(cleanup);
const compare = (value: number): Condition => ({ field: 'workers.runningCount', op: 'eq', value });
function Harness() {
  const [value, setValue] = useState<Condition>({ and: [compare(11), compare(22)] });
  return <><ConditionEditor value={value} onChange={setValue} /><output data-testid="condition">{JSON.stringify(value)}</output><button onClick={() => setValue(compare(33))}>Replace externally</button></>;
}
it('keeps the surviving literal display equal to the condition after deleting its predecessor', () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole('button', { name: 'Remove condition 1' }));
  expect((screen.getByLabelText('Compare value (JSON)') as HTMLInputElement).value).toBe('22');
  expect(JSON.parse(screen.getByTestId('condition').textContent!)).toMatchObject({ and: [{ value: 22 }] });
});
it('syncs an external literal and clears obsolete custom validity', () => {
  const { rerender } = render(<ConditionEditor value={compare(11)} onChange={() => {}} />);
  const input = screen.getByLabelText('Compare value (JSON)') as HTMLInputElement;
  fireEvent.change(input, { target: { value: '11e' } });
  expect(input.checkValidity()).toBe(false);
  rerender(<ConditionEditor value={compare(33)} onChange={() => {}} />);
  expect(input.value).toBe('33'); expect(input.checkValidity()).toBe(true);
});
it('retains unfinished invalid text on the surviving condition and on unrelated rerenders', () => {
  render(<Harness />);
  const input = screen.getAllByLabelText('Compare value (JSON)')[1] as HTMLInputElement;
  fireEvent.change(input, { target: { value: '22e' } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove condition 1' }));
  const survivor = screen.getByLabelText('Compare value (JSON)') as HTMLInputElement;
  expect(survivor.value).toBe('22e'); expect(survivor.checkValidity()).toBe(false);
});
it('preserves a valid unnormalized lexeme through its own edit and a sibling edit', () => {
  render(<Harness />);
  fireEvent.change(screen.getAllByLabelText('Compare value (JSON)')[0]!, { target: { value: '11.0' } });
  fireEvent.change(screen.getAllByLabelText('Compare value (JSON)')[1]!, { target: { value: '23' } });
  expect((screen.getAllByLabelText('Compare value (JSON)')[0] as HTMLInputElement).value).toBe('11.0');
});
it('replacing the condition structure resets obsolete literal buffers and validity', () => {
  render(<Harness />);
  fireEvent.change(screen.getAllByLabelText('Compare value (JSON)')[0]!, { target: { value: '11e' } });
  fireEvent.change(screen.getAllByLabelText('Condition type')[0]!, { target: { value: 'not' } });
  const input = screen.getByLabelText('Compare value (JSON)') as HTMLInputElement;
  expect(input.value).toBe('0'); expect(input.checkValidity()).toBe(true);
  expect(JSON.parse(screen.getByTestId('condition').textContent!)).toMatchObject({ not: { value: 0 } });
});
