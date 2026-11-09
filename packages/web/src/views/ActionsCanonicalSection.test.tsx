// @vitest-environment jsdom
/**
 * The designed directory stays the page; canonical-only records are an
 * additive block that opens the version/release detail. Creation goes through
 * the read-only definition adapter into the canonical single authority.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LegacyActionsView } from './ActionsView';
import { actionRequest, canonicalRequest } from '@/services/api';
import { resetActionCachesForTests, useActionSessionsStore } from '@/stores/actionRegistryStore';

vi.mock('@/services/api', async (original) => ({
  ...(await original<object>()),
  actionRequest: vi.fn(),
  canonicalRequest: vi.fn(),
}));

const ref = (revision: number) => ({ id: 'canon_echo', revision, digest: String(revision).repeat(64) });

const canonicalAction = {
  id: 'canon_echo',
  name: 'Canonical Echo',
  description: 'canonical only',
  state: 'published',
  enabled: true,
  revision: 2,
  stateRevision: 2,
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  execution: { operation: 'process.command', config: {} },
  effectClass: 'read',
};

const versions = [
  { revision: 1, ref: ref(1), state: 'published', enabled: true, definition: { name: 'Canonical Echo' } },
  { revision: 2, ref: ref(2), state: 'published', enabled: true, definition: { name: 'Canonical Echo' } },
];

beforeEach(() => {
  sessionStorage.clear();
  resetActionCachesForTests();
  useActionSessionsStore.getState().reset();
  vi.mocked(actionRequest).mockImplementation(async (path) => {
    if (path === 'actions') return { revision: 3, actions: [], groups: [] } as never;
    if (path === 'sessions') return { sessions: [] } as never;
    if (path === 'mcp/servers') return { servers: [], loaded: true } as never;
    throw new Error(`Unhandled legacy ${path}`);
  });
  vi.mocked(canonicalRequest).mockImplementation(async (path) => {
    if (path === 'jobs/actions')
      return {
        items: [canonicalAction],
        versions: { canon_echo: { count: 2, latest: ref(2) } },
        references: { canon_echo: ref(2) },
      } as never;
    if (path === 'action-groups') return { items: [] } as never;
    if (String(path).startsWith('action-groups/legacy-action-bindings'))
      return { items: [], nextCursor: null } as never;
    if (path === 'jobs/actions/canon_echo/versions') return { items: versions } as never;
    throw new Error(`Unhandled canonical ${path}`);
  });
});

afterEach(cleanup);

it('shows canonical-only Actions inside the designed page and opens their versions', async () => {
  render(<LegacyActionsView />);
  // One table: the canonical-only Action sits in the same list as legacy rows.
  expect(await screen.findByRole('button', { name: 'Inspect Canonical Echo' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Canonical Echo' }));
  const select = await screen.findByLabelText('Action version');
  expect((select as HTMLSelectElement).value).toBe('2');
  expect(select.querySelectorAll('option')).toHaveLength(2);
  fireEvent.change(select, { target: { value: '1' } });
  expect((select as HTMLSelectElement).value).toBe('1');
  // Switching the viewing version is read-only.
  expect(vi.mocked(canonicalRequest).mock.calls.some(([, method]) => method === 'POST')).toBe(false);
});

it('does not require JSON for creation: the designed form posts a mapped definition', async () => {
  vi.mocked(canonicalRequest).mockImplementation(async (path, method, body) => {
    if (path === 'jobs/actions')
      return { items: [], versions: {}, references: {} } as never;
    if (path === 'action-groups') return { items: [] } as never;
    if (String(path).startsWith('action-groups/legacy-action-bindings'))
      return { items: [], nextCursor: null } as never;
    if (path === 'jobs/actions/definition-preview')
      return { definition: { name: 'Wire Action', execution: { operation: 'api.get', config: {} } } } as never;
    if (path === 'jobs/actions' && method === 'POST')
      return {
        status: 'completed',
        operationId: 'op',
        requestKey: (body as { requestKey: string }).requestKey,
        result: { object: { id: 'action_wire', revision: 1, stateRevision: 1 } },
      } as never;
    throw new Error(`Unhandled canonical ${path}`);
  });
  render(<LegacyActionsView />);
  fireEvent.click(await screen.findByRole('button', { name: 'New Action' }));
  fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: 'Wire Action' } });
  fireEvent.change(screen.getByLabelText(/^Description/), { target: { value: 'No JSON needed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create Action' }));
  await waitFor(() =>
    expect(
      vi.mocked(canonicalRequest).mock.calls.some(([path]) => path === 'jobs/actions/definition-preview'),
    ).toBe(true),
  );
  const preview = vi.mocked(canonicalRequest).mock.calls.find(
    ([path]) => path === 'jobs/actions/definition-preview',
  )!;
  const draft = (preview[2] as { draft: Record<string, unknown> }).draft;
  expect(draft).not.toHaveProperty('id');
  expect(draft.name).toBe('Wire Action');
  expect(
    vi.mocked(canonicalRequest).mock.calls.some(
      ([path, method]) => path === 'jobs/actions' && method === 'POST',
    ),
  ).toBe(true);
  // The legacy writer must not be used for a new record.
  expect(
    vi.mocked(actionRequest).mock.calls.some(
      ([path, method]) => path === 'actions/actions' && method === 'POST',
    ),
  ).toBe(false);
});

it('edits an expressible canonical Action through the designed form', async () => {
  vi.mocked(canonicalRequest).mockImplementation(async (path, method, body) => {
    if (path === 'jobs/actions')
      return {
        items: [{ ...canonicalAction, execution: { operation: 'api.get', config: { method: 'GET', path: '/api/sessions' } } }],
        versions: { canon_echo: { count: 2, latest: ref(2) } },
        references: { canon_echo: ref(2) },
      } as never;
    if (path === 'action-groups') return { items: [] } as never;
    if (String(path).startsWith('action-groups/legacy-action-bindings'))
      return { items: [], nextCursor: null } as never;
    if (path === 'jobs/actions/canon_echo/versions')
      return {
        items: [
          {
            revision: 2,
            ref: ref(2),
            state: 'published',
            enabled: true,
            definition: {
              name: 'Canonical Echo',
              description: 'canonical only',
              effectClass: 'read',
              inputSchema: { type: 'object', properties: {}, additionalProperties: false },
              execution: { operation: 'api.get', config: { method: 'GET', path: '/api/sessions' } },
            },
          },
        ],
      } as never;
    if (path === 'jobs/actions/definition-preview')
      return { definition: { name: 'Renamed head', execution: { operation: 'api.get', config: {} } } } as never;
    if (path === 'jobs/actions/canon_echo' && method === 'PUT')
      return { status: 'completed', operationId: 'op', requestKey: (body as { requestKey: string }).requestKey } as never;
    throw new Error(`Unhandled canonical ${path}`);
  });
  render(<LegacyActionsView />);
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect Canonical Echo' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit with form' }));
  // The designed form is populated from the canonical definition, not JSON.
  fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: 'Renamed head' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Action' }));
  await waitFor(() =>
    expect(
      vi.mocked(canonicalRequest).mock.calls.some(
        ([path, method]) => path === 'jobs/actions/canon_echo' && method === 'PUT',
      ),
    ).toBe(true),
  );
  const put = vi.mocked(canonicalRequest).mock.calls.find(
    ([path, method]) => path === 'jobs/actions/canon_echo' && method === 'PUT',
  )!;
  expect((put[2] as { expectedRevision: number }).expectedRevision).toBe(2);
  expect(
    vi.mocked(canonicalRequest).mock.calls.some(([path]) => path === 'jobs/actions/definition-preview'),
  ).toBe(true);
  // The legacy writer is not used for a canonical record.
  expect(
    vi.mocked(actionRequest).mock.calls.some(
      ([path, method]) => path?.startsWith('actions/actions/') && method === 'PUT',
    ),
  ).toBe(false);
});
