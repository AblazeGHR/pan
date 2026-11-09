// @vitest-environment jsdom
import { expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { RetiredJobsRoute } from './views/RetiredJobsRoute';
it('redirects retired aliases directly to the single Jobs entry', () => {
  render(<MemoryRouter initialEntries={['/schedules']}><Routes><Route path="/schedules" element={<RetiredJobsRoute />} /><Route path="/jobs" element={<h1>Jobs destination</h1>} /></Routes></MemoryRouter>);
  expect(screen.getByRole('heading', {name: 'Jobs destination'})).toBeTruthy();
});
