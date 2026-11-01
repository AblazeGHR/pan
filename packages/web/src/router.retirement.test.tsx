// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { RetiredJobsRoute } from './views/RetiredJobsRoute';

describe('retired Jobs route', () => {
  it('explains migration and links to canonical management', () => {
    render(<MemoryRouter><RetiredJobsRoute /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: 'Jobs 已迁移' })).toBeTruthy();
    expect(screen.getByRole('link', { name: '打开 Canonical Jobs' }).getAttribute('href')).toBe('/automation/canonical');
  });
});

