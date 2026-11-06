// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import UnifiedActionsView from './UnifiedActionsView';
import { canonicalRequest, ApiRequestError } from '@/services/api';
import { useAutomationAuth } from '@/stores/automationStore';
import { ReferenceUpdates } from '@/components/actions/ReferenceUpdates';
import { TriggerTarget } from '@/components/automation/TriggerTarget';
vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  canonicalRequest: vi.fn(),
}));
const request = vi.mocked(canonicalRequest);
const ref = (revision: number) => ({ id: 'echo', revision, digest: String(revision).repeat(64) });
const definition = {
  name: 'Echo',
  system: false,
  inputSchema: { type: 'object', properties: { value: { type: 'string' } }, required: ['value'] },
  execution: { operation: 'process.command', config: { argv: ['fixed'] } },
  outputSchema: { type: 'object' },
};
const versions = [1, 2].map((revision) => ({
  revision,
  ref: ref(revision),
  state: 'published',
  enabled: true,
  definition: { ...definition, description: `version ${revision}` },
}));
beforeEach(() => {
  sessionStorage.clear();
  useAutomationAuth.setState({
    token: 'owned',
    authenticated: true,
    expiresAt: Date.now() + 600000,
  });
  request.mockReset();
  request.mockImplementation(async (path) => {
    if (path === 'jobs/actions')
      return {
        items: [
          { ...definition, id: 'echo', revision: 2, stateRevision: 2, state: 'published' },
          { id: 'system', name: 'System', system: true, state: 'published' },
        ],
        versions: { echo: { count: 2 }, system: { count: 1 } },
      };
    if (path === 'jobs/actions/echo/versions') return { items: versions };
    if (path === 'action-groups') return { items: [] };
    throw new Error(`Unhandled ${path}`);
  });
});
afterEach(cleanup);
async function detail() {
  render(<UnifiedActionsView />);
  fireEvent.click(await screen.findByRole('button', { name: /Echo 用户/ }));
  await screen.findByRole('option', { name: 'v2 · published · enabled' });
}
it('aggregates logical Actions and changes only viewing version, with fixed ref and full definition', async () => {
  await detail();
  expect(screen.getByLabelText('Action version')).toHaveProperty('value', '2');
  fireEvent.change(screen.getByLabelText('Action version'), { target: { value: '1' } });
  expect(screen.getByLabelText('Action version')).toHaveProperty('value', '1');
  expect(request.mock.calls.filter(([, method]) => method === 'POST')).toHaveLength(0);
  expect(screen.getAllByText(/version 1/).length).toBeGreaterThan(0);
});
it('preserves original identity after lost response, switch and remount; only GET queries', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, ...args) => {
    if (path?.endsWith('/invocations')) throw new TypeError('lost response');
    if (path?.startsWith('operations?'))
      return {
        status: 'unknown',
        operationId: 'op-original',
        requestKey: 'original',
        result: { status: 'uncertain', stdout: 'partial' },
      };
    return original(path, ...args);
  });
  await detail();
  fireEvent.change(screen.getByLabelText('value'), { target: { value: 'hello' } });
  fireEvent.click(screen.getByRole('button', { name: '运行固定版本' }));
  await screen.findByText(/lost response/);
  const body = request.mock.calls.find(([p]) => p?.endsWith('/invocations'))![2] as {
    requestKey: string;
  };
  expect(screen.getByLabelText('Action version')).toHaveProperty('disabled', true);
  cleanup();
  await detail();
  fireEvent.click(screen.getByRole('button', { name: '查询原操作' }));
  await screen.findByText(/partial/);
  expect(request.mock.calls.filter(([p]) => p?.endsWith('/invocations'))).toHaveLength(1);
  expect(request.mock.calls.some(([p]) => p?.includes(encodeURIComponent(body.requestKey)))).toBe(
    true,
  );
});
it('a permission rejection is visible and never reported as successful invocation', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, ...args) => {
    if (path?.endsWith('/invocations')) throw new ApiRequestError(403, 'denied');
    return original(path, ...args);
  });
  await detail();
  fireEvent.click(screen.getByRole('button', { name: '运行固定版本' }));
  await screen.findByRole('alert');
  expect(screen.queryByRole('button', { name: '查询原操作' })).toBeNull();
  expect(screen.getByLabelText('Action version')).toHaveProperty('disabled', false);
});
it('all Groups preview includes legacy blockers and cannot apply a reduced batch', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, ...args) =>
    path === 'action-groups/preview-reference-update'
      ? {
          previewDigest: 'digest',
          items: [
            {
              groupId: 'canonical',
              expectedRevision: 1,
              blockers: [],
              changes: [{ from: ref(1), to: ref(2) }],
            },
          ],
          excluded: [{ groupId: 'old', reason: 'legacy_json_preview_only' }],
        }
      : original(path, ...args),
  );
  render(<ReferenceUpdates actionRef={ref(2)} />);
  fireEvent.click(screen.getByRole('button', { name: '预览此版本更新所有 Groups' }));
  await screen.findByText(/尚未完成安全 writer adoption/);
  expect(screen.getByRole('button', { name: '应用已预览批次' })).toHaveProperty('disabled', true);
  expect(request.mock.calls.some(([p]) => p?.endsWith('apply-reference-update'))).toBe(false);
});
it('Group apply binds exact preview digest, revisions and original selection', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, ...args) =>
    path === 'action-groups/preview-reference-update'
      ? {
          previewDigest: 'digest',
          items: [{ groupId: 'one', expectedRevision: 7, blockers: [] }],
          excluded: [],
        }
      : path === 'action-groups/apply-reference-update'
        ? { status: 'completed', operationId: 'applied' }
        : original(path, ...args),
  );
  render(<ReferenceUpdates actionRef={ref(2)} />);
  fireEvent.click(screen.getByRole('button', { name: '预览此版本更新所有 Groups' }));
  await screen.findByRole('button', { name: '应用已预览批次' });
  fireEvent.click(screen.getByRole('button', { name: '应用已预览批次' }));
  await waitFor(() =>
    expect(request.mock.calls.some(([p]) => p === 'action-groups/apply-reference-update')).toBe(
      true,
    ),
  );
  expect(
    request.mock.calls.find(([p]) => p === 'action-groups/apply-reference-update')![2],
  ).toMatchObject({
    selection: { scope: 'action', actionRef: ref(2) },
    targets: [{ groupId: 'one', expectedRevision: 7 }],
    previewDigest: 'digest',
  });
});
it('Trigger target changes preserve business fields, remove legacy target and freeze explicit version', async () => {
  let value: Record<string, unknown> = {
    name: 'Trigger',
    targetJobId: 'old',
    arguments: { x: { from: 'event', path: 'payload.x' } },
    concurrency: { maxActive: 3, maxPending: 4 },
  };
  const change = vi.fn((next) => {
    value = next;
  });
  const view = render(<TriggerTarget value={value} jobs={[]} onChange={change} />);
  fireEvent.change(screen.getByLabelText('Trigger target type'), { target: { value: 'action' } });
  expect(value).not.toHaveProperty('targetJobId');
  expect(value.arguments).toEqual({ x: { from: 'event', path: 'payload.x' } });
  view.rerender(<TriggerTarget value={value} jobs={[]} onChange={change} />);
  await screen.findByRole('option', { name: 'Echo' });
  fireEvent.change(screen.getByLabelText('Trigger Action'), { target: { value: 'echo' } });
  view.rerender(<TriggerTarget value={value} jobs={[]} onChange={change} />);
  await screen.findByRole('option', { name: 'v2 · published' });
  fireEvent.change(screen.getByLabelText('Trigger Action version'), { target: { value: '2' } });
  expect(value.target).toEqual({ type: 'action', actionRef: ref(2) });
  expect(value.concurrency).toEqual({ maxActive: 3, maxPending: 4 });
});
it('Job preview keeps exact steps, selected subscribers and frozen Run boundary', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, ...args) => {
    if (path?.includes('/job-references'))
      return {
        items: [
          {
            jobId: 'job1',
            name: 'Job One',
            configRevision: 9,
            steps: [
              { stepId: 'a', actionRef: ref(1) },
              { stepId: 'b', actionRef: ref(1) },
            ],
          },
        ],
      };
    if (path === 'jobs/action-references/preview')
      return {
        previewDigest: 'job-digest',
        jobs: [{ jobId: 'job1' }],
        subscribers: [{ triggerId: 'tr1', revision: 8, pinned: true }],
        blockers: (args[1] as { triggers: unknown[] }).triggers.length
          ? []
          : [{ reason: 'subscriber_not_selected' }],
        existingRuns: 'frozen',
      };
    if (path === 'jobs/action-references/apply')
      return { status: 'completed', result: { existingRuns: 'frozen' } };
    return original(path, ...args);
  });
  render(<ReferenceUpdates actionRef={ref(2)} />);
  fireEvent.click(screen.getByRole('button', { name: '加载引用与 Groups' }));
  fireEvent.click(await screen.findByLabelText(/Job One/));
  fireEvent.click(screen.getByRole('button', { name: '预览未来 Run 引用更新' }));
  expect(await screen.findByRole('button', { name: '应用已预览批次' })).toHaveProperty(
    'disabled',
    true,
  );
  fireEvent.click(screen.getByLabelText(/同步固定订阅 Trigger tr1/));
  fireEvent.click(screen.getByRole('button', { name: '预览未来 Run 引用更新' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '应用已预览批次' })).toHaveProperty(
      'disabled',
      false,
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: '应用已预览批次' }));
  await waitFor(() =>
    expect(request.mock.calls.some(([p]) => p === 'jobs/action-references/apply')).toBe(true),
  );
  expect(request.mock.calls.find(([p]) => p === 'jobs/action-references/apply')![2]).toMatchObject({
    previewDigest: 'job-digest',
    scope: {
      jobs: [
        {
          jobId: 'job1',
          expectedRevision: 9,
          steps: [
            { stepId: 'a', actionRef: ref(2) },
            { stepId: 'b', actionRef: ref(2) },
          ],
        },
      ],
      triggers: [{ triggerId: 'tr1', expectedRevision: 8 }],
    },
  });
});
it('a stale preview fails without a second apply or false success', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, ...args) => {
    if (path === 'action-groups/preview-reference-update')
      return {
        previewDigest: 'old',
        items: [{ groupId: 'g', expectedRevision: 1, blockers: [] }],
        excluded: [],
      };
    if (path === 'action-groups/apply-reference-update')
      throw new ApiRequestError(409, 'revision conflict');
    return original(path, ...args);
  });
  render(<ReferenceUpdates actionRef={ref(2)} />);
  fireEvent.click(screen.getByRole('button', { name: '预览此版本更新所有 Groups' }));
  fireEvent.click(await screen.findByRole('button', { name: '应用已预览批次' }));
  await screen.findByRole('alert');
  expect(
    request.mock.calls.filter(([p]) => p === 'action-groups/apply-reference-update'),
  ).toHaveLength(1);
  expect(screen.queryByRole('button', { name: '查询原更新操作' })).toBeNull();
});
it('query permission failure preserves an unresolved update identity across remount', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, ...args) => {
    if (path === 'action-groups/preview-reference-update')
      return {
        previewDigest: 'p',
        items: [{ groupId: 'g', expectedRevision: 1, blockers: [] }],
        excluded: [],
      };
    if (path === 'action-groups/apply-reference-update') throw new TypeError('lost');
    if (path?.startsWith('operations?')) throw new ApiRequestError(403, 'revoked');
    return original(path, ...args);
  });
  render(<ReferenceUpdates actionRef={ref(2)} />);
  fireEvent.click(screen.getByRole('button', { name: '预览此版本更新所有 Groups' }));
  fireEvent.click(await screen.findByRole('button', { name: '应用已预览批次' }));
  fireEvent.click(await screen.findByRole('button', { name: '查询原更新操作' }));
  await screen.findByRole('alert');
  cleanup();
  render(<ReferenceUpdates actionRef={ref(2)} />);
  expect(screen.getByRole('button', { name: '查询原更新操作' })).toBeTruthy();
  expect(
    request.mock.calls.filter(([p]) => p === 'action-groups/apply-reference-update'),
  ).toHaveLength(1);
});
it('does not invoke previous valid arguments while raw JSON is incomplete',async()=>{
 await detail();const input=screen.getByLabelText('Invocation arguments JSON');fireEvent.change(input,{target:{value:'{"value":"valid"}'}});fireEvent.change(input,{target:{value:'{"value":'}});expect(screen.getByRole('button',{name:'运行固定版本'})).toHaveProperty('disabled',true);fireEvent.click(screen.getByRole('button',{name:'运行固定版本'}));expect(request.mock.calls.filter(([p])=>p?.endsWith('/invocations'))).toHaveLength(0);
});
