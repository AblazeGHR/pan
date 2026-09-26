import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import JobsView from './views/JobsView';
import './index.css';

const jobs = Array.from({ length: 48 }, (_, index) => {
  const number = String(index + 1).padStart(2, '0');
  const timedOut = index % 2 === 0;
  return {
    jobId: `job-layout-${number}`,
    kind: timedOut ? 'main-lifecycle' : 'session-message',
    status: timedOut ? 'timed_out' : 'completed',
    name: `Job row ${number}`,
    description: `Responsive layout fixture for row ${number}`,
    source: { type: 'system' },
    target: { sessionId: `session-${number}` },
    paused: false,
    schedule: { legacy: 'scalar-safe' },
    runCount: index,
    createdAt: 1,
    updatedAt: 48 - index,
  };
});

createRoot(document.getElementById('root')!).render(
  <MemoryRouter>
    <div className="flex h-[100dvh] min-h-0 flex-col">
      <JobsView />
    </div>
  </MemoryRouter>,
);

export { jobs };
