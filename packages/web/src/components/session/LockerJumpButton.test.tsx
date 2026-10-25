// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { LockerJumpButton } from './LockerJumpButton';

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function mount(
  count = 3,
  sessionName = 'Build agent',
  sessionId = 'ses_target',
) {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route
          path="/"
          element={
            <LockerJumpButton sessionId={sessionId} sessionName={sessionName} count={count} />
          }
        />
        <Route path="/locker" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Locker jump button', () => {
  afterEach(cleanup);

  it('labels the count and Session name, and navigates to the Session Locker', () => {
    mount(3);
    const button = screen.getByTestId('session-locker-jump');
    expect(button.getAttribute('aria-label')).toContain('未读 3 条');
    expect(button.getAttribute('aria-label')).toContain('Build agent');
    expect(button.getAttribute('title')).toContain('未读 3 条');
    fireEvent.click(button);
    expect(screen.getByTestId('location').textContent).toBe('/locker?session=ses_target');
  });

  it('is keyboard focusable as a native button', () => {
    mount(1);
    const button = screen.getByTestId('session-locker-jump');
    button.focus();
    expect(document.activeElement).toBe(button);
    expect(button.tagName).toBe('BUTTON');
  });

  it('escapes the Session id and caps the visible count', () => {
    mount(120, '名字 with spaces', 'ses/with spaces&x');
    const button = screen.getByTestId('session-locker-jump');
    expect(button.textContent).toContain('99+');
    expect(button.getAttribute('aria-label')).toContain('未读 120 条');
    expect(button.getAttribute('aria-label')).toContain('名字 with spaces');
    fireEvent.click(button);
    expect(screen.getByTestId('location').textContent).toBe(
      '/locker?session=ses%2Fwith%20spaces%26x',
    );
  });
});
