import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import CanonicalJobsView from './views/CanonicalJobsView';
import { useAutomationAuth } from './stores/automationStore';
import './index.css';

declare global { interface Window { __ownedR14Token: string } }
// Only the owned browser runner supplies its temporary server's login token.
// This entry uses real canonical HTTP; no route interception or fixture replies.
useAutomationAuth.setState({ token: window.__ownedR14Token, expiresAt: Date.now() + 600000 });
createRoot(document.getElementById('root')!).render(<MemoryRouter><CanonicalJobsView /></MemoryRouter>);
