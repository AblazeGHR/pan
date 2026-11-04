// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { EmptyState, Notice, PageHeader, StateBadge, stateTone } from './display';

afterEach(cleanup);

describe('automation display primitives', () => {
  it('keeps the raw state visible and gives known families a distinct tone', () => {
    const { rerender } = render(<StateBadge state="enabled" />);
    expect(screen.getByText('enabled').className).toContain('text-success');
    for (const [state, tone] of [
      ['draft', 'warning'],
      ['paused', 'warning'],
      ['running', 'accent'],
      ['failed', 'danger'],
      ['unknown', 'danger'],
      ['retired', 'neutral'],
    ] as const) {
      rerender(<StateBadge state={state} />);
      expect(screen.getByText(state, { exact: true })).toBeTruthy();
      expect(stateTone(state)).toBe(tone);
    }
  });

  it('never hides an unrecognized persisted state', () => {
    render(<StateBadge state="future-provider-state" />);
    expect(screen.getByText('future-provider-state', { exact: true })).toBeTruthy();
    expect(stateTone('future-provider-state')).toBe('neutral');
  });

  it('renders page context and explicit navigation links', () => {
    render(
      <MemoryRouter>
        <PageHeader
          eyebrow="Pan / Automation"
          title="Canonical Jobs"
          description="统一定义与 Run 管理"
          actions={<a href="/automation">Automation</a>}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { level: 1, name: 'Canonical Jobs' })).toBeTruthy();
    expect(screen.getByText('Pan / Automation')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Automation' }).getAttribute('href')).toBe(
      '/automation',
    );
  });

  it('exposes alerts and empty states as readable text', () => {
    render(
      <>
        <Notice tone="danger" role="alert">
          没有执行此操作的权限
        </Notice>
        <EmptyState title="暂无 canonical Jobs。" hint="创建 Jobs 或刷新列表。" />
      </>,
    );
    expect(screen.getByRole('alert').textContent).toContain('没有执行此操作的权限');
    expect(screen.getByText('暂无 canonical Jobs。')).toBeTruthy();
    expect(screen.getByText('创建 Jobs 或刷新列表。')).toBeTruthy();
  });
});
