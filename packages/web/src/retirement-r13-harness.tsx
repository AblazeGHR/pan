import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import CanonicalJobsView from './views/CanonicalJobsView';
import { useAutomationAuth } from './stores/automationStore';
import './index.css';

// Owned fixture credentials only. Requests are intercepted by the browser runner.
useAutomationAuth.setState({ token: 'owned-r13-browser', expiresAt: Date.now() + 600000 });
createRoot(document.getElementById('root')!).render(<MemoryRouter><CanonicalJobsView /></MemoryRouter>);
