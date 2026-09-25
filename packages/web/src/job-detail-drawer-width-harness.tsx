import { createRoot } from 'react-dom/client';
import { JobDetailDrawer } from './components/jobs/JobDetailDrawer';
import type { Job } from './types/jobs';
import './index.css';

const job: Job = {
  jobId: 'job-session-message',
  kind: 'session-message',
  status: 'pending',
  name: 'Session message',
  description: '',
  source: { type: 'agent' },
  target: { sessionId: 'session-1' },
  paused: false,
  schedule: [],
  runCount: 0,
  createdAt: 1,
  updatedAt: 1,
};

createRoot(document.getElementById('root')!).render(
  <JobDetailDrawer
    job={job}
    kindLabel="Session message"
    runs={[]}
    runsLoading={false}
    onLoadMoreRuns={() => {}}
    onClose={() => {}}
    onRunNow={() => {}}
    onTogglePaused={() => {}}
    onDelete={() => {}}
    onChangeTarget={() => {}}
    onEdit={() => {}}
    onToggleEntryEnabled={() => {}}
  />,
);
