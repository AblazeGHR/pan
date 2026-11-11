import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import JobsView from './views/JobsView';
import { useAutomationAuth } from './stores/automationStore';
import './index.css';
declare global { interface Window { __ownedR14Token: string } }
useAutomationAuth.setState({ token: window.__ownedR14Token, authenticated: true, expiresAt: Date.now() + 600000 });
createRoot(document.getElementById('root')!).render(<MemoryRouter><main className="h-screen"><JobsView /></main></MemoryRouter>);
